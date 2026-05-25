"""
wine_reg.py — read/write Wine registry files (user.reg) in place of winreg.

Background
----------
On Windows, Pixel Gun 3D stores skin data in the Windows registry under
  HKEY_CURRENT_USER\\Software\\Pixel Gun Team\\Pixel Gun 3D

When the game runs on Linux via Proton, Wine emulates that registry as a
plain-text file at:
  <proton_prefix>/user.reg   (text, this is what we use)

The text format looks like:

  [Software\\Pixel Gun Team\\Pixel Gun 3D] 1234567890
  #time=1dabcdef1234567
  "User Skins"="{...json...}"
  "SomeInt"=dword:00000001
  "SomeBin"=hex:01,02,03

IMPORTANT: This parser preserves ALL line types faithfully — dword: values,
hex: values, #time= timestamps, key-header timestamps, blank lines, and
comments.  Only "string"="string" values are decoded into Python strings for
reading; everything else is kept as verbatim raw text so a round-trip never
corrupts the registry.

The original wine_reg.py silently dropped dword, hex, and #time lines, which
caused the game to think it was launching for the first time on every run.

This module provides a minimal subset of the winreg API surface that
skinimporter.py actually uses:
  - open_key / create_key (context managers)
  - query_value / set_value / delete_value
  - enum_values
"""

import os
import re
import shutil
import threading
from contextlib import contextmanager
from typing import Generator

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# String escaping
# ---------------------------------------------------------------------------

def _unescape(s: str) -> str:
    result: list[str] = []
    i = 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            if nxt == "\\":
                result.append("\\")
            elif nxt == '"':
                result.append('"')
            elif nxt == "n":
                result.append("\n")
            elif nxt == "r":
                result.append("\r")
            elif nxt == "t":
                result.append("\t")
            else:
                result.append(nxt)
            i += 2
        else:
            result.append(s[i])
            i += 1
    return "".join(result)


def _escape(s: str) -> str:
    return (
        s.replace("\\", "\\\\")
         .replace('"', '\\"')
         .replace("\n", "\\n")
         .replace("\r", "\\r")
         .replace("\t", "\\t")
    )


# ---------------------------------------------------------------------------
# Regexes
# ---------------------------------------------------------------------------

# Key header: [Software\\Pixel Gun Team\\Pixel Gun 3D] 1234567890
_KEY_RE = re.compile(r"^\[([^\]]+)\](.*)")

# String value: "Name"="value"
_VALUE_RE = re.compile(r'^"((?:[^"\\]|\\.)*?)"\s*=\s*"((?:[^"\\]|\\.)*)"')

# Any value (string or typed) — just capture the name
_ANY_VALUE_NAME_RE = re.compile(r'^"((?:[^"\\]|\\.)*?)"\s*=')


# ---------------------------------------------------------------------------
# _KeyBlock — all lines for one key, ordered, with full fidelity
# ---------------------------------------------------------------------------

class _KeyBlock:
    """
    Entries are tuples:
      ("raw", raw_line)              – blank lines, comments, #time=, dword/hex values
      ("str", name, value, raw_line) – parsed string value
    """

    def __init__(self, header_suffix: str) -> None:
        self.header_suffix = header_suffix
        self.entries: list[tuple] = []

    def get_string_value(self, name: str) -> str:
        for e in self.entries:
            if e[0] == "str" and e[1] == name:
                return e[2]
        raise FileNotFoundError(f"Value '{name}' not found")

    def set_string_value(self, name: str, value: str) -> None:
        new_raw = f'"{_escape(name)}"="{_escape(value)}"\n'
        for i, e in enumerate(self.entries):
            if e[0] == "str" and e[1] == name:
                self.entries[i] = ("str", name, value, new_raw)
                return
            if e[0] == "raw":
                m = _ANY_VALUE_NAME_RE.match(e[1])
                if m and _unescape(m.group(1)) == name:
                    self.entries[i] = ("str", name, value, new_raw)
                    return
        self.entries.append(("str", name, value, new_raw))

    def delete_value(self, name: str) -> None:
        for i, e in enumerate(self.entries):
            if e[0] == "str" and e[1] == name:
                del self.entries[i]
                return
            if e[0] == "raw":
                m = _ANY_VALUE_NAME_RE.match(e[1])
                if m and _unescape(m.group(1)) == name:
                    del self.entries[i]
                    return
        raise FileNotFoundError(f"Value '{name}' not found")

    def enum_string_values(self) -> list[tuple[str, str]]:
        return [(e[1], e[2]) for e in self.entries if e[0] == "str"]

    def serialise(self, key_path: str) -> list[str]:
        lines: list[str] = [f"[{key_path}]{self.header_suffix}\n"]
        for e in self.entries:
            lines.append(e[1] if e[0] == "raw" else e[3])
        return lines


# ---------------------------------------------------------------------------
# RegFile
# ---------------------------------------------------------------------------

