import os

import pytest


@pytest.fixture(autouse=True)
def isolated_environment(tmp_path, monkeypatch):
    """Run each test in an empty folder without ADOBE_* variables.

    A real `.env` in the project root or variables from the shell must never leak into tests.
    """
    for name in [key for key in os.environ if key.startswith("ADOBE_")]:
        monkeypatch.delenv(name)
    monkeypatch.chdir(tmp_path)
