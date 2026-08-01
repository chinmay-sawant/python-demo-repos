"""Unit tests for the Zerodha renderer (engine.render).

Verifies from the emitted bytes: the %PDF-2.0 header, the Zerodha theme
colours in the page content (header bar #154360, section rows #21618C,
buy #27AE60, sell #E74C3C, alt rows #F8F9F9), footer text and page labels
recorded into the font subset (ToUnicode CMap), the watermark only when
the flag is set, multi-page HFT output with a matching pages-tree /Count,
and the non-compliant build differing (no /StructTreeRoot, no
/OutputIntents).  Ends with the veraPDF compliance gate (retail must pass
``-f 4`` and ``-f ua2``), skipped when the binary is missing.
"""

from __future__ import annotations

import os
import re
import subprocess
import tempfile
import unittest
import zlib
from pathlib import Path
from typing import List, Set, Tuple

from engine.color import hex_to_rgb
from engine.model import ContractNote, expand_trades, load_json
from engine.render import build_document, build_trade_table
from engine.tests.helpers import (
    find_object_with,
    inflate_stream,
    object_bytes,
    parse_xref,
)

DATA_DIR = Path(__file__).resolve().parents[2] / "sampledata" / "zerodha"

VERAPDF_BIN = os.environ.get(
    "VERAPDF_BIN", str(Path(__file__).resolve().parents[2] / "verapdf" / "verapdf")
)

_RGB_RE = re.compile(rb"([0-9.]+) ([0-9.]+) ([0-9.]+) rg")
_BFCHAR_RE = re.compile(rb"<([0-9A-F]+)> <([0-9A-F]+)>")
_PAGE_COUNT_RE = re.compile(rb"/Count (\d+)")


def _inflated_streams(data: bytes) -> List[bytes]:
    """Every FlateDecode stream body in the document (inflated)."""
    offsets = parse_xref(data)
    streams: List[bytes] = []
    for obj_id, offset in sorted(offsets.items()):
        if b"stream\n" not in object_bytes(data, offset):
            continue
        try:
            streams.append(inflate_stream(data, offset))
        except zlib.error:
            continue
    return streams


def _content_streams(data: bytes) -> List[bytes]:
    """Inflated page content streams only (contain ``Tf`` font selects)."""
    return [stream for stream in _inflated_streams(data) if b" Tf" in stream]


def _rgb_triples(streams: List[bytes]) -> List[Tuple[float, float, float]]:
    triples: List[Tuple[float, float, float]] = []
    for stream in streams:
        for match in _RGB_RE.finditer(stream):
            triples.append(tuple(float(match.group(i)) for i in (1, 2, 3)))
    return triples


def _has_color(triples: List[Tuple[float, float, float]], target) -> bool:
    return any(
        all(abs(actual - expected) < 1e-9 for actual, expected in zip(found, target))
        for found in triples
    )


def _tounicode_chars(data: bytes) -> Set[str]:
    """The set of unicode characters covered by the font subsets."""
    chars: Set[str] = set()
    for stream in _inflated_streams(data):
        if b"beginbfchar" not in stream:
            continue
        for _cid_hex, unicode_hex in _BFCHAR_RE.findall(stream):
            chars.update(
                bytes.fromhex(unicode_hex.decode("ascii")).decode("utf-16-be")
            )
    return chars


def _chars_of(text: str) -> Set[str]:
    return set(text)


def _verapdf_available() -> bool:
    path = Path(VERAPDF_BIN)
    return path.is_file() and os.access(path, os.X_OK)


class TestRetailRender(unittest.TestCase):
    def setUp(self) -> None:
        self.note = load_json(DATA_DIR / "retail_investor.json")
        self.data = build_document(self.note, compliant=True)

    def test_bytes_start_pdf20(self) -> None:
        self.assertEqual(self.data[:9], b"%PDF-2.0\n")

    def test_theme_colors_in_page_content(self) -> None:
        triples = _rgb_triples(_content_streams(self.data))
        self.assertTrue(_has_color(triples, hex_to_rgb("#154360")), "header bar")
        self.assertTrue(_has_color(triples, hex_to_rgb("#21618C")), "section rows")
        self.assertTrue(_has_color(triples, hex_to_rgb("#27AE60")), "buy text")
        self.assertTrue(_has_color(triples, hex_to_rgb("#E74C3C")), "sell text")
        self.assertTrue(_has_color(triples, hex_to_rgb("#F8F9F9")), "alt row bg")

    def test_footer_text_in_font_subset(self) -> None:
        chars = _tounicode_chars(self.data)
        self.assertTrue(_chars_of(self.note.footer_text) <= chars, "footer chars")

    def test_page_number_recorded(self) -> None:
        chars = _tounicode_chars(self.data)
        self.assertTrue(_chars_of("Page 1 of 1") <= chars, "page label chars")

    def test_single_page_count(self) -> None:
        offsets = parse_xref(self.data)
        pages = object_bytes(self.data, offsets[find_object_with(self.data, b"/Type /Pages", offsets)])
        self.assertEqual(re.search(_PAGE_COUNT_RE, pages).group(1), b"1")


