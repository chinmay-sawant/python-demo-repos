"""Unit tests for minimal document generation (engine.doc / engine.__init__).

Parses the produced bytes with the hand-rolled helpers in helpers.py:
xref offsets must resolve to ``N 0 obj`` headers, the trailer /Root must
point at the catalog, and /ID must carry two equal 16-byte entries.
"""

from __future__ import annotations

import datetime
import tempfile
import unittest
from pathlib import Path

import engine
from engine import ModeEmbedFonts, ModePDF20, ModePDFA4, ModePDFUA2, generate_minimal_pdf
from engine.doc import Document
from engine.fixtures import generate_fixtures
from engine.tests.helpers import (
    find_object_with,
    object_bytes,
    parse_obj_header,
    parse_xref,
    startxref_offset,
    trailer_dict_bytes,
    trailer_dict_values,
)

FIXED_CREATED = datetime.datetime(2026, 8, 1, 12, 0, 0)


class TestHeader(unittest.TestCase):
    def test_header_starts_pdf_20(self) -> None:
        self.assertTrue(generate_minimal_pdf().startswith(b"%PDF-2.0\n"))

    def test_binary_comment_line_present(self) -> None:
        second_line = generate_minimal_pdf().split(b"\n")[1]
        self.assertTrue(second_line.startswith(b"%"))
        self.assertTrue(any(byte >= 128 for byte in second_line))


class TestDocumentStructure(unittest.TestCase):
    def setUp(self) -> None:
        self.data = generate_minimal_pdf(created=FIXED_CREATED)
        self.offsets = parse_xref(self.data)
        self.trailer = trailer_dict_values(trailer_dict_bytes(self.data))

    def test_xref_offsets_resolve_to_object_headers(self) -> None:
        for obj_id, offset in self.offsets.items():
            number, _gen = parse_obj_header(self.data, offset)
            self.assertEqual(number, obj_id)
            header = b"%d 0 obj\n" % obj_id
            self.assertEqual(self.data[offset:offset + len(header)], header)

    def test_object_ids_are_contiguous(self) -> None:
        ids = sorted(self.offsets)
        self.assertEqual(ids, list(range(1, len(ids) + 1)))

    def test_size_matches_object_count(self) -> None:
        self.assertEqual(self.trailer["size"], max(self.offsets) + 1)

    def test_root_points_at_catalog(self) -> None:
        catalog_id = find_object_with(self.data, b"/Type /Catalog", self.offsets)
        self.assertEqual(self.trailer["root"], catalog_id)

    def test_id_has_two_equal_hex_entries(self) -> None:
        self.assertEqual(self.trailer["id_first"], self.trailer["id_second"])
        self.assertEqual(len(self.trailer["id_first"]), 32)
        self.assertEqual(len(self.trailer["id_second"]), 32)

    def test_startxref_points_at_xref(self) -> None:
        self.assertEqual(startxref_offset(self.data), self.data.index(b"xref\n"))

    def test_ends_with_eof(self) -> None:
        self.assertTrue(self.data.rstrip().endswith(b"%%EOF"))


