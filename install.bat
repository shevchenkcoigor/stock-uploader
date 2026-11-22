@echo off
rem Builds the desktop app (Tauri) on Windows. Needs Node 22+ and a Rust toolchain (https://rustup.rs).
cd /d "%~dp0app"
call npm ci || exit /b 1
call npm run tauri -- build %*
exit /b %errorlevel%