class TestWatermark(unittest.TestCase):
    def test_watermark_rotated_text_when_flag_set(self) -> None:
        note = load_json(DATA_DIR / "active_trader.json")
        data = build_document(note, compliant=True)
        streams = _content_streams(data)
        self.assertTrue(any(b"/F1 60 Tf" in stream for stream in streams), "watermark size")
        self.assertTrue(
            any(re.search(rb"0\.7071\d* 0\.7071\d* -0\.7071\d* 0\.7071\d*", stream) for stream in streams),
            "45-degree rotation matrix",
        )

    def test_no_watermark_when_flag_off(self) -> None:
        note = load_json(DATA_DIR / "retail_investor.json")
        data = build_document(note, compliant=True)
        self.assertFalse(any(b"/F1 60 Tf" in stream for stream in _content_streams(data)))


class TestMultipageHft(unittest.TestCase):
    def test_hft_pages_tree_count_matches_kids(self) -> None:
        note = expand_trades(load_json(DATA_DIR / "hft_algo.json"), 2000, 42)
        data = build_document(note, compliant=True)
        offsets = parse_xref(data)
        pages = object_bytes(data, offsets[find_object_with(data, b"/Type /Pages", offsets)])
        match = re.search(_PAGE_COUNT_RE, pages)
        self.assertIsNotNone(match)
        count = int(match.group(1))
        self.assertGreaterEqual(count, 28)
        self.assertEqual(len(re.findall(rb"(\d+) 0 R", pages)), count)


class TestNonCompliant(unittest.TestCase):
    def test_nocomply_differs_and_has_no_structure(self) -> None:
        note = load_json(DATA_DIR / "retail_investor.json")
        compliant = build_document(note, compliant=True)
        plain = build_document(note, compliant=False)
        self.assertEqual(plain[:9], b"%PDF-2.0\n")
        self.assertNotEqual(plain, compliant)
        offsets = parse_xref(plain)
        catalog = object_bytes(plain, offsets[find_object_with(plain, b"/Type /Catalog", offsets)])
        self.assertNotIn(b"/StructTreeRoot", catalog)
        self.assertNotIn(b"/OutputIntents", catalog)
        self.assertNotIn(b"/Metadata", catalog)


class TestTradeTable(unittest.TestCase):
    def test_fixed_columns_and_rows(self) -> None:
        note = load_json(DATA_DIR / "retail_investor.json")
        table = build_trade_table(note)
        self.assertEqual(table.header, ["Symbol", "ISIN", "Action", "Qty", "Price", "Total"])
        self.assertEqual(len(table.rows), 2)
        self.assertEqual(table.rows[0][2], "BUY")
        self.assertEqual(table.rows[1][2], "SELL")
        self.assertAlmostEqual(sum(table.col_widths), 490.0)
        self.assertLessEqual(sum(table.col_widths), 499.0)


@unittest.skipUnless(_verapdf_available(), "veraPDF binary not found; skipping compliance gate")
class TestVerapdfCompliance(unittest.TestCase):
    """The retail contract note must pass veraPDF -f 4 and -f ua2."""

    def _assert_verapdf(self, note: ContractNote, flavour: str) -> None:
        data = build_document(note, compliant=True)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "note.pdf"
            path.write_bytes(data)
            result = subprocess.run(
                [VERAPDF_BIN, "-f", flavour, str(path)],
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('isCompliant="true"', result.stdout)
        self.assertIn('failedRules="0"', result.stdout)

    def test_retail_passes_pdfa4(self) -> None:
        self._assert_verapdf(load_json(DATA_DIR / "retail_investor.json"), "4")

    def test_retail_passes_ua2(self) -> None:
        self._assert_verapdf(load_json(DATA_DIR / "retail_investor.json"), "ua2")


if __name__ == "__main__":
    unittest.main()
