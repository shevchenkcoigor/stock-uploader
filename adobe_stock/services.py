"""Pipeline operations shared by the command line and the desktop app.

Nothing here prints or prompts. Functions return small result objects and report progress through
callbacks, so the same code can drive a terminal or a window. Long operations accept `should_stop`
(a callable returning True once the user cancelled) and check it between steps.
"""

import sqlite3
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import paramiko

from adobe_stock import backup, caption, csv_batches, state
from adobe_stock.metadata_csv import (
    Item,
    check_items,
    read_source,
    validate_csv_file,
    with_ai_label,
    write_adobe_csv,
)
from adobe_stock.settings import Settings
from adobe_stock.sftp_upload import upload_files
from adobe_stock.validate import validate_image

IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg"})
DEFAULT_BATCH_SIZE = 1000
DEFAULT_CAPTION_LIMIT = 50
DEFAULT_IMAGES_PER_CALL = 5
MAX_CONSECUTIVE_CALL_FAILURES = 2

# (done, total, message)
ProgressCallback = Callable[[int, int, str], None]
StopCheck = Callable[[], bool]


def _never() -> bool:
    return False


def _ignore_progress(done: int, total: int, message: str) -> None:
    return None


class StateDbError(RuntimeError):
    """The state database is missing (but backups exist) or failed its integrity check."""


def guard_state_db(db: Path) -> None:
    """Never silently start from an empty DB when it vanished or is corrupt."""
    backups = backup.list_backups(backup.backup_dir_for(db), db.stem)
    if not db.exists() and backups:
        raise StateDbError(f"{db} is missing but {len(backups)} backups exist. Run: restore")
    if db.exists() and not backup.integrity_ok(db):
        raise StateDbError(f"{db} failed the integrity check. Run: restore --force")


def snapshot(db: Path) -> None:
    if db.exists():
        backup.create_backup(db, backup.backup_dir_for(db))


@contextmanager
def protected(db: Path, mutating: bool = True) -> Iterator[None]:
    """Check the DB before an operation and, for mutating ones, back it up before and after."""
    guard_state_db(db)
    if not mutating:
        yield
        return
    snapshot(db)
    try:
        yield
    finally:
        snapshot(db)  # also runs after a failed or interrupted operation


# ---------------------------------------------------------------------------- scan


def find_images(root: Path, recursive: bool = True) -> list[Path]:
    """JPEGs under root (at any depth unless recursive is off); hidden files and folders (e.g. macOS ._x)
    are skipped."""
    return sorted(
        p.resolve()
        for p in (root.rglob("*") if recursive else root.glob("*"))
        if p.is_file()
        and p.suffix.lower() in IMAGE_SUFFIXES
        and not any(part.startswith(".") for part in p.relative_to(root).parts)
    )


