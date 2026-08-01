"""Multi-page integration tests: pages tree, per-page streams and resources.

End-to-end through DocumentBuilder: a document mixing a long paragraph and a
long table must produce several pages whose /Kids entries all resolve to
/Type /Page objects via the classic xref, with /Count equal to the number
of pages, each page carrying its own compressed content stream and only the
resources it actually uses.
"""

from __future__ import annotations

import re
import unittest

from engine import DocumentBuilder, PageMargins, TableLayout
from engine.tests.helpers import (
    all_object_ids,
    all_objects_with,
    find_object_with,
    inflate_stream,
    object_bytes,
    parse_obj_header,
    parse_xref,
    refs_in,
    startxref_offset,
    stream_refs_in,
    trailer_dict_bytes,
)


def _render_mixed_document() -> bytes:
    builder = DocumentBuilder(margins=PageMargins(48, 48, 48, 48))
    flow = builder.flow()
    flow.paragraph(
        "Intro. " * 60 + "\n" + "Body text. " * 120, size=11
    )
    flow.table(
        TableLayout(
            col_widths=[60, 100, 100, 80, 60],
            header=[f"H{i}" for i in range(5)],
            rows=[[f"r{r}c{c}" for c in range(5)] for r in range(70)],
            size=9,
        )
    )
    return builder.render()


