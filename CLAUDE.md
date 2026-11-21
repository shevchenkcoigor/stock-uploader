# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Prepares image batches for Adobe Stock: validates JPEGs, generates titles/keywords/categories via the local `claude` CLI (no API key), uploads over SFTP, and builds the metadata CSV. Portal steps (Upload CSV, submit for review) stay manual because Adobe has no API. Two front ends share one core: a CLI (`adobe`) and a PySide6 desktop app (`adobe-gui`). `README.md` (in Russian) is the detailed user-facing reference for commands, statuses, settings and Adobe AI-content rules; read it before changing behavior.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # dev extra includes PySide6, keyring, pytest, ruff, basedpyright, pre-commit
pytest -q                          # all tests
pytest tests/test_state.py::test_name -q   # single test
ruff check --fix && ruff format    # line length 110
basedpyright                       # "standard" mode, uses .venv
python packaging/build.py          # build the desktop app (PyInstaller; builds only for the host OS); ./install.sh wraps it
```

Pre-commit (`pre-commit install` once) runs ruff check --fix, ruff format, basedpyright and the full pytest on every commit, using `.venv/bin/…`; a failing hook blocks the commit, and ruff autofixes must be re-staged.

## Architecture

Pipeline: `scan → caption → (review/approve) → upload → csv → [portal, manual] → submitted`.

- `adobe_stock/state.py` — SQLite (`state.db`), the **single source of truth**. Owns the schema, file status lifecycle (`needs_metadata`, `review`, `pending`, `uploaded`, `submitted`, `invalid`), batches, CSV records and the `events` log. `filename` is the flat SFTP/CSV name; duplicates in different folders get a content-hash suffix.
- `adobe_stock/services.py` — pipeline operations shared by CLI and GUI. Must not print or prompt: return small result objects, report progress via `ProgressCallback(done, total, msg)`, and honor `should_stop` between steps. `services.protected(db)` wraps every mutating run with backups before/after (`backup.py`, last 30 kept) and an integrity check; a missing `state.db` with existing backups aborts with a `restore` hint rather than creating an empty DB.
- `adobe_stock/cli.py` — argparse front end; only place that prompts (model/batch pickers) and prints.
- `adobe_stock/caption.py` — shells out to `claude` in a lightweight mode; `MODELS` lists selectable models; images are downscaled to 1568 px and sent several per call. Stops after `MAX_CONSECUTIVE_CALL_FAILURES`; progress already saved is kept. `categories.py` holds the 21 Adobe category codes.
- `adobe_stock/metadata_csv.py`, `csv_batches.py` — item validation (keyword/title rules), Adobe CSV writing and re-validation, batch splitting, `with_ai_label` (appends the "Generative AI" title suffix at CSV time only; DB titles are stored without it).
- `adobe_stock/settings.py` — pydantic-settings `Settings`, env `ADOBE_*` or `.env`. `sftp_upload.py` — paramiko upload, status written per file so interrupted uploads resume.
- `adobe_stock/gui/` — PySide6 app. `AppContext` (`context.py`) gives pages preferences plus `job()` (wraps work for a worker thread with its own DB connection and `services.protected`) and `read()` (UI-thread read-only query). Long operations subclass `TaskPage` and run in a `worker.Task` QThread with progress/cancel. GUI keeps its own DB path/SFTP prefs (`prefs.py`; password in keyring), so it can point at a different `state.db` than the CLI.

Because `services.py` is shared, new pipeline logic goes there (not in `cli.py` or a page) and takes callbacks instead of doing I/O.

## Conventions and gotchas

- Python ≥3.12, fully typed (basedpyright standard). Tests use an isolated environment fixture in `tests/conftest.py`; GUI tests are in `tests/test_gui.py`.
- Never overwrite or delete the real `state.db` (also holds the log); for demos use a separate DB and ini (`packaging/seed_demo.py`).
- Files are never copied or moved by the tool; the DB stores absolute paths, so renaming scanned files before upload breaks things.
- Every new feature is developed in its own git worktree (a separate branch and directory), never directly in the main checkout. Merge back to `main` when done.
- A new feature requires a GitHub issue. If none exists, create one (`gh issue create`) before starting work, and reference it in the branch/commits.
- CI (`.github/workflows/build.yml`) runs lint/types/tests, then builds the desktop app.
- After committing, the user expects the `.app` to be rebuilt and reopened (`python packaging/build.py`, then `open "dist/Adobe Stock Uploader.app"`).
