"""Unit tests for PDF/A-4 mode wiring (engine.doc.DocumentBuilder, engine.pdfa).

Builds documents with ``mode_pdfa4=True`` and verifies the object set the
A-4 rules require, parsed from the emitted bytes with the helpers in
helpers.py: the trailer omits /Info, the catalog references the metadata
stream and the OutputIntents array, every page's resources carry
DefaultRGB/DefaultGray ICCBased colour spaces, all fonts are embedded
(no bare Type1 /BaseFont /Helvetica), and image colour spaces are
rewritten to [/ICCBased ...] arrays while filters stay untouched.
"""

from __future__ import annotations

import datetime
import re
import unittest

from engine import DocumentBuilder
from engine.color import ICCProfile
from engine.fixtures import _gradient_png
from engine.tests.helpers import (
    find_object_with,
    inflate_stream,
    is_stream_object,
    object_bytes,
    parse_xref,
    stream_bytes,
    trailer_dict_bytes,
)

FIXED_CREATED = datetime.datetime(2026, 8, 1, 12, 0, 0)

_DEFAULT_RGB_RE = re.compile(
    rb"/ColorSpace << /DefaultRGB \[/ICCBased (\d+) 0 R\] "
    rb"/DefaultGray \[/ICCBased (\d+) 0 R\] >>"
)
_ICCBASED_IMAGE_RE = re.compile(rb"/ColorSpace \[/ICCBased (\d+) 0 R\]")


def _pdfa4_document() -> bytes:
    builder = DocumentBuilder(created=FIXED_CREATED, mode_pdfa4=True)
    flow = builder.flow()
    flow.text("PDF/A-4 test", size=16, color=(0.1, 0.1, 0.1))
    flow.paragraph(
        "Embedded Liberation Sans text with caf\u00e9 r\u00e9sum\u00e9 "
        "and 12345 for subsetting coverage.",
        size=11,
        color=(0.15, 0.15, 0.15),
    )
    return builder.render()


def _pdfa4_image_document() -> bytes:
    builder = DocumentBuilder(created=FIXED_CREATED, mode_pdfa4=True)
    flow = builder.flow()
    flow.text("Image page", size=16)
    flow.image(_gradient_png(), x=0, y=30, width=128, height=128)
    return builder.render()


