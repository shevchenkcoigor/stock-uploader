import importlib.util
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "packaging" / "build.py"
spec = importlib.util.spec_from_file_location("build_script", SCRIPT)
assert spec and spec.loader
build = importlib.util.module_from_spec(spec)
spec.loader.exec_module(build)

ROOT = Path("/project")


def test_macos_builds_an_app_bundle_with_a_bundle_id():
    args = build.pyinstaller_args("Darwin")
    assert "--windowed" in args and "--osx-bundle-identifier" in args
    assert build.output_path("Darwin", ROOT) == ROOT / "dist" / "Adobe Stock Uploader.app"
    assert build.executable_path("Darwin", ROOT) == (
        ROOT / "dist" / "Adobe Stock Uploader.app" / "Contents" / "MacOS" / "Adobe Stock Uploader"
    )


def test_windows_builds_a_windowed_exe_without_mac_flags():
    args = build.pyinstaller_args("Windows")
    assert "--windowed" in args and "--osx-bundle-identifier" not in args
    assert build.executable_path("Windows", ROOT) == (
        ROOT / "dist" / "Adobe Stock Uploader" / "Adobe Stock Uploader.exe"
    )
    assert build.venv_python("Windows", ROOT) == ROOT / ".venv" / "Scripts" / "python.exe"


@pytest.mark.parametrize("system", ["Darwin", "Windows", "Linux"])
def test_every_platform_collects_keyring_backends_and_the_project_path(system):
    args = build.pyinstaller_args(system)
    assert "keyring.backends" in args and "PIL.ImageCms" in args
    assert args[args.index("--paths") + 1] == "."
    assert args[0] == "packaging/adobe_gui.py"


def test_linux_and_mac_use_the_unix_venv_layout():
    assert build.venv_python("Linux", ROOT) == ROOT / ".venv" / "bin" / "python"
    assert (
        build.executable_path("Linux", ROOT)
        == ROOT / "dist" / "Adobe Stock Uploader" / "Adobe Stock Uploader"
    )


def test_dry_run_prints_the_plan_and_changes_nothing(capsys):
    assert build.main(["--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "pyinstaller packaging/adobe_gui.py" in out and "result:" in out
