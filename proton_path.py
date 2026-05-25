"""
proton_path.py — locate the Pixel Gun 3D Proton prefix on Linux.

Pixel Gun 3D on Steam has App ID 1047820.  When run via Proton, Steam
creates a compatibility data directory (a "prefix") that contains a
Wine-style filesystem and registry.  This module finds that prefix so
we know where to read/write the game's registry.

Search order
------------
1. A user-overridden path saved in ~/.config/pg3d-skinimporter/config.json
2. All Steam library folders listed in libraryfolders.vdf (both the
   default Steam location and any extras the user has added)
3. Flatpak Steam location (~/.var/app/com.valvesoftware.Steam/...)

The prefix we return is the directory that contains user.reg, e.g.:
  ~/.steam/steam/steamapps/compatdata/1047820/pfx/
"""

import json
import os
import re

PG3D_APP_ID = "2524890"

CONFIG_DIR = os.path.expanduser("~/.config/pg3d-skinimporter")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

# Candidate base Steam directories (in search order)
_STEAM_BASES = [
    os.path.expanduser("~/.steam/steam"),
    os.path.expanduser("~/.local/share/Steam"),
    os.path.expanduser("~/.var/app/com.valvesoftware.Steam/data/Steam"),
    os.path.expanduser("~/snap/steam/common/.steam/steam"),
]


# ---------------------------------------------------------------------------
# Config helpers
# ---------------------------------------------------------------------------

def load_config() -> dict:
    if os.path.exists(CONFIG_FILE):
        try:
            with open(CONFIG_FILE) as fh:
                return json.load(fh)
        except Exception:
            pass
    return {}


def save_config(data: dict) -> None:
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as fh:
        json.dump(data, fh, indent=2)


def get_saved_prefix_path() -> str | None:
    """Return the manually-set prefix path from config, if any."""
    return load_config().get("proton_prefix_path")


def save_prefix_path(path: str) -> None:
    cfg = load_config()
    cfg["proton_prefix_path"] = path
    save_config(cfg)


# ---------------------------------------------------------------------------
# libraryfolders.vdf parser
# ---------------------------------------------------------------------------

def _parse_library_folders(vdf_path: str) -> list[str]:
    """
    Extract library root paths from libraryfolders.vdf.
    We use a simple regex rather than a full VDF parser.
    """
    paths: list[str] = []
    if not os.path.exists(vdf_path):
        return paths
    try:
        with open(vdf_path, encoding="utf-8", errors="replace") as fh:
            content = fh.read()
    except OSError:
        return paths

    # Match "path"  "value" entries (handles varying whitespace/quotes)
    for match in re.finditer(r'"path"\s+"([^"]+)"', content):
        paths.append(match.group(1))
    return paths


def _all_steam_library_steamapps_dirs() -> list[str]:
    """Return a list of .../steamapps directories from all known Steam libraries."""
    seen: set[str] = set()
    result: list[str] = []

    for base in _STEAM_BASES:
        steamapps = os.path.join(base, "steamapps")
        if steamapps not in seen and os.path.isdir(steamapps):
            seen.add(steamapps)
            result.append(steamapps)

        vdf_path = os.path.join(steamapps, "libraryfolders.vdf")
        for lib_root in _parse_library_folders(vdf_path):
            lib_steamapps = os.path.join(lib_root, "steamapps")
            if lib_steamapps not in seen and os.path.isdir(lib_steamapps):
                seen.add(lib_steamapps)
                result.append(lib_steamapps)

    return result


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def find_proton_prefix() -> str | None:
    """
    Return the Proton prefix path for PG3D (the directory containing user.reg),
    or None if it cannot be found automatically.

    Checks the user-saved override first, then scans all Steam library folders.
    """
    # 1. Check user config
    saved = get_saved_prefix_path()
    if saved:
        if _is_valid_prefix(saved):
            return saved
        # Saved path is stale — ignore but don't delete it so the user can fix it

    # 2. Scan Steam libraries
    for steamapps in _all_steam_library_steamapps_dirs():
        prefix = os.path.join(steamapps, "compatdata", PG3D_APP_ID, "pfx")
        if _is_valid_prefix(prefix):
            return prefix

    return None


def _is_valid_prefix(prefix_path: str) -> bool:
    """A valid Proton prefix contains user.reg."""
    return os.path.isfile(os.path.join(prefix_path, "user.reg"))


def user_reg_path(prefix: str) -> str:
    """Return the full path to user.reg inside a prefix."""
    return os.path.join(prefix, "user.reg")
