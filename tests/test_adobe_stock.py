import csv
from pathlib import Path

import pytest
from PIL import Image

from adobe_stock.metadata_csv import HEADERS, Item, check_items, read_source, write_adobe_csv
from adobe_stock.validate import validate_image


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


def test_sftp_config_requires_credentials(monkeypatch):
    from adobe_stock.sftp_upload import SftpConfig

    monkeypatch.delenv("ADOBE_SFTP_USER", raising=False)
    with pytest.raises(RuntimeError):
        SftpConfig.from_env()
