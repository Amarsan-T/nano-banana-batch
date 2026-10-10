import hashlib
import tempfile
import unittest
from pathlib import Path

from PIL import Image

from nano_batch.downloads import (
    ImageValidationError,
    canonical_extension_for_image_format,
    commit_staged_file,
    deterministic_candidate_path,
    validate_image_file,
)


class DownloadFileTests(unittest.TestCase):
    def test_validated_formats_have_one_canonical_extension(self):
        self.assertEqual(canonical_extension_for_image_format("PNG"), "png")
        self.assertEqual(canonical_extension_for_image_format("JPEG"), "jpeg")
        self.assertEqual(canonical_extension_for_image_format("WEBP"), "webp")
        with self.assertRaisesRegex(ImageValidationError, "Unsupported canonical"):
            canonical_extension_for_image_format("TIFF")

    def test_deterministic_candidate_path_is_run_controlled(self):
        with tempfile.TemporaryDirectory() as tmp:
            run_dir = Path(tmp) / "run"
            path = deterministic_candidate_path(
                run_dir,
                "0001__scene_0001_visual_001__v01.png",
            )
            self.assertEqual(
                path,
                (run_dir / "assets" / "0001__scene_0001_visual_001__v01.png").resolve(),
            )
            with self.assertRaisesRegex(ValueError, "Unsafe candidate filename"):
                deterministic_candidate_path(run_dir, "../escape.png")

    def test_png_decode_dimensions_mime_size_and_sha256(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "candidate.png"
            Image.new("RGB", (7, 5), (20, 40, 60)).save(image_path, format="PNG")
            metadata = validate_image_file(
                image_path,
                expected_filename="candidate.png",
            )
            self.assertEqual(metadata.image_format, "PNG")
            self.assertEqual(metadata.mime_type, "image/png")
            self.assertEqual((metadata.width, metadata.height), (7, 5))
            self.assertEqual(metadata.byte_size, image_path.stat().st_size)
            self.assertEqual(
                metadata.sha256,
                hashlib.sha256(image_path.read_bytes()).hexdigest(),
            )

    def test_empty_and_invalid_images_are_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            empty = Path(tmp) / "empty.png"
            empty.write_bytes(b"")
            with self.assertRaisesRegex(ImageValidationError, "empty"):
                validate_image_file(empty, expected_filename=empty.name)

            invalid = Path(tmp) / "invalid.png"
            invalid.write_bytes(b"not an image")
            with self.assertRaisesRegex(ImageValidationError, "decodable"):
                validate_image_file(invalid, expected_filename=invalid.name)

    def test_actual_format_must_match_deterministic_extension(self):
        with tempfile.TemporaryDirectory() as tmp:
            image_path = Path(tmp) / "mismatch.png"
            Image.new("RGB", (3, 2), "red").save(image_path, format="JPEG")
            with self.assertRaisesRegex(ImageValidationError, "does not match"):
                validate_image_file(image_path, expected_filename=image_path.name)

    def test_commit_refuses_overwrite_without_explicit_permission(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            staged = root / "staged"
            target = root / "target.png"
            staged.write_bytes(b"new")
            target.write_bytes(b"old")
            with self.assertRaisesRegex(FileExistsError, "Refusing to overwrite"):
                commit_staged_file(staged, target, overwrite=False)
            self.assertEqual(target.read_bytes(), b"old")


if __name__ == "__main__":
    unittest.main()
