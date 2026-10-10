from __future__ import annotations

import hashlib
import os
import shutil
from dataclasses import dataclass
from pathlib import Path


class ImageValidationError(RuntimeError):
    """Raised when a downloaded file is not a supported, decodable image."""


SUPPORTED_IMAGE_FORMATS = {
    "PNG": ("image/png", {".png"}),
    "JPEG": ("image/jpeg", {".jpg", ".jpeg"}),
    "WEBP": ("image/webp", {".webp"}),
}

CANONICAL_IMAGE_EXTENSIONS = {
    "PNG": "png",
    "JPEG": "jpeg",
    "WEBP": "webp",
}


@dataclass(frozen=True)
class ValidatedImage:
    image_format: str
    mime_type: str
    width: int
    height: int
    byte_size: int
    sha256: str


def canonical_extension_for_image_format(image_format: str) -> str:
    """Return the one project-standard extension for validated image bytes."""
    normalized = str(image_format or "").upper()
    try:
        return CANONICAL_IMAGE_EXTENSIONS[normalized]
    except KeyError as exc:
        raise ImageValidationError(
            f"Unsupported canonical image format {normalized or '(unknown)'!r}."
        ) from exc


def deterministic_candidate_path(run_dir: Path, filename: str) -> Path:
    """Return a traversal-safe, run-controlled candidate asset path."""
    if not filename or Path(filename).is_absolute() or Path(filename).name != filename:
        raise ValueError(f"Unsafe candidate filename: {filename!r}")
    asset_dir = (run_dir / "assets").resolve()
    target = (asset_dir / filename).resolve()
    try:
        target.relative_to(asset_dir)
    except ValueError as exc:
        raise ValueError("Candidate path escapes the run asset directory.") from exc
    if target.parent != asset_dir:
        raise ValueError("Candidate files must be direct children of the run asset directory.")
    return target


def validate_image_file(path: Path, *, expected_filename: str | None = None) -> ValidatedImage:
    """Decode an image fully and return normalized integrity metadata."""
    path = path.resolve()
    if not path.is_file():
        raise ImageValidationError(f"Downloaded image does not exist: {path}")
    byte_size = path.stat().st_size
    if byte_size <= 0:
        raise ImageValidationError(f"Downloaded image is empty: {path}")

    try:
        from PIL import Image, UnidentifiedImageError
    except ImportError as exc:
        raise RuntimeError(
            "Pillow is required for image validation. Install project requirements first."
        ) from exc

    try:
        with Image.open(path) as image:
            image_format = str(image.format or "").upper()
            image.verify()
        with Image.open(path) as image:
            image.load()
            width, height = image.size
    except (OSError, SyntaxError, UnidentifiedImageError) as exc:
        raise ImageValidationError(f"Downloaded file is not a decodable image: {path}") from exc

    if image_format not in SUPPORTED_IMAGE_FORMATS:
        raise ImageValidationError(
            f"Unsupported downloaded image format {image_format or '(unknown)'!r}: {path}"
        )
    if width <= 0 or height <= 0:
        raise ImageValidationError(f"Downloaded image has invalid dimensions {width}x{height}: {path}")

    mime_type, allowed_suffixes = SUPPORTED_IMAGE_FORMATS[image_format]
    if expected_filename:
        expected_suffix = Path(expected_filename).suffix.lower()
        if expected_suffix not in allowed_suffixes:
            raise ImageValidationError(
                f"Downloaded {image_format} content does not match expected filename "
                f"{expected_filename!r}."
            )

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return ValidatedImage(
        image_format=image_format,
        mime_type=mime_type,
        width=width,
        height=height,
        byte_size=byte_size,
        sha256=digest.hexdigest(),
    )


def commit_staged_file(staged_path: Path, target_path: Path, *, overwrite: bool) -> None:
    """Install a validated staged file without accidental overwrite."""
    staged_path = staged_path.resolve()
    target_path = target_path.resolve()
    if not staged_path.is_file():
        raise FileNotFoundError(f"Staged download is missing: {staged_path}")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    if target_path.exists() and not overwrite:
        raise FileExistsError(f"Refusing to overwrite existing candidate asset: {target_path}")
    if overwrite:
        os.replace(staged_path, target_path)
        return

    try:
        os.link(staged_path, target_path)
        return
    except FileExistsError as exc:
        raise FileExistsError(
            f"Refusing to overwrite existing candidate asset: {target_path}"
        ) from exc
    except OSError:
        pass

    created_target = False
    try:
        with staged_path.open("rb") as source, target_path.open("xb") as destination:
            created_target = True
            shutil.copyfileobj(source, destination)
            destination.flush()
            os.fsync(destination.fileno())
    except Exception:
        if created_target:
            try:
                target_path.unlink()
            except FileNotFoundError:
                pass
        raise
