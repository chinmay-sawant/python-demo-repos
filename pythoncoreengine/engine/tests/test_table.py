"""Unit tests for engine/layout.py TableLayout: grid, header styling, page splits.

Tables are exercised both at the content-op level (border paths, fill
colours, font usage) and end-to-end through DocumentBuilder (rows split
across pages, /Count matches the actual page count, header redrawn on the
continuation page).
"""

from __future__ import annotations

import re
import unittest

from engine import DocumentBuilder, PageMargins, PageFlow, TableLayout
from engine.content import ContentStream
from engine.write import ObjectId
from engine.tests.helpers import (
    all_objects_with,
    find_object_with,
    inflate_stream,
    object_bytes,
    parse_xref,
    refs_in,
    stream_refs_in,
)


class _StubHost:
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


def _table_flow(**kwargs) -> PageFlow:
    flow = PageFlow(host=_StubHost())
    header = kwargs.pop("header", ["H1", "H2", "H3"])
    rows = kwargs.pop("rows", [["a", "b", "c"], ["d", "e", "f"]])
    table = TableLayout(col_widths=[100, 100, 100], header=header, rows=rows, **kwargs)
    table.emit(flow)
    return flow


class TestTableGrid(unittest.TestCase):
    def test_3x3_grid_emits_border_paths(self) -> None:
        flow = _table_flow(cell_borders=True)
        ops = flow.stream.render()
        self.assertIn(b" re", ops)
        self.assertIn(b"S", ops)
        self.assertIn(b" m", ops)
        self.assertIn(b" l", ops)
        # 3 columns x (header + 2 body rows) = 9 stroked cell rectangles
        self.assertEqual(ops.count(b" re\nS"), 9)

    def test_grid_lines_use_stroke_colour_and_width(self) -> None:
        flow = _table_flow()
        ops = flow.stream.render()
        self.assertIn(b"0.4 0.4 0.4 RG", ops)
        self.assertIn(b"0.5 w", ops)

    def test_header_uses_bold_font_and_background(self) -> None:
        flow = _table_flow()
        ops = flow.stream.render()
        self.assertIn(b"/F2", ops)  # header_font
        self.assertIn(b"0.13 0.26 0.38 rg", ops)  # header background
        self.assertIn(b"re\nf", ops)

    def test_header_text_never_inherits_band_fill(self) -> None:
        # The header band fill must not leak into the header glyphs (text
        # without an explicit colour would render in the band colour and be
        # invisible), nor dim the body rows that follow.
        flow = _table_flow()
        ops = flow.stream.render()
        # white header text over the dark band, black body text
        self.assertIn(b"1 1 1 rg", ops)
        self.assertIn(b"0 0 0 rg", ops)

    def test_body_rows_use_body_font(self) -> None:
        flow = _table_flow(font="F1")
        ops = flow.stream.render()
        self.assertIn(b"/F1 10 Tf", ops)

    def test_table_wider_than_content_box_raises(self) -> None:
        flow = PageFlow(host=_StubHost())
        table = TableLayout(col_widths=[300, 300, 300], rows=[["a", "b", "c"]])
        with self.assertRaises(ValueError):
            table.emit(flow)

    def test_cell_text_is_wrapped_within_column(self) -> None:
        flow = _table_flow(
            rows=[["word " * 30, "b", "c"], ["d", "e", "f"]], size=10
        )
        ops = flow.stream.render()
        # a 92pt-wide text column holds ~3 lines of "word word ..." at 10pt
        lines = [op for op in ops.split(b"\n") if op.endswith(b"Tj")]
        self.assertGreaterEqual(len(lines), 5)

    def test_per_cell_border_skip(self) -> None:
        # row -1 is the header row; skip its cell outlines
        flow = _table_flow(
            cell_borders=True, border_skip=lambda row, col: row == -1
        )
        ops = flow.stream.render()
        self.assertEqual(ops.count(b" re\nS"), 6)


def _render_long_table(n_rows: int = 60, n_cols: int = 5) -> bytes:
    builder = DocumentBuilder(margins=PageMargins(48, 48, 48, 48))
    flow = builder.flow()
    header = [f"H{i}" for i in range(n_cols)]
    rows = [[f"r{row}c{col}" for col in range(n_cols)] for row in range(n_rows)]
    flow.table(
        TableLayout(
            col_widths=[60, 90, 90, 90, 60][:n_cols],
            header=header,
            rows=rows,
            size=9,
        )
    )
    return builder.render()


class TestTablePageBreaks(unittest.TestCase):
    def test_long_table_spans_pages_with_matching_count(self) -> None:
        data = _render_long_table()
        offsets = parse_xref(data)
        pages_id = find_object_with(data, b"/Type /Pages /", offsets)
        pages = object_bytes(data, offsets[pages_id])
        kids = refs_in(pages)
        self.assertGreater(len(kids), 1)
        self.assertIn(b"/Count %d" % len(kids), pages)
        page_ids = all_objects_with(data, offsets, b"/Type /Page /")
        self.assertEqual(len(kids), len(page_ids))

    def test_header_redrawn_on_continuation_pages(self) -> None:
        data = _render_long_table()
        offsets = parse_xref(data)
        page_ids = all_objects_with(data, offsets, b"/Type /Page /")
        header_text = b"(H0) Tj"
        drawn_on = 0
        for page_id in page_ids:
            body = object_bytes(data, offsets[page_id])
            for ref in stream_refs_in(data, offsets, body):
                if header_text in inflate_stream(data, offsets[ref]):
                    drawn_on += 1
        self.assertGreater(drawn_on, 1)  # first page + every continuation page

    def test_body_rows_split_between_pages(self) -> None:
        data = _render_long_table(n_rows=50)
        offsets = parse_xref(data)
        page_ids = all_objects_with(data, offsets, b"/Type /Page /")
        self.assertGreater(len(page_ids), 1)
        page_texts = []
        for page_id in page_ids:
            body = object_bytes(data, offsets[page_id])
            page_texts.append(
                b"\n".join(
                    inflate_stream(data, offsets[ref])
                    for ref in stream_refs_in(data, offsets, body)
                )
            )
        for row in range(50):
            marker = b"(r%dc0) Tj" % row
            self.assertTrue(any(marker in text for text in page_texts))
        self.assertNotIn(b"(r49c0) Tj", page_texts[0])  # last row moved to a later page

    def test_every_row_rendered_once(self) -> None:
        data = _render_long_table(n_rows=25)
        offsets = parse_xref(data)
        page_ids = all_objects_with(data, offsets, b"/Type /Page /")
        text = []
        for page_id in page_ids:
            body = object_bytes(data, offsets[page_id])
            for ref in stream_refs_in(data, offsets, body):
                text.append(inflate_stream(data, offsets[ref]))
        for row in range(25):
            count = sum(t.count(b"(r%dc0) Tj" % row) for t in text)
            self.assertEqual(count, 1, f"row {row} drawn {count} times")


if __name__ == "__main__":
    unittest.main()
