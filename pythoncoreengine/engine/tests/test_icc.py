"""Unit tests for the in-code ICC profile builder (engine.color).

Verifies both generated profiles are structurally valid ICC v2.1:
the header fields (size field matching the byte length, version 2.1,
``mntr`` class, ``acsp`` file signature, D50 illuminant), the profile ID
(md5 of the whole profile with the ID field zeroed), a sane tag table
(4-byte-aligned, in-bounds entries covering the required tag set), the
D50 white point and sRGB primary values as s15Fixed16, and the 256-entry
tone-response curves matching the sRGB / gamma-2.2 transfer functions.
``ICCProfile.components`` / ``.alternate`` carry the PDF-side parameters
(``/N`` and ``/Alternate``).
"""

from __future__ import annotations

import hashlib
import struct
import unittest
from typing import Dict, List, Tuple

from engine.color import ICCProfile, gray_icc, srgb_icc

_TAG_DATA: Dict[bytes, List[Tuple[int, int]]] = {}
_REQUIRED_SRGB_TAGS = (
    b"desc",
    b"cprt",
    b"wtpt",
    b"rXYZ",
    b"gXYZ",
    b"bXYZ",
    b"rTRC",
    b"gTRC",
    b"bTRC",
)
_REQUIRED_GRAY_TAGS = (b"desc", b"cprt", b"wtpt", b"kTRC")


def _tags(profile: bytes) -> Dict[bytes, Tuple[int, int]]:
    """Parse the tag table; return {signature: (offset, size)}."""
    count = struct.unpack(">I", profile[128:132])[0]
    result: Dict[bytes, Tuple[int, int]] = {}
    for index in range(count):
        sig, offset, size = struct.unpack(">4sII", profile[132 + 12 * index : 144 + 12 * index])
        result[sig] = (offset, size)
    return result


def _s15fixed16(data: bytes, offset: int) -> float:
    return struct.unpack(">i", data[offset : offset + 4])[0] / 65536.0


def _curve_values(profile: bytes, sig: bytes) -> List[int]:
    """The 8-bit entries of a curveType tag."""
    offset, size = _tags(profile)[sig]
    count = struct.unpack(">I", profile[offset + 8 : offset + 12])[0]
    return list(profile[offset + 12 : offset + 12 + count])


class TestICCHeader(unittest.TestCase):
    def setUp(self) -> None:
        self.srgb = ICCProfile.srgb()
        self.gray = ICCProfile.gray()

    def test_profile_file_signature(self) -> None:
        for profile in (self.srgb.data, self.gray.data):
            self.assertEqual(profile[36:40], b"acsp")
            self.assertEqual(profile[12:16], b"mntr")
            self.assertEqual(profile[20:24], b"XYZ ")

    def test_version_is_2_1(self) -> None:
        for profile in (self.srgb.data, self.gray.data):
            self.assertEqual(profile[8], 2)
            self.assertEqual(profile[9], 0x10)
            self.assertEqual(struct.unpack(">I", profile[8:12])[0], 0x02100000)

    def test_size_field_matches_length(self) -> None:
        for profile in (self.srgb.data, self.gray.data):
            self.assertEqual(struct.unpack(">I", profile[0:4])[0], len(profile))

    def test_colour_space_field(self) -> None:
        self.assertEqual(self.srgb.data[16:20], b"RGB ")
        self.assertEqual(self.gray.data[16:20], b"GRAY")

    def test_profile_id_is_md5_of_zeroed_profile(self) -> None:
        for profile in (self.srgb.data, self.gray.data):
            body = profile[:84] + b"\x00" * 16 + profile[100:]
            self.assertEqual(hashlib.md5(body).digest(), profile[84:100])

    def test_illuminant_is_d50(self) -> None:
        for profile in (self.srgb.data, self.gray.data):
            x = _s15fixed16(profile, 68)
            y = _s15fixed16(profile, 72)
            z = _s15fixed16(profile, 76)
            self.assertAlmostEqual(x, 0.9642, places=4)
            self.assertAlmostEqual(y, 1.0, places=4)
            self.assertAlmostEqual(z, 0.8249, places=4)

    def test_deterministic(self) -> None:
        self.assertEqual(ICCProfile.srgb().data, self.srgb.data)
        self.assertEqual(ICCProfile.gray().data, self.gray.data)
        self.assertEqual(srgb_icc().data, self.srgb.data)
        self.assertEqual(gray_icc().data, self.gray.data)


