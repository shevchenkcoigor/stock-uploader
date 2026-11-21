#!/usr/bin/env python3
"""Build a demo workspace from ONE picture: copies of it, scanned for real, in every status.

    .venv/bin/python packaging/seed_demo.py [--source photo.jpg] [--out ~/adobe-stock-demo]

The copies are slightly altered (so each is a different file for the duplicate check), put in a folder
tree like a trip, scanned with the app's own scan, then moved through the steps in the database:
new, held for review, described, queued, uploaded (with a CSV), submitted; plus files that really are
invalid (too small, not an image, a duplicate). Your real state.db is never touched: everything goes
to --out (recreated on every run). In the app open «Настройки», set «Файл базы» and «Папка для CSV» to
the printed paths and press «Сохранить»; «Папка с изображениями» on the scan page is printed too.
"""

import argparse
import shutil
import sys
from collections.abc import Iterator
from dataclasses import dataclass
from itertools import cycle
from pathlib import Path

from PIL import Image, ImageCms, ImageDraw, ImageEnhance, ImageOps
from pydantic import SecretStr

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from adobe_stock import services, state  # noqa: E402
from adobe_stock.settings import Settings  # noqa: E402

COPY_SIZE = (2000, 3000)  # 6 MP: valid for Adobe (4-100 MP), quick to write
TINY_SIZE = (900, 600)  # 0.5 MP: too small
JPEG_QUALITY = 85
DAYS = ("day_01", "day_02", "day_03")
TOPICS = (
    ("Baby panda in watercolor", ("panda", "baby", "watercolor", "cute", "animal", "bamboo"), "1"),
    ("Cute wallpaper with a young animal", ("wallpaper", "cute", "animal", "baby", "pastel", "soft"), "1"),
    ("Renaissance style animal portrait", ("portrait", "renaissance", "painting", "animal", "art"), "1"),
    ("Soft safari nursery illustration", ("safari", "nursery", "illustration", "giraffe", "kids"), "1"),
)
REVIEW_REASONS = (
    "Possible real person or character in the picture",
    "Looks like a branded character",
    "Possible third-party artwork",
)
UPLOAD_ERROR = "SFTP: connection timed out"
AI_LABEL = "Created using generative AI tools"


@dataclass(frozen=True)
class Group:
    """Files that go through the steps together and end in one batch."""

    count: int
    steps: tuple[str, ...]  # any of: review, described, uploaded, csv, submitted, error


GROUPS = (
    Group(4, ("described", "uploaded", "csv", "submitted")),  # batch 1: sent to the portal
    Group(6, ("described", "uploaded", "csv")),  # batch 2: uploaded, CSV ready
    Group(4, ("described", "uploaded")),  # batch 3: uploaded, no CSV yet
    Group(5, ("described",)),  # batch 4: waits for upload
    Group(3, ("described", "error")),  # batch 5: waits, one upload failed
)
# Problem files: enough for 20+ list pages of 10 (so paging and sorting can be tried properly).
REVIEW_COUNT = 20
DESCRIBED_FREE = 14  # described, in no batch yet: still shown on the description page
SMALL_COUNT = 90
FAKE_PNG_COUNT = 30
BROKEN_COUNT = 40
DUPLICATE_COUNT = 40
OTHER_PROFILE_COUNT = 12
SIZE_STEPS = 25
PROFILE_SIZE = (2200, 2000)  # 4.4 MP: valid size, so only the colour profile is wrong
FRESH_COUNT = 12  # a second folder, not scanned: to watch a scan happen
FRESH_START = 1000  # numbering offset, so these copies differ from the first folder's
NEW_COUNT = 90  # ready to describe: enough for several pages of the description list and of the tiles


def default_source() -> Path:
    candidates = sorted(p for p in (ROOT / "images").glob("*") if p.suffix.lower() in {".jpg", ".jpeg"})
    plain = [p for p in candidates if "(" not in p.stem]
    if not (plain or candidates):
        raise SystemExit("Нет картинки: положите JPEG в images/ или укажите --source")
    return (plain or candidates)[0]


def variants(source: Path, start: int = 0) -> Iterator[Image.Image]:
    """Endless slightly different versions of the picture (brightness + a marker block)."""
    with Image.open(source) as original:
        base = original.convert("RGB").resize(COPY_SIZE)
    number = start
    while True:
        image = ImageEnhance.Brightness(base).enhance(0.85 + (number % 7) * 0.04)
        left = 40 + (number * 97) % (COPY_SIZE[0] - 120)
        top = 40 + (number * 61) % (COPY_SIZE[1] - 120)
        ImageDraw.Draw(image).rectangle((left, top, left + 8, top + 8), fill=(number % 256, 90, 160))
        yield image
        number += 1


def write_files(source: Path, folder: Path, count: int, start: int = 0) -> list[Path]:
    pictures = variants(source, start)
    paths = []
    for number in range(count):
        day = folder / DAYS[number % len(DAYS)]
        day.mkdir(parents=True, exist_ok=True)
        path = day / f"panda_{start + number:03}.jpg"
        next(pictures).save(path, "JPEG", quality=JPEG_QUALITY)
        paths.append(path)
    return paths