def load_metadata(
    images: list[Path], override: Path | None
) -> tuple[dict[Path, Item], dict[Path, list[str]]]:
    """Match images to metadata rows by name, using the metadata.csv next to each image.

    `override` replaces that with one CSV for the whole tree. Returns the rows found and
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


@dataclass(frozen=True)
class ScanReport:
    result: state.ScanResult
    invalid: tuple[tuple[str, str], ...]  # (path, reason) of every invalid file under the root
    found: int = 0  # images the scan was given
    stopped: bool = False  # ended early on request


def scan(
    conn: sqlite3.Connection,
    root: Path,
    images: list[Path],
    metadata_csv: Path | None,
    batch_size: int,
    on_progress: ProgressCallback | None = None,
    should_stop: StopCheck | None = None,
) -> ScanReport:
    """Register the images of `root` (from find_images) and split new ones into batches."""
    metadata, metadata_problems = load_metadata(images, metadata_csv)

    def check_image(image: Path) -> list[str]:
        return validate_image(image) + metadata_problems.get(image, [])

    result = state.scan_folder(
        conn, root, images, metadata, check_image, batch_size, on_progress, should_stop
    )
    state.log_event(
        conn,
        "scan",
        f"{root}: added {result.added}, need metadata {result.needs_metadata}, "
        f"unchanged {result.unchanged}, invalid {result.invalid}, new batches {result.batches_created}",
    )
    invalid = tuple((row["path"], row["error"]) for row in state.invalid_files(conn, root))
    for path, error in invalid:
        state.log_event(conn, "scan", f"invalid: {error}", "warning", Path(path).name)
    for name in result.changed_after_upload:
        state.log_event(conn, "scan", "changed after upload; not re-uploaded", "warning", name)
    return ScanReport(result, invalid, len(images), bool(should_stop and should_stop()))


# ------------------------------------------------------------------------- caption


@dataclass(frozen=True)
class CaptionReport:
    total: int
    captioned: int
    flagged: tuple[str, ...]  # "name: reason" for files held back for review
    errors: tuple[str, ...]
    new_batches: int
    stopped: bool = False


def _store_results(
    conn: sqlite3.Connection,
    results: list[caption.CaptionResult],
    errors: list[str],
    flagged: list[str],
    redo: bool = False,
    ai_label: str = "",
) -> int:
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
            with_ai_label(result.title, ai_label),
            result.keywords,
            result.category,
            result.people_visible,
            result.content_type,
            result.review_reason,
            redo=redo,
        )
        if result.review_reason:
            state.log_event(conn, "caption", result.review_reason, "warning", result.path.name)
            flagged.append(f"{result.path.name}: {result.review_reason}")
            continue
        stored += 1
    return stored


def caption_files(
    conn: sqlite3.Connection,
    model: str,
    *,
    limit: int = DEFAULT_CAPTION_LIMIT,
    per_call: int = DEFAULT_IMAGES_PER_CALL,
    workers: int = 1,
    batch_size: int = DEFAULT_BATCH_SIZE,
    names: list[str] | None = None,
    redo: bool = False,
    ai_label: str = "",
    on_progress: ProgressCallback = _ignore_progress,
    should_stop: StopCheck = _never,
) -> CaptionReport:
    """Generate titles and keywords for files that have none (only `names` if given), then batch them.

    With `redo` the described files among `names` are captioned again and their text is replaced.
    `ai_label` is appended to every new title (see `with_ai_label`); empty leaves titles as Claude wrote them.
    """
    todo = state.files_needing_metadata(conn, limit, names, redo=redo)
    if not todo:
        return CaptionReport(0, 0, (), (), 0)
    groups = [todo[i : i + per_call] for i in range(0, len(todo), per_call)]
    captioned = done = failed_calls = 0
    errors: list[str] = []
    flagged: list[str] = []
    stopped = False

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(groups), workers):
            if should_stop():
                stopped = True
                break
            window = groups[start : start + workers]
            futures = [(group, pool.submit(caption.caption_group, group, model)) for group in window]
            for group, future in futures:
                try:
                    results = future.result()
                except caption.ClaudeCallError as error:
                    failed_calls += 1
                    errors.append(str(error))
                    state.log_event(conn, "caption", str(error), "error")
                else:
                    failed_calls = 0
                    captioned += _store_results(conn, results, errors, flagged, redo, ai_label)
                done += len(group)
                on_progress(done, len(todo), f"{done} of {len(todo)} files")
            if failed_calls >= MAX_CONSECUTIVE_CALL_FAILURES:
                errors.append("stopping: repeated call failures (usage limit or login problem?)")
                break

    new_batches = state.assign_batches(conn, batch_size)
    state.log_event(
        conn,
        "caption",
        f"model {model}: captioned {captioned} of {len(todo)}, flagged {len(flagged)}, "
        f"errors {len(errors)}, new batches {new_batches}" + (", cancelled" if stopped else ""),
        "error" if errors else "info",
    )
    return CaptionReport(len(todo), captioned, tuple(flagged), tuple(errors), new_batches, stopped)


@dataclass(frozen=True)
class ApproveReport:
    count: int
    new_batches: int


def approve(conn: sqlite3.Connection, names: list[str] | None, batch_size: int) -> ApproveReport:
    """Release reviewed files into the upload queue; names=None approves all of them."""
    count = state.approve_reviewed(conn, names)
    new_batches = state.assign_batches(conn, batch_size)
    scope = "all" if names is None else ", ".join(names)
    state.log_event(conn, "approve", f"approved {count} files ({scope}), new batches {new_batches}")
    return ApproveReport(count, new_batches)


def label_titles(conn: sqlite3.Connection, label: str) -> int:
    """Append `label` to every stored title that does not end with it yet; returns how many changed."""
    count = state.relabel_titles(conn, lambda title: with_ai_label(title, label))
    state.log_event(conn, "label-titles", f"added the label {label!r} to {count} titles")
    return count


def set_status(conn: sqlite3.Connection, names: list[str], status: str, batch_size: int) -> ApproveReport:
    """Move files to `status` by hand; files that become "described" are put into batches as usual."""
    count = state.set_status(conn, names, status)
    new_batches = state.assign_batches(conn, batch_size)
    state.log_event(
        conn, "status", f"{count} of {len(names)} files moved to {status}, new batches {new_batches}"
    )
    return ApproveReport(count, new_batches)


# ------------------------------------------------------------------------- upload


@dataclass(frozen=True)
class UploadReport:
    batch: int
    total: int
    uploaded: int
    failed: tuple[str, ...]
    error: str | None = None  # the whole run could not start or was cut off (DNS, login, ...)
    stopped: bool = False

    @property
    def ok(self) -> bool:
        return self.error is None and not self.failed


def upload_batch(
    conn: sqlite3.Connection,
    batch: int,
    settings: Settings,
    on_progress: ProgressCallback = _ignore_progress,
    should_stop: StopCheck = _never,
) -> UploadReport:
    """SFTP-upload the pending files of a batch; progress survives an interrupted run."""
    files = state.pending_files(conn, batch)
    if not files:
        return UploadReport(batch, 0, 0, ())
    total = len(files)
    state.log_event(conn, "upload", f"batch {batch}: started, {total} files")
    remote_names = dict(files)
    path_by_name = {name: path for path, name in files}
    counts = {"done": 0, "uploaded": 0}
    errors_by_name: dict[str, str] = {}

    def on_success(path: Path) -> None:
        state.mark_uploaded(conn, path)
        state.log_event(conn, "upload", f"uploaded as {remote_names[path]}", filename=path.name)
        counts["done"] += 1
        counts["uploaded"] += 1
        on_progress(counts["done"], total, f"uploaded {remote_names[path]}")

    def on_failure(remote_name: str, error: str) -> None:
        errors_by_name[remote_name] = error
        state.record_error(
            conn, path_by_name[remote_name], error
        )  # the upload page shows it as a failed file
        counts["done"] += 1
        on_progress(counts["done"], total, f"FAILED {remote_name}: {error}")

    try:
        failed = upload_files(
            settings, files, on_success=on_success, on_failure=on_failure, should_stop=should_stop
        )
    except (OSError, paramiko.SSHException, RuntimeError) as error:
        # DNS, network, login or missing-credential problems end the whole run; files stay pending
        message = f"cannot upload batch {batch}: {error}"
        state.log_event(conn, "upload", message, "error")
        return UploadReport(batch, total, counts["uploaded"], (), error=message)
    for name in failed:
        state.log_event(conn, "upload", errors_by_name.get(name, "upload failed"), "error", name)
    stopped = should_stop()
    state.log_event(
        conn,
        "upload",
        f"batch {batch}: {'cancelled' if stopped else 'finished'}, "
        f"{counts['uploaded']} uploaded, {len(failed)} failed",
        "error" if failed else "info",
    )
    return UploadReport(batch, total, counts["uploaded"], tuple(failed), stopped=stopped)


# ---------------------------------------------------------------------- csv, submit


def new_csv_path(csv_dir: Path, batch_id: int) -> Path:
    """metadata_<date>_batch<N>.csv; the date is when the batch's CSV was first made."""
    return csv_dir / f"metadata_{date.today().isoformat()}_batch{batch_id}.csv"


