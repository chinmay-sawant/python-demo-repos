"""Unit tests for the embedded CID font chain: Type0/CIDFontType2/descriptor.

Builds a real embedded-font document through DocumentBuilder and parses the
emitted font objects with a small recursive-descent PDF value parser, then
verifies the Type0 dictionary keys, the CIDFont keys (/CIDSystemInfo, /DW,
/W, /CIDToGIDMap), the FontDescriptor metrics, that FontFile2 is a valid
subset TTF containing the used characters, that the ToUnicode CMap covers
every used character, that page resources reference the embedded (prefixed)
font, and that the content stream shows text as UTF-16BE hex Tj.
"""

from __future__ import annotations

import datetime
import re
import unittest
import zlib
from typing import Any, Dict, List, Tuple

from engine import DocumentBuilder
from engine.font import LIBERATION_FONT_PATHS, TTFFont, TTFSubsetter
from engine.tests.helpers import (
    find_object_with,
    inflate_stream,
    is_stream_object,
    object_bytes,
    parse_xref,
    stream_bytes,
)

FIXED_CREATED = datetime.datetime(2026, 8, 1, 12, 0, 0)
USED_TEXT = "Hello, wörld 123"
_PREFIX_RE = re.compile(r"^[A-P]{6}\+LiberationSans$")


# ---------------------------------------------------------------------
# Minimal recursive-descent parser for the PDF value syntax in font objects
# ---------------------------------------------------------------------


def _parse_value(data: bytes, pos: int) -> Tuple[Any, int]:
    """Parse one PDF value at ``pos``; returns (value, end position).

    Supports names, numbers, literal/hex strings, arrays, dictionaries and
    ``N 0 R`` indirect references (returned as int).  Dict keys are the raw
    name strings without the leading slash.
    """
    while pos < len(data) and data[pos] in b" \t\r\n":
        pos += 1
    if data[pos : pos + 2] == b"<<":
        return _parse_dict(data, pos + 2)
    if data[pos : pos + 1] == b"[":
        items: List[Any] = []
        pos += 1
        while True:
            while pos < len(data) and data[pos] in b" \t\r\n":
                pos += 1
            if data[pos : pos + 1] == b"]":
                return items, pos + 1
            item, pos = _parse_value(data, pos)
            items.append(item)
    if data[pos : pos + 1] == b"/":
        pos += 1
        start = pos
        while pos < len(data) and data[pos] not in b" \t\r\n[]<>(){}":
            pos += 1
        return data[start:pos].decode("ascii"), pos
    if data[pos : pos + 1] == b"(":
        depth = 0
        buffer = bytearray()
        pos += 1
        while pos < len(data):
            ch = data[pos]
            if ch == 0x5C and pos + 1 < len(data):
                buffer.append(data[pos + 1])
                pos += 2
            elif ch == 0x28:
                depth += 1
                buffer.append(ch)
                pos += 1
            elif ch == 0x29:
                if depth == 0:
                    return bytes(buffer), pos + 1
                depth -= 1
                buffer.append(ch)
                pos += 1
            else:
                buffer.append(ch)
                pos += 1
        raise ValueError("unterminated string")
    if data[pos : pos + 1] == b"<":
        end = data.index(b">", pos)
        return bytes.fromhex(data[pos + 1 : end].decode("ascii")), end + 1
    start = pos
    while pos < len(data) and data[pos] in b"0123456789+-.":
        pos += 1
    token = data[start:pos]
    if not token:
        raise ValueError(f"no value at offset {pos}: {data[pos:pos + 16]!r}")
    if re.fullmatch(rb"\d+", token):
        after = pos
        while after < len(data) and data[after] in b" \t\r\n":
            after += 1
        if data[after : after + 3] == b"0 R":
            return int(token), after + 3
        return int(token), pos
    if re.fullmatch(rb"-?\d+(\.\d+)?", token):
        return (float(token) if b"." in token else int(token)), pos
    raise ValueError(f"cannot parse token {token!r}")


def _parse_dict(data: bytes, pos: int) -> Tuple[Dict[str, Any], int]:
    result: Dict[str, Any] = {}
    while True:
        while pos < len(data) and data[pos] in b" \t\r\n":
            pos += 1
        if data[pos : pos + 2] == b">>":
            return result, pos + 2
        key, pos = _parse_value(data, pos)
        value, pos = _parse_value(data, pos)
        result[key] = value