def write_invalid(source: Path, folder: Path, valid: list[Path]) -> None:
    """Files that really fail the checks, spread over the day folders (enough for several list pages)."""
    with Image.open(source) as original:
        small = original.convert("RGB")
        for number in range(SMALL_COUNT):  # too few megapixels
            day = folder / DAYS[number % len(DAYS)]
            step = number % SIZE_STEPS  # stays under 4 MP however many files there are
            size = (TINY_SIZE[0] + step * 40, TINY_SIZE[1] + step * 25)
            ImageOps.contain(small, size).save(day / f"icon_{number:02}.jpg", "JPEG")  # keeps the proportions
        for number in range(FAKE_PNG_COUNT):  # a PNG with a .jpg name
            day = folder / DAYS[number % len(DAYS)]
            ImageOps.contain(small, TINY_SIZE).save(day / f"screenshot_{number:02}.jpg", "PNG")
    profile = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB")).tobytes()  # anything but sRGB
    for number in range(OTHER_PROFILE_COUNT):
        day = folder / DAYS[number % len(DAYS)]
        flat = Image.new("RGB", PROFILE_SIZE, (40 + number * 15, 100, 180))
        flat.save(day / f"scan_lab_{number:02}.jpg", "JPEG", icc_profile=profile)
    for number in range(BROKEN_COUNT):  # not a picture at all
        day = folder / DAYS[number % len(DAYS)]
        (day / f"broken_{number:02}.jpg").write_text("this is not a picture", encoding="utf-8")
    for number in range(DUPLICATE_COUNT):  # same bytes as a good file
        # in the last day folder, named to sort last: the original is always scanned first
        shutil.copy(valid[number % len(valid)], folder / DAYS[-1] / f"zz_copy_{number:02}.jpg")


def advance(conn, paths: list[Path], csv_dir: Path) -> None:
    """Move the scanned files through the steps, group by group (each group becomes one batch)."""
    cursor = 0
    for number, group in enumerate(GROUPS):
        chunk = paths[cursor : cursor + group.count]
        cursor += group.count
        for index, path in enumerate(chunk):
            title, keywords, category = TOPICS[(number + index) % len(TOPICS)]
            state.set_metadata(conn, path, f"{title} {number}-{index}", keywords, category)
        with conn:
            state.batch_unassigned(conn, group.count)
        batch_id = conn.execute("SELECT batch_id FROM files WHERE path = ?", (str(chunk[0]),)).fetchone()[0]
        if "uploaded" in group.steps:
            for path in chunk:
                state.mark_uploaded(conn, path)
        if "csv" in group.steps:
            settings = Settings(
                _env_file=None,  # pyright: ignore[reportCallIssue]
                sftp_user="demo",
                sftp_password=SecretStr("demo"),
                csv_dir=csv_dir,
            )
            services.build_csv(conn, batch_id, settings, csv_dir=csv_dir)
        if "submitted" in group.steps:
            state.mark_batch_submitted(conn, batch_id)
        if "error" in group.steps:
            state.record_error(conn, chunk[-1], UPLOAD_ERROR)
            state.log_event(conn, "upload", UPLOAD_ERROR, "error", chunk[-1].name)
    for index, path in enumerate(paths[cursor : cursor + DESCRIBED_FREE]):  # no batch_unassigned: stays free
        title, keywords, category = TOPICS[index % len(TOPICS)]
        state.set_metadata(conn, path, f"{title} free {index}", keywords, category)
    cursor += DESCRIBED_FREE
    held = paths[cursor : cursor + REVIEW_COUNT]
    for number, (reason, path) in enumerate(zip(cycle(REVIEW_REASONS), held, strict=False)):
        state.set_metadata(conn, path, f"Held for a look {number}", ("animal",), "1", review_reason=reason)
        state.log_event(conn, "caption", reason, "warning", path.name)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "--source", type=Path, default=None, help="picture to copy (default: first in images/)"
    )
    parser.add_argument("--out", type=Path, default=Path.home() / "adobe-stock-demo")
    args = parser.parse_args()
    source = (args.source or default_source()).expanduser().resolve()
    out = args.out.expanduser().resolve()
    if out.exists():
        shutil.rmtree(out)
    folder, csv_dir = out / "trip_2026", out / "csv"
    csv_dir.mkdir(parents=True)

    total = sum(group.count for group in GROUPS) + DESCRIBED_FREE + REVIEW_COUNT + NEW_COUNT
    print(f"Копирую {source.name} → {total} файлов…")
    valid = write_files(source, folder, total)
    write_invalid(source, folder, valid)

    fresh = out / "new_photos"
    write_files(source, fresh, FRESH_COUNT, FRESH_START)
    shutil.copy(valid[1], fresh / DAYS[0] / "zz_copy.jpg")  # one duplicate of the first folder's picture

    conn = state.open_db(out / "state.db")
    try:
        report = services.scan(conn, folder.resolve(), services.find_images(folder), None, 1000)
        by_name = {path.name: path.resolve() for path in valid}
        advance(conn, [by_name[path.name] for path in valid], csv_dir)
        state.log_event(conn, "submit", "Batch 1 marked as submitted", "info")
    finally:
        conn.close()
    print(f"Скан: {report.found} файлов, из них невалидных {report.result.invalid}")
    print(f"Файл базы:             {out / 'state.db'}")
    print(f"Папка для CSV:         {csv_dir}")
    print(f"Папка с изображениями: {folder}  (уже просканирована)")
    print(f"Новая папка для скана: {fresh}  (не сканировалась: просканируйте её и смотрите ход)")


if __name__ == "__main__":
    main()
