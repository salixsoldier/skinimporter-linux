# Skin Importer — Linux/Proton Fork

A Linux-native fork of [YeetDisDude/skinimporter](https://github.com/YeetDisDude/skinimporter).

## What changed from the original

| Area | Original (Windows) | This fork (Linux) |
|---|---|---|
| Registry backend | `winreg` (Windows API) | `wine_reg.py` — reads/writes Wine's `user.reg` text file directly |
| Game path | Hardcoded Windows registry path | Auto-detected via Proton prefix scanner |
| "Copy Skins" tab | Fetched player skins from modfs.top (now shut down) | Local folder browser — pick any folder of `.png` files |
| Network calls | modfs.top API | None — fully offline |
| Build output | `.exe` | Linux ELF binary |
| Icon | `.ico` | `.png` |

## Requirements

- Python 3.11+
- Steam with Proton (game must have been launched at least once to create the prefix)
- Pixel Gun 3D (Steam App ID 1047820)

```
pip install -r requirements.txt
python main.py
```

## Building a standalone binary

PyInstaller:
```
python build.py
```

Nuitka (produces a faster binary, takes longer to compile):
```
python build_nuitka.py
```

## Proton prefix path

The app auto-detects the prefix by scanning:
- `~/.steam/steam/steamapps/compatdata/1047820/pfx/`
- `~/.local/share/Steam/steamapps/compatdata/1047820/pfx/`
- Flatpak: `~/.var/app/com.valvesoftware.Steam/data/Steam/steamapps/compatdata/1047820/pfx/`
- Snap: `~/snap/steam/common/.steam/steam/steamapps/compatdata/1047820/pfx/`
- Any extra Steam library folders listed in `libraryfolders.vdf`

If auto-detection fails, go to **Settings → Proton Prefix** and set it manually.
The path is saved to `~/.config/pg3d-skinimporter/config.json`.

## File overview

| File | Purpose |
|---|---|
| `main.py` | GUI — CustomTkinter app |
| `skinimporter.py` | Skin read/write logic (registry operations) |
| `wine_reg.py` | Wine `user.reg` parser/writer (replaces `winreg`) |
| `proton_path.py` | Proton prefix auto-detection and config |
| `skin_utils.py` | Image validation and base64 helpers |
| `build.py` | PyInstaller build script |
| `build_nuitka.py` | Nuitka build script |
