"""
build.py — PyInstaller build script for the Linux fork.

Changes from the original
--------------------------
- Output is a Linux ELF binary, not a .exe.
- --noconsole replaced with --windowed (Tk apps on Linux still work fine
  with the console visible, but --windowed suppresses it; both are valid).
- --icon accepts a .png on Linux; .ico is Windows-only.
- add-data separator is ':' on Linux (';' is Windows-only).
- Source/destination paths updated for no .exe extension.
"""

import os
import shutil
import subprocess
import venv


APP_NAME = "skin-importer"
ENTRY_FILE = "main.py"
SPECFILE = f"{APP_NAME}.spec"
DISTFOLDER = "dist"
BUILDFOLDER = "build"
BUILD_VENV = ".build_venv"
REQUIRED_PACKAGES = ["pyinstaller", "customtkinter", "pillow", "colorama"]


def project_files_for_data(base_dir: str) -> list[str]:
    files: list[str] = []
    for file_name in os.listdir(base_dir):
        file_path = os.path.join(base_dir, file_name)
        if not os.path.isfile(file_path):
            continue
        # skip executables and Python bytecode
        if file_name.lower().endswith((".exe", ".pyc")):
            continue
        files.append(file_name)
    return files


def venv_python_path(venv_dir: str) -> str:
    # Linux always uses bin/python
    return os.path.join(venv_dir, "bin", "python")


def ensure_build_venv(script_dir: str) -> str:
    venv_dir = os.path.join(script_dir, BUILD_VENV)
    python_path = venv_python_path(venv_dir)

    if not os.path.exists(python_path):
        print("Creating build virtual environment...")
        venv.EnvBuilder(with_pip=True).create(venv_dir)

    print("Installing build dependencies in venv...")
    subprocess.run([python_path, "-m", "pip", "install", "--upgrade", "pip"], check=True)
    subprocess.run([python_path, "-m", "pip", "install", *REQUIRED_PACKAGES], check=True)

    return python_path


def main() -> None:
    script_dir = os.path.dirname(os.path.abspath(__file__))
    os.chdir(script_dir)

    if not os.path.exists(ENTRY_FILE):
        raise FileNotFoundError(f"Entry file not found: {ENTRY_FILE}")

    venv_python = ensure_build_venv(script_dir)

    data_files = project_files_for_data(script_dir)
    add_data_args: list[str] = []
    for file_name in data_files:
        # Linux separator is ':' not ';'
        add_data_args.extend(["--add-data", f"{file_name}:."])

    icon_args: list[str] = []
    if os.path.exists("icon.png"):
        icon_args = ["--icon", "icon.png"]

    command = [
        venv_python,
        "-m", "PyInstaller",
        "--clean",
        "--onefile",
        "--windowed",          # suppress the terminal window for the GUI
        "--name", APP_NAME,
        *icon_args,
        *add_data_args,
        ENTRY_FILE,
    ]

    print("Building with PyInstaller...")
    subprocess.run(command, check=True)

    source_file = os.path.join(DISTFOLDER, APP_NAME)          # no .exe
    destination_file = os.path.join(script_dir, APP_NAME)

    if os.path.exists(destination_file):
        os.remove(destination_file)

    shutil.move(source_file, destination_file)
    os.chmod(destination_file, 0o755)
    print(f"Built '{APP_NAME}' binary.")

    print("Cleaning up...")
    for path in [SPECFILE, DISTFOLDER, BUILDFOLDER]:
        if os.path.isfile(path):
            os.remove(path)
            print(f"Removed file: {path}")
        elif os.path.isdir(path):
            shutil.rmtree(path)
            print(f"Removed folder: {path}")
        else:
            print(f"Not found (skipping): {path}")


if __name__ == "__main__":
    main()
