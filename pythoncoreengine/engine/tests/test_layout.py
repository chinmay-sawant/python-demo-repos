"""Unit tests for engine/layout.py: text metrics, wrapping, page flow, breaks.

Uses a minimal stub FlowHost so PageFlow can be exercised without building
a full document, plus end-to-end checks through DocumentBuilder where the
page-break behaviour actually matters (object counts, /Count).
"""

from __future__ import annotations

import re
import unittest

from engine import DocumentBuilder, PageMargins, PageFlow, wrap_text, text_width
from engine.content import ContentStream
from engine.layout import FlowHost
from engine.page import A4_POINTS
from engine.write import ObjectId
from engine.tests.helpers import (
    all_objects_with,
    find_object_with,
    object_bytes,
    parse_xref,
    refs_in,
)


class _StubHost:
    """FlowHost stand-in: records pages, returns a fresh stream per page."""

    def __init__(self) -> None:
        self.streams: list = []

    def new_page(self) -> ContentStream:
        stream = ContentStream()
        self.streams.append(stream)
        return stream

    def font_ref(self, name: str) -> ObjectId:
        return ObjectId(99)

    def image_ref(self, data: bytes) -> str:
        return "Im1"

    def font_is_cid(self, name: str) -> bool:
        return False

    def record_font_usage(self, name: str, text: str) -> None:
        return None


class TestTextMetrics(unittest.TestCase):
    def test_known_helvetica_widths(self) -> None:
        # "Hello": H=722 e=556 l=222 l=222 o=556 -> 2278/1000 em at 12pt.
        self.assertAlmostEqual(text_width("Hello", 12), 27.336, places=3)

    def test_space_width(self) -> None:
        self.assertAlmostEqual(text_width(" ", 12), 3.336, places=3)

    def test_empty_text(self) -> None:
        self.assertEqual(text_width("", 12), 0.0)

    def test_high_bytes_fallback_width(self) -> None:
        self.assertEqual(text_width("\u00e9", 1000), 556)  # non-ASCII byte -> 556

    def test_scales_with_size(self) -> None:
        self.assertEqual(text_width("A", 500), text_width("A", 1000) / 2)


class TestWrapText(unittest.TestCase):
    def test_short_text_stays_one_line(self) -> None:
        self.assertEqual(wrap_text("Hello world", 500, 12), ["Hello world"])

    def test_wraps_on_word_boundaries(self) -> None:
        self.assertEqual(wrap_text("aaa bbb ccc", 21, 12), ["aaa", "bbb", "ccc"])

    def test_long_word_is_hard_split(self) -> None:
        word = "supercalifragilisticexpialidocious"
        lines = wrap_text(word, 50, 12)
        self.assertGreater(len(lines), 1)
        self.assertEqual("".join(lines), word)
        for line in lines:
            self.assertLessEqual(text_width(line, 12), 50)

    def test_newlines_force_breaks(self) -> None:
        self.assertEqual(wrap_text("a\nb\nc", 500, 12), ["a", "b", "c"])

    def test_empty_input(self) -> None:
        self.assertEqual(wrap_text("", 500, 12), [])


class TestPageFlow(unittest.TestCase):
    def test_margins_define_content_box(self) -> None:
        flow = PageFlow(host=_StubHost(), margins=PageMargins(72, 72, 72, 72))
        self.assertAlmostEqual(flow.content_width, A4_POINTS[0] - 144, places=3)

    def test_flow_starts_with_one_page(self) -> None:
        flow = PageFlow(host=_StubHost())
        self.assertEqual(flow.page_count, 1)

    def test_text_advances_cursor_by_leading(self) -> None:
        flow = PageFlow(host=_StubHost())
        flow.text("Hi", size=10, leading=12)
        self.assertEqual(flow.y, 12)
        flow.text("there", size=10)  # default leading = 1.2 * size
        self.assertEqual(flow.y, 12 + 12)

    def test_text_position_and_color_ops(self) -> None:
        flow = PageFlow(host=_StubHost())
        flow.text("Hi", x=10, size=12, color=(0.5, 0.0, 0.0))
        ops = flow.stream.render()
        self.assertIn(b"0.5 0 0 rg", ops)
        self.assertIn(b"(Hi) Tj", ops)
        self.assertIn(b"82 ", ops)  # x = left margin (72) + offset (10)

    def test_text_origin_is_margin_relative(self) -> None:
        flow = PageFlow(host=_StubHost())
        flow.text("Hi", x=0, size=12)
        ops = flow.stream.render()
        # baseline y = page height - top margin - cursor (0) = 769.89
        self.assertIn(b"72 769.89 Td", ops)

    def test_ensure_space_breaks_page(self) -> None:
        flow = PageFlow(host=_StubHost())
        flow.y = 600
        flow.ensure_space(100)
        self.assertEqual(flow.page_count, 2)
        self.assertEqual(flow.y, 0)

    def test_ensure_space_within_bounds_keeps_page(self) -> None:
        flow = PageFlow(host=_StubHost())
        flow.y = 100
        flow.ensure_space(100)
        self.assertEqual(flow.page_count, 1)

    def test_oversized_block_does_not_loop(self) -> None:
        flow = PageFlow(host=_StubHost())
        flow.ensure_space(flow._usable_height * 2)
        self.assertEqual(flow.page_count, 1)

    def test_resource_usage_tracked_per_page(self) -> None:
        flow = PageFlow(host=_StubHost())
        flow.use_font("F1")
        flow.y = 100
        flow.ensure_space(690)  # 100 + 690 > usable height (697.89) -> page break
        flow.use_font("F2")
        self.assertEqual(flow.page_fonts[0], {"F1"})
        self.assertEqual(flow.page_fonts[1], {"F2"})


def _render_long_paragraph(lines: int = 60) -> bytes:
    """A 60-raw-line paragraph (~240 wrapped lines) that must span pages."""
    builder = DocumentBuilder()
    flow = builder.flow()
    body = "Word " * 40 + "end"
    flow.paragraph("\n".join([body] * lines))
    return builder.render()


class TestPageBreakDocument(unittest.TestCase):
    def test_overflow_creates_new_pages_with_correct_count(self) -> None:
        data = _render_long_paragraph()
        offsets = parse_xref(data)
        pages_id = find_object_with(data, b"/Type /Pages /", offsets)
        pages = object_bytes(data, offsets[pages_id])
        kids = refs_in(pages)
        self.assertGreater(len(kids), 1)
        self.assertIn(b"/Count %d" % len(kids), pages)

    def test_every_page_has_own_stream(self) -> None:
        data = _render_long_paragraph()
        offsets = parse_xref(data)
        page_ids = all_objects_with(data, offsets, b"/Type /Page /")
        contents = set()
        for page_id in page_ids:
            body = object_bytes(data, offsets[page_id])
            match = re.search(rb"/Contents\s+(\d+)\s+0\s+R", body)
            self.assertIsNotNone(match)
            contents.add(int(match.group(1)))
        self.assertGreater(len(page_ids), 1)
        self.assertEqual(len(contents), len(page_ids))


if __name__ == "__main__":
    unittest.main()
