mod checks;
mod db;
mod error;
mod events;
mod prefs;
mod secrets;
mod settings;
mod summary;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .invoke_handler(tauri::generate_handler![
            summary::status_summary,
            events::search_events,
            events::event_commands,
            settings::get_settings,
            settings::save_settings,
            checks::check_claude,
            checks::check_sftp,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Adobe Stock Uploader");
}