class TestICCTagTable(unittest.TestCase):
    def setUp(self) -> None:
        self.srgb = ICCProfile.srgb().data
        self.gray = ICCProfile.gray().data

    def test_tag_entries_aligned_and_in_bounds(self) -> None:
        for profile in (self.srgb, self.gray):
            for offset, size in _tags(profile).values():
                self.assertEqual(offset % 4, 0)
                self.assertGreaterEqual(offset, 132 + 12 * len(_tags(profile)))
                self.assertLessEqual(offset + size, len(profile))

    def test_srgb_required_tags_present(self) -> None:
        present = set(_tags(self.srgb))
        for sig in _REQUIRED_SRGB_TAGS:
            self.assertIn(sig, present)

    def test_gray_required_tags_present(self) -> None:
        present = set(_tags(self.gray))
        for sig in _REQUIRED_GRAY_TAGS:
            self.assertIn(sig, present)
        self.assertNotIn(b"rXYZ", present)

    def test_gray_trc_tag_is_four_byte_signature(self) -> None:
        self.assertIn(b"kTRC", _tags(self.gray))
        for sig in _tags(self.gray):
            self.assertEqual(len(sig), 4)

    def test_text_tags_are_ascii_text_description(self) -> None:
        for profile in (self.srgb, self.gray):
            offset, _size = _tags(profile)[b"desc"]
            self.assertEqual(profile[offset : offset + 4], b"desc")
            count = struct.unpack(">I", profile[offset + 8 : offset + 12])[0]
            text = profile[offset + 12 : offset + 12 + count - 1]
            self.assertTrue(text.isascii())

    def test_xyz_tags_carry_type_signature(self) -> None:
        for profile in (self.srgb, self.gray):
            offset, _size = _tags(profile)[b"wtpt"]
            self.assertEqual(profile[offset : offset + 4], b"XYZ ")


class TestICCValues(unittest.TestCase):
    def setUp(self) -> None:
        self.srgb = ICCProfile.srgb()
        self.gray = ICCProfile.gray()

    def test_s15fixed16_values_within_range(self) -> None:
        for profile in (self.srgb.data, self.gray.data):
            for offset, size in _tags(profile).values():
                if profile[offset : offset + 4] != b"XYZ ":
                    continue
                for pos in range(offset + 8, offset + size, 4):
                    raw = struct.unpack(">i", profile[pos : pos + 4])[0]
                    self.assertGreaterEqual(raw, 0)

    def test_white_point_is_d50(self) -> None:
        for profile in (self.srgb.data, self.gray.data):
            offset, _size = _tags(profile)[b"wtpt"]
            x = _s15fixed16(profile, offset + 8)
            y = _s15fixed16(profile, offset + 12)
            z = _s15fixed16(profile, offset + 16)
            self.assertAlmostEqual(x, 0.9642, places=4)
            self.assertAlmostEqual(y, 1.0, places=4)
            self.assertAlmostEqual(z, 0.8249, places=4)

    def test_srgb_primaries_in_expected_range(self) -> None:
        expected = (
            (0.4360, 0.2224, 0.0139),
            (0.3851, 0.7169, 0.0971),
            (0.1431, 0.0606, 0.7139),
        )
        for sig, (ex, ey, ez) in zip((b"rXYZ", b"gXYZ", b"bXYZ"), expected):
            offset, _size = _tags(self.srgb.data)[sig]
            x = _s15fixed16(self.srgb.data, offset + 8)
            y = _s15fixed16(self.srgb.data, offset + 12)
            z = _s15fixed16(self.srgb.data, offset + 16)
            self.assertAlmostEqual(x, ex, places=3)
            self.assertAlmostEqual(y, ey, places=3)
            self.assertAlmostEqual(z, ez, places=3)

    def test_srgb_curve_matches_transfer_function(self) -> None:
        values = _curve_values(self.srgb.data, b"rTRC")
        self.assertEqual(len(values), 256)
        self.assertEqual(values[0], 0)
        self.assertEqual(values[255], 255)
        for index, value in enumerate(values):
            c = index / 255.0
            v = c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4
            self.assertEqual(value, round(255 * v))

    def test_all_srgb_trc_tags_identical(self) -> None:
        curves = [_curve_values(self.srgb.data, sig) for sig in (b"rTRC", b"gTRC", b"bTRC")]
        self.assertEqual(curves[0], curves[1])
        self.assertEqual(curves[0], curves[2])

    def test_gray_curve_is_gamma_2_2(self) -> None:
        values = _curve_values(self.gray.data, b"kTRC")
        self.assertEqual(len(values), 256)
        self.assertEqual(values[0], 0)
        self.assertEqual(values[255], 255)
        for index, value in enumerate(values):
            self.assertEqual(value, round(255 * ((index / 255.0) ** (1.0 / 2.2))))

    def test_pdf_side_parameters(self) -> None:
        self.assertEqual(self.srgb.components, 3)
        self.assertEqual(self.srgb.alternate, "DeviceRGB")
        self.assertEqual(self.gray.components, 1)
        self.assertEqual(self.gray.alternate, "DeviceGray")


if __name__ == "__main__":
    unittest.main()
