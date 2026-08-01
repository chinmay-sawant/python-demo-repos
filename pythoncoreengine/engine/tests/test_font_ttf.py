"""Unit tests for engine/font.py TTF parsing: sfnt header, tables, cmap.

Parses the real Liberation Sans Regular font on this machine (a pure data
input, never modified) and verifies the metrics and mappings the subsetter
and the PDF metric pipeline rely on: unitsPerEm, glyph advances, the cmap
lookups for plain ASCII, Latin-1 and punctuation, and composite glyph
component walks.
"""

from __future__ import annotations

import unittest

from engine.font import LIBERATION_FONT_PATHS, TTFFont

SANS_REGULAR = LIBERATION_FONT_PATHS["LiberationSans-Regular"]


class TestTTFHeadAndTables(unittest.TestCase):
    def setUp(self) -> None:
        self.ttf = TTFFont.from_file(SANS_REGULAR)

    def test_units_per_em_is_2048(self) -> None:
        self.assertEqual(self.ttf.units_per_em, 2048)

    def test_num_glyphs_positive(self) -> None:
        self.assertGreater(self.ttf.num_glyphs, 100)

    def test_head_bounding_box_present(self) -> None:
        self.assertLess(self.ttf.x_min, 0)
        self.assertGreater(self.ttf.x_max, 1000)

    def test_hhea_vertical_metrics(self) -> None:
        self.assertGreater(self.ttf.ascent, 0)
        self.assertLess(self.ttf.descent, 0)
        self.assertLessEqual(self.ttf.number_of_hmetrics, self.ttf.num_glyphs)

    def test_advances_aligned_with_num_glyphs(self) -> None:
        self.assertEqual(len(self.ttf.advances), self.ttf.num_glyphs)
        self.assertEqual(len(self.ttf.left_side_bearings), self.ttf.num_glyphs)

    def test_loca_format_is_short_for_liberation_sans(self) -> None:
        self.assertEqual(self.ttf.index_to_loc_format, 0)

    def test_os2_cap_height_present(self) -> None:
        self.assertIsNotNone(self.ttf.cap_height)
        self.assertEqual(self.ttf.us_weight_class, 400)


class TestTTFCmap(unittest.TestCase):
    def setUp(self) -> None:
        self.ttf = TTFFont.from_file(SANS_REGULAR)

    def test_cmap_maps_uppercase_a(self) -> None:
        self.assertEqual(self.ttf.glyph_id("A"), 36)
        self.assertEqual(self.ttf.glyph_advance(36), 1366)

    def test_cmap_maps_latin_1_e_acute(self) -> None:
        self.assertIsNotNone(self.ttf.glyph_id("\u00e9"))
        gid = self.ttf.glyph_id("\u00e9")
        self.assertEqual(self.ttf.glyph_advance(gid), 1139)

    def test_cmap_maps_en_dash_punctuation(self) -> None:
        self.assertIsNotNone(self.ttf.glyph_id("\u2013"))
        gid = self.ttf.glyph_id("\u2013")
        self.assertEqual(self.ttf.glyph_advance(gid), 1139)

    def test_cmap_maps_hyphen(self) -> None:
        gid = self.ttf.glyph_id("-")
        self.assertIsNotNone(gid)
        self.assertEqual(self.ttf.glyph_advance(gid), 682)

    def test_cmap_missing_char_returns_none(self) -> None:
        self.assertIsNone(self.ttf.glyph_id("\U0001F600"))  # emoji, not in font

    def test_scaled_hyphen_width_around_333(self) -> None:
        gid = self.ttf.glyph_id("-")
        scaled = self.ttf.glyph_advance(gid) * 1000 / self.ttf.units_per_em
        self.assertAlmostEqual(scaled, 333.0, delta=1.0)


class TestTTFComposite(unittest.TestCase):
    def setUp(self) -> None:
        self.ttf = TTFFont.from_file(SANS_REGULAR)

    def test_capital_a_with_acute_is_composite(self) -> None:
        gid = self.ttf.glyph_id("\u00c1")
        self.assertIsNotNone(gid)
        self.assertTrue(self.ttf.is_composite(gid))
        components = self.ttf.composite_components(gid)
        self.assertEqual(components, [36, 674])  # A plus acute accent

    def test_plain_a_is_not_composite(self) -> None:
        self.assertFalse(self.ttf.is_composite(36))

    def test_all_components_in_bounds(self) -> None:
        gid = self.ttf.glyph_id("\u00c1")
        for component in self.ttf.composite_components(gid):
            self.assertLess(component, self.ttf.num_glyphs)


class TestTTFErrors(unittest.TestCase):
    def test_from_bytes_round_trip(self) -> None:
        data = SANS_REGULAR.read_bytes()
        self.assertEqual(TTFFont(data).num_glyphs, TTFFont.from_file(SANS_REGULAR).num_glyphs)

    def test_cff_flavoured_font_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TTFFont(b"OTTO" + b"\x00" * 64)

    def test_bad_sfnt_version_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TTFFont(b"BOGUS" + b"\x00" * 64)

    def test_truncated_font_rejected(self) -> None:
        with self.assertRaises(ValueError):
            TTFFont(b"\x00\x01\x00\x00\x00\x05" + b"\x00" * 10)

    def test_missing_table_rejected(self) -> None:
        fake = b"\x00\x01\x00\x00\x00\x01\x00\x00\x00\x00\x00\x00"
        with self.assertRaises(ValueError):
            TTFFont(fake + b"\x00" * 64)


if __name__ == "__main__":
    unittest.main()
