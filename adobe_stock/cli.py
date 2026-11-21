"""Incremental Adobe Stock pipeline: scan -> upload -> csv -> (manual portal steps) -> submitted.

State lives in a SQLite file (default state.db). The CSV upload and the submit-for-review
step stay manual in the Contributor Portal.

This module only parses arguments, asks questions and prints; the work itself is in `services`.
"""

import argparse
import sqlite3
import sys
from collections.abc import Callable
from pathlib import Path

from adobe_stock import backup, caption, services, state
from adobe_stock.metadata_csv import MAX_ROWS
from adobe_stock.settings import load_settings

DEFAULT_DB = Path("state.db")
DEFAULT_IMAGES_DIR = Path("images")
MAX_INVALID_SHOWN = 30
MUTATING_COMMANDS = frozenset({"scan", "caption", "approve", "label-titles", "upload", "csv", "submitted"})
MAX_ERRORS_SHOWN = 20

_connections: list[sqlite3.Connection] = []


def open_state(db: Path) -> sqlite3.Connection:
    """Open the state DB and remember the connection so main() closes it (Windows locks open files)."""
    conn = state.open_db(db)
    _connections.append(conn)
    return conn


def _close_connections() -> None:
    while _connections:
        _connections.pop().close()


def cmd_scan(args: argparse.Namespace) -> int:
    root = args.folder.resolve()
    images = services.find_images(root)
    if not images:
        print("no JPEG files found")
        return 2
    conn = open_state(args.db)
    report = services.scan(conn, root, images, args.metadata, args.batch_size)
    result = report.result
    print(
        f"added {result.added}, need metadata {result.needs_metadata}, "
        f"unchanged {result.unchanged}, invalid {result.invalid}, "
        f"new batches {result.batches_created}"
    )
    if result.needs_metadata:
        print("Files without a metadata row are waiting: run `caption` to generate them.")
    for path, error in report.invalid[:MAX_INVALID_SHOWN]:
        print(f"INVALID {path}: {error}")
    for name in result.changed_after_upload:
        print(f"WARNING {name} changed after upload; not re-uploaded")
    return 0


def pick_model(ask: Callable[[str], str] = input) -> str:
    """Show the available models; Enter picks the default, a number or an id picks that one."""
    print("Models:")
    for number, (model_id, note) in enumerate(caption.MODELS, start=1):
        marker = "  (default)" if model_id == caption.DEFAULT_MODEL else ""
        print(f"  {number}: {model_id} - {note}{marker}")
    answer = ask(f"Model [{caption.DEFAULT_MODEL}]: ").strip()
    if not answer:
        return caption.DEFAULT_MODEL
    if answer.isdigit() and 1 <= int(answer) <= len(caption.MODELS):
        return caption.MODEL_IDS[int(answer) - 1]
    if answer in caption.MODEL_IDS:
        return answer
    raise SystemExit(f"'{answer}' is not a known model; choose 1-{len(caption.MODELS)}")


def cmd_caption(args: argparse.Namespace) -> int:
    """Generate titles and keywords for files that have none, then batch them."""
    model = args.model or (pick_model() if sys.stdin.isatty() else caption.DEFAULT_MODEL)
    conn = open_state(args.db)
    report = services.caption_files(
        conn,
        model,
        limit=args.limit,
        per_call=args.per_call,
        workers=args.workers,
        batch_size=args.batch_size,
        ai_label=load_settings().ai_title_label,
    )
    if report.total == 0:
        print("nothing to caption")
        return 0
    print(
        f"captioned {report.captioned} of {report.total}, flagged for review {len(report.flagged)}, "
        f"new batches {report.new_batches}"
    )
    for message in report.flagged[:MAX_ERRORS_SHOWN]:
        print(f"REVIEW {message}")
    if report.flagged:
        print("Flagged files are held back from upload. See `adobe review`, then `adobe approve`.")
    for message in report.errors[:MAX_ERRORS_SHOWN]:
        print(f"ERROR {message}")
    return 1 if report.errors else 0


