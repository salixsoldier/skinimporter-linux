"""
skinimporter.py — read/write PG3D skin data in the Wine registry.

Changes from the original (Windows) version
--------------------------------------------
- ALL winreg imports and calls replaced with wine_reg + proton_path.
- The registry key path uses forward slashes as Wine user.reg uses them
  (Wine internally normalises to \\, but user.reg stores them as \\).
- A module-level `init(prefix_path)` call must be made before anything
  else; main.py calls this once the prefix has been resolved.
- Everything else — skin IDs, JSON structure, hashed value names — is
  identical to the original so existing registry data is fully compatible.
"""

import base64
import json
import os
import re

from wine_reg import RegFile, open_key, create_key, open_reg_file
import proton_path as _pp

# ---------------------------------------------------------------------------
# Registry constants  (same logical path as on Windows)
# ---------------------------------------------------------------------------

# Wine user.reg stores HKCU keys without the HKEY_CURRENT_USER prefix.
# The path separator in the file is \\ (escaped backslash).
REGISTRY_KEY_PATH = "Software\\\\Pixel Gun Team\\\\Pixel Gun 3D"

USER_SKINS_VALUE = "User Skins"
USER_NAME_SKINS_VALUE = "User Name Skins"
CURRENT_EQUIPED_SKIN_VALUE = "Current Equiped Skin"
STARTING_SKIN_ID = 1001

# ---------------------------------------------------------------------------
# Module-level state: the active RegFile
# ---------------------------------------------------------------------------

_reg_file: RegFile | None = None


def init(prefix_path: str) -> None:
    """
    Initialise the module with the Proton prefix path.
    Must be called before any skin operations.
    """
    global _reg_file
    reg_path = _pp.user_reg_path(prefix_path)
    _reg_file = open_reg_file(reg_path)


def _get_reg() -> RegFile:
    if _reg_file is None:
        raise OSError(
            "Registry not initialised. "
            "Make sure a valid Proton prefix path is set in Settings."
        )
    return _reg_file


# ---------------------------------------------------------------------------
# Hashed value name helpers  (identical logic to original)
# ---------------------------------------------------------------------------

def _find_hashed_value_names(key_handle, base_name: str) -> list[str]:
    pattern = re.compile(r"^" + re.escape(base_name) + r"_h\d+$")
    matches = []
    for name, _ in key_handle.enum_values():
        if pattern.match(name):
            matches.append(name)
    return matches


def _read_registry_json(key_handle, value_name: str) -> dict[str, str]:
    names_to_try = _find_hashed_value_names(key_handle, value_name) + [value_name]
    for name in names_to_try:
        try:
            raw_value = key_handle.query_value(name)
        except FileNotFoundError:
            continue

        if not raw_value:
            continue

        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            continue

        if isinstance(parsed, dict):
            return {str(k): str(v) for k, v in parsed.items()}
    return {}


def _write_registry_json(key_handle, value_name: str, value: dict[str, str]) -> None:
    json_str = json.dumps(value)
    key_handle.set_value(value_name, json_str)
    for hashed_name in _find_hashed_value_names(key_handle, value_name):
        key_handle.set_value(hashed_name, json_str)


# ---------------------------------------------------------------------------
# Public API  (same signatures as the original)
# ---------------------------------------------------------------------------

def png_to_base64(image_path: str) -> str:
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("ascii")


def add_skins(skin_base64_list: list[str], skin_names_list: list[str]) -> list[str]:
    if len(skin_base64_list) != len(skin_names_list):
        raise ValueError("skin_base64_list and skin_names_list must have the same length")
    if not skin_base64_list:
        return []

    reg = _get_reg()
    with create_key(reg, REGISTRY_KEY_PATH) as key:
        user_skins: dict[str, str] = {}
        user_name_skins: dict[str, str] = {}
        next_skin_id = STARTING_SKIN_ID

        added_skin_ids = []
        for skin_base64, skin_name in zip(skin_base64_list, skin_names_list):
            skin_id = str(next_skin_id)
            user_skins[skin_id] = skin_base64
            user_name_skins[skin_id] = skin_name
            added_skin_ids.append(skin_id)
            next_skin_id += 1

        _write_registry_json(key, USER_SKINS_VALUE, user_skins)
        _write_registry_json(key, USER_NAME_SKINS_VALUE, user_name_skins)

        first_id = added_skin_ids[0]
        key.set_value(CURRENT_EQUIPED_SKIN_VALUE, first_id)
        for hashed_name in _find_hashed_value_names(key, CURRENT_EQUIPED_SKIN_VALUE):
            key.set_value(hashed_name, first_id)

    return added_skin_ids