class TestTrailer(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _pdfa4_document()
        self.trailer = trailer_dict_bytes(self.data)

    def test_no_info_in_trailer(self) -> None:
        self.assertNotIn(b"/Info", self.trailer)

    def test_root_size_and_id_kept(self) -> None:
        self.assertIn(b"/Root", self.trailer)
        self.assertIn(b"/Size", self.trailer)
        self.assertIn(b"/ID [<", self.trailer)

    def test_no_info_object_emitted(self) -> None:
        self.assertNotIn(b"/CreationDate", self.data)
        self.assertNotIn(b"/Producer", self.data)


class TestCatalog(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _pdfa4_document()
        self.offsets = parse_xref(self.data)
        catalog_id = find_object_with(self.data, b"/Type /Catalog", self.offsets)
        self.catalog = object_bytes(self.data, self.offsets[catalog_id])

    def test_catalog_has_metadata_and_output_intents(self) -> None:
        self.assertRegex(self.catalog, rb"/Metadata (\d+) 0 R")
        self.assertRegex(self.catalog, rb"/OutputIntents \[\s*(\d+) 0 R\s*\]")


class TestMetadataStream(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _pdfa4_document()
        self.offsets = parse_xref(self.data)
        metadata_id = find_object_with(self.data, b"/Type /Metadata", self.offsets)
        self.metadata = object_bytes(self.data, self.offsets[metadata_id])

    def test_metadata_object_is_xml_stream(self) -> None:
        self.assertIn(b"/Subtype /XML", self.metadata)
        self.assertIn(b"/Length", self.metadata)
        self.assertTrue(is_stream_object(self.data, self.offsets, self._stream_id()))

    def _stream_id(self) -> int:
        catalog_id = find_object_with(self.data, b"/Type /Catalog", self.offsets)
        catalog = object_bytes(self.data, self.offsets[catalog_id])
        match = re.search(rb"/Metadata (\d+) 0 R", catalog)
        return int(match.group(1))

    def test_stream_body_is_xmp_packet(self) -> None:
        raw = stream_bytes(self.data, self.offsets[self._stream_id()])
        self.assertTrue(raw.startswith(b'<?xpacket begin="\xef\xbb\xbf"'))
        self.assertIn(b"<pdfaid:part>4</pdfaid:part>", raw)
        self.assertIn(b"<pdfaid:rev>2020</pdfaid:rev>", raw)
        self.assertTrue(raw.rstrip().endswith(b'<?xpacket end="w"?>'))


class TestOutputIntentAndICC(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _pdfa4_document()
        self.offsets = parse_xref(self.data)
        intent_id = find_object_with(self.data, b"/Type /OutputIntent", self.offsets)
        self.intent = object_bytes(self.data, self.offsets[intent_id])

    def test_output_intent_fields(self) -> None:
        self.assertIn(b"/S /GTS_PDFA1", self.intent)
        self.assertIn(b"/OutputConditionIdentifier (sRGB IEC61966-2.1)", self.intent)
        self.assertIn(b"/RegistryName (http://www.color.org)", self.intent)
        self.assertIn(b"/Info (sRGB IEC61966-2.1)", self.intent)
        self.assertRegex(self.intent, rb"/DestOutputProfile (\d+) 0 R")

    def test_dest_profile_is_valid_icc_stream(self) -> None:
        match = re.search(rb"/DestOutputProfile (\d+) 0 R", self.intent)
        profile_id = int(match.group(1))
        self.assertTrue(is_stream_object(self.data, self.offsets, profile_id))
        profile = inflate_stream(self.data, self.offsets[profile_id])
        self.assertEqual(profile[36:40], b"acsp")
        self.assertEqual(profile[16:20], b"RGB ")
        self.assertEqual(len(profile), len(ICCProfile.srgb().data))

    def test_icc_srgb_stream_dict(self) -> None:
        srgb_id = find_object_with(self.data, b"/Alternate /DeviceRGB", self.offsets)
        srgb = object_bytes(self.data, self.offsets[srgb_id])
        self.assertIn(b"/N 3", srgb)
        self.assertIn(b"/Filter /FlateDecode", srgb)
        profile = inflate_stream(self.data, self.offsets[srgb_id])
        self.assertEqual(profile[36:40], b"acsp")

    def test_icc_gray_stream_dict(self) -> None:
        gray_id = find_object_with(self.data, b"/Alternate /DeviceGray", self.offsets)
        gray = object_bytes(self.data, self.offsets[gray_id])
        self.assertIn(b"/N 1", gray)
        self.assertIn(b"/Filter /FlateDecode", gray)
        profile = inflate_stream(self.data, self.offsets[gray_id])
        self.assertEqual(profile[36:40], b"acsp")
        self.assertEqual(profile[16:20], b"GRAY")


class TestPageResources(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _pdfa4_document()
        self.offsets = parse_xref(self.data)
        page_id = find_object_with(self.data, b"/Type /Page /", self.offsets)
        self.page = object_bytes(self.data, self.offsets[page_id])

    def test_default_rgb_and_gray_iccbased(self) -> None:
        match = _DEFAULT_RGB_RE.search(self.page)
        self.assertIsNotNone(match, self.page[:400])
        srgb_ref, gray_ref = int(match.group(1)), int(match.group(2))
        self.assertNotEqual(srgb_ref, gray_ref)
        self.assertTrue(is_stream_object(self.data, self.offsets, srgb_ref))
        self.assertTrue(is_stream_object(self.data, self.offsets, gray_ref))

    def test_every_page_has_default_colour_spaces(self) -> None:
        for page_id, offset in self.offsets.items():
            raw = object_bytes(self.data, offset)
            if b"/Type /Page /" not in raw:
                continue
            self.assertIsNotNone(_DEFAULT_RGB_RE.search(raw))


class TestFontsEmbedded(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _pdfa4_document()

    def test_no_bare_standard_fonts(self) -> None:
        self.assertNotIn(b"/Subtype /Type1", self.data)
        self.assertNotIn(b"/BaseFont /Helvetica", self.data)

    def test_cid_chain_present(self) -> None:
        self.assertIn(b"/Subtype /Type0", self.data)
        self.assertIn(b"/Subtype /CIDFontType2", self.data)
        self.assertIn(b"/FontFile2", self.data)
        self.assertIn(b"/CIDSet", self.data)

    def test_fonts_forced_embedded_without_embed_flag(self) -> None:
        builder = DocumentBuilder(created=FIXED_CREATED, mode_pdfa4=True)
        flow = builder.flow()
        flow.text("forced embed", size=12)
        data = builder.render()
        self.assertNotIn(b"/Subtype /Type1", data)
        self.assertIn(b"/Subtype /Type0", data)


class TestImagesICCBased(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _pdfa4_image_document()
        self.offsets = parse_xref(self.data)
        image_id = find_object_with(self.data, b"/Subtype /Image", self.offsets)
        self.image = object_bytes(self.data, self.offsets[image_id])

    def test_image_colorspace_is_iccbased_ref(self) -> None:
        self.assertNotIn(b"/ColorSpace /DeviceRGB", self.image)
        match = _ICCBASED_IMAGE_RE.search(self.image)
        self.assertIsNotNone(match, self.image)
        ref = int(match.group(1))
        self.assertTrue(is_stream_object(self.data, self.offsets, ref))
        profile = inflate_stream(self.data, self.offsets[ref])
        self.assertEqual(profile[16:20], b"RGB ")

    def test_image_filters_unchanged(self) -> None:
        self.assertIn(b"/Filter /FlateDecode", self.image)


class TestNonCompliantPathUnchanged(unittest.TestCase):
    def setUp(self) -> None:
        builder = DocumentBuilder(created=FIXED_CREATED)
        flow = builder.flow()
        flow.text("plain", size=12)
        self.data = builder.render()
        self.offsets = parse_xref(self.data)

    def test_trailer_still_has_info(self) -> None:
        self.assertIn(b"/Info", trailer_dict_bytes(self.data))

    def test_no_metadata_or_output_intents(self) -> None:
        self.assertNotIn(b"/Type /Metadata", self.data)
        self.assertNotIn(b"/Type /OutputIntent", self.data)
        self.assertNotIn(b"/Alternate /DeviceRGB", self.data)

    def test_page_has_no_color_space_resources(self) -> None:
        page_id = find_object_with(self.data, b"/Type /Page /", self.offsets)
        page = object_bytes(self.data, self.offsets[page_id])
        self.assertNotIn(b"/ColorSpace", page)

    def test_images_keep_device_colour_spaces_outside_a4(self) -> None:
        builder = DocumentBuilder(created=FIXED_CREATED)
        flow = builder.flow()
        flow.image(_gradient_png(), x=0, y=30, width=64, height=64)
        data = builder.render()
        offsets = parse_xref(data)
        image_id = find_object_with(data, b"/Subtype /Image", offsets)
        image = object_bytes(data, offsets[image_id])
        self.assertIn(b"/ColorSpace /DeviceRGB", image)


if __name__ == "__main__":
    unittest.main()
