# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Prepares image batches for Adobe Stock: validates JPEGs, generates titles/keywords/categories via the local `claude` CLI (no API key), uploads over SFTP, and builds the metadata CSV. Portal steps (Upload CSV, submit for review) stay manual because Adobe has no API. Front ends: a CLI (`adobe`) and the Tauri desktop app in `app/` (Rust core + React UI; it replaced the PySide6 app, issue #15). `README.md` (in Russian) is the detailed user-facing reference for commands, statuses, settings and Adobe AI-content rules; read it before changing behavior.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"            # dev extra includes pytest, ruff, basedpyright, pre-commit
pytest -q                          # all tests
pytest tests/test_state.py::test_name -q   # single test
ruff check --fix && ruff format    # line length 110
basedpyright                       # "standard" mode, uses .venv
./install.sh                       # build the Tauri desktop app (npm ci + tauri build; host OS only)
cd app && npm run lint && npm test && npm run build   # frontend checks
cd app/src-tauri && cargo test     # Rust core tests
```

Pre-commit (`pre-commit install` once) runs ruff check --fix, ruff format, basedpyright and the full pytest on every commit, using `.venv/bin/…`; a failing hook blocks the commit, and ruff autofixes must be re-staged.

## Architecture

Pipeline: `scan → caption → (review/approve) → csv → upload → [portal, manual] → submitted`. The Tauri app builds the CSV before uploading and uploads a batch only while its CSV matches the DB; «mark submitted» is on its Upload page. The Python CLI/GUI still use the older order.

- `adobe_stock/state.py` — SQLite (`state.db`), the **single source of truth**. Owns the schema, file status lifecycle (`needs_metadata`, `review`, `pending`, `uploaded`, `submitted`, `invalid`), batches, CSV records and the `events` log. `filename` is the flat SFTP/CSV name; duplicates in different folders get a content-hash suffix.
- `adobe_stock/services.py` — pipeline operations shared by CLI and GUI. Must not print or prompt: return small result objects, report progress via `ProgressCallback(done, total, msg)`, and honor `should_stop` between steps. `services.protected(db)` wraps every mutating run with backups before/after (`backup.py`, last 30 kept) and an integrity check; a missing `state.db` with existing backups aborts with a `restore` hint rather than creating an empty DB.
- `adobe_stock/cli.py` — argparse front end; only place that prompts (model/batch pickers) and prints.
- `adobe_stock/caption.py` — shells out to `claude` in a lightweight mode; `MODELS` lists selectable models; images are downscaled to 1568 px and sent several per call. Stops after `MAX_CONSECUTIVE_CALL_FAILURES`; progress already saved is kept. `categories.py` holds the 21 Adobe category codes.
- `adobe_stock/metadata_csv.py`, `csv_batches.py` — item validation (keyword/title rules), Adobe CSV writing and re-validation, batch splitting, `with_ai_label` (appends the "Generative AI" title suffix when captioning, so DB titles already carry it and the CSV writes them as stored; `adobe label-titles` backfills older titles).
- `adobe_stock/settings.py` — pydantic-settings `Settings`, env `ADOBE_*` or `.env`. `sftp_upload.py` — paramiko upload, status written per file so interrupted uploads resume.
- `app/` — Tauri 2 app: `src-tauri/src/core/` is the Rust port of the core (same `state.db` schema, byte-identical CSV), `*_cmd.rs` are Tauri commands, `src/pages/` the React pages. Parity with Python is checked by running both on a copy of the DB.

Because `services.py` is shared, new pipeline logic goes there (not in `cli.py` or a page) and takes callbacks instead of doing I/O.

## Conventions and gotchas

- Python ≥3.12, fully typed (basedpyright standard). Tests use an isolated environment fixture in `tests/conftest.py`.
- Never overwrite or delete the real `state.db` (also holds the log); for demos use a separate DB and ini (`packaging/seed_demo.py`).
- Files are never copied or moved by the tool; the DB stores absolute paths, so renaming scanned files before upload breaks things.
- Every new feature **and every fix** is developed in its own git worktree (a separate branch and directory, kept in `../worktrees/`), never directly in the main checkout. Merge back to `main` when done.
- A new feature requires a GitHub issue. If none exists, create one (`gh issue create`) before starting work, and reference it in the branch/commits.
- CI (`.github/workflows/build.yml`) runs lint/types/tests, then builds the desktop app.
- After committing, the user expects the `.app` to be rebuilt and reopened from the worktree (`cd app && npm run tauri -- build --bundles app`, then `open -n "app/src-tauri/target/release/bundle/macos/Adobe Stock Uploader.app"`).