class TestShellObjects(unittest.TestCase):
    def setUp(self) -> None:
        self.data = generate_minimal_pdf(created=FIXED_CREATED)
        self.offsets = parse_xref(self.data)

    def test_catalog_and_pages_present(self) -> None:
        for marker in (b"/Type /Catalog", b"/Type /Pages /", b"/Type /Page /"):
            find_object_with(self.data, marker, self.offsets)

    def test_pages_tree_single_kid_and_count(self) -> None:
        pages_id = find_object_with(self.data, b"/Type /Pages /", self.offsets)
        pages = object_bytes(self.data, self.offsets[pages_id])
        self.assertIn(b"/Count 1", pages)
        self.assertIn(b"/Kids [", pages)

    def test_page_media_box_and_resources(self) -> None:
        page_id = find_object_with(self.data, b"/Type /Page /", self.offsets)
        page = object_bytes(self.data, self.offsets[page_id])
        self.assertIn(b"/Parent", page)
        self.assertIn(b"/MediaBox [0 0 595.276 841.89]", page)
        self.assertIn(b"/Contents", page)
        self.assertIn(b"/Resources", page)
        self.assertIn(b"/Font", page)

    def test_font_dict_is_type1_helvetica(self) -> None:
        font_id = find_object_with(self.data, b"/BaseFont /Helvetica", self.offsets)
        font = object_bytes(self.data, self.offsets[font_id])
        self.assertIn(b"/Type /Font", font)
        self.assertIn(b"/Subtype /Type1", font)

    def test_content_stream_draws_text(self) -> None:
        self.assertIn(b"BT", self.data)
        self.assertIn(b"ET", self.data)
        self.assertIn(b"(Hello) Tj", self.data)

    def test_info_dict_has_dates(self) -> None:
        info_id = find_object_with(self.data, b"/CreationDate", self.offsets)
        info = object_bytes(self.data, self.offsets[info_id])
        self.assertIn(b"(D:20260801120000)", info)
        self.assertIn(b"/ModDate", info)


class TestPublicApi(unittest.TestCase):
    def test_custom_text(self) -> None:
        data = generate_minimal_pdf("Hi there", created=FIXED_CREATED)
        self.assertIn(b"(Hi there) Tj", data)

    def test_custom_page_size(self) -> None:
        data = generate_minimal_pdf(created=FIXED_CREATED, page_size=(200, 100))
        self.assertIn(b"/MediaBox [0 0 200 100]", data)

    def test_deterministic_output_with_fixed_created(self) -> None:
        first = generate_minimal_pdf(created=FIXED_CREATED)
        second = generate_minimal_pdf(created=FIXED_CREATED)
        self.assertEqual(first, second)

    def test_output_is_reproducible_pdf(self) -> None:
        data = generate_minimal_pdf(created=FIXED_CREATED)
        self.assertEqual(data.count(b"endobj"), data.count(b" 0 obj"))
        self.assertEqual(data.count(b"n \n"), 6)


class TestModeFlags(unittest.TestCase):
    def test_module_mode_flags(self) -> None:
        self.assertTrue(ModePDF20)
        self.assertFalse(ModePDFA4)
        self.assertFalse(ModePDFUA2)
        self.assertFalse(ModeEmbedFonts)

    def test_document_mode_flags(self) -> None:
        doc = Document()
        self.assertTrue(doc.mode_pdf20)
        self.assertFalse(doc.mode_pdfa4)
        self.assertFalse(doc.mode_pdfua2)
        self.assertFalse(doc.mode_embed_fonts)

    def test_document_mode_constructor_hooks(self) -> None:
        doc = Document(mode_pdfa4=True, mode_pdfua2=True, mode_embed_fonts=True)
        self.assertTrue(doc.mode_pdfa4)
        self.assertTrue(doc.mode_pdfua2)
        self.assertTrue(doc.mode_embed_fonts)

    def test_compliance_modes_change_output_since_phase4(self) -> None:
        plain = generate_minimal_pdf(created=FIXED_CREATED)
        from engine.doc import build_minimal_document

        compliant = build_minimal_document(
            created=FIXED_CREATED, mode_pdfa4=True, mode_pdfua2=True
        )
        self.assertNotEqual(plain, compliant)
        self.assertIn(b"/Type /Metadata /Subtype /XML", compliant)
        self.assertIn(b"/OutputIntents [", compliant)
        trailer = trailer_dict_bytes(compliant)
        self.assertNotIn(b"/Info", trailer)
        self.assertIn(b"/Root", trailer)


class TestFixtures(unittest.TestCase):
    def test_fixture_generation_produces_parseable_pdf(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            paths = generate_fixtures(Path(tmp))
            fixture = paths["phase1_minimal.pdf"]
            data = fixture.read_bytes()
            self.assertTrue(data.startswith(b"%PDF-2.0\n"))
            self.assertEqual(parse_xref(data)[1], data.index(b"1 0 obj"))


if __name__ == "__main__":
    unittest.main()
