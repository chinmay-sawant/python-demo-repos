"""Unit tests for engine/font.py FontRegistry: Liberation map, isolation, modes.

Covers the per-document registry (two documents never share subsets), the
standard-face -> Liberation substitution table, the non-embed fallback that
keeps the phase-1 Type1 placeholder, custom TTF registration from file and
bytes, the ``font_face`` selection API and usage tracking collected after
registration (content-first, subsets-at-render).
"""

from __future__ import annotations

import datetime
import unittest

from engine import DocumentBuilder
from engine.font import (
    FontRegistry,
    LIBERATION_FONT_PATHS,
    STANDARD_TO_LIBERATION,
    TTFFont,
    standard_font_name,
)
from engine.tests.helpers import (
    find_object_with,
    inflate_stream,
    object_bytes,
    parse_xref,
)

FIXED_CREATED = datetime.datetime(2026, 8, 1, 12, 0, 0)


class TestLiberationMap(unittest.TestCase):
    def test_standard_to_liberation_table(self) -> None:
        self.assertEqual(STANDARD_TO_LIBERATION["Helvetica"], "LiberationSans-Regular")
        self.assertEqual(STANDARD_TO_LIBERATION["Helvetica-Bold"], "LiberationSans-Bold")
        self.assertEqual(STANDARD_TO_LIBERATION["Helvetica-Oblique"], "LiberationSans-Italic")
        self.assertEqual(
            STANDARD_TO_LIBERATION["Helvetica-BoldOblique"], "LiberationSans-BoldItalic"
        )
        self.assertEqual(STANDARD_TO_LIBERATION["Times-Roman"], "LiberationSerif-Regular")
        self.assertEqual(STANDARD_TO_LIBERATION["Times-Bold"], "LiberationSerif-Bold")
        self.assertEqual(STANDARD_TO_LIBERATION["Times-Italic"], "LiberationSerif-Italic")
        self.assertEqual(
            STANDARD_TO_LIBERATION["Times-BoldItalic"], "LiberationSerif-BoldItalic"
        )
        self.assertEqual(STANDARD_TO_LIBERATION["Courier"], "LiberationMono-Regular")
        self.assertEqual(STANDARD_TO_LIBERATION["Courier-Bold"], "LiberationMono-Bold")
        self.assertEqual(STANDARD_TO_LIBERATION["Courier-Oblique"], "LiberationMono-Italic")
        self.assertEqual(
            STANDARD_TO_LIBERATION["Courier-BoldOblique"], "LiberationMono-BoldItalic"
        )

    def test_all_liberation_paths_exist_on_this_machine(self) -> None:
        for face, path in LIBERATION_FONT_PATHS.items():
            self.assertTrue(path.is_file(), f"missing {face} at {path}")

    def test_registry_entry_resolves_helvetica_to_liberation_sans(self) -> None:
        registry = FontRegistry(embed=True)
        entry = registry.entry("F1", "Helvetica")
        self.assertEqual(entry.face_name, "LiberationSans")
        self.assertEqual(entry.base_font, "Helvetica")

    def test_unknown_standard_font_rejected(self) -> None:
        registry = FontRegistry(embed=True)
        with self.assertRaises(ValueError):
            registry.entry("F1", "Symbol")

    def test_registry_entry_returns_same_instance(self) -> None:
        registry = FontRegistry(embed=True)
        self.assertIs(registry.entry("F1", "Helvetica"), registry.entry("F1", "Helvetica"))


class TestStandardFontName(unittest.TestCase):
    def test_helvetica_combinations(self) -> None:
        self.assertEqual(standard_font_name("Helvetica"), "Helvetica")
        self.assertEqual(standard_font_name("Helvetica", bold=True), "Helvetica-Bold")
        self.assertEqual(standard_font_name("Helvetica", italic=True), "Helvetica-Oblique")
        self.assertEqual(
            standard_font_name("Helvetica", bold=True, italic=True), "Helvetica-BoldOblique"
        )

    def test_times_uses_italic_naming(self) -> None:
        self.assertEqual(standard_font_name("Times", italic=True), "Times-Italic")
        self.assertEqual(standard_font_name("Times-Roman", bold=True), "Times-Bold")
        self.assertEqual(
            standard_font_name("Times", bold=True, italic=True), "Times-BoldItalic"
        )

    def test_courier_combinations(self) -> None:
        self.assertEqual(standard_font_name("Courier"), "Courier")
        self.assertEqual(
            standard_font_name("Courier", bold=True, italic=True), "Courier-BoldOblique"
        )

    def test_full_standard_names_pass_through(self) -> None:
        self.assertEqual(standard_font_name("Times-Italic", bold=True), "Times-Italic")

    def test_unknown_family_rejected(self) -> None:
        with self.assertRaises(ValueError):
            standard_font_name("Comic Sans")


class TestRegistryIsolation(unittest.TestCase):
    def test_two_documents_have_disjoint_usage(self) -> None:
        first = FontRegistry(embed=True)
        second = FontRegistry(embed=True)
        first.entry("F1", "Helvetica").add_chars("abc")
        second.entry("F1", "Helvetica").add_chars("xyz")
        first.generate_subsets()
        second.generate_subsets()
        self.assertNotEqual(
            first.subset_ttf_bytes("F1"), second.subset_ttf_bytes("F1")
        )

    def test_usage_collected_after_registration_counts(self) -> None:
        registry = FontRegistry(embed=True)
        entry = registry.entry("F1", "Helvetica")
        registry.record_chars("F1", "Hello")
        registry.record_chars("F1", "world")
        entry.add_chars("!")  # direct add is equivalent
        registry.generate_subsets()
        subset = TTFFont(registry.subset_ttf_bytes("F1"), "<subset>")
        for char in "Helloworld!":
            self.assertIsNotNone(subset.glyph_id(char), f"{char!r} not subsetted")

    def test_two_builder_documents_produce_different_subsets(self) -> None:
        def render(text: str) -> bytes:
            builder = DocumentBuilder(mode_embed_fonts=True, created=FIXED_CREATED)
            builder.flow().text(text, size=12)
            return builder.render()

        first = render("abc")
        second = render("xyz")
        self.assertNotEqual(first, second)


