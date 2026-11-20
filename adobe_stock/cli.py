"""Incremental Adobe Stock pipeline: scan -> upload -> csv -> (manual portal steps) -> submitted.

State lives in a SQLite file (default state.db). The CSV upload and the submit-for-review
step stay manual in the Contributor Portal.
"""

import argparse
import os
import sys
from collections.abc import Callable
from datetime import date
from pathlib import Path

from adobe_stock import backup, state
from adobe_stock.metadata_csv import MAX_ROWS, Item, check_items, read_source, write_adobe_csv
from adobe_stock.sftp_upload import SftpConfig, upload_files
from adobe_stock.validate import validate_image

IMAGE_SUFFIXES = {".jpg", ".jpeg"}
DEFAULT_DB = Path("state.db")
DEFAULT_IMAGES_DIR = Path("images")
DEFAULT_CSV_DIR = Path("csv")
CSV_DIR_ENV = "ADOBE_CSV_DIR"
DEFAULT_BATCH_SIZE = 1000
MAX_INVALID_SHOWN = 30
MUTATING_COMMANDS = frozenset({"scan", "upload", "csv", "submitted"})


def find_images(root: Path) -> list[Path]:
    """All JPEGs under root, at any depth; hidden files and folders (e.g. macOS ._x) are skipped."""
    return sorted(
        p.resolve()
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in IMAGE_SUFFIXES
        and not any(part.startswith(".") for part in p.relative_to(root).parts)
    )


def load_metadata(images: list[Path], override: Path | None) -> tuple[dict[Path, Item], dict[Path, list[str]]]:
    """Match images to metadata rows by name, using the metadata.csv next to each image.

    --metadata overrides that with one CSV for the whole tree. Returns the rows found and
    per-image metadata problems (empty title, no keywords, ...).
    """
    rows_by_source: dict[Path, dict[str, Item]] = {}
    mapping: dict[Path, Item] = {}
    problems: dict[Path, list[str]] = {}
    for image in images:
        source = override or image.parent / "metadata.csv"
        if source not in rows_by_source:
            rows_by_source[source] = (
                {item.filename: item for item in read_source(source)} if source.exists() else {}
            )
        item = rows_by_source[source].get(image.name)
        if item is not None:
            mapping[image] = item
            problems[image] = check_items([item], {item.filename})
    return mapping, problems


def cmd_scan(args: argparse.Namespace) -> int:
    root = args.folder.resolve()
    images = find_images(root)
    if not images:
        print("no JPEG files found")
        return 2
    metadata, metadata_problems = load_metadata(images, args.metadata)

    def check_image(image: Path) -> list[str]:
        return validate_image(image) + metadata_problems.get(image, [])

    conn = state.open_db(args.db)
    result = state.scan_folder(conn, root, images, metadata, check_image, args.batch_size)
    print(
        f"added {result.added}, unchanged {result.unchanged}, invalid {result.invalid}, "
        f"new batches {result.batches_created}"
    )
    for row in state.invalid_files(conn, root)[:MAX_INVALID_SHOWN]:
        print(f"INVALID {row['path']}: {row['error']}")
    for name in result.changed_after_upload:
        print(f"WARNING {name} changed after upload; not re-uploaded")
    return 0


def pick_batch(batches: list, ask: Callable[[str], str] = input) -> int:
    """Show batches with pending files and let the user choose; Enter picks the last one."""
    default = batches[-1]["batch_id"]
    print("Batches with pending files:")
    for row in batches:
        marker = "  (default)" if row["batch_id"] == default else ""
        print(f"  {row['batch_id']}: {row['folder']} part {row['part']} - {row['n']} files{marker}")
    answer = ask(f"Batch to upload [{default}]: ").strip()
    if not answer:
        return default
    valid = {row["batch_id"] for row in batches}
    if not answer.isdigit() or int(answer) not in valid:
        raise SystemExit(f"'{answer}' is not one of: {', '.join(str(b) for b in sorted(valid))}")
    return int(answer)


def cmd_upload(args: argparse.Namespace) -> int:
    conn = state.open_db(args.db)
    batch = args.batch
    if batch is None:
        batches = state.pending_batches(conn)
        if not batches:
            print("nothing pending")
            return 0
        batch = pick_batch(batches) if sys.stdin.isatty() else batches[-1]["batch_id"]
    files = state.pending_files(conn, batch)
    if not files:
        print(f"nothing pending in batch {batch}")
        return 0
    print(f"uploading batch {batch}: {len(files)} files")
    failed = upload_files(
        SftpConfig.from_env(), files, on_success=lambda path: state.mark_uploaded(conn, path)
    )
    return 1 if failed else 0


def new_csv_path(csv_dir: Path, batch_id: int) -> Path:
    """metadata_<date>_batch<N>.csv; the date is when the batch's CSV was first made."""
    return csv_dir / f"metadata_{date.today().isoformat()}_batch{batch_id}.csv"


