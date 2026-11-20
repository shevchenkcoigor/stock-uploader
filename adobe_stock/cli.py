"""Incremental Adobe Stock pipeline: scan -> upload -> csv -> (manual portal steps) -> submitted.

State lives in a SQLite file (default state.db). The CSV upload and the submit-for-review
step stay manual in the Contributor Portal.
"""

import argparse
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from pathlib import Path

from adobe_stock import backup, caption, state
from adobe_stock.metadata_csv import (
    MAX_ROWS,
    Item,
    check_items,
    read_source,
    validate_csv_file,
    write_adobe_csv,
)
from adobe_stock.settings import load_settings
from adobe_stock.sftp_upload import upload_files
from adobe_stock.validate import validate_image

IMAGE_SUFFIXES = {".jpg", ".jpeg"}
DEFAULT_DB = Path("state.db")
DEFAULT_IMAGES_DIR = Path("images")
DEFAULT_BATCH_SIZE = 1000
MAX_INVALID_SHOWN = 30
MUTATING_COMMANDS = frozenset({"scan", "caption", "approve", "upload", "csv", "submitted"})
DEFAULT_CAPTION_LIMIT = 50
DEFAULT_IMAGES_PER_CALL = 5
MAX_CONSECUTIVE_CALL_FAILURES = 2
MAX_ERRORS_SHOWN = 20


def find_images(root: Path) -> list[Path]:
    """All JPEGs under root, at any depth; hidden files and folders (e.g. macOS ._x) are skipped."""
    return sorted(
        p.resolve()
        for p in root.rglob("*")
        if p.is_file()
        and p.suffix.lower() in IMAGE_SUFFIXES
        and not any(part.startswith(".") for part in p.relative_to(root).parts)
    )


def load_metadata(
    images: list[Path], override: Path | None
) -> tuple[dict[Path, Item], dict[Path, list[str]]]:
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
        f"added {result.added}, need metadata {result.needs_metadata}, "
        f"unchanged {result.unchanged}, invalid {result.invalid}, "
        f"new batches {result.batches_created}"
    )
    if result.needs_metadata:
        print("Files without a metadata row are waiting: run `caption` to generate them.")
    for row in state.invalid_files(conn, root)[:MAX_INVALID_SHOWN]:
        print(f"INVALID {row['path']}: {row['error']}")
    state.log_event(
        conn,
        "scan",
        f"{root}: added {result.added}, need metadata {result.needs_metadata}, "
        f"unchanged {result.unchanged}, invalid {result.invalid}, new batches {result.batches_created}",
    )
    for row in state.invalid_files(conn, root):
        state.log_event(conn, "scan", f"invalid: {row['error']}", "warning", Path(row["path"]).name)
    for name in result.changed_after_upload:
        print(f"WARNING {name} changed after upload; not re-uploaded")
        state.log_event(conn, "scan", "changed after upload; not re-uploaded", "warning", name)
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
    conn = state.open_db(args.db)
    todo = state.files_needing_metadata(conn, args.limit)
    if not todo:
        print("nothing to caption")
        return 0
    groups = [todo[i : i + args.per_call] for i in range(0, len(todo), args.per_call)]
    captioned = 0
    errors: list[str] = []
    flagged: list[str] = []
    failed_calls = 0

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for start in range(0, len(groups), args.workers):
            window = groups[start : start + args.workers]
            futures = [pool.submit(caption.caption_group, group, model) for group in window]
            for future in futures:
                try:
                    results = future.result()
                except caption.ClaudeCallError as error:
                    failed_calls += 1
                    errors.append(str(error))
                    state.log_event(conn, "caption", str(error), "error")
                    continue
                failed_calls = 0
                captioned += _store_results(conn, results, errors, flagged)
            if failed_calls >= MAX_CONSECUTIVE_CALL_FAILURES:
                errors.append("stopping: repeated call failures (usage limit or login problem?)")
                break

    batches = state.assign_batches(conn, args.batch_size)
    state.log_event(
        conn,
        "caption",
        f"model {model}: captioned {captioned} of {len(todo)}, flagged {len(flagged)}, "
        f"errors {len(errors)}, new batches {batches}",
        "error" if errors else "info",
    )
    print(f"captioned {captioned} of {len(todo)}, flagged for review {len(flagged)}, new batches {batches}")
    for message in flagged[:MAX_ERRORS_SHOWN]:
        print(f"REVIEW {message}")
    if flagged:
        print("Flagged files are held back from upload. See `adobe review`, then `adobe approve`.")
    for message in errors[:MAX_ERRORS_SHOWN]:
        print(f"ERROR {message}")
    return 1 if errors else 0


