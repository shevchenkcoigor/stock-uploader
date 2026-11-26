//! The "Проверить" buttons: Claude Code is installed and runs, the SFTP login works.

use std::net::{TcpStream, ToSocketAddrs};
use std::path::{Path, PathBuf};
use std::process::{Command, Stdio};
use std::time::Duration;

use ssh2::Session;

use crate::error::{AppError, AppResult};
use crate::settings::{claude_path_setting, sftp_login};

const NETWORK_TIMEOUT: Duration = Duration::from_secs(20);
const CLAUDE_ENV: &str = "ADOBE_CLAUDE_PATH";
// Apps started from Finder do not get the shell's PATH, so look where Claude Code usually installs itself.
const CLAUDE_DIRS: [&str; 6] =
    ["~/.local/bin", "~/.claude/local", "~/.npm-global/bin", "~/.bun/bin", "/opt/homebrew/bin", "/usr/local/bin"];

fn home_relative(dir: &str) -> PathBuf {
    match dir.strip_prefix("~/") {
        Some(rest) => dirs::home_dir().unwrap_or_default().join(rest),
        None => PathBuf::from(dir),
    }
}

fn nvm_bins() -> Vec<PathBuf> {
    let versions = home_relative("~/.nvm/versions/node");
    std::fs::read_dir(versions)
        .map(|entries| entries.flatten().map(|entry| entry.path().join("bin")).collect())
        .unwrap_or_default()
}

fn on_path() -> Vec<PathBuf> {
    std::env::var_os("PATH").map(|paths| std::env::split_paths(&paths).collect()).unwrap_or_default()
}

/// The configured path (setting, then the env variable), else the first `claude` on PATH or a usual dir.
pub fn find_claude(configured: &str) -> Option<PathBuf> {
    let configured = if configured.trim().is_empty() {
        std::env::var(CLAUDE_ENV).unwrap_or_default()
    } else {
        configured.to_string()
    };
    if !configured.trim().is_empty() {
        let path = PathBuf::from(configured.trim());
        return path.exists().then_some(path);
    }
    let dirs = on_path().into_iter().chain(CLAUDE_DIRS.iter().map(|dir| home_relative(dir))).chain(nvm_bins());
    dirs.map(|dir| dir.join("claude")).find(|path| is_file(path))
}

fn is_file(path: &Path) -> bool {
    path.is_file()
}

#[tauri::command]
pub async fn check_claude() -> AppResult<String> {
    tauri::async_runtime::spawn_blocking(run_check_claude).await.map_err(|error| AppError::Check(error.to_string()))?
}

fn run_check_claude() -> AppResult<String> {
    let found = find_claude(&claude_path_setting()).ok_or_else(|| {
        AppError::Check(
            "Claude Code (`claude`) не найден. Установите его и войдите в аккаунт, либо укажите путь выше.".into(),
        )
    })?;
    let output = Command::new(&found)
        .arg("--version")
        .stdin(Stdio::null())
        .output()
        .map_err(|error| AppError::Check(format!("`{}` не запустился: {error}", found.display())))?;
    if !output.status.success() {
        return Err(AppError::Check(format!(
            "`{} --version` завершился с ошибкой: {}",
            found.display(),
            String::from_utf8_lossy(&output.stderr).trim()
        )));
    }
    Ok(format!("Claude найден: {}\nВерсия: {}", found.display(), String::from_utf8_lossy(&output.stdout).trim()))
}

#[tauri::command]
pub async fn check_sftp() -> AppResult<String> {
    tauri::async_runtime::spawn_blocking(run_check_sftp).await.map_err(|error| AppError::Check(error.to_string()))?
}

/// Connects and logs in without transferring anything.
fn run_check_sftp() -> AppResult<String> {
    let login = sftp_login()?;
    let fail = |step: &str, error: &dyn std::fmt::Display| AppError::Check(format!("{step}: {error}"));
    let address = (login.host.as_str(), login.port)
        .to_socket_addrs()
        .map_err(|error| fail("Не удалось найти сервер", &error))?
        .next()
        .ok_or_else(|| AppError::Check("Не удалось найти сервер".into()))?;
    let tcp = TcpStream::connect_timeout(&address, NETWORK_TIMEOUT).map_err(|error| fail("Нет соединения", &error))?;
    let mut session = Session::new().map_err(|error| fail("SSH", &error))?;
    session.set_timeout(u32::try_from(NETWORK_TIMEOUT.as_millis()).unwrap_or(20_000));
    session.set_tcp_stream(tcp);
    session.handshake().map_err(|error| fail("SSH рукопожатие", &error))?;
    session.userauth_password(&login.user, &login.password).map_err(|error| fail("Вход не выполнен", &error))?;
    Ok(format!("Вход выполнен: {}@{}:{}", login.user, login.host, login.port))
}
