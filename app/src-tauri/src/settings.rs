use serde::{Deserialize, Serialize};

use crate::error::{AppError, AppResult};
use crate::prefs::{self, PrefMap};
use crate::secrets;

const DEFAULT_SFTP_HOST: &str = "sftp.contributor.adobestock.com";
const DEFAULT_SFTP_PORT: u32 = 22;
const DEFAULT_AI_TITLE_LABEL: &str = "Generative AI";
const DEFAULT_BATCH_SIZE: u32 = 1000;
const DEFAULT_CAPTION_WORKERS: u32 = 1;
const DEFAULT_IMAGES_PER_CALL: u32 = 5;
const MAX_BATCH_SIZE: u32 = 20_000;
const MAX_CAPTION_WORKERS: u32 = 8;
const MAX_IMAGES_PER_CALL: u32 = 20;
const MAX_PORT: u32 = 65_535;
const THEMES: [&str; 3] = ["system", "light", "dark"];

/// What the settings page shows and saves. Empty paths mean "the default place".
#[derive(Serialize, Deserialize, Clone, Debug, PartialEq)]
#[serde(rename_all = "camelCase")]
pub struct SettingsDto {
    pub db_path: String,
    pub csv_dir: String,
    pub sftp_host: String,
    pub sftp_port: u32,
    pub sftp_user: String,
    pub ai_title_label: String,
    pub batch_size: u32,
    pub caption_limit: u32,
    pub caption_workers: u32,
    pub caption_per_call: u32,
    pub claude_path: String,
    pub theme: String,
}

#[derive(Serialize)]
#[serde(rename_all = "camelCase")]
pub struct SettingsView {
    pub settings: SettingsDto,
    pub default_db_path: String,
    pub default_csv_dir: String,
}

fn number(map: &PrefMap, key: &str, default: u32) -> u32 {
    prefs::text(map, key, "").trim().parse().unwrap_or(default)
}

fn from_map(map: &PrefMap) -> SettingsDto {
    let batch_size = number(map, "batch_size", DEFAULT_BATCH_SIZE);
    SettingsDto {
        db_path: prefs::text(map, "db_path", ""),
        csv_dir: prefs::text(map, "csv_dir", ""),
        sftp_host: prefs::text(map, "sftp_host", DEFAULT_SFTP_HOST),
        sftp_port: number(map, "sftp_port", DEFAULT_SFTP_PORT),
        sftp_user: prefs::text(map, "sftp_user", ""),
        ai_title_label: prefs::text(map, "ai_title_label", DEFAULT_AI_TITLE_LABEL),
        batch_size,
        caption_limit: number(map, "caption_limit", batch_size),
        caption_workers: number(map, "caption_workers", DEFAULT_CAPTION_WORKERS),
        caption_per_call: number(map, "caption_per_call", DEFAULT_IMAGES_PER_CALL),
        claude_path: prefs::text(map, "claude_path", ""),
        theme: prefs::text(map, "theme", "system"),
    }
}

/// `host`, `sftp://host/` or `host:22` pasted from a docs page all mean the bare host name.
fn bare_hostname(value: &str) -> String {
    let trimmed = value.trim();
    let without_scheme = trimmed
        .strip_prefix("sftp://")
        .or_else(|| trimmed.strip_prefix("ssh://"))
        .unwrap_or(trimmed);
    let host = without_scheme.split('/').next().unwrap_or("");
    match host.rsplit_once(':') {
        Some((name, _)) if host.matches(':').count() == 1 => name.to_string(),
        _ => host.to_string(),
    }
}

fn in_range(label: &str, value: u32, max: u32) -> AppResult<()> {
    if (1..=max).contains(&value) {
        return Ok(());
    }
    Err(AppError::Invalid(format!("{label}: от 1 до {max}")))
}

/// The checked copy of the settings: trimmed text, bare host, `~` expanded in paths.
fn validated(dto: &SettingsDto) -> AppResult<SettingsDto> {
    let host = bare_hostname(&dto.sftp_host);
    if host.is_empty() {
        return Err(AppError::Invalid("SFTP хост не может быть пустым".into()));
    }
    in_range("SFTP порт", dto.sftp_port, MAX_PORT)?;
    in_range("Файлов в батче", dto.batch_size, MAX_BATCH_SIZE)?;
    in_range("Файлов за одно нажатие «Caption»", dto.caption_limit, MAX_BATCH_SIZE)?;
    in_range("Параллельных вызовов Claude", dto.caption_workers, MAX_CAPTION_WORKERS)?;
    in_range("Картинок в одном вызове", dto.caption_per_call, MAX_IMAGES_PER_CALL)?;
    if !THEMES.contains(&dto.theme.as_str()) {
        return Err(AppError::Invalid("Неизвестная тема".into()));
    }
    Ok(SettingsDto {
        db_path: expand_home(dto.db_path.trim()),
        csv_dir: expand_home(dto.csv_dir.trim()),
        sftp_host: host,
        sftp_user: dto.sftp_user.trim().to_string(),
        ai_title_label: dto.ai_title_label.trim().to_string(),
        claude_path: dto.claude_path.trim().to_string(),
        ..dto.clone()
    })
}