def cmd_log(args: argparse.Namespace) -> int:
    """Show the history of what the commands did, newest last."""
    rows = state.recent_events(open_state(args.db), args.limit, args.command, args.level)
    if not rows:
        print("no events")
        return 0
    for row in rows:
        target = f" {row['filename']}:" if row["filename"] else ""
        print(f"{row['at']}  {row['level'].upper():7}  {row['command']:9}{target} {row['message']}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """List files that Claude flagged as possible Adobe AI-policy risks."""
    rows = state.review_files(open_state(args.db))
    if not rows:
        print("nothing to review")
        return 0
    for row in rows:
        print(f"{row['filename']}: {row['error']}\n    title: {row['title']}\n    {row['path']}")
    print(
        f"{len(rows)} files held back. Look at them, then `adobe approve NAME...` or `adobe approve --all`."
    )
    print("Delete or move files you do not want to submit.")
    return 0


def cmd_approve(args: argparse.Namespace) -> int:
    """Release reviewed files into the upload queue."""
    if not args.names and not args.all:
        print("give file names to approve, or --all")
        return 2
    conn = open_state(args.db)
    report = services.approve(conn, None if args.all else args.names, args.batch_size)
    print(f"approved {report.count} files, new batches {report.new_batches}")
    return 0


def cmd_label_titles(args: argparse.Namespace) -> int:
    """Add the title label to stored titles that lack it."""
    label = args.label if args.label is not None else load_settings().ai_title_label
    if not label.strip():
        print("no label given and the setting is empty")
        return 2
    count = services.label_titles(open_state(args.db), label)
    print(f"added {label!r} to {count} titles")
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
    conn = open_state(args.db)
    batch = args.batch
    if batch is None:
        batches = state.pending_batches(conn)
        if not batches:
            print("nothing pending")
            return 0
        batch = pick_batch(batches) if sys.stdin.isatty() else batches[-1]["batch_id"]
    pending = state.pending_files(conn, batch)
    if not pending:
        print(f"nothing pending in batch {batch}")
        return 0
    print(f"uploading batch {batch}: {len(pending)} files")
    report = services.upload_batch(
        conn, batch, load_settings(), on_progress=lambda _done, _total, message: print(message)
    )
    if report.error:
        print(f"ERROR {report.error}")
        print("Files stay pending. Check ADOBE_SFTP_HOST/PORT/USER/PASSWORD (.env) and the network.")
    return 0 if report.ok else 1


def cmd_csv(args: argparse.Namespace) -> int:
    conn = open_state(args.db)
    report = services.build_csv(conn, args.batch, load_settings(), args.output, args.csv_dir)
    if report.empty:
        print("no uploaded, unsubmitted files in this batch")
        return 1
    if report.problems:
        for problem in report.problems:
            print(f"CSV PROBLEM {problem}")
        return 1
    print(f"wrote {report.path} ({report.rows} rows, checked against Adobe's CSV rules)")
    print("Portal: Uploaded Files > New > Upload CSV. Then, before submitting for review:")
    print(f"  - tick 'Created using generative AI tools' for ALL {report.rows} files (required)")
    print(f"  - tick 'People and Property are fictional' for the {report.people} files with people")
    print(f"Checklist with file names: {report.notes}")
    return 0


def cmd_submitted(args: argparse.Namespace) -> int:
    conn = open_state(args.db)
    report = services.mark_submitted(conn, args.batch)
    if report.refused:
        left = ", ".join(f"{status}: {n}" for status, n in report.waiting)
        print(f"batch {args.batch} is not fully uploaded yet ({left}); nothing marked")
        return 1
    for warning in report.warnings:
        print(f"WARNING {warning}")
    print(f"marked {report.count} files as submitted")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    conn = open_state(args.db)
    current_batch = object()
    for row in state.status_summary(conn):
        if row["batch_id"] != current_batch:
            current_batch = row["batch_id"]
            if current_batch is None:
                print("no batch (invalid or waiting for metadata)")
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
        "folder",
        type=Path,
        nargs="?",
        default=DEFAULT_IMAGES_DIR,
        help=f"root folder with images in any nesting (default {DEFAULT_IMAGES_DIR})",
    )
    scan.add_argument(
        "--metadata", type=Path, help="one CSV for the whole tree; default: metadata.csv next to each image"
    )
    scan.add_argument(
        "--batch-size",
        type=_batch_size,
        default=services.DEFAULT_BATCH_SIZE,
        help=f"files per batch, 1-{MAX_ROWS} (default {services.DEFAULT_BATCH_SIZE})",
    )
    scan.set_defaults(func=cmd_scan)

    caption_cmd = sub.add_parser(
        "caption", help="generate title and keywords with Claude for files waiting for metadata"
    )
    caption_cmd.add_argument(
        "--limit", type=int, default=services.DEFAULT_CAPTION_LIMIT, help="max files per run"
    )
    caption_cmd.add_argument(
        "--model",
        choices=caption.MODEL_IDS,
        help=f"model id; without it you choose from a list (default {caption.DEFAULT_MODEL})",
    )
    caption_cmd.add_argument(
        "--per-call",
        type=int,
        default=services.DEFAULT_IMAGES_PER_CALL,
        help="images sent in one Claude call",
    )
    caption_cmd.add_argument("--workers", type=int, default=1, help="parallel Claude calls")
    caption_cmd.add_argument("--batch-size", type=_batch_size, default=services.DEFAULT_BATCH_SIZE)
    caption_cmd.set_defaults(func=cmd_caption)

    upload = sub.add_parser("upload", help="SFTP-upload pending files")
    upload.add_argument(
        "--batch",
        type=int,
        help="batch id; without it you choose from a list (default: the last pending batch)",
    )
    upload.set_defaults(func=cmd_upload)

    csv_cmd = sub.add_parser("csv", help="write the Adobe CSV for a batch")
    csv_cmd.add_argument("batch", type=int)
    csv_cmd.add_argument(
        "--csv-dir",
        type=Path,
        help="folder for generated CSVs (default: ADOBE_CSV_DIR from env/.env, else csv/)",
    )
    csv_cmd.add_argument("--output", type=Path, help="explicit file instead of an auto-named one")
    csv_cmd.set_defaults(func=cmd_csv)

    submitted = sub.add_parser("submitted", help="mark a batch as submitted in the portal")
    submitted.add_argument("batch", type=int)
    submitted.set_defaults(func=cmd_submitted)

    restore = sub.add_parser("restore", help="restore the state DB from the latest valid backup")
    restore.add_argument("--force", action="store_true", help="replace an existing DB")
    restore.set_defaults(func=cmd_restore)

    sub.add_parser("review", help="list files Claude flagged as possible Adobe AI-policy risks").set_defaults(
        func=cmd_review
    )

    approve = sub.add_parser("approve", help="release reviewed files into the upload queue")
    approve.add_argument("names", nargs="*", help="file names as shown by `review`")
    approve.add_argument("--all", action="store_true", help="approve every file under review")
    approve.add_argument("--batch-size", type=_batch_size, default=services.DEFAULT_BATCH_SIZE)
    approve.set_defaults(func=cmd_approve)

    label_cmd = sub.add_parser(
        "label-titles", help="add the title label (default: the setting) to stored titles that lack it"
    )
    label_cmd.add_argument("label", nargs="?", help='label text, e.g. "Generative AI"')
    label_cmd.set_defaults(func=cmd_label_titles)

    log = sub.add_parser("log", help="show the history of scans, captions, uploads and other actions")
    log.add_argument("--limit", type=int, default=50, help="newest N events (default 50)")
    log.add_argument("--command", choices=sorted(MUTATING_COMMANDS | {"restore"}), help="only this command")
    log.add_argument("--level", choices=("info", "warning", "error"), help="only this level")
    log.set_defaults(func=cmd_log)

    sub.add_parser("status", help="show batches and file statuses").set_defaults(func=cmd_status)
    return parser


def cmd_restore(args: argparse.Namespace) -> int:
    if args.db.exists() and not args.force:
        print(f"{args.db} exists; use --force to replace it with the latest backup")
        return 1
    if args.db.exists():
        args.db.replace(args.db.with_suffix(".db.replaced"))
    restored = backup.restore_latest(args.db, backup.backup_dir_for(args.db))
    state.log_event(open_state(args.db), "restore", f"restored from {restored}")
    print(f"restored from {restored}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "restore":
        try:
            return cmd_restore(args)
        finally:
            _close_connections()
    try:
        with services.protected(args.db, mutating=args.command in MUTATING_COMMANDS):
            try:
                return args.func(args)
            finally:
                _close_connections()  # before the final backup
    except services.StateDbError as error:
        raise SystemExit(str(error)) from error


if __name__ == "__main__":
    sys.exit(main())
