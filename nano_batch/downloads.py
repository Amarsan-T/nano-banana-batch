from __future__ import annotations

import hashlib
import os
import re
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


def _resolve_download_path(path: Path) -> Path:
    """Resolve existing ancestry strictly; permit only genuinely missing suffixes."""
    missing = []
    current = path
    try:
        while True:
            try:
                resolved = current.resolve(strict=True)
                target = resolved.joinpath(*reversed(missing))
                if path.resolve() != target:
                    raise ValueError("Download path resolution is inconsistent.")
                return target
            except FileNotFoundError:
                try:
                    current.lstat()
                except FileNotFoundError:
                    if current == current.parent:
                        raise ValueError("Download path has no resolvable filesystem root.")
                    missing.append(current.name)
                    current = current.parent
                else:
                    raise ValueError("Download path contains a dangling filesystem link.")
    except (OSError, RuntimeError) as exc:
        raise ValueError("Download path cannot be safely resolved.") from exc


def run_asset_directory(run_dir: Path) -> Path:
    """Require the literal assets child of the resolved, trusted run root."""
    root = _resolve_download_path(run_dir)
    assets = root / "assets"
    if _resolve_download_path(assets) != assets:
        raise ValueError("Run assets directory contains symlink/junction redirection.")
    return assets


def deterministic_candidate_path(run_dir: Path, filename: str) -> Path:
    """Return a traversal-safe, run-controlled candidate asset path."""
    if not filename or Path(filename).is_absolute() or Path(filename).name != filename:
        raise ValueError(f"Unsafe candidate filename: {filename!r}")
    asset_dir = run_asset_directory(run_dir)
    target = asset_dir / filename
    if _resolve_download_path(target) != target:
        raise ValueError("Candidate path escapes or redirects its deterministic asset location.")
    return target


def deterministic_staging_path(run_dir: Path, staging_dir: Path, variant: int) -> Path:
    """Validate a planned staging destination before allocating its directory."""
    asset_dir = run_asset_directory(run_dir)
    if (
        staging_dir.parent != asset_dir
        or not re.fullmatch(r"\.staging-[a-z0-9_-]+", staging_dir.name)
        or type(variant) is not int or variant <= 0
    ):
        raise ValueError("Staging path does not match its run-owned layout.")
    target = staging_dir / f"v{variant:02d}.download"
    if _resolve_download_path(staging_dir) != staging_dir or _resolve_download_path(target) != target:
        raise ValueError("Staging path escapes or redirects its run-owned location.")
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
    except OSError as exc:
        if os.name != "nt":
            raise OSError("No safe no-overwrite fallback is available on this platform.") from exc

    # Callers validate ownership and content before installation. Reuse those
    # complete staging bytes; never stream into the public canonical name.
    if staged_path.stat().st_dev != target_path.parent.stat().st_dev:
        raise OSError("No-overwrite staging publication requires the same volume.")
    with staged_path.open("r+b") as source:
        source.flush()
        os.fsync(source.fileno())
    # Windows rename refuses an existing destination and does not enable a
    # cross-volume copy. Close the flush handle first; do not chmod or clean up
    # either pathname on failure. Filesystem/power-loss guarantees remain local.
    os.rename(staged_path, target_path)