def write_portal_notes(output: Path, batch_id: int, rows: list[sqlite3.Row]) -> Path:
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


@dataclass(frozen=True)
class CsvReport:
    batch: int
    rows: int
    path: Path | None = None
    notes: Path | None = None
    people: int = 0  # files that need the "People and Property are fictional" tick
    problems: tuple[str, ...] = ()

    @property
    def empty(self) -> bool:
        return self.rows == 0

    @property
    def ok(self) -> bool:
        return self.rows > 0 and not self.problems


def build_csv(
    conn: sqlite3.Connection,
    batch: int,
    settings: Settings,
    output: Path | None = None,
    csv_dir: Path | None = None,
) -> CsvReport:
    """Write (or rewrite) the Adobe CSV and portal checklist of a batch."""
    items = state.batch_items(conn, batch)
    if not items:
        return CsvReport(batch, 0)
    # A batch keeps one CSV: reuse (overwrite) the path recorded on its first run.
    target = (
        output or state.recorded_csv_path(conn, batch) or new_csv_path(csv_dir or settings.csv_dir, batch)
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    write_adobe_csv(items, target)
    problems = validate_csv_file(target, {item.filename for item in items})
    if problems:
        for problem in problems:
            state.log_event(conn, "csv", f"batch {batch}: {problem}", "error")
        return CsvReport(batch, len(items), target, problems=tuple(problems))
    state.record_csv(conn, batch, target.resolve())
    state.log_event(conn, "csv", f"batch {batch}: wrote {target} ({len(items)} rows)")
    rows = state.portal_notes(conn, batch)
    notes = write_portal_notes(target, batch, rows)
    people = sum(1 for row in rows if row["people_visible"])
    return CsvReport(batch, len(items), target, notes, people)


@dataclass(frozen=True)
class SubmittedReport:
    count: int
    warnings: tuple[str, ...]
    waiting: tuple[tuple[str, int], ...] = ()  # set when refused: files of the batch not uploaded yet

    @property
    def refused(self) -> bool:
        return bool(self.waiting)


def mark_submitted(conn: sqlite3.Connection, batch: int) -> SubmittedReport:
    """Record that the batch was uploaded and submitted in the portal (a manual step).

    Refused while some files of the batch are not on SFTP yet: the portal CSV would not cover them.
    """
    waiting = csv_batches.batch_waiting(conn, batch)
    if waiting:
        state.log_event(
            conn, "submitted", f"batch {batch}: refused, not uploaded yet: {dict(waiting)}", "warning"
        )
        return SubmittedReport(0, (), waiting)
    warnings: list[str] = []
    csv_path = state.recorded_csv_path(conn, batch)
    if csv_path is None:
        warnings.append(f"batch {batch} has no CSV; metadata may not have been applied")
    elif not csv_path.exists():
        warnings.append(f"recorded CSV is missing on disk: {csv_path}")
    count = state.mark_batch_submitted(conn, batch)
    state.log_event(conn, "submitted", f"batch {batch}: {count} files marked submitted")
    return SubmittedReport(count, tuple(warnings))
