mod checks;
pub mod core;
mod db;
mod describe_cmd;
mod error;
mod events;
mod files;
mod prefs;
mod scan_cmd;
mod secrets;
mod settings;
mod summary;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(scan_cmd::ScanControl::default())
        .invoke_handler(tauri::generate_handler![
            summary::status_summary,
            events::search_events,
            events::event_commands,
            settings::get_settings,
            settings::save_settings,
            checks::check_claude,
            checks::check_sftp,
            prefs::get_pref,
            prefs::set_pref,
            scan_cmd::scan_start,
            scan_cmd::scan_cancel,
            scan_cmd::problem_files,
            describe_cmd::describe_files,
            describe_cmd::save_metadata,
            describe_cmd::bulk_apply,
            describe_cmd::set_files_status,
            describe_cmd::approve_files,
            describe_cmd::image_sizes,
            files::thumbnail,
            files::open_path,
            files::reveal_path,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Adobe Stock Uploader");
}
