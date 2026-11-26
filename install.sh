#!/bin/sh
# Builds the desktop app (Tauri) for this machine: npm ci, then `tauri build`.
# Needs Node 22+ and a Rust toolchain (https://rustup.rs). On macOS the result is
# app/src-tauri/target/release/bundle/macos/Stock Uploader.app
set -e
cd "$(dirname "$0")/app"
for tool in node npm cargo; do
    command -v "$tool" >/dev/null 2>&1 || { echo "$tool is required (Node 22+, Rust: https://rustup.rs)" >&2; exit 1; }
done
npm ci
npm run tauri -- build "$@"
