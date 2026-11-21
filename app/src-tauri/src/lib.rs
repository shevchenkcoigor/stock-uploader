mod db;
mod error;
mod events;
mod summary;

#[cfg_attr(mobile, tauri::mobile_entry_point)]
pub fn run() {
    tauri::Builder::default()
        .invoke_handler(tauri::generate_handler![
            summary::status_summary,
            events::search_events,
            events::event_commands,
        ])
        .run(tauri::generate_context!())
        .expect("error while running Adobe Stock Uploader");
}