def _object_dict(data: bytes, offsets: Dict[int, int], obj_id: int) -> Dict[str, Any]:
    body = object_bytes(data, offsets[obj_id])
    value, _end = _parse_value(body, body.index(b"obj") + 3)
    return value


def _render_embedded(text: str = USED_TEXT) -> bytes:
    builder = DocumentBuilder(mode_embed_fonts=True, created=FIXED_CREATED)
    flow = builder.flow()
    flow.text(text, size=12)
    return builder.render()


class TestType0Chain(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _render_embedded()
        self.offsets = parse_xref(self.data)
        self.type0_id = find_object_with(self.data, b"/Subtype /Type0", self.offsets)
        self.type0 = _object_dict(self.data, self.offsets, self.type0_id)

    def test_type0_keys(self) -> None:
        self.assertEqual(self.type0["Type"], "Font")
        self.assertEqual(self.type0["Subtype"], "Type0")
        self.assertRegex(self.type0["BaseFont"], _PREFIX_RE)
        self.assertEqual(self.type0["Encoding"], "Identity-H")

    def test_descendant_fonts_single_ref_resolves_to_cid_font(self) -> None:
        descendants = self.type0["DescendantFonts"]
        self.assertEqual(len(descendants), 1)
        cid_id = descendants[0]
        cid = _object_dict(self.data, self.offsets, cid_id)
        self.assertEqual(cid["Subtype"], "CIDFontType2")

    def test_tounicode_ref_resolves_to_stream(self) -> None:
        self.assertTrue(is_stream_object(self.data, self.offsets, self.type0["ToUnicode"]))


class TestCIDFontDict(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _render_embedded()
        self.offsets = parse_xref(self.data)
        self.cid_id = find_object_with(self.data, b"/Subtype /CIDFontType2", self.offsets)
        self.cid = _object_dict(self.data, self.offsets, self.cid_id)

    def test_cid_font_keys(self) -> None:
        self.assertEqual(self.cid["Type"], "Font")
        self.assertEqual(self.cid["Subtype"], "CIDFontType2")
        self.assertEqual(self.cid["CIDToGIDMap"], "Identity")
        self.assertRegex(self.cid["BaseFont"], _PREFIX_RE)

    def test_cid_system_info(self) -> None:
        info = self.cid["CIDSystemInfo"]
        self.assertEqual(info["Registry"], b"Adobe")
        self.assertEqual(info["Ordering"], b"Identity")
        self.assertEqual(info["Supplement"], 0)

    def test_descriptor_ref_resolves_to_font_descriptor(self) -> None:
        descriptor = _object_dict(self.data, self.offsets, self.cid["FontDescriptor"])
        self.assertEqual(descriptor["Type"], "FontDescriptor")

    def test_widths_cover_used_chars(self) -> None:
        widths = self.cid["W"]
        self.assertIsInstance(widths, list)
        self.assertGreater(len(widths), 0)
        self.assertIsInstance(self.cid["DW"], int)

    def test_letter_h_width_is_722(self) -> None:
        self.assertIn(722, _flatten_w(self.cid["W"]))


def _flatten_w(entries: List[Any]) -> List[int]:
    """Expand a flat ``/W`` array into one width per CID."""
    result: List[int] = []
    index = 0
    while index < len(entries):
        cid = entries[index]
        index += 1
        if isinstance(entries[index], list):
            result.extend(entries[index])
            index += 1
        else:
            end = entries[index]
            index += 1
            width = entries[index]
            index += 1
            result.extend([width] * (end - cid + 1))
    return result


class TestFontDescriptor(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _render_embedded()
        self.offsets = parse_xref(self.data)
        cid_id = find_object_with(self.data, b"/Subtype /CIDFontType2", self.offsets)
        cid = _object_dict(self.data, self.offsets, cid_id)
        self.descriptor = _object_dict(self.data, self.offsets, cid["FontDescriptor"])

    def test_descriptor_metrics(self) -> None:
        self.assertRegex(self.descriptor["FontName"], _PREFIX_RE)
        self.assertEqual(self.descriptor["Flags"], 32)  # nonsymbolic sans-serif
        self.assertEqual(self.descriptor["ItalicAngle"], 0)
        self.assertEqual(self.descriptor["Ascent"], 905)
        self.assertEqual(self.descriptor["Descent"], -212)
        self.assertEqual(self.descriptor["CapHeight"], 688)
        self.assertEqual(self.descriptor["XHeight"], 528)
        self.assertEqual(self.descriptor["StemV"], 80)
        self.assertEqual(self.descriptor["FontBBox"], [-203, -303, 1050, 910])

    def test_font_file2_ref_resolves_to_subset_ttf(self) -> None:
        file_id = self.descriptor["FontFile2"]
        self.assertTrue(is_stream_object(self.data, self.offsets, file_id))
        raw = inflate_stream(self.data, self.offsets[file_id])
        self.assertTrue(raw.startswith(b"\x00\x01\x00\x00"))
        parsed = TTFFont(raw, "<FontFile2>")
        for char in USED_TEXT:
            self.assertIsNotNone(parsed.glyph_id(char), f"{char!r} missing in subset")
        self.assertEqual(parsed.units_per_em, 2048)


class TestToUnicode(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _render_embedded()
        self.offsets = parse_xref(self.data)
        type0_id = find_object_with(self.data, b"/Subtype /Type0", self.offsets)
        type0 = _object_dict(self.data, self.offsets, type0_id)
        self.cmap = inflate_stream(self.data, self.offsets[type0["ToUnicode"]])

    def test_cmap_header_present(self) -> None:
        for marker in (
            b"/CIDInit /ProcSet findresource begin",
            b"/CMapName /Adobe-Identity-UCS def",
            b"/CMapType 2 def",
            b"begincodespacerange",
            b"beginbfchar",
        ):
            self.assertIn(marker, self.cmap)

    def test_every_used_char_mapped(self) -> None:
        for char in USED_TEXT:
            hex_code = char.encode("utf-16-be").hex().upper().encode("ascii")
            self.assertIn(b"<" + hex_code + b">", self.cmap, f"missing {char!r}")


class TestPageResourcesAndContent(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _render_embedded()
        self.offsets = parse_xref(self.data)

    def test_page_resources_reference_embedded_font(self) -> None:
        page_id = find_object_with(self.data, b"/Type /Page /", self.offsets)
        page = _object_dict(self.data, self.offsets, page_id)
        font_ref = page["Resources"]["Font"]["F1"]
        type0 = _object_dict(self.data, self.offsets, font_ref)
        self.assertEqual(type0["Subtype"], "Type0")

    def test_content_stream_uses_hex_tj(self) -> None:
        stream_obj = find_object_with(self.data, b"stream", self.offsets)
        raw = zlib.decompress(stream_bytes(self.data, self.offsets[stream_obj]))
        self.assertIn(b"/F1 12 Tf", raw)
        # CIDs are the subset glyph IDs, e.g. 'H' is glyph 6 in the subset.
        subsetter = TTFSubsetter(
            TTFFont.from_file(LIBERATION_FONT_PATHS["LiberationSans-Regular"]),
            USED_TEXT,
        )
        hex_text = "".join("%04X" % subsetter.subset_gids[ch] for ch in "Hello")
        self.assertIn(b"<" + hex_text.encode("ascii"), raw)
        self.assertNotIn(b"(Hello) Tj", raw)
        self.assertNotIn(b"Hello", raw)

    def test_build_minimal_document_embeds(self) -> None:
        from engine.doc import build_minimal_document

        data = build_minimal_document(
            "Café – 123", created=FIXED_CREATED, mode_embed_fonts=True
        )
        offsets = parse_xref(data)
        find_object_with(data, b"/Subtype /Type0", offsets)
        self.assertIn(b"/Encoding /Identity-H", data)
        self.assertIn(b"+LiberationSans", data)

    def test_subset_name_has_plus_prefix(self) -> None:
        match = re.search(rb"/BaseFont /([A-P]{6})\+LiberationSans", self.data)
        self.assertIsNotNone(match)


if __name__ == "__main__":
    unittest.main()
