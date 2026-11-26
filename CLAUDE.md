# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Prepares images for Adobe Stock: validates JPEGs, generates titles/keywords/categories via the local `claude` CLI (no API key), embeds the title and keywords (XMP + IPTC) into copies of the files, and uploads those copies over SFTP. Portal steps (category, AI/fictional-people ticks, submit for review) stay manual because Adobe has no API. It is one Tauri desktop app in `app/` (Rust core + React UI); the old PySide6 app and the Python CLI are gone, and so is the Adobe CSV. `README.md` (in Russian) is the detailed user-facing reference for the flow, statuses, settings and Adobe AI-content rules; read it before changing behavior.

## Commands

```bash
./install.sh                       # build the Tauri desktop app (npm ci + tauri build; host OS only)
cd app && npm run tauri -- dev     # run the desktop app in dev mode (Vite + Rust)
cd app && npm run lint && npm test && npm run build   # frontend checks
cd app && npx vitest run src/path/to/file.test.ts     # single frontend test
cd app/src-tauri && cargo test     # Rust core tests
cd app/src-tauri && cargo test --lib core::export     # Rust tests matching a path filter
cd app/src-tauri && cargo fmt --check && cargo clippy --all-targets -- -D warnings   # Rust format and lint (CI)
```

Window tests (`app/e2e`, own `package.json`; part of `CI gate` on Linux, Windows and macOS): `cd app/e2e && npm run build:app && npm test`.

`cd app && npm run fix` applies the automatic fixes. `.githooks/pre-commit` only checks `cargo fmt` and `oxlint` for staged files (enable once: `git config core.hooksPath .githooks`); CI runs the checks in `checks.yml` (lint, vitest, build, `cargo fmt`/`clippy`/`test`, dependency audit) on every pull request, so run them locally only when you want faster feedback.

## Architecture

Pipeline: `scan → caption → (review/approve) → prepare + upload → [portal, manual] → submitted`. The Upload page's button first prepares every pending file (copies with embedded metadata, a no-op when they are current) and then uploads the copies; files already on SFTP are listed but skipped; «mark submitted» (all uploaded files) is on the same page. There are no batches.

- `core/state.rs` — SQLite (`state.db`), the **single source of truth**. Owns the schema, file status lifecycle (`needs_metadata`, `review`, `pending`, `uploaded`, `submitted`, `invalid`) and the `events` log. `filename` is the flat SFTP name; duplicates in different folders get a content-hash suffix. Older databases are upgraded by `add_missing_columns`; the unused `batches` table, `files.batch_id` and `batches.csv_path` stay in them.
- `core/embed.rs` (+ `embed/xmp.rs`, `embed/iptc.rs`) — writes title/description/keywords into a copy of a JPEG with `img-parts`, replacing XMP and IPTC, then reads the copy back to verify. A title over 64 bytes skips IPTC `ObjectName` (a 64-byte field); the full title stays in XMP and `Headline`. Never modifies the source.
- `core/export.rs` — `prepare_pending` makes copies in `<state.db dir>/export/` and records `files.export_path` and `files.embedded_hash` (fingerprint of title + keywords + `EMBED_FORMAT`); `export_readiness` says whether the pending files may be uploaded (`Ready`/`Missing`/`Stale`). `upload.rs` refuses to run unless `Ready` and sends the copies.
- `core/migrate.rs` copies the data folder of the previous app name (`Adobe Stock Uploader`) on start and never overwrites; `identifier`, keychain service and env vars were renamed too, with the old ones still read.
- `core/metadata.rs` — item rules (title/keyword/category checks, AI-term filter, `with_ai_label` which appends the "Generative AI" suffix when captioning), the optional input `metadata.csv` read on scan. `core/submit.rs` — «mark submitted».
- `core/caption.rs`, `caption_run.rs` — shell out to `claude` in a lightweight mode; images are downscaled and sent several per call; stops after repeated call failures, keeping saved progress. `core/categories.rs` holds the 21 Adobe category codes.
- `core/backup.rs` — `backup::protected` wraps every mutating run with backups before/after (last 30 kept) and an integrity check; a missing `state.db` with existing backups aborts rather than creating an empty DB. Restoring is manual (copy the newest backup over `state.db`).

### Where to make a change

Pipeline logic goes in `app/src-tauri/src/core/` (no Tauri types, no UI; progress and should-stop via callbacks). `*_cmd.rs` stay thin: parse args, call `core`, emit events. React pages in `app/src/pages/` only render state and invoke commands; they hold no pipeline logic.

## Conventions and gotchas

- Never overwrite or delete the real `state.db` (it also holds the log); for experiments use a separate DB (`STOCK_STATE_DB` or the «Файл базы» setting).
- Original files are never modified or moved; the DB stores their absolute paths, so renaming scanned files before upload breaks things. Only the copies in `export/` carry embedded metadata.
- Test embedded metadata with real images: a flat synthetic image did not get its metadata picked up by the Adobe portal, a real one did (web upload, 2026-11-22). SFTP upload of copies is still to be verified on a real batch.
- Every new feature **and every fix** is developed in its own git worktree (a separate branch and directory, kept in `../worktrees/`), never directly in the main checkout.
- **`main` is changed only through a pull request.** Never commit, merge or push to `main` directly (`git push origin main`, local `git merge` into `main`, force-pushes). GitHub enforces this with a ruleset (a pull request, the `CI gate` check and a merge queue, admins included); `.claude/settings.json` deny rules and the `.githooks/pre-push` hook stop mistakes earlier (enable once: `git config core.hooksPath .githooks`). When work is done, do these steps without waiting to be asked:
  1. Run `cd app && npm run fix`, check the diff it made, then commit in the worktree branch (`refs #N`).
  2. `git push -u origin <branch>`.
  3. `gh pr create` into `main`; the body references the issue (`Closes #N`).
  4. `gh pr merge --auto --merge`: once green, the PR enters the merge queue, which tests it on top of `main` and the PRs ahead, then merges. It is merged only when `gh pr view` shows `MERGED`. If CI is red, fix the branch and push again; never merge by hand, change the branch protection or bypass CI.
  5. After the PR shows `MERGED`: `git pull --ff-only` in the main checkout, then remove the worktree and the local branch.
- A new feature requires a GitHub issue. If none exists, create one (`gh issue create`) before starting work, and reference it in the branch/commits.
- CI (`.github/workflows/build.yml`) starts on every pull request (and on push to `main`): the checks of `checks.yml`, then builds on every OS, then `CI gate`, the one required check. A new job must be added to the `needs` of `CI gate`.
- After committing, the user expects the `.app` to be rebuilt and reopened from the worktree (`cd app && npm run tauri -- build --bundles app`, then `open -n "app/src-tauri/target/release/bundle/macos/Stock Uploader.app"`).
