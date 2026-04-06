import os
import shutil
import subprocess
import sys
import venv


APP_NAME = "Skin Importer"
ENTRY_FILE = "main.py"
DISTFOLDER = "dist"
BUILDFOLDER = "build"
BUILD_VENV = ".build_venv"
REQUIRED_PACKAGES = ["nuitka", "customtkinter", "requests", "pillow", "colorama"]


def project_files_for_data(base_dir: str) -> list[str]:
    files: list[str] = []
    for file_name in os.listdir(base_dir):
        file_path = os.path.join(base_dir, file_name)
        if not os.path.isfile(file_path):
            continue
        if file_name.lower().endswith(".exe"):
            continue
        files.append(file_name)
    return files


def venv_python_path(venv_dir: str) -> str:
    if os.name == "nt":
        return os.path.join(venv_dir, "Scripts", "python.exe")
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
        "-m",
        "nuitka",
        "--onefile",
        "--windows-console-mode=disable",
        "--enable-plugin=tk-inter"
        "--windows-icon-from-ico=icon.ico",
        "--output-dir=" + DISTFOLDER,
        "--output-filename=" + f"{APP_NAME}.exe",
        "--assume-yes-for-downloads",
        *data_args,
        ENTRY_FILE,
    ]

    print("Building with Nuitka...")
    subprocess.run(command, check=True)

    source_file = os.path.join(DISTFOLDER, f"{APP_NAME}.exe")
    destination_file = os.path.join(script_dir, f"{APP_NAME}.exe")

    if os.path.exists(destination_file):
        os.remove(destination_file)

    shutil.move(source_file, destination_file)
    print(f"Built {APP_NAME}.exe")
    print("Cleaning up...")

    if os.path.exists(DISTFOLDER):
        shutil.rmtree(DISTFOLDER)

    if os.path.exists(BUILDFOLDER):
        shutil.rmtree(BUILDFOLDER)


if __name__ == "__main__":
    main()