class RegFile:
    """Parsed, in-memory, lossless view of a Wine user.reg file."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._blocks: list[tuple[str, _KeyBlock]] = []
        self._index: dict[str, int] = {}
        self._preamble: list[str] = []
        if os.path.exists(path):
            self._parse()

    def _parse(self) -> None:
        with open(self.path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()

        current_block: _KeyBlock | None = None
        header_done = False

        for line in lines:
            stripped = line.rstrip("\r\n")
            raw_line = line if line.endswith("\n") else line + "\n"

            key_match = _KEY_RE.match(stripped)
            if key_match:
                header_done = True
                key_path = key_match.group(1)
                suffix = key_match.group(2)
                if key_path in self._index:
                    current_block = self._blocks[self._index[key_path]][1]
                else:
                    block = _KeyBlock(suffix)
                    self._index[key_path] = len(self._blocks)
                    self._blocks.append((key_path, block))
                    current_block = block
                continue

            if not header_done:
                self._preamble.append(line)
                continue

            if current_block is None:
                continue

            val_match = _VALUE_RE.match(stripped)
            if val_match:
                name = _unescape(val_match.group(1))
                value = _unescape(val_match.group(2))
                current_block.entries.append(("str", name, value, raw_line))
            else:
                # Preserve verbatim: blank lines, #time=, dword:, hex:, comments
                current_block.entries.append(("raw", raw_line))

    def _get_block(self, key_path: str) -> "_KeyBlock | None":
        idx = self._index.get(key_path)
        return self._blocks[idx][1] if idx is not None else None

    def ensure_key(self, key_path: str) -> None:
        if key_path not in self._index:
            block = _KeyBlock("")
            self._index[key_path] = len(self._blocks)
            self._blocks.append((key_path, block))

    def get_value(self, key_path: str, value_name: str) -> str:
        block = self._get_block(key_path)
        if block is None:
            raise FileNotFoundError(f"Key '{key_path}' not found")
        return block.get_string_value(value_name)

    def set_value(self, key_path: str, value_name: str, value: str) -> None:
        self.ensure_key(key_path)
        self._get_block(key_path).set_string_value(value_name, value)  # type: ignore[union-attr]

    def delete_value(self, key_path: str, value_name: str) -> None:
        block = self._get_block(key_path)
        if block is None:
            raise FileNotFoundError(f"Key '{key_path}' not found")
        block.delete_value(value_name)

    def enum_values(self, key_path: str) -> list[tuple[str, str]]:
        block = self._get_block(key_path)
        return [] if block is None else block.enum_string_values()

    def save(self) -> None:
        """Write back to disk atomically, keeping one .bak copy."""
        lines: list[str] = list(self._preamble)
        if not lines:
            lines = [
                "WINE REGISTRY Version 2\n",
                ";; All keys relative to \\\\User\\\\S-1-5-21\n",
                "\n",
            ]
        for key_path, block in self._blocks:
            lines.append("\n")
            lines.extend(block.serialise(key_path))

        tmp_path = self.path + ".tmp"
        bak_path = self.path + ".bak"
        with open(tmp_path, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        if os.path.exists(self.path):
            shutil.copy2(self.path, bak_path)
        os.replace(tmp_path, self.path)


# ---------------------------------------------------------------------------
# Key handles
# ---------------------------------------------------------------------------

class KeyHandle:
    def __init__(self, reg: RegFile, key_path: str, write: bool = False) -> None:
        self._reg = reg
        self._key_path = key_path
        self._write = write

    def query_value(self, value_name: str) -> str:
        return self._reg.get_value(self._key_path, value_name)

    def set_value(self, value_name: str, value: str) -> None:
        if not self._write:
            raise PermissionError("Key opened read-only")
        self._reg.set_value(self._key_path, value_name, value)

    def delete_value(self, value_name: str) -> None:
        if not self._write:
            raise PermissionError("Key opened read-only")
        self._reg.delete_value(self._key_path, value_name)

    def enum_values(self) -> list[tuple[str, str]]:
        return self._reg.enum_values(self._key_path)

    def __enter__(self) -> "KeyHandle":
        return self

    def __exit__(self, *_) -> None:
        if self._write:
            self._reg.save()


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------

def open_reg_file(reg_path: str) -> RegFile:
    return RegFile(reg_path)


@contextmanager
def open_key(reg: RegFile, key_path: str) -> Generator[KeyHandle, None, None]:
    if reg._get_block(key_path) is None:
        raise FileNotFoundError(f"Key '{key_path}' not found")
    yield KeyHandle(reg, key_path, write=False)


@contextmanager
def create_key(reg: RegFile, key_path: str) -> Generator[KeyHandle, None, None]:
    with _lock:
        reg.ensure_key(key_path)
        handle = KeyHandle(reg, key_path, write=True)
        try:
            yield handle
        finally:
            reg.save()
