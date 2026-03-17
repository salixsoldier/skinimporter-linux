import base64
import json
import os
import re
import winreg


REGISTRY_PATH = r"Software\Pixel Gun Team\Pixel Gun 3D"
USER_SKINS_VALUE = "User Skins"
USER_NAME_SKINS_VALUE = "User Name Skins"
CURRENT_EQUIPED_SKIN_VALUE = "Current Equiped Skin"
STARTING_SKIN_ID = 1001


def png_to_base64(image_path: str) -> str:
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("ascii")


def _find_hashed_value_names(key: winreg.HKEYType, base_name: str) -> list[str]:
    pattern = re.compile(r'^' + re.escape(base_name) + r'_h\d+$')
    matches = []
    try:
        index = 0
        while True:
            value_name, _, _ = winreg.EnumValue(key, index)
            if pattern.match(value_name):
                matches.append(value_name)
            index += 1
    except OSError:
        pass
    return matches


def _read_registry_json(key: winreg.HKEYType, value_name: str) -> dict[str, str]:
    names_to_try = _find_hashed_value_names(key, value_name) + [value_name]
    for name in names_to_try:
        try:
            raw_value, _ = winreg.QueryValueEx(key, name)
        except FileNotFoundError:
            continue

        if not raw_value:
            continue

        try:
            parsed = json.loads(raw_value)
        except json.JSONDecodeError:
            continue

        if isinstance(parsed, dict):
            return {str(entry_key): str(entry_value) for entry_key, entry_value in parsed.items()}
    return {}


def _write_registry_json(key: winreg.HKEYType, value_name: str, value: dict[str, str]) -> None:
    json_str = json.dumps(value)
    winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, json_str)
    for hashed_name in _find_hashed_value_names(key, value_name):
        winreg.SetValueEx(key, hashed_name, 0, winreg.REG_SZ, json_str)


def add_skins(skin_base64_list: list[str], skin_names_list: list[str]) -> list[str]:
    if len(skin_base64_list) != len(skin_names_list):
        raise ValueError("skin_base64_list and skin_names_list must have the same length")

    if not skin_base64_list:
        return []

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH) as registry_key:
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

        _write_registry_json(registry_key, USER_SKINS_VALUE, user_skins)
        _write_registry_json(registry_key, USER_NAME_SKINS_VALUE, user_name_skins)

        first_id = added_skin_ids[0]
        winreg.SetValueEx(registry_key, CURRENT_EQUIPED_SKIN_VALUE, 0, winreg.REG_SZ, first_id)
        for hashed_name in _find_hashed_value_names(registry_key, CURRENT_EQUIPED_SKIN_VALUE):
            winreg.SetValueEx(registry_key, hashed_name, 0, winreg.REG_SZ, first_id)

    return added_skin_ids


def add_skins_from_files(image_paths: list[str]) -> list[str]:
    skin_base64_list = [png_to_base64(image_path) for image_path in image_paths]
    skin_names_list = [os.path.basename(image_path) for image_path in image_paths]
    return add_skins(skin_base64_list, skin_names_list)


def append_skin(skin_base64: str, skin_name: str) -> str:
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH) as registry_key:
        user_skins = _read_registry_json(registry_key, USER_SKINS_VALUE)
        user_name_skins = _read_registry_json(registry_key, USER_NAME_SKINS_VALUE)

        existing_ids = [int(k) for k in user_skins.keys() if k.isdigit()]
        skin_id = str(max(existing_ids, default=STARTING_SKIN_ID - 1) + 1)

        user_skins[skin_id] = skin_base64
        user_name_skins[skin_id] = skin_name

        _write_registry_json(registry_key, USER_SKINS_VALUE, user_skins)
        _write_registry_json(registry_key, USER_NAME_SKINS_VALUE, user_name_skins)

        try:
            winreg.QueryValueEx(registry_key, CURRENT_EQUIPED_SKIN_VALUE)
        except FileNotFoundError:
            winreg.SetValueEx(registry_key, CURRENT_EQUIPED_SKIN_VALUE, 0, winreg.REG_SZ, skin_id)
            for hashed_name in _find_hashed_value_names(registry_key, CURRENT_EQUIPED_SKIN_VALUE):
                winreg.SetValueEx(registry_key, hashed_name, 0, winreg.REG_SZ, skin_id)

    return skin_id


def get_added_skins() -> list[dict[str, str]]:
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH, 0, winreg.KEY_READ) as registry_key:
            user_skins = _read_registry_json(registry_key, USER_SKINS_VALUE)
            user_name_skins = _read_registry_json(registry_key, USER_NAME_SKINS_VALUE)
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
    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH) as registry_key:
        user_skins = _read_registry_json(registry_key, USER_SKINS_VALUE)
        user_name_skins = _read_registry_json(registry_key, USER_NAME_SKINS_VALUE)

        if skin_id not in user_skins and skin_id not in user_name_skins:
            return

        user_skins.pop(skin_id, None)
        user_name_skins.pop(skin_id, None)

        _write_registry_json(registry_key, USER_SKINS_VALUE, user_skins)
        _write_registry_json(registry_key, USER_NAME_SKINS_VALUE, user_name_skins)

        current_value = None
        for current_name in _find_hashed_value_names(registry_key, CURRENT_EQUIPED_SKIN_VALUE) + [CURRENT_EQUIPED_SKIN_VALUE]:
            try:
                current_value, _ = winreg.QueryValueEx(registry_key, current_name)
                break
            except FileNotFoundError:
                continue

        if str(current_value) == skin_id:
            next_id = None
            digit_ids = sorted([int(entry_id) for entry_id in user_skins.keys() if entry_id.isdigit()])
            if digit_ids:
                next_id = str(digit_ids[0])

            if next_id is not None:
                winreg.SetValueEx(registry_key, CURRENT_EQUIPED_SKIN_VALUE, 0, winreg.REG_SZ, next_id)
                for hashed_name in _find_hashed_value_names(registry_key, CURRENT_EQUIPED_SKIN_VALUE):
                    winreg.SetValueEx(registry_key, hashed_name, 0, winreg.REG_SZ, next_id)
            else:
                for hashed_name in _find_hashed_value_names(registry_key, CURRENT_EQUIPED_SKIN_VALUE):
                    try:
                        winreg.DeleteValue(registry_key, hashed_name)
                    except FileNotFoundError:
                        pass
                try:
                    winreg.DeleteValue(registry_key, CURRENT_EQUIPED_SKIN_VALUE)
                except FileNotFoundError:
                    pass


def clear_modded_skins() -> None:
    managed_values = [USER_SKINS_VALUE, USER_NAME_SKINS_VALUE, CURRENT_EQUIPED_SKIN_VALUE]
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, REGISTRY_PATH, 0, winreg.KEY_ALL_ACCESS) as registry_key:
            for base_name in managed_values:
                for hashed_name in _find_hashed_value_names(registry_key, base_name):
                    try:
                        winreg.DeleteValue(registry_key, hashed_name)
                    except FileNotFoundError:
                        pass
                try:
                    winreg.DeleteValue(registry_key, base_name)
                except FileNotFoundError:
                    pass
    except FileNotFoundError:
        pass