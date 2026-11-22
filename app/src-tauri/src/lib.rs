mod checks;
pub mod core;
mod db;
mod caption_cmd;
mod csv_cmd;
mod describe_cmd;
mod error;
mod events;
mod files;
mod prefs;
mod scan_cmd;
mod upload_cmd;
mod secrets;
mod settings;
mod summary;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .plugin(tauri_plugin_dialog::init())
        .manage(scan_cmd::ScanControl::default())
        .manage(caption_cmd::CaptionControl::default())
        .manage(upload_cmd::UploadControl::default())
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
            csv_cmd::csv_overview,
            csv_cmd::csv_batch_rows,
            csv_cmd::csv_build,
            csv_cmd::csv_mark_submitted,
            upload_cmd::upload_overview,
            upload_cmd::upload_queue,
            upload_cmd::upload_prepare,
            upload_cmd::upload_start,
            upload_cmd::upload_pause,
            caption_cmd::caption_models,
            caption_cmd::caption_start,
            caption_cmd::caption_cancel,
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
