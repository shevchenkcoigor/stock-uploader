"""Incremental Adobe Stock pipeline: scan -> upload -> csv -> (manual portal steps) -> submitted.

State lives in a SQLite file (default state.db). The CSV upload and the submit-for-review
step stay manual in the Contributor Portal.
"""

import argparse
import sys
from pathlib import Path

from adobe_stock import state
from adobe_stock.metadata_csv import Item, check_items, read_source, write_adobe_csv
from adobe_stock.sftp_upload import SftpConfig, upload_files
from adobe_stock.validate import validate_image

IMAGE_SUFFIXES = {".jpg", ".jpeg"}
DEFAULT_DB = Path("state.db")


def find_images(folder: Path) -> list[Path]:
    return sorted(p.resolve() for p in folder.iterdir() if p.suffix.lower() in IMAGE_SUFFIXES)


def cmd_scan(args: argparse.Namespace) -> int:
    folder = args.folder.resolve()
    images = find_images(folder)
    if not images:
        print("no JPEG files found")
        return 2
    metadata_path = args.metadata or folder / "metadata.csv"
    items = read_source(metadata_path)
    for problem in check_items(items, {image.name for image in images}):
        if "no such file" not in problem:  # metadata rows without images are just ignored
            print(f"metadata: {problem}")
    metadata = {item.filename: item for item in items}
    problems = {image: validate_image(image) for image in images}

    conn = state.open_db(args.db)
    result = state.scan_folder(conn, folder, images, metadata, problems)
    print(
        f"added {result.added}, unchanged {result.unchanged}, invalid {result.invalid}, "
        f"new batches {result.batches_created}"
    )
    for name in result.changed_after_upload:
        print(f"WARNING {name} changed after upload; not re-uploaded")
    return 0


def cmd_upload(args: argparse.Namespace) -> int:
    conn = state.open_db(args.db)
    files = state.pending_files(conn, args.batch)
    if not files:
        print("nothing pending")
        return 0
    failed = upload_files(
        SftpConfig.from_env(), files, on_success=lambda path: state.mark_uploaded(conn, path)
    )
    return 1 if failed else 0


def cmd_csv(args: argparse.Namespace) -> int:
    conn = state.open_db(args.db)
    items: list[Item] = state.batch_items(conn, args.batch)
    if not items:
        print("no uploaded, unsubmitted files in this batch")
        return 1
    output = args.output or Path(f"adobe_batch_{args.batch}.csv")
    write_adobe_csv(items, output)
    state.record_csv(conn, args.batch, output.resolve())
    print(f"wrote {output} ({len(items)} rows). Portal: Uploaded Files > New > Upload CSV")
    return 0


def cmd_submitted(args: argparse.Namespace) -> int:
    conn = state.open_db(args.db)
    count = state.mark_batch_submitted(conn, args.batch)
    print(f"marked {count} files as submitted")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    conn = state.open_db(args.db)
    for row in state.status_summary(conn):
        print(f"batch {row['batch_id']} {row['folder']} part {row['part']}: {row['status']} x{row['n']}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    sub = parser.add_subparsers(dest="command", required=True)

    scan = sub.add_parser("scan", help="register new images of a folder and split them into batches")
    scan.add_argument("folder", type=Path)
    scan.add_argument("--metadata", type=Path, help="default: <folder>/metadata.csv")
    scan.set_defaults(func=cmd_scan)

    upload = sub.add_parser("upload", help="SFTP-upload pending files")
    upload.add_argument("--batch", type=int, help="only this batch id")
    upload.set_defaults(func=cmd_upload)

    csv_cmd = sub.add_parser("csv", help="write the Adobe CSV for a batch")
    csv_cmd.add_argument("batch", type=int)
    csv_cmd.add_argument("--output", type=Path)
    csv_cmd.set_defaults(func=cmd_csv)

    submitted = sub.add_parser("submitted", help="mark a batch as submitted in the portal")
    submitted.add_argument("batch", type=int)
    submitted.set_defaults(func=cmd_submitted)

    sub.add_parser("status", help="show batches and file statuses").set_defaults(func=cmd_status)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
