//! Scaled decoding of pictures: JPEGs are shrunk while they are decoded, so even a 100 MP file stays fast.

use std::io::BufReader;
use std::path::Path;

use image::{DynamicImage, GrayImage, RgbImage};

/// An RGB image at least `min_side` pixels on its longer side (when the picture is that big),
/// decoded with the JPEG decoder's own downscaling. `None` for anything but JPEG.
pub fn decode_jpeg_scaled(path: &Path, min_side: u32) -> Option<RgbImage> {
    let requested = u16::try_from(min_side).ok()?;
    let mut decoder = jpeg_decoder::Decoder::new(BufReader::new(std::fs::File::open(path).ok()?));
    decoder.read_info().ok()?;
    decoder.scale(requested, requested).ok()?;
    let pixels = decoder.decode().ok()?;
    let info = decoder.info()?;
    let (width, height) = (u32::from(info.width), u32::from(info.height));
    match info.pixel_format {
        jpeg_decoder::PixelFormat::RGB24 => RgbImage::from_raw(width, height, pixels),
        jpeg_decoder::PixelFormat::L8 => {
            GrayImage::from_raw(width, height, pixels).map(|gray| DynamicImage::ImageLuma8(gray).to_rgb8())
        }
        _ => None,
    }
}

/// The scaled JPEG decode, or a full decode by any supported decoder (CMYK and other odd files).
pub fn decode_rgb(path: &Path, min_side: u32) -> Option<RgbImage> {
    decode_jpeg_scaled(path, min_side).or_else(|| image::open(path).ok().map(|picture| picture.to_rgb8()))
}

/// Shrinks to fit `max_side` x `max_side` (never enlarges), keeping the proportions.
pub fn fit_within(picture: RgbImage, max_side: u32) -> RgbImage {
    if picture.width().max(picture.height()) <= max_side {
        return picture;
    }
    DynamicImage::ImageRgb8(picture).thumbnail(max_side, max_side).to_rgb8()
}
