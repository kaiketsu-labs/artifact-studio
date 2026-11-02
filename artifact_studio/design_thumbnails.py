"""Create bounded raster previews without executing presentation or image content."""

import io
import warnings
import zlib
from pathlib import Path
from zipfile import BadZipFile, ZipFile

from PIL import Image, ImageOps

_MAX_ARCHIVE_BYTES = 100_000_000
_MAX_IMAGE_BYTES = 30_000_000
_MAX_THUMBNAIL_BYTES = 10_000_000
_MAX_PIXELS = 20_000_000
_MAX_MEMBERS = 10_000
_POWERPOINT_SUFFIXES = {".pptx", ".potx"}
_RASTER_FORMATS = ["PNG", "JPEG", "WEBP", "GIF", "AVIF", "BMP", "TIFF", "ICO"]
THUMBNAIL_SUFFIXES = _POWERPOINT_SUFFIXES | {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".avif",
    ".bmp",
    ".tif",
    ".tiff",
    ".ico",
    ".emf",
    ".wdp",
}


def _embedded_thumbnail(path: Path) -> io.BytesIO:
    """Read a single bounded thumbnail from an Office container."""
    if path.stat().st_size > _MAX_ARCHIVE_BYTES:
        raise ValueError("Archive too large")
    with ZipFile(path) as archive:
        members = archive.infolist()
        if len(members) > _MAX_MEMBERS:
            raise ValueError("Too many archive members")
        matches = [
            member
            for member in members
            if member.filename in {"docProps/thumbnail.jpeg", "docProps/thumbnail.png"}
        ]
        if len(matches) != 1 or matches[0].file_size > _MAX_THUMBNAIL_BYTES:
            raise ValueError("Thumbnail missing or too large")
        with archive.open(matches[0]) as source:
            data = source.read(_MAX_THUMBNAIL_BYTES + 1)
        if len(data) > _MAX_THUMBNAIL_BYTES:
            raise ValueError("Thumbnail too large")
        return io.BytesIO(data)


def design_thumbnail(path: Path) -> bytes:
    """Return a bounded first-frame PNG or signal that no preview is available."""
    try:
        if path.suffix.lower() in _POWERPOINT_SUFFIXES:
            source: Path | io.BytesIO = _embedded_thumbnail(path)
            formats = ["PNG", "JPEG"]
        else:
            if (
                path.suffix.lower() not in THUMBNAIL_SUFFIXES
                or path.stat().st_size > _MAX_IMAGE_BYTES
            ):
                raise ValueError("Unsupported or oversized image")
            source = path
            formats = _RASTER_FORMATS
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(source, formats=formats) as image:
                if image.width * image.height > _MAX_PIXELS:
                    raise ValueError("Image dimensions too large")
                oriented = ImageOps.exif_transpose(image)
                oriented.thumbnail((640, 640))
                has_alpha = "A" in oriented.getbands() or "transparency" in oriented.info
                raster = oriented.convert("RGBA" if has_alpha else "RGB")
                output = io.BytesIO()
                raster.save(output, format="PNG")
                return output.getvalue()
    except (
        OSError,
        ValueError,
        BadZipFile,
        RuntimeError,
        NotImplementedError,
        EOFError,
        zlib.error,
        Image.DecompressionBombError,
        Image.DecompressionBombWarning,
    ) as error:
        raise FileNotFoundError("No thumbnail available.") from error
