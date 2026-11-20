import csv
from pathlib import Path

import pytest
from PIL import Image
from pydantic import ValidationError

from adobe_stock.metadata_csv import HEADERS, Item, check_items, read_source, write_adobe_csv
from adobe_stock.validate import validate_image
from adobe_stock.settings import DEFAULT_SFTP_HOST, DEFAULT_SFTP_PORT, load_settings


def make_jpeg(path: Path, size=(2000, 2000)) -> Path:
    Image.new("RGB", size, "white").save(path, "JPEG")
    return path


def test_valid_jpeg_has_no_problems(tmp_path):
    assert validate_image(make_jpeg(tmp_path / "a.jpg")) == []


def test_too_small_image_is_reported(tmp_path):
    problems = validate_image(make_jpeg(tmp_path / "a.jpg", (500, 500)))
    assert any("MP" in p for p in problems)


def test_png_is_rejected(tmp_path):
    path = tmp_path / "a.png"
    Image.new("RGB", (2000, 2000)).save(path, "PNG")
    problems = validate_image(path)
    assert any("extension" in p for p in problems)
    assert any("not JPEG" in p for p in problems)


def test_non_srgb_profile_is_reported(tmp_path):
    from PIL import ImageCms

    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB"))
    path = tmp_path / "a.jpg"
    Image.new("RGB", (2000, 2000)).save(path, "JPEG", icc_profile=profile.tobytes())
    assert any("profile" in p for p in validate_image(path))


def test_check_items_flags_missing_file_and_empty_title():
    items = [Item("x.jpg", "", ("k",))]
    problems = check_items(items, existing_files=set())
    assert any("no such file" in p for p in problems)
    assert any("empty title" in p for p in problems)


def test_roundtrip_writes_adobe_headers(tmp_path):
    source = tmp_path / "src.csv"
    source.write_text("filename,title,keywords\na.jpg,Cat,\"cat, pet\"\n", encoding="utf-8")
    items = read_source(source)
    assert items[0].keywords == ("cat", "pet")
    assert items[0].ai_generated

    out = tmp_path / "out.csv"
    write_adobe_csv(items, out)
    rows = list(csv.reader(out.open(encoding="utf-8")))
    assert tuple(rows[0]) == HEADERS
    assert rows[1][0] == "a.jpg"
    assert rows[1][2] == "cat, pet"
    assert rows[1][4] == "Yes"


def test_explicit_non_ai_item_is_rejected(tmp_path):
    source = tmp_path / "src.csv"
    source.write_text("filename,title,keywords,ai_generated\na.jpg,Cat,cat,no\n", encoding="utf-8")
    items = read_source(source)
    assert any("AI-generated" in p for p in check_items(items, {"a.jpg"}))
    with pytest.raises(ValueError):
        write_adobe_csv(items, tmp_path / "out.csv")


def test_too_many_rows_is_reported():
    items = [Item(f"{i}.jpg", "t", ("k",)) for i in range(5001)]
    problems = check_items(items, {item.filename for item in items})
    assert any("limit is 5000" in p for p in problems)


def test_settings_defaults_and_missing_credentials():
    settings = load_settings()
    assert (settings.sftp_host, settings.sftp_port) == (DEFAULT_SFTP_HOST, DEFAULT_SFTP_PORT)
    with pytest.raises(RuntimeError):
        settings.require_sftp_credentials()


def test_settings_read_dotenv_and_hide_the_password(tmp_path):
    (tmp_path / ".env").write_text(
        "ADOBE_SFTP_HOST=sftp.example.test\nADOBE_SFTP_PORT=2222\nADOBE_SFTP_USER=me\n"
        "ADOBE_SFTP_PASSWORD='p w'\nUNRELATED=1\n",
        encoding="utf-8",
    )
    settings = load_settings()
    assert (settings.sftp_host, settings.sftp_port, settings.sftp_user) == ("sftp.example.test", 2222, "me")
    assert settings.sftp_password.get_secret_value() == "p w"
    assert "p w" not in repr(settings)
    settings.require_sftp_credentials()


def test_real_environment_overrides_dotenv(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text("ADOBE_SFTP_PORT=2222\n", encoding="utf-8")
    monkeypatch.setenv("ADOBE_SFTP_PORT", "22000")
    assert load_settings().sftp_port == 22000


@pytest.mark.parametrize("port", ["abc", "0", "70000"])
def test_invalid_port_is_rejected(monkeypatch, port):
    monkeypatch.setenv("ADOBE_SFTP_PORT", port)
    with pytest.raises(ValidationError):
        load_settings()


def test_folder_settings_come_from_env(tmp_path, monkeypatch):
    assert load_settings().backup_dir is None
    assert load_settings().csv_dir == Path("csv")
    monkeypatch.setenv("ADOBE_BACKUP_DIR", str(tmp_path / "bk"))
    monkeypatch.setenv("ADOBE_CSV_DIR", str(tmp_path / "out"))
    settings = load_settings()
    assert settings.backup_dir == tmp_path / "bk" and settings.csv_dir == tmp_path / "out"
