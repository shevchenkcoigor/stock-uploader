# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Prepares image batches for Adobe Stock: validates JPEGs, generates titles/keywords/categories via the local `claude` CLI (no API key), embeds the title and keywords (XMP + IPTC) into copies of the files, and uploads those copies over SFTP. Portal steps (category, AI/fictional-people ticks, submit for review) stay manual because Adobe has no API. It is one Tauri desktop app in `app/` (Rust core + React UI); the old PySide6 app and the Python CLI are gone, and so is the Adobe CSV. `README.md` (in Russian) is the detailed user-facing reference for the flow, statuses, settings and Adobe AI-content rules; read it before changing behavior.

## Commands

```bash
./install.sh                       # build the Tauri desktop app (npm ci + tauri build; host OS only)
cd app && npm run tauri dev        # develop with hot reload
cd app && npm run lint && npm test && npm run build   # frontend checks
cd app/src-tauri && cargo test     # Rust core tests
cd app/src-tauri && cargo test --lib core::export     # one module's tests
```

Pre-commit (`pipx install pre-commit && pre-commit install` once) runs oxlint, vitest and `cargo test` on every commit; a failing hook blocks the commit.

## Architecture

Pipeline: `scan → caption → (review/approve) → prepare + upload → [portal, manual] → submitted`. The Upload page's button first prepares the batch (copies with embedded metadata, a no-op when they are current) and then uploads the copies; «mark submitted» is on the same page.

- `app/src-tauri/src/core/` is the core; `*_cmd.rs` next to it are the Tauri commands, `app/src/pages/` the React pages. New pipeline logic goes into `core/` and takes callbacks (progress, should-stop) instead of doing I/O; commands only wire it to the UI.
- `core/state.rs` — SQLite (`state.db`), the **single source of truth**. Owns the schema, file status lifecycle (`needs_metadata`, `review`, `pending`, `uploaded`, `submitted`, `invalid`), batches and the `events` log. `filename` is the flat SFTP name; duplicates in different folders get a content-hash suffix. Older databases are upgraded by `add_missing_columns`; the unused `batches.csv_path` column stays in them.
- `core/embed.rs` (+ `embed/xmp.rs`, `embed/iptc.rs`) — writes title/description/keywords into a copy of a JPEG with `img-parts`, replacing XMP and IPTC, then reads the copy back to verify. A title over 64 bytes skips IPTC `ObjectName` (a 64-byte field); the full title stays in XMP and `Headline`. Never modifies the source.
- `core/export.rs` — `prepare_batch` makes copies in `<state.db dir>/export/<batch>/` and records `files.export_path` and `files.embedded_hash` (fingerprint of title + keywords + `EMBED_FORMAT`); `export_readiness` says whether a batch may be uploaded (`Ready`/`Missing`/`Stale`). `upload.rs` refuses a batch that is not `Ready` and sends the copies.
- `core/metadata.rs` — item rules (title/keyword/category checks, AI-term filter, `with_ai_label` which appends the "Generative AI" suffix when captioning), the optional input `metadata.csv` read on scan, and batch splitting. `core/submit.rs` — «mark submitted».
- `core/caption.rs`, `caption_run.rs` — shell out to `claude` in a lightweight mode; images are downscaled and sent several per call; stops after repeated call failures, keeping saved progress. `core/categories.rs` holds the 21 Adobe category codes.
- `core/backup.rs` — `backup::protected` wraps every mutating run with backups before/after (last 30 kept) and an integrity check; a missing `state.db` with existing backups aborts rather than creating an empty DB. Restoring is manual (copy the newest backup over `state.db`).

## Conventions and gotchas

- Never overwrite or delete the real `state.db` (it also holds the log); for experiments use a separate DB (`ADOBE_STATE_DB` or the «Файл базы» setting).
- Original files are never modified or moved; the DB stores their absolute paths, so renaming scanned files before upload breaks things. Only the copies in `export/` carry embedded metadata.
- Test embedded metadata with real images: a flat synthetic image did not get its metadata picked up by the Adobe portal, a real one did (web upload, 2026-11-22). SFTP upload of copies is still to be verified on a real batch.
- Every new feature **and every fix** is developed in its own git worktree (a separate branch and directory, kept in `../worktrees/`), never directly in the main checkout. Merge back to `main` when done.
- A new feature requires a GitHub issue. If none exists, create one (`gh issue create`) before starting work, and reference it in the branch/commits.
- CI (`.github/workflows/build.yml`) runs the frontend and Rust checks, then builds the desktop app on every OS.
- After committing, the user expects the `.app` to be rebuilt and reopened from the worktree (`cd app && npm run tauri -- build --bundles app`, then `open -n "app/src-tauri/target/release/bundle/macos/Adobe Stock Uploader.app"`).
