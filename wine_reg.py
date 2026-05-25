"""
wine_reg.py — read/write Wine registry files (user.reg) in place of winreg.

Background
----------
On Windows, Pixel Gun 3D stores skin data in the Windows registry under
  HKEY_CURRENT_USER\\Software\\Pixel Gun Team\\Pixel Gun 3D

When the game runs on Linux via Proton, Wine emulates that registry as a
plain-text file at:
  <proton_prefix>/drive_c/users/<username>/ntuser.dat   (binary, skip)
  <proton_prefix>/user.reg                              (text, this is what we use)

The text format looks like:

  [Software\\Pixel Gun Team\\Pixel Gun 3D]
  "User Skins"="{...json...}"
  "User Skins_h1234567890"="{...json...}"

Values are double-quoted strings after an = sign.  Backslash escapes inside
the string follow Wine conventions: \\ → \, \" → ", \n → newline, etc.

This module provides a minimal subset of the winreg API surface that
skinimporter.py actually uses:
  - open_key / create_key (context managers)
  - query_value / set_value / delete_value
  - enum_values
"""

import os
import re
import threading
from contextlib import contextmanager
from typing import Generator

# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

_lock = threading.Lock()


def _unescape(s: str) -> str:
    """Decode Wine registry string escapes."""
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
    """Encode a Python string as a Wine registry string value (no surrounding quotes)."""
    return s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t")


# Regex to match a Wine registry key header, e.g.
#   [Software\\Pixel Gun Team\\Pixel Gun 3D]
_KEY_RE = re.compile(r"^\[([^\]]+)\]")

# Regex to match a value line, e.g.
#   "User Skins"="some json"
_VALUE_RE = re.compile(r'^"((?:[^"\\]|\\.)*)"\s*=\s*"((?:[^"\\]|\\.)*)"')


class RegFile:
    """Parsed, in-memory view of a Wine user.reg file."""

    def __init__(self, path: str) -> None:
        self.path = path
        # keys: normalised-key-path → {value_name: raw_value_string}
        self._data: dict[str, dict[str, str]] = {}
        self._header_lines: list[str] = []  # lines before the first key block
        if os.path.exists(path):
            self._parse()

    # ------------------------------------------------------------------
    # Parsing
    # ------------------------------------------------------------------

    def _parse(self) -> None:
        with open(self.path, encoding="utf-8", errors="replace") as fh:
            lines = fh.readlines()

        current_key: str | None = None
        header_done = False

        for line in lines:
            stripped = line.rstrip("\r\n")

            key_match = _KEY_RE.match(stripped)
            if key_match:
                header_done = True
                current_key = key_match.group(1)
                if current_key not in self._data:
                    self._data[current_key] = {}
                continue

            if not header_done:
                self._header_lines.append(line)
                continue

            if current_key is None:
                continue

            val_match = _VALUE_RE.match(stripped)
            if val_match:
                name = _unescape(val_match.group(1))
                value = _unescape(val_match.group(2))
                self._data[current_key][name] = value

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def ensure_key(self, key_path: str) -> None:
        if key_path not in self._data:
            self._data[key_path] = {}

    def get_value(self, key_path: str, value_name: str) -> str:
        key = self._data.get(key_path, {})
        if value_name not in key:
            raise FileNotFoundError(f"Value '{value_name}' not found in key '{key_path}'")
        return key[value_name]

    def set_value(self, key_path: str, value_name: str, value: str) -> None:
        self.ensure_key(key_path)
        self._data[key_path][value_name] = value

    def delete_value(self, key_path: str, value_name: str) -> None:
        key = self._data.get(key_path, {})
        if value_name not in key:
            raise FileNotFoundError(f"Value '{value_name}' not found")
        del key[value_name]

    def enum_values(self, key_path: str) -> list[tuple[str, str]]:
        """Return list of (name, value) tuples for all values under key_path."""
        return list(self._data.get(key_path, {}).items())

    def save(self) -> None:
        """Write the registry back to disk."""
        lines: list[str] = list(self._header_lines)
        if not lines:
            # Minimal Wine reg file header
            lines = [
                "WINE REGISTRY Version 2\n",
                ";; All keys relative to \\\\User\\\\S-1-5-21\n",
                "\n",
            ]

        for key_path, values in self._data.items():
            lines.append(f"\n[{key_path}]\n")
            for name, value in values.items():
                esc_name = _escape(name)
                esc_value = _escape(value)
                lines.append(f'"{esc_name}"="{esc_value}"\n')

        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.writelines(lines)
        os.replace(tmp, self.path)


# ---------------------------------------------------------------------------
# Context-manager key handles (mimic winreg.CreateKey / OpenKey)
# ---------------------------------------------------------------------------

class KeyHandle:
    """Thin wrapper around (RegFile, key_path) that mimics a winreg key handle."""

    def __init__(self, reg: RegFile, key_path: str, write: bool = False) -> None:
        self._reg = reg
        self._key_path = key_path
        self._write = write

    # --- value access ---

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

    def save(self) -> None:
        if self._write:
            self._reg.save()

    def __enter__(self) -> "KeyHandle":
        return self

    def __exit__(self, *_) -> None:
        if self._write:
            self._reg.save()


# ---------------------------------------------------------------------------
# Module-level helpers used by skinimporter.py
# ---------------------------------------------------------------------------

def open_reg_file(reg_path: str) -> RegFile:
    return RegFile(reg_path)


@contextmanager
def open_key(reg: RegFile, key_path: str) -> Generator[KeyHandle, None, None]:
    """Read-only key handle."""
    if key_path not in reg._data:
        raise FileNotFoundError(f"Key '{key_path}' not found")
    yield KeyHandle(reg, key_path, write=False)


@contextmanager
def create_key(reg: RegFile, key_path: str) -> Generator[KeyHandle, None, None]:
    """Read-write key handle; creates the key if it doesn't exist."""
    with _lock:
        reg.ensure_key(key_path)
        handle = KeyHandle(reg, key_path, write=True)
        try:
            yield handle
        finally:
            reg.save()
