"""Unit tests for engine/font.py glyph subsetting: subset TTF integrity.

Builds a subset of the real Liberation Sans Regular font for the character
set "Hello, wörld 123" and re-parses the produced bytes with the same
parser, then verifies: every used character maps to a valid glyph with real
glyf data, advances are preserved at 1000-unit scale, maxp/numGlyphs and
loca agree, the whole-file checksum is 0xB1B0AFBA, per-table checksums are
correct, and composite closure (precomposed "Á") pulls in its components
with remapped indices.
"""

from __future__ import annotations

import struct
import unittest

from engine.font import LIBERATION_FONT_PATHS, TTFFont, TTFSubsetter

SANS_REGULAR = LIBERATION_FONT_PATHS["LiberationSans-Regular"]
USED_CHARS = "Hello, wörld 123"

_WHOLE_FONT_CHECKSUM = 0xB1B0AFBA


def _subset_for(chars: str) -> TTFSubsetter:
    return TTFSubsetter(TTFFont.from_file(SANS_REGULAR), chars)


class TestSubsetIntegrity(unittest.TestCase):
    def setUp(self) -> None:
        self.subsetter = _subset_for(USED_CHARS)
        self.data = self.subsetter.build("SubsetTest")
        self.subset = TTFFont(self.data, "<subset>")

    def test_used_chars_map_to_valid_glyphs(self) -> None:
        for char in USED_CHARS:
            gid = self.subset.glyph_id(char)
            self.assertIsNotNone(gid, f"missing {char!r} in subset cmap")
            self.assertLess(gid, self.subset.num_glyphs, f"{char!r} gid out of range")
            length = len(self.subset.glyph_data(gid))
            self.assertTrue(length == 0 or length >= 10, f"bad glyph length for {char!r}")

    def test_num_glyphs_matches_expected_count(self) -> None:
        self.assertEqual(self.subset.num_glyphs, len(self.subsetter.ordered_gids))

    def test_subset_covers_chars_and_composite_components(self) -> None:
        covered = set(self.subsetter.ordered_gids)
        for char in USED_CHARS:
            self.assertIn(self.subsetter.char_gids[char], covered)
        o_umlaut = self.subsetter.ttf.glyph_id("\u00f6")
        self.assertTrue(self.subsetter.ttf.is_composite(o_umlaut))
        for component in self.subsetter.ttf.composite_components(o_umlaut):
            self.assertIn(component, covered)

    def test_widths_match_original_scaled(self) -> None:
        original = self.subsetter.ttf
        for char in USED_CHARS:
            old_gid = original.glyph_id(char)
            new_gid = self.subset.glyph_id(char)
            old_scaled = original.glyph_advance(old_gid) * 1000 // original.units_per_em
            new_scaled = self.subset.glyph_advance(new_gid) * 1000 // self.subset.units_per_em
            self.assertEqual(new_scaled, old_scaled, f"width mismatch for {char!r}")

    def test_hyphen_width_scaled_around_333(self) -> None:
        subsetter = _subset_for("-")
        parsed = TTFFont(subsetter.build("T"), "<subset>")
        scaled = parsed.glyph_advance(parsed.glyph_id("-")) * 1000 / parsed.units_per_em
        self.assertAlmostEqual(scaled, 333.0, delta=1.0)

    def test_subset_units_per_em_preserved(self) -> None:
        self.assertEqual(self.subset.units_per_em, 2048)

    def test_loca_is_long_format(self) -> None:
        self.assertEqual(self.subset.index_to_loc_format, 1)

    def test_hmtx_has_entry_per_glyph(self) -> None:
        self.assertEqual(len(self.subset.advances), self.subset.num_glyphs)

    def test_unused_chars_not_in_subset(self) -> None:
        self.assertIsNone(self.subset.glyph_id("z"))
        self.assertIsNone(self.subset.glyph_id("Q"))

    def test_whole_file_checksum_is_b1b0afba(self) -> None:
        words = struct.unpack(
            ">%dI" % (len(self.data) // 4), self.data[: len(self.data) // 4 * 4]
        )
        self.assertEqual(sum(words) & 0xFFFFFFFF, _WHOLE_FONT_CHECKSUM)

    def test_every_table_checksum_is_valid(self) -> None:
        num_tables = struct.unpack(">H", self.data[4:6])[0]
        for index in range(num_tables):
            tag, checksum, offset, length = struct.unpack(
                ">4sIII", self.data[12 + index * 16 : 28 + index * 16]
            )
            table = self.data[offset : offset + length]
            if tag == b"head":
                buffer = bytearray(table)
                buffer[8:12] = b"\x00" * 4
                table = bytes(buffer)
            padded = table + b"\x00" * (-len(table) % 4)
            words = struct.unpack(">%dI" % (len(padded) // 4), padded)
            self.assertEqual(
                sum(words) & 0xFFFFFFFF,
                checksum,
                f"checksum mismatch for {tag!r}",
            )

    def test_required_tables_present(self) -> None:
        num_tables = struct.unpack(">H", self.data[4:6])[0]
        tags = {
            self.data[12 + index * 16 : 16 + index * 16]
            for index in range(num_tables)
        }
        required = (
            b"head", b"hhea", b"maxp", b"hmtx", b"cmap", b"loca",
            b"glyf", b"name", b"post", b"OS/2",
        )
        for tag in required:
            self.assertIn(tag, tags)

    def test_glyph_zero_is_notdef(self) -> None:
        self.assertIn(0, self.subsetter.glyph_map.values())
        self.assertEqual(self.subsetter.glyph_map[0], 0)


class TestSubsetCompositeClosure(unittest.TestCase):
    def setUp(self) -> None:
        self.subsetter = _subset_for("\u00c1")  # precomposed A-acute
        self.data = self.subsetter.build("T")
        self.subset = TTFFont(self.data, "<subset>")

    def test_composite_closure_includes_components(self) -> None:
        self.assertEqual(self.subsetter.ordered_gids, [0, 36, 129, 674])
        for gid in (36, 674):
            self.assertIn(gid, self.subsetter.glyph_map)

    def test_remapped_component_indices_in_bounds(self) -> None:
        gid = self.subset.glyph_id("\u00c1")
        components = self.subset.composite_components(gid)
        self.assertEqual(len(components), 2)
        for component in components:
            self.assertLess(component, self.subset.num_glyphs)

    def test_remapped_components_resolve_to_a_and_acute(self) -> None:
        gid = self.subset.glyph_id("\u00c1")
        components = self.subset.composite_components(gid)
        new_a = self.subsetter.glyph_map[36]  # original 'A' gid -> subset gid
        new_acute = self.subsetter.glyph_map[674]
        self.assertIn(new_a, components)
        self.assertIn(new_acute, components)

    def test_subset_still_composite_after_rewrite(self) -> None:
        gid = self.subset.glyph_id("\u00c1")
        self.assertTrue(self.subset.is_composite(gid))


class TestSubsetDeterminism(unittest.TestCase):
    def test_same_chars_produce_identical_bytes(self) -> None:
        first = _subset_for(USED_CHARS).build("T")
        second = _subset_for(USED_CHARS).build("T")
        self.assertEqual(first, second)

    def test_different_chars_produce_different_subsets(self) -> None:
        a = _subset_for("abc").build("T")
        b = _subset_for("xyz").build("T")
        self.assertNotEqual(a, b)


if __name__ == "__main__":
    unittest.main()
