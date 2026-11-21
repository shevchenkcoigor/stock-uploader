//! Looking at image files: scaled thumbnails (cached on disk) and opening a file in the system.

use std::io::{BufReader, Cursor};
use std::path::{Path, PathBuf};
use std::process::Command;

use base64::Engine;
use image::{codecs::jpeg::JpegEncoder, DynamicImage, ExtendedColorType, RgbImage};
use sha2::{Digest, Sha256};

use crate::error::{AppError, AppResult};

const DEVICE_SCALE: u32 = 2; // drawn at twice the size so it stays sharp on retina screens
const THUMB_QUALITY: u8 = 82;
const CACHE_DIR: &str = "com.adobe-stock-uploader.desktop/thumbs";

fn cache_file(path: &Path, side: u32) -> Option<PathBuf> {
    let meta = std::fs::metadata(path).ok()?;
    let modified = meta.modified().ok()?.duration_since(std::time::UNIX_EPOCH).ok()?.as_nanos();
    let key = format!("{}|{modified}|{}|{side}", path.display(), meta.len());
    let digest: String = Sha256::digest(key.as_bytes()).iter().map(|byte| format!("{byte:02x}")).collect();
    Some(dirs::cache_dir()?.join(CACHE_DIR).join(format!("{digest}.jpg")))
}

/// A scaled decode: JPEGs are shrunk while decoding, so even a 100 MP file stays fast.
fn decode_scaled(path: &Path, side: u32) -> Option<RgbImage> {
    let requested = u16::try_from(side).ok()?;
    let mut decoder = jpeg_decoder::Decoder::new(BufReader::new(std::fs::File::open(path).ok()?));
    decoder.read_info().ok()?;
    decoder.scale(requested, requested).ok()?;
    let pixels = decoder.decode().ok()?;
    let info = decoder.info()?;
    let (width, height) = (u32::from(info.width), u32::from(info.height));
    match info.pixel_format {
        jpeg_decoder::PixelFormat::RGB24 => RgbImage::from_raw(width, height, pixels),
        jpeg_decoder::PixelFormat::L8 => {
            Some(image::GrayImage::from_raw(width, height, pixels).map(|gray| DynamicImage::ImageLuma8(gray).to_rgb8())?)
        }
        _ => None,
    }
}

fn render(path: &Path, side: u32) -> Option<Vec<u8>> {
    let pixels = side * DEVICE_SCALE;
    let decoded = decode_scaled(path, pixels).or_else(|| image::open(path).ok().map(|image| image.to_rgb8()))?;
    let small = DynamicImage::ImageRgb8(decoded).thumbnail(pixels, pixels).to_rgb8();
    let mut bytes = Vec::new();
    JpegEncoder::new_with_quality(Cursor::new(&mut bytes), THUMB_QUALITY)
        .encode(small.as_raw(), small.width(), small.height(), ExtendedColorType::Rgb8)
        .ok()?;
    Some(bytes)
}

fn data_url(jpeg: &[u8]) -> String {
    format!("data:image/jpeg;base64,{}", base64::engine::general_purpose::STANDARD.encode(jpeg))
}

fn thumbnail_blocking(path: &str, side: u32) -> Option<String> {
    let path = Path::new(path);
    let cached = cache_file(path, side);
    if let Some(bytes) = cached.as_ref().and_then(|file| std::fs::read(file).ok()) {
        return Some(data_url(&bytes));
    }
    let bytes = render(path, side)?;
    if let Some(file) = cached {
        if let Some(dir) = file.parent() {
            let _ = std::fs::create_dir_all(dir);
        }
        let _ = std::fs::write(file, &bytes); // a missing cache only costs time
    }
    Some(data_url(&bytes))
}

/// A data URL of the scaled picture, or `None` when it cannot be read (the page shows a placeholder).
#[tauri::command]
pub async fn thumbnail(path: String, side: u32) -> Option<String> {
    tauri::async_runtime::spawn_blocking(move || thumbnail_blocking(&path, side)).await.ok().flatten()
}

fn run(program: &str, args: &[&str]) -> AppResult<()> {
    Command::new(program)
        .args(args)
        .spawn()
        .map(|_| ())
        .map_err(|error| AppError::Check(format!("Не удалось выполнить {program}: {error}")))
}

fn require_file(path: &str) -> AppResult<()> {
    if Path::new(path).exists() {
        return Ok(());
    }
    Err(AppError::Invalid(format!("Файл не найден: {path}")))
}

#[tauri::command]
pub fn open_path(path: String) -> AppResult<()> {
    require_file(&path)?;
    if cfg!(target_os = "macos") {
        run("open", &[&path])
    } else if cfg!(target_os = "windows") {
        run("explorer", &[&path])
    } else {
        run("xdg-open", &[&path])
    }
}

#[tauri::command]
pub fn reveal_path(path: String) -> AppResult<()> {
    require_file(&path)?;
    if cfg!(target_os = "macos") {
        run("open", &["-R", &path])
    } else if cfg!(target_os = "windows") {
        run("explorer", &[&format!("/select,{path}")])
    } else {
        let parent = Path::new(&path).parent().map(|dir| dir.display().to_string()).unwrap_or_default();
        run("xdg-open", &[&parent])
    }
}
