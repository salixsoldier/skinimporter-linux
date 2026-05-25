"""
build_nuitka.py — Nuitka build script for the Linux fork.

Changes from the original
--------------------------
- Output is a Linux ELF binary, not a .exe.
- --windows-console-mode and --windows-icon-from-ico removed (Windows-only).
- --enable-plugin=tk-inter replaces the original (fixed missing comma bug
  from the original where the comma was missing between two flags).
- --output-filename has no .exe extension.
- Data file separator uses the Linux path format.
"""

import os
import shutil
import subprocess
import venv


APP_NAME = "skin-importer"
ENTRY_FILE = "main.py"
DISTFOLDER = "dist"
BUILDFOLDER = "build"
BUILD_VENV = ".build_venv"
REQUIRED_PACKAGES = ["nuitka", "customtkinter", "pillow", "colorama"]


def project_files_for_data(base_dir: str) -> list[str]:
    files: list[str] = []
    for file_name in os.listdir(base_dir):
        file_path = os.path.join(base_dir, file_name)
        if not os.path.isfile(file_path):
            continue
        if file_name.lower().endswith((".exe", ".pyc")):
            continue
        files.append(file_name)
    return files


def venv_python_path(venv_dir: str) -> str:
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
    data_args: list[str] = []
    for file_name in data_files:
        data_args.append(f"--include-data-files={file_name}={file_name}")

    command = [
        venv_python,
        "-m", "nuitka",
        "--onefile",
        "--enable-plugin=tk-inter",          # fixed: was missing comma in original
        "--output-dir=" + DISTFOLDER,
        "--output-filename=" + APP_NAME,     # no .exe
        "--assume-yes-for-downloads",
        *data_args,
        ENTRY_FILE,
    ]

    print("Building with Nuitka...")
    subprocess.run(command, check=True)

    source_file = os.path.join(DISTFOLDER, APP_NAME)
    destination_file = os.path.join(script_dir, APP_NAME)

    if os.path.exists(destination_file):
        os.remove(destination_file)

    shutil.move(source_file, destination_file)
    os.chmod(destination_file, 0o755)
    print(f"Built '{APP_NAME}' binary.")

    print("Cleaning up...")
    for path in [DISTFOLDER, BUILDFOLDER]:
        if os.path.isdir(path):
            shutil.rmtree(path)


if __name__ == "__main__":
    main()