fn expand_home(path: &str) -> String {
    let Some(rest) = path.strip_prefix('~') else {
        return path.to_string();
    };
    let home = dirs::home_dir().unwrap_or_default();
    format!("{}{rest}", home.display())
}

fn merged(map: &PrefMap, dto: &SettingsDto) -> PrefMap {
    let updates = [
        ("db_path", dto.db_path.clone()),
        ("csv_dir", dto.csv_dir.clone()),
        ("sftp_host", dto.sftp_host.clone()),
        ("sftp_port", dto.sftp_port.to_string()),
        ("sftp_user", dto.sftp_user.clone()),
        ("ai_title_label", dto.ai_title_label.clone()),
        ("batch_size", dto.batch_size.to_string()),
        ("caption_limit", dto.caption_limit.to_string()),
        ("caption_workers", dto.caption_workers.to_string()),
        ("caption_per_call", dto.caption_per_call.to_string()),
        ("claude_path", dto.claude_path.clone()),
        ("theme", dto.theme.clone()),
    ];
    let mut next = map.clone();
    next.extend(updates.into_iter().map(|(key, value)| (key.to_string(), value)));
    next
}

fn view(dto: SettingsDto) -> SettingsView {
    let data_dir = prefs::legacy_data_dir();
    SettingsView {
        settings: dto,
        default_db_path: data_dir.join("state.db").display().to_string(),
        default_csv_dir: data_dir.join("csv").display().to_string(),
    }
}

#[tauri::command]
pub fn get_settings() -> AppResult<SettingsView> {
    Ok(view(from_map(&prefs::load()?)))
}

/// Saves the settings; a non-empty `password` goes to the keychain (never to the settings file).
#[tauri::command]
pub fn save_settings(settings: SettingsDto, password: Option<String>) -> AppResult<SettingsView> {
    let checked = validated(&settings)?;
    if let Some(password) = password.filter(|value| !value.is_empty()) {
        secrets::set(&password)?;
    }
    prefs::store(&merged(&prefs::load()?, &checked))?;
    Ok(view(checked))
}

/// SFTP login data for the checks and the upload, read from the saved settings and the keychain.
pub struct SftpLogin {
    pub host: String,
    pub port: u16,
    pub user: String,
    pub password: String,
}

pub fn sftp_login() -> AppResult<SftpLogin> {
    let dto = from_map(&prefs::load()?);
    let password = secrets::get()?;
    if dto.sftp_user.is_empty() || password.is_empty() {
        return Err(AppError::Check("Укажите SFTP логин и пароль".into()));
    }
    Ok(SftpLogin {
        host: dto.sftp_host,
        port: u16::try_from(dto.sftp_port).unwrap_or(22),
        user: dto.sftp_user,
        password,
    })
}

pub fn claude_path_setting() -> String {
    prefs::load()
        .map(|map| prefs::text(&map, "claude_path", ""))
        .unwrap_or_default()
}

#[cfg(test)]
mod tests {
    use super::*;

    fn sample() -> SettingsDto {
        from_map(&PrefMap::new())
    }

    #[test]
    fn host_pasted_as_url_becomes_bare_name() {
        assert_eq!(bare_hostname("sftp://example.com/"), "example.com");
        assert_eq!(bare_hostname(" example.com:22 "), "example.com");
        assert_eq!(bare_hostname("example.com"), "example.com");
    }

    #[test]
    fn defaults_match_the_python_app() {
        let dto = sample();
        assert_eq!(dto.sftp_host, DEFAULT_SFTP_HOST);
        assert_eq!(dto.batch_size, 1000);
        assert_eq!(dto.caption_limit, 1000);
        assert_eq!(dto.caption_workers, 1);
        assert_eq!(dto.caption_per_call, 5);
        assert_eq!(dto.theme, "system");
    }

    #[test]
    fn rejects_out_of_range_values() {
        let too_many = SettingsDto { caption_workers: 9, ..sample() };
        assert!(validated(&too_many).is_err());
        let bad_port = SettingsDto { sftp_port: 0, ..sample() };
        assert!(validated(&bad_port).is_err());
        let bad_theme = SettingsDto { theme: "neon".into(), ..sample() };
        assert!(validated(&bad_theme).is_err());
    }

    #[test]
    fn saving_keeps_keys_the_page_does_not_know() {
        let mut map = PrefMap::new();
        map.insert("thumb_size".into(), "S".into());
        let next = merged(&map, &sample());
        assert_eq!(next.get("thumb_size").map(String::as_str), Some("S"));
        assert_eq!(next.get("sftp_port").map(String::as_str), Some("22"));
    }

    #[test]
    fn trims_text_and_expands_home() {
        let dto = SettingsDto { sftp_user: " user ".into(), csv_dir: "~/csv".into(), ..sample() };
        let checked = validated(&dto).unwrap();
        assert_eq!(checked.sftp_user, "user");
        assert!(!checked.csv_dir.starts_with('~'));
    }
}