def add_skins_from_files(image_paths: list[str]) -> list[str]:
    skin_base64_list = [png_to_base64(p) for p in image_paths]
    skin_names_list = [os.path.basename(p) for p in image_paths]
    return add_skins(skin_base64_list, skin_names_list)


def append_skin(skin_base64: str, skin_name: str) -> str:
    reg = _get_reg()
    with create_key(reg, REGISTRY_KEY_PATH) as key:
        user_skins = _read_registry_json(key, USER_SKINS_VALUE)
        user_name_skins = _read_registry_json(key, USER_NAME_SKINS_VALUE)

        existing_ids = [int(k) for k in user_skins.keys() if k.isdigit()]
        skin_id = str(max(existing_ids, default=STARTING_SKIN_ID - 1) + 1)

        user_skins[skin_id] = skin_base64
        user_name_skins[skin_id] = skin_name

        _write_registry_json(key, USER_SKINS_VALUE, user_skins)
        _write_registry_json(key, USER_NAME_SKINS_VALUE, user_name_skins)

        try:
            key.query_value(CURRENT_EQUIPED_SKIN_VALUE)
        except FileNotFoundError:
            key.set_value(CURRENT_EQUIPED_SKIN_VALUE, skin_id)
            for hashed_name in _find_hashed_value_names(key, CURRENT_EQUIPED_SKIN_VALUE):
                key.set_value(hashed_name, skin_id)

    return skin_id


def get_added_skins() -> list[dict[str, str]]:
    reg = _get_reg()
    try:
        with open_key(reg, REGISTRY_KEY_PATH) as key:
            user_skins = _read_registry_json(key, USER_SKINS_VALUE)
            user_name_skins = _read_registry_json(key, USER_NAME_SKINS_VALUE)
    except FileNotFoundError:
        return []

    def sort_key(item: tuple[str, str]) -> tuple[int, str]:
        skin_id = item[0]
        return (0, f"{int(skin_id):010d}") if skin_id.isdigit() else (1, skin_id)

    records: list[dict[str, str]] = []
    for skin_id, skin_b64 in sorted(user_skins.items(), key=sort_key):
        records.append({
            "id": skin_id,
            "name": user_name_skins.get(skin_id, f"Skin {skin_id}"),
            "skin": skin_b64,
        })
    return records


def delete_skin(skin_id: str) -> None:
    reg = _get_reg()
    with create_key(reg, REGISTRY_KEY_PATH) as key:
        user_skins = _read_registry_json(key, USER_SKINS_VALUE)
        user_name_skins = _read_registry_json(key, USER_NAME_SKINS_VALUE)

        if skin_id not in user_skins and skin_id not in user_name_skins:
            return

        user_skins.pop(skin_id, None)
        user_name_skins.pop(skin_id, None)

        _write_registry_json(key, USER_SKINS_VALUE, user_skins)
        _write_registry_json(key, USER_NAME_SKINS_VALUE, user_name_skins)

        current_value = None
        for current_name in _find_hashed_value_names(key, CURRENT_EQUIPED_SKIN_VALUE) + [CURRENT_EQUIPED_SKIN_VALUE]:
            try:
                current_value = key.query_value(current_name)
                break
            except FileNotFoundError:
                continue

        if str(current_value) == skin_id:
            digit_ids = sorted([int(eid) for eid in user_skins.keys() if eid.isdigit()])
            next_id = str(digit_ids[0]) if digit_ids else None

            if next_id is not None:
                key.set_value(CURRENT_EQUIPED_SKIN_VALUE, next_id)
                for hashed_name in _find_hashed_value_names(key, CURRENT_EQUIPED_SKIN_VALUE):
                    key.set_value(hashed_name, next_id)
            else:
                for hashed_name in _find_hashed_value_names(key, CURRENT_EQUIPED_SKIN_VALUE):
                    try:
                        key.delete_value(hashed_name)
                    except FileNotFoundError:
                        pass
                try:
                    key.delete_value(CURRENT_EQUIPED_SKIN_VALUE)
                except FileNotFoundError:
                    pass


def clear_modded_skins() -> None:
    managed_values = [USER_SKINS_VALUE, USER_NAME_SKINS_VALUE, CURRENT_EQUIPED_SKIN_VALUE]
    reg = _get_reg()
    try:
        with create_key(reg, REGISTRY_KEY_PATH) as key:
            for base_name in managed_values:
                for hashed_name in _find_hashed_value_names(key, base_name):
                    try:
                        key.delete_value(hashed_name)
                    except FileNotFoundError:
                        pass
                try:
                    key.delete_value(base_name)
                except FileNotFoundError:
                    pass
    except FileNotFoundError:
        pass
