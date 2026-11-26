//! The app's preferences: one flat string map in `settings.json`, like QSettings in the PySide app.
//!
//! Keys the settings page does not know (image folder, thumbnail size, last scan ...) are kept as they are,
//! so pages that move over later find their values. On the first run the map is imported from the PySide
//! app's preferences file.

use std::collections::BTreeMap;
use std::fs;
use std::path::PathBuf;

use crate::core::migrate;
use crate::error::AppResult;

const APP_ID: &str = "com.stock-uploader.desktop";
const OLD_APP_ID: &str = "com.adobe-stock-uploader.desktop";
const APP_DIR: &str = "Stock Uploader";
const OLD_APP_DIR: &str = "Adobe Stock Uploader";
const LEGACY_PLIST: &str = "com.adobe-stock-uploader.Adobe Stock Uploader.plist";

pub type PrefMap = BTreeMap<String, String>;

fn support_dir(name: &str) -> PathBuf {
    dirs::data_dir().unwrap_or_default().join(name)
}

/// `~/Library/Application Support/Adobe Stock Uploader`: the data folder of the previous app name.
pub fn old_data_dir() -> PathBuf {
    support_dir(OLD_APP_DIR)
}

/// Where the data folder will be (or already is) under the current name.
pub fn new_data_dir() -> PathBuf {
    support_dir(APP_DIR)
}

/// The folder holding state.db: the new one, or the old one while its data has not been copied.
pub fn data_dir() -> PathBuf {
    migrate::data_dir(&old_data_dir(), &new_data_dir())
}

fn settings_file() -> PathBuf {
    support_dir(APP_ID).join("settings.json")
}

/// The settings file to read: the previous name's one until the first save under the new name.
fn readable_settings(current: PathBuf, old: PathBuf) -> Option<PathBuf> {
    [current, old].into_iter().find(|file| file.exists())
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
    dict.iter().filter_map(|(key, value)| plist_value_text(value).map(|text| (key.clone(), text))).collect()
}

pub fn load() -> AppResult<PrefMap> {
    if let Some(file) = readable_settings(settings_file(), support_dir(OLD_APP_ID).join("settings.json")) {
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

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_old_settings_are_read_until_new_ones_exist() {
        let dir = tempfile::tempdir().unwrap();
        let (current, old) = (dir.path().join("new.json"), dir.path().join("old.json"));
        assert_eq!(readable_settings(current.clone(), old.clone()), None);

        fs::write(&old, "{}").unwrap();
        assert_eq!(readable_settings(current.clone(), old.clone()), Some(old.clone()));

        fs::write(&current, "{}").unwrap();
        assert_eq!(readable_settings(current.clone(), old), Some(current));
    }
}