def cmd_csv(args: argparse.Namespace) -> int:
    conn = state.open_db(args.db)
    items: list[Item] = state.batch_items(conn, args.batch)
    if not items:
        print("no uploaded, unsubmitted files in this batch")
        return 1
    # A batch keeps one CSV: reuse (overwrite) the path recorded on its first run.
    output = (
        args.output
        or state.recorded_csv_path(conn, args.batch)
        or new_csv_path(args.csv_dir, args.batch)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    write_adobe_csv(items, output)
    state.record_csv(conn, args.batch, output.resolve())
    print(f"wrote {output} ({len(items)} rows). Portal: Uploaded Files > New > Upload CSV")
    return 0


def cmd_submitted(args: argparse.Namespace) -> int:
    conn = state.open_db(args.db)
    csv_path = state.recorded_csv_path(conn, args.batch)
    if csv_path is None:
        print(f"WARNING batch {args.batch} has no CSV; metadata may not have been applied")
    elif not csv_path.exists():
        print(f"WARNING recorded CSV is missing on disk: {csv_path}")
    count = state.mark_batch_submitted(conn, args.batch)
    print(f"marked {count} files as submitted")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    conn = state.open_db(args.db)
    current_batch = object()
    for row in state.status_summary(conn):
        if row["batch_id"] != current_batch:
            current_batch = row["batch_id"]
            if current_batch is None:
                print("no batch (invalid files)")
            else:
                csv_path = row["csv_path"] or "-"
                print(f"batch {current_batch}: {row['folder']} part {row['part']}, csv: {csv_path}")
        print(f"    {row['status']}: {row['n']}")
    return 0


def _batch_size(value: str) -> int:
    size = int(value)
    if not 1 <= size <= MAX_ROWS:
        raise argparse.ArgumentTypeError(f"must be between 1 and {MAX_ROWS}")
    return size


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="register new images of a folder and split them into batches")
    scan.add_argument(
        "folder", type=Path, nargs="?", default=DEFAULT_IMAGES_DIR,
        help=f"root folder with images in any nesting (default {DEFAULT_IMAGES_DIR})",
    )
    scan.add_argument(
        "--metadata", type=Path, help="one CSV for the whole tree; default: metadata.csv next to each image"
    )
    scan.add_argument(
        "--batch-size",
        type=_batch_size,
        default=DEFAULT_BATCH_SIZE,
        help=f"files per batch, 1-{MAX_ROWS} (default {DEFAULT_BATCH_SIZE})",
    )
    scan.set_defaults(func=cmd_scan)

    upload = sub.add_parser("upload", help="SFTP-upload pending files")
    upload.add_argument("--batch", type=int, help="batch id; without it you choose from a list (default: the last pending batch)")
    upload.set_defaults(func=cmd_upload)

    csv_cmd = sub.add_parser("csv", help="write the Adobe CSV for a batch")
    csv_cmd.add_argument("batch", type=int)
    csv_cmd.add_argument(
        "--csv-dir", type=Path, default=Path(os.environ.get(CSV_DIR_ENV, DEFAULT_CSV_DIR)),
        help=f"folder for generated CSVs (default {DEFAULT_CSV_DIR}, or ${CSV_DIR_ENV})",
    )
    csv_cmd.add_argument("--output", type=Path, help="explicit file instead of an auto-named one")
    csv_cmd.set_defaults(func=cmd_csv)

    submitted = sub.add_parser("submitted", help="mark a batch as submitted in the portal")
    submitted.add_argument("batch", type=int)
    submitted.set_defaults(func=cmd_submitted)

    restore = sub.add_parser("restore", help="restore the state DB from the latest valid backup")
    restore.add_argument("--force", action="store_true", help="replace an existing DB")
    restore.set_defaults(func=cmd_restore)

    sub.add_parser("status", help="show batches and file statuses").set_defaults(func=cmd_status)
    return parser


def cmd_restore(args: argparse.Namespace) -> int:
    if args.db.exists() and not args.force:
        print(f"{args.db} exists; use --force to replace it with the latest backup")
        return 1
    if args.db.exists():
        args.db.replace(args.db.with_suffix(".db.replaced"))
    restored = backup.restore_latest(args.db, backup.backup_dir_for(args.db))
    print(f"restored from {restored}")
    return 0


def guard_state_db(db: Path) -> None:
    """Never silently start from an empty DB when it vanished or is corrupt."""
    backups = backup.list_backups(backup.backup_dir_for(db), db.stem)
    if not db.exists() and backups:
        raise SystemExit(f"{db} is missing but {len(backups)} backups exist. Run: restore")
    if db.exists() and not backup.integrity_ok(db):
        raise SystemExit(f"{db} failed the integrity check. Run: restore --force")


def snapshot(db: Path) -> None:
    if db.exists():
        backup.create_backup(db, backup.backup_dir_for(db))


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "restore":
        return cmd_restore(args)
    guard_state_db(args.db)
    if args.command not in MUTATING_COMMANDS:
        return args.func(args)
    snapshot(args.db)
    try:
        return args.func(args)
    finally:
        snapshot(args.db)  # also runs after a failed or interrupted upload


if __name__ == "__main__":
    sys.exit(main())
