//! The app's preferences: one flat string map in `settings.json`, like QSettings in the PySide app.
//!
//! Keys the settings page does not know (image folder, thumbnail size, last scan ...) are kept as they are,
//! so pages that move over later find their values. On the first run the map is imported from the PySide
//! app's preferences file.

use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

use crate::error::AppResult;

const APP_ID: &str = "com.adobe-stock-uploader.desktop";
const LEGACY_APP_DIR: &str = "Adobe Stock Uploader";
const LEGACY_PLIST: &str = "com.adobe-stock-uploader.Adobe Stock Uploader.plist";

pub type PrefMap = BTreeMap<String, String>;

/// `~/Library/Application Support/Adobe Stock Uploader` — where the PySide app keeps state.db and csv/.
pub fn legacy_data_dir() -> PathBuf {
    dirs::data_dir().unwrap_or_default().join(LEGACY_APP_DIR)
}

fn settings_file() -> PathBuf {
    dirs::data_dir()
        .unwrap_or_default()
        .join(APP_ID)
        .join("settings.json")
}

fn plist_value_text(value: &plist::Value) -> Option<String> {
    match value {
        plist::Value::String(text) => Some(text.clone()),
        plist::Value::Integer(number) => Some(number.to_string()),
        plist::Value::Boolean(flag) => Some(i32::from(*flag).to_string()),
        plist::Value::Real(number) => Some(number.to_string()),
        _ => None,
    }
}

fn import_legacy() -> PrefMap {
    let Some(prefs_dir) = dirs::preference_dir() else {
        return PrefMap::new();
    };
    let Ok(plist::Value::Dictionary(dict)) = plist::Value::from_file(prefs_dir.join(LEGACY_PLIST)) else {
        return PrefMap::new();
    };
    dict.iter()
        .filter_map(|(key, value)| plist_value_text(value).map(|text| (key.clone(), text)))
        .collect()
}

pub fn load() -> AppResult<PrefMap> {
    let file = settings_file();
    if file.exists() {
        return Ok(serde_json::from_str(&fs::read_to_string(file)?)?);
    }
    Ok(import_legacy())
}

/// Writes the whole map through a temporary file, so a crash never leaves half a file.
pub fn store(map: &PrefMap) -> AppResult<()> {
    let file = settings_file();
    if let Some(dir) = file.parent() {
        fs::create_dir_all(dir)?;
    }
    let temporary = file.with_extension("json.tmp");
    fs::write(&temporary, serde_json::to_string_pretty(map)?)?;
    fs::rename(temporary, file)?;
    Ok(())
}

pub fn text(map: &PrefMap, key: &str, default: &str) -> String {
    map.get(key).cloned().unwrap_or_else(|| default.to_string())
}

/// A saved database path, if the user has chosen one.
pub fn db_path_override() -> Option<PathBuf> {
    let map = load().ok()?;
    let path = text(&map, "db_path", "");
    (!path.trim().is_empty()).then(|| PathBuf::from(path))
}

/// Changes some keys and keeps the rest (the whole map is written back).
pub fn set_many(updates: &[(&str, String)]) -> AppResult<()> {
    let mut map = load()?;
    map.extend(updates.iter().map(|(key, value)| ((*key).to_string(), value.clone())));
    store(&map)
}

#[tauri::command]
pub fn get_pref(key: String, default: Option<String>) -> AppResult<String> {
    Ok(text(&load()?, &key, default.as_deref().unwrap_or("")))
}

#[tauri::command]
pub fn set_pref(key: String, value: String) -> AppResult<()> {
    set_many(&[(key.as_str(), value)])
}
