#!/usr/bin/env python3
"""Build the desktop app for the operating system this runs on.

    ./install.sh              macOS / Linux
    install.bat               Windows
    python packaging/build.py any of them (needs Python 3.12 or newer)

It creates .venv if missing, installs the project with the GUI and build extras, runs PyInstaller
with the flags of the current OS, starts the result once as a smoke test and prints where it is.
PyInstaller cannot cross-compile: build on Windows for Windows, on macOS for macOS.
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
APP_NAME = "Adobe Stock Uploader"
BUNDLE_ID = "local.adobestockuploader"
MIN_PYTHON = (3, 12)
SMOKE_TIMEOUT_SECONDS = 90
SUPPORTED = ("Darwin", "Windows", "Linux")


def venv_python(system: str, root: Path = ROOT) -> Path:
    scripts = "Scripts/python.exe" if system == "Windows" else "bin/python"
    return root / ".venv" / scripts


def output_path(system: str, root: Path = ROOT) -> Path:
    """What to hand to the user: a .app bundle on macOS, a folder with the program elsewhere."""
    return root / "dist" / (f"{APP_NAME}.app" if system == "Darwin" else APP_NAME)


def executable_path(system: str, root: Path = ROOT) -> Path:
    built = output_path(system, root)
    if system == "Darwin":
        return built / "Contents" / "MacOS" / APP_NAME
    return built / (f"{APP_NAME}.exe" if system == "Windows" else APP_NAME)


def pyinstaller_args(system: str) -> list[str]:
    args = [
        "packaging/adobe_gui.py",
        "--name",
        APP_NAME,
        "--windowed",  # a window, not a console
        "--noconfirm",
        "--clean",
        "--specpath",
        "packaging",
        "--paths",
        ".",  # the project is installed editable; PyInstaller must see its sources
        "--collect-submodules",
        "keyring.backends",  # Keychain / Credential Manager backends are loaded dynamically
        "--hidden-import",
        "PIL.ImageCms",
    ]
    if system == "Darwin":
        args += ["--osx-bundle-identifier", BUNDLE_ID]
    return args


def run(command: list[str], **options) -> None:
    print("+ " + " ".join(command), flush=True)
    subprocess.run(command, check=True, cwd=ROOT, **options)


def ensure_environment(system: str) -> Path:
    if sys.version_info < MIN_PYTHON:
        wanted = ".".join(map(str, MIN_PYTHON))
        raise SystemExit(f"Python {wanted} or newer is required; this is {platform.python_version()}")
    python = venv_python(system)
    if not python.exists():
        run([sys.executable, "-m", "venv", str(ROOT / ".venv")])
    run([str(python), "-m", "pip", "install", "--quiet", "-e", ".[gui,build]"])
    return python


def smoke_test(system: str) -> None:
    """Start the built program once; with --smoke it opens its window and quits by itself."""
    env = {**os.environ, "QT_QPA_PLATFORM": "offscreen"}  # no window flashes on screen
    exe = executable_path(system)
    print(f"+ {exe} --smoke", flush=True)
    completed = subprocess.run([str(exe), "--smoke"], env=env, timeout=SMOKE_TIMEOUT_SECONDS, check=False)
    if completed.returncode != 0:
        raise SystemExit(f"the built program failed its start-up check (exit code {completed.returncode})")


def make_zip(system: str) -> Path:
    """A zip of the Windows/Linux folder, convenient to copy to another computer."""
    dist = ROOT / "dist"
    archive = shutil.make_archive(
        str(dist / f"{APP_NAME.replace(' ', '-')}-{system.lower()}"), "zip", root_dir=dist, base_dir=APP_NAME
    )
    return Path(archive)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("--no-smoke", action="store_true", help="skip the start-up check of the result")
    parser.add_argument("--zip", action="store_true", help="also make a zip (Windows/Linux)")
    parser.add_argument("--dry-run", action="store_true", help="print what would run and stop")
    args = parser.parse_args(argv)

    system = platform.system()
    if system not in SUPPORTED:
        raise SystemExit(f"unsupported system: {system}")
    print(f"Building for {system}")
    if args.dry_run:
        print(f"python: {venv_python(system)}")
        print("pyinstaller " + " ".join(pyinstaller_args(system)))
        print(f"result: {output_path(system)}")
        return 0

    python = ensure_environment(system)
    run([str(python), "-m", "PyInstaller", *pyinstaller_args(system)])
    if not args.no_smoke:
        smoke_test(system)
    if args.zip and system != "Darwin":
        print(f"Zip: {make_zip(system)}")
    print(f"\nBuilt: {output_path(system)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