def _store_results(conn, results: list, errors: list[str], flagged: list[str]) -> int:
    stored = 0
    for result in results:
        if result.error:
            state.record_error(conn, result.path, result.error)
            state.log_event(conn, "caption", result.error, "error", result.path.name)
            errors.append(f"{result.path.name}: {result.error}")
            continue
        item = Item(result.path.name, result.title, result.keywords, result.category)
        problems = check_items([item], {item.filename})
        if problems:
            state.record_error(conn, result.path, "; ".join(problems))
            state.log_event(conn, "caption", "; ".join(problems), "error", result.path.name)
            errors.append(f"{result.path.name}: {'; '.join(problems)}")
            continue
        state.set_metadata(
            conn,
            result.path,
            result.title,
            result.keywords,
            result.category,
            result.people_visible,
            result.content_type,
            result.review_reason,
        )
        if result.review_reason:
            state.log_event(conn, "caption", result.review_reason, "warning", result.path.name)
            flagged.append(f"{result.path.name}: {result.review_reason}")
            continue
        stored += 1
    return stored


def cmd_log(args: argparse.Namespace) -> int:
    """Show the history of what the commands did, newest last."""
    rows = state.recent_events(state.open_db(args.db), args.limit, args.command, args.level)
    if not rows:
        print("no events")
        return 0
    for row in rows:
        target = f" {row['filename']}:" if row["filename"] else ""
        print(f"{row['at']}  {row['level'].upper():7}  {row['command']:9}{target} {row['message']}")
    return 0


def cmd_review(args: argparse.Namespace) -> int:
    """List files that Claude flagged as possible Adobe AI-policy risks."""
    rows = state.review_files(state.open_db(args.db))
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
    conn = state.open_db(args.db)
    count = state.approve_reviewed(conn, None if args.all else args.names)
    batches = state.assign_batches(conn, args.batch_size)
    scope = "all" if args.all else ", ".join(args.names)
    state.log_event(conn, "approve", f"approved {count} files ({scope}), new batches {batches}")
    print(f"approved {count} files, new batches {batches}")
    return 0