class TestMultiPageDocument(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _render_mixed_document()
        self.offsets = parse_xref(self.data)
        self.pages_id = find_object_with(self.data, b"/Type /Pages /", self.offsets)
        self.pages_body = object_bytes(self.data, self.offsets[self.pages_id])
        self.kids = refs_in(self.pages_body)

    def test_multiple_pages_produced(self) -> None:
        self.assertGreater(len(self.kids), 1)

    def test_pages_count_matches_kids(self) -> None:
        match = re.search(rb"/Count\s+(\d+)", self.pages_body)
        self.assertIsNotNone(match)
        self.assertEqual(int(match.group(1)), len(self.kids))

    def test_every_kid_resolves_to_a_page_object(self) -> None:
        for kid in self.kids:
            self.assertIn(kid, self.offsets)
            body = object_bytes(self.data, self.offsets[kid])
            self.assertIn(b"/Type /Page /", body)
            self.assertIn(b"/Parent %d 0 R" % self.pages_id, body)

    def test_each_page_has_own_content_stream(self) -> None:
        contents = set()
        for kid in self.kids:
            body = object_bytes(self.data, self.offsets[kid])
            match = re.search(rb"/Contents\s+(\d+)\s+0\s+R", body)
            self.assertIsNotNone(match, f"page {kid} missing /Contents")
            contents.add(int(match.group(1)))
        self.assertEqual(len(contents), len(self.kids))

    def test_each_content_stream_compressed_and_nonempty(self) -> None:
        page_ids = all_objects_with(self.data, self.offsets, b"/Type /Page /")
        for page_id in page_ids:
            body = object_bytes(self.data, self.offsets[page_id])
            for ref in stream_refs_in(self.data, self.offsets, body):
                raw = inflate_stream(self.data, self.offsets[ref])
                # Continuation pages legitimately open with the redrawn
                # header band (fill + stroke colour operators and rects);
                # every page must still draw at least one text block.
                self.assertIn(b"BT", raw, "page stream has no text")
                self.assertGreater(len(raw), 10)

    def test_page_resources_reference_fonts(self) -> None:
        page_ids = all_objects_with(self.data, self.offsets, b"/Type /Page /")
        for page_id in page_ids:
            body = object_bytes(self.data, self.offsets[page_id])
            self.assertIn(b"/Resources", body)
            self.assertIn(b"/Font", body)

    def test_xref_offsets_resolve_for_every_object(self) -> None:
        for obj_id, offset in self.offsets.items():
            number, _gen = parse_obj_header(self.data, offset)
            self.assertEqual(number, obj_id)

    def test_startxref_still_points_at_xref(self) -> None:
        self.assertEqual(startxref_offset(self.data), self.data.index(b"xref\n"))

    def test_header_redrawn_at_top_of_continuation_pages(self) -> None:
        # Every page after the first must open with the redrawn header band
        # (fill colour + rect + header text) and NOT with a body row.
        page_ids = all_objects_with(self.data, self.offsets, b"/Type /Page /")
        first = True
        for page_id in page_ids:
            body = object_bytes(self.data, self.offsets[page_id])
            for ref in stream_refs_in(self.data, self.offsets, body):
                raw = inflate_stream(self.data, self.offsets[ref])
                if first:
                    first = False
                    continue  # page 1 opens with the intro paragraph
                self.assertIn(b"0.13 0.26 0.38 rg", raw,
                              "continuation page missing header band")
                self.assertIn(b"re\nf", raw)
                header_y = re.search(rb"^([0-9.]+) ([0-9.]+) ([0-9.]+) ([0-9.]+) re\nf\n",
                                     raw, re.M)
                self.assertIsNotNone(header_y)
                # The band's top edge (y + height) sits at the very top of
                # the page content box (A4 841.89 - 48 top margin).
                self.assertGreater(float(header_y.group(2))
                                   + float(header_y.group(4)), 780.0)

    def test_all_objects_contiguous(self) -> None:
        ids = all_object_ids(self.offsets)
        self.assertEqual(ids, list(range(1, len(ids) + 1)))

    def test_font_objects_are_shared(self) -> None:
        page_ids = all_objects_with(self.data, self.offsets, b"/Type /Page /")
        font_refs = set()
        for page_id in page_ids:
            body = object_bytes(self.data, self.offsets[page_id])
            font_refs.update(
                int(n) for n in re.findall(rb"/Font\s*<<\s*/F1\s+(\d+)\s+0\s+R", body)
            )
        self.assertEqual(len(font_refs), 1)  # one shared Helvetica object

    def test_image_only_page_resource_usage(self) -> None:
        from engine import decode_png
        from engine.tests.test_image import make_gradient_png

        builder = DocumentBuilder()
        flow = builder.flow()
        flow.image(make_gradient_png(4, 4), x=20, y=20, width=40, height=40)
        flow.image(make_gradient_png(4, 4), x=20, y=20, width=40, height=40)
        data = builder.render()
        offsets = parse_xref(data)
        page_ids = all_objects_with(data, offsets, b"/Type /Page /")
        self.assertEqual(len(page_ids), 1)
        body = object_bytes(data, offsets[page_ids[0]])
        self.assertIn(b"/XObject", body)
        self.assertIn(b"/Im1", body)
        # the deduplicated XObject is referenced by exactly one object
        image_objects = [
            obj
            for obj in offsets
            if b"/Subtype /Image" in object_bytes(data, offsets[obj])
        ]
        self.assertEqual(len(image_objects), 1)
        self.assertEqual(decode_png(make_gradient_png(4, 4)).width, 4)


class TestNoCompressionMode(unittest.TestCase):
    def test_compress_flag_disables_flate(self) -> None:
        builder = DocumentBuilder(compress=False)
        flow = builder.flow()
        flow.text("plain")
        data = builder.render()
        offsets = parse_xref(data)
        self.assertNotIn(b"/Filter /FlateDecode", data)
        page_id = find_object_with(data, b"/Type /Page /", offsets)
        body = object_bytes(data, offsets[page_id])
        for ref in stream_refs_in(data, offsets, body):
            self.assertIn(b"BT", data[offsets[ref]:offsets[ref] + 400])

    def test_default_is_compressed(self) -> None:
        data = _render_mixed_document()
        self.assertIn(b"/Filter /FlateDecode", data)

    def test_trailer_root_resolves_to_catalog(self) -> None:
        data = _render_mixed_document()
        trailer = trailer_dict_bytes(data)
        match = re.search(rb"/Root\s+(\d+)\s+0\s+R", trailer)
        self.assertIsNotNone(match)
        catalog_id = int(match.group(1))
        body = object_bytes(data, parse_xref(data)[catalog_id])
        self.assertIn(b"/Type /Catalog", body)


if __name__ == "__main__":
    unittest.main()