class TestCustomRegistration(unittest.TestCase):
    def test_register_ttf_from_file(self) -> None:
        registry = FontRegistry(embed=True)
        entry = registry.register_ttf(
            "F3", LIBERATION_FONT_PATHS["LiberationSans-Bold"], base_font="CustomBold"
        )
        self.assertEqual(entry.face_name, "LiberationSans-Bold")
        entry.add_chars("Bold")
        registry.generate_subsets()
        subset = TTFFont(registry.subset_ttf_bytes("F3"), "<subset>")
        self.assertIsNotNone(subset.glyph_id("B"))

    def test_register_ttf_from_bytes(self) -> None:
        registry = FontRegistry(embed=True)
        data = LIBERATION_FONT_PATHS["LiberationMono-Regular"].read_bytes()
        registry.register_ttf_bytes("F4", data, base_font="MonoFromBytes")
        registry.record_chars("F4", "mono")
        registry.generate_subsets()
        subset = TTFFont(registry.subset_ttf_bytes("F4"), "<subset>")
        self.assertIsNotNone(subset.glyph_id("m"))

    def test_face_metrics_flags_by_family(self) -> None:
        registry = FontRegistry(embed=True)
        mono = registry.entry("F1", "Courier")
        mono.add_chars("x")
        registry.generate_subsets()
        self.assertTrue(mono.metrics["flags"] & 1)  # fixed pitch
        self.assertFalse(mono.metrics["flags"] & 2)  # not serif
        self.assertEqual(mono.metrics["italic_angle"], 0)

    def test_italic_face_has_italic_flag(self) -> None:
        registry = FontRegistry(embed=True)
        entry = registry.entry("F1", "Helvetica-Oblique")
        entry.add_chars("x")
        registry.generate_subsets()
        self.assertTrue(entry.metrics["flags"] & 64)


class TestEmbedModeSwitch(unittest.TestCase):
    def test_non_embed_keeps_type1_placeholder(self) -> None:
        builder = DocumentBuilder(created=FIXED_CREATED)
        flow = builder.flow()
        flow.text("Hello", size=12)
        data = builder.render()
        offsets = parse_xref(data)
        font_id = find_object_with(data, b"/BaseFont /Helvetica", offsets)
        font = object_bytes(data, offsets[font_id])
        self.assertIn(b"/Subtype /Type1", font)
        self.assertNotIn(b"+LiberationSans", data)
        self.assertNotIn(b"/Identity-H", data)

    def test_embed_mode_uses_cid_chain(self) -> None:
        builder = DocumentBuilder(mode_embed_fonts=True, created=FIXED_CREATED)
        flow = builder.flow()
        flow.text("Hello", size=12)
        data = builder.render()
        offsets = parse_xref(data)
        find_object_with(data, b"/Subtype /Type0", offsets)
        self.assertIn(b"+LiberationSans", data)

    def test_embed_mode_flag_defaults_off(self) -> None:
        self.assertFalse(DocumentBuilder()._doc.mode_embed_fonts)


class TestFontFaceSelection(unittest.TestCase):
    def test_builder_font_face_allocation(self) -> None:
        builder = DocumentBuilder(mode_embed_fonts=True, created=FIXED_CREATED)
        self.assertEqual(builder.font_face("Helvetica"), "F1")
        self.assertEqual(builder.font_face("Helvetica", bold=True), "F2")
        self.assertEqual(builder.font_face("Times-Roman"), "F3")
        self.assertEqual(builder.font_face("Times", italic=True), "F4")
        self.assertEqual(builder.font_face("Helvetica"), "F1")  # stable

    def test_font_face_registers_resource(self) -> None:
        builder = DocumentBuilder(mode_embed_fonts=True, created=FIXED_CREATED)
        name = builder.font_face("Times-Roman")
        flow = builder.flow()
        flow.text("Times", size=12, font=name)
        data = builder.render()
        offsets = parse_xref(data)
        find_object_with(data, b"/Subtype /CIDFontType2", offsets)
        self.assertIn(b"+LiberationSerif", data)

    def test_subset_name_prefix_format(self) -> None:
        builder = DocumentBuilder(mode_embed_fonts=True, created=FIXED_CREATED)
        flow = builder.flow()
        flow.text("A", size=12)
        data = builder.render()
        self.assertRegex(data, rb"/BaseFont /[A-P]{6}\+LiberationSans\b")

    def test_font_file2_stream_is_compressed_ttf(self) -> None:
        builder = DocumentBuilder(mode_embed_fonts=True, created=FIXED_CREATED)
        flow = builder.flow()
        flow.text("stream", size=12)
        data = builder.render()
        offsets = parse_xref(data)
        descriptor_id = find_object_with(data, b"/FontFile2", offsets)
        descriptor = object_bytes(data, offsets[descriptor_id])
        match = __import__("re").search(rb"/FontFile2 (\d+) 0 R", descriptor)
        self.assertIsNotNone(match)
        raw = inflate_stream(data, offsets[int(match.group(1))])
        self.assertTrue(raw.startswith(b"\x00\x01\x00\x00"))


if __name__ == "__main__":
    unittest.main()