def write_portal_notes(output: Path, batch_id: int, rows: list) -> Path:
    """A checklist for the Contributor Portal: the AI labels cannot be set from the CSV."""
    people = [row["filename"] for row in rows if row["people_visible"]]
    unknown = [row["filename"] for row in rows if not row["content_type"]]
    photos = [row["filename"] for row in rows if row["content_type"] == "photo"]
    illustrations = [row["filename"] for row in rows if row["content_type"] == "illustration"]
    lines = [
        f"Portal checklist for batch {batch_id} ({len(rows)} files)",
        "",
        f"1. Created using generative AI tools: tick for ALL {len(rows)} files (required by Adobe).",
        "",
        f"2. People and Property are fictional: tick for the {len(people)} files that show people or "
        "human-like figures:",
        *[f"   {name}" for name in people],
        "   If a file shows a real, identifiable person, Adobe requires a model release instead.",
        "",
        f"3. Content type: {len(photos)} photo-realistic, {len(illustrations)} illustrations.",
        "   Photos:",
        *[f"   {name}" for name in photos],
        "   Illustrations:",
        *[f"   {name}" for name in illustrations],
    ]
    if unknown:
        lines += [
            "",
            f"{len(unknown)} files have no people/type information (metadata came from your own CSV), "
            "check them yourself:",
            *[f"   {name}" for name in unknown],
        ]
    notes = output.with_name(f"{output.stem}_portal_notes.txt")
    notes.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return notes


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
    state.log_event(conn, "upload", f"batch {batch}: started, {len(files)} files")
    remote_names = dict(files)

    def on_success(path: Path) -> None:
        state.mark_uploaded(conn, path)
        state.log_event(conn, "upload", f"uploaded as {remote_names[path]}", filename=path.name)

    try:
        failed = upload_files(load_settings(), files, on_success=on_success)
    except Exception as error:  # connection or login problems end the whole run
        state.log_event(conn, "upload", f"batch {batch}: aborted: {error}", "error")
        raise
    for name in failed:
        state.log_event(conn, "upload", "upload failed", "error", name)
    state.log_event(
        conn,
        "upload",
        f"batch {batch}: finished, {len(files) - len(failed)} uploaded, {len(failed)} failed",
        "error" if failed else "info",
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
        or new_csv_path(args.csv_dir or load_settings().csv_dir, args.batch)
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    write_adobe_csv(items, output)
    problems = validate_csv_file(output, {item.filename for item in items})
    if problems:
        for problem in problems:
            print(f"CSV PROBLEM {problem}")
            state.log_event(conn, "csv", f"batch {args.batch}: {problem}", "error")
        return 1
    state.record_csv(conn, args.batch, output.resolve())
    state.log_event(conn, "csv", f"batch {args.batch}: wrote {output} ({len(items)} rows)")
    print(f"wrote {output} ({len(items)} rows, checked against Adobe's CSV rules)")
    rows = state.portal_notes(conn, args.batch)
    notes = write_portal_notes(output, args.batch, rows)
    people = sum(1 for row in rows if row["people_visible"])
    print("Portal: Uploaded Files > New > Upload CSV. Then, before submitting for review:")
    print(f"  - tick 'Created using generative AI tools' for ALL {len(rows)} files (required)")
    print(f"  - tick 'People and Property are fictional' for the {people} files with people")
    print(f"Checklist with file names: {notes}")
    return 0


def cmd_submitted(args: argparse.Namespace) -> int:
    conn = state.open_db(args.db)
    csv_path = state.recorded_csv_path(conn, args.batch)
    if csv_path is None:
        print(f"WARNING batch {args.batch} has no CSV; metadata may not have been applied")
    elif not csv_path.exists():
        print(f"WARNING recorded CSV is missing on disk: {csv_path}")
    count = state.mark_batch_submitted(conn, args.batch)
    state.log_event(conn, "submitted", f"batch {args.batch}: {count} files marked submitted")
    print(f"marked {count} files as submitted")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    conn = state.open_db(args.db)
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
        default=DEFAULT_BATCH_SIZE,
        help=f"files per batch, 1-{MAX_ROWS} (default {DEFAULT_BATCH_SIZE})",
    )
    scan.set_defaults(func=cmd_scan)

    caption_cmd = sub.add_parser(
        "caption", help="generate title and keywords with Claude for files waiting for metadata"
    )
    caption_cmd.add_argument("--limit", type=int, default=DEFAULT_CAPTION_LIMIT, help="max files per run")
    caption_cmd.add_argument(
        "--model",
        choices=caption.MODEL_IDS,
        help=f"model id; without it you choose from a list (default {caption.DEFAULT_MODEL})",
    )
    caption_cmd.add_argument(
        "--per-call", type=int, default=DEFAULT_IMAGES_PER_CALL, help="images sent in one Claude call"
    )
    caption_cmd.add_argument("--workers", type=int, default=1, help="parallel Claude calls")
    caption_cmd.add_argument("--batch-size", type=_batch_size, default=DEFAULT_BATCH_SIZE)
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
    approve.add_argument("--batch-size", type=_batch_size, default=DEFAULT_BATCH_SIZE)
    approve.set_defaults(func=cmd_approve)

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
    state.log_event(state.open_db(args.db), "restore", f"restored from {restored}")
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
