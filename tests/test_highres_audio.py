"""Unit tests for the checksum-guarded high-resolution audio patcher."""
from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "highres_audio", ROOT / "tools" / "highres_audio.py"
)
assert SPEC and SPEC.loader
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class HighResPatcherTests(unittest.TestCase):
    def test_binary_patch_checks_source_and_output_hashes(self) -> None:
        source = b"0123456789abcdef"
        expected = b"0123AB6789abcdef"
        relative = "unit-test/audio.primary.waydroid.so"
        original_files = MODULE.FILES
        MODULE.FILES = {
            relative: {
                "source_sha256": digest(source),
                "patched_sha256": digest(expected),
                "edits": {4: ("3435", "4142")},
            }
        }
        try:
            self.assertEqual(MODULE.patch_binary(relative, source), expected)
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                MODULE.patch_binary(relative, b"unknown binary")
        finally:
            MODULE.FILES = original_files

    def test_binary_patch_rejects_unknown_file(self) -> None:
        with self.assertRaisesRegex(ValueError, "No tested high-resolution patch"):
            MODULE.patch_binary("unlisted/audio.so", b"arbitrary")

    def test_policy_patch_uses_exact_hashes(self) -> None:
        original_old = MODULE.POLICY_OLD
        original_new = MODULE.POLICY_NEW
        original_source_hash = MODULE.POLICY_SOURCE_SHA256
        original_patched_hash = MODULE.POLICY_PATCHED_SHA256
        old = b'format="AUDIO_FORMAT_PCM_16_BIT"\n                             samplingRates="48000" channelMasks="AUDIO_CHANNEL_OUT_STEREO"'
        new = b'format="AUDIO_FORMAT_PCM_32_BIT"\n                             samplingRates="192000" channelMasks="AUDIO_CHANNEL_OUT_STEREO"'
        source = b"<policy>" + old + b"</policy>"
        expected = b"<policy>" + new + b"</policy>"
        MODULE.POLICY_OLD = old
        MODULE.POLICY_NEW = new
        MODULE.POLICY_SOURCE_SHA256 = digest(source)
        MODULE.POLICY_PATCHED_SHA256 = digest(expected)
        try:
            self.assertEqual(MODULE.patch_policy(source), expected)
            with self.assertRaisesRegex(ValueError, "Unsupported"):
                MODULE.patch_policy(b"<policy>unknown</policy>")
        finally:
            MODULE.POLICY_OLD = original_old
            MODULE.POLICY_NEW = original_new
            MODULE.POLICY_SOURCE_SHA256 = original_source_hash
            MODULE.POLICY_PATCHED_SHA256 = original_patched_hash

    def test_build_overlay_rejects_unknown_image_before_writing(self) -> None:
        original_files = MODULE.FILES
        original_policy_old = MODULE.POLICY_OLD
        original_policy_source_hash = MODULE.POLICY_SOURCE_SHA256
        original_policy_patched_hash = MODULE.POLICY_PATCHED_SHA256
        relative = "unit-test/audio.so"
        valid_source = b"test binary bytes"
        MODULE.FILES = {
            relative: {
                "source_sha256": digest(valid_source),
                "patched_sha256": digest(valid_source),
                "edits": {},
            }
        }
        MODULE.POLICY_OLD = b"old-profile"
        MODULE.POLICY_SOURCE_SHA256 = digest(b"unknown-policy")
        MODULE.POLICY_PATCHED_SHA256 = digest(b"patched-policy")
        try:
            with tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                image = root / "vendor.img"
                image.write_bytes(b"fake image")
                output = root / "output"
                with patch.object(MODULE, "_extract", side_effect=[b"unknown binary"]):
                    with self.assertRaisesRegex(ValueError, "Unsupported"):
                        MODULE.build_overlay(image, output)
                self.assertFalse(output.exists())
        finally:
            MODULE.FILES = original_files
            MODULE.POLICY_OLD = original_policy_old
            MODULE.POLICY_SOURCE_SHA256 = original_policy_source_hash
            MODULE.POLICY_PATCHED_SHA256 = original_policy_patched_hash


if __name__ == "__main__":
    unittest.main()
