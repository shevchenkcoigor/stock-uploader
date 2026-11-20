"""Pre-upload image checks for Adobe Stock (JPEG, sRGB, 4-100 MP, <= 45 MB)."""

import io
from pathlib import Path

from PIL import Image, ImageCms

MAX_FILE_BYTES = 45 * 1024 * 1024
MIN_MEGAPIXELS = 4
MAX_MEGAPIXELS = 100
ALLOWED_EXTENSIONS = frozenset({".jpg", ".jpeg"})
BYTES_PER_MEGAPIXEL = 1_000_000

# Pillow refuses huge images by default; the Adobe limit is 100 MP, so allow a bit more
# and let our own check report the real problem.
Image.MAX_IMAGE_PIXELS = 200 * BYTES_PER_MEGAPIXEL


def _profile_name(image: Image.Image) -> str | None:
    raw = image.info.get("icc_profile")
    if not raw:
        return None
    try:
        profile = ImageCms.ImageCmsProfile(io.BytesIO(raw))
        return ImageCms.getProfileDescription(profile).strip()
    except (ImageCms.PyCMSError, OSError):
        return "unreadable"


def validate_image(path: Path) -> list[str]:
    """Return a list of human-readable problems; empty means the file is OK."""
    problems: list[str] = []
    if path.suffix.lower() not in ALLOWED_EXTENSIONS:
        problems.append(f"extension {path.suffix!r} is not .jpg/.jpeg")
    size = path.stat().st_size
    if size > MAX_FILE_BYTES:
        problems.append(f"file is {size / 1024 / 1024:.1f} MB, limit is 45 MB")

    try:
        with Image.open(path) as image:
            if image.format != "JPEG":
                problems.append(f"content is {image.format}, not JPEG")
            megapixels = image.width * image.height / BYTES_PER_MEGAPIXEL
            if not MIN_MEGAPIXELS <= megapixels <= MAX_MEGAPIXELS:
                problems.append(
                    f"{megapixels:.1f} MP is outside {MIN_MEGAPIXELS}-{MAX_MEGAPIXELS} MP"
                )
            profile = _profile_name(image)
            # No embedded profile is treated as sRGB; a different profile is an error.
            if profile is not None and "srgb" not in profile.lower():
                problems.append(f"color profile is {profile!r}, expected sRGB")
    except (OSError, Image.DecompressionBombError) as error:
        problems.append(f"cannot read image: {error}")
    return problems
