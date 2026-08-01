"""Phase 6 performance regression tests (engine.write / engine.doc / engine.font).

Verifies the optimisation layer never changes emitted bytes: parallel
compression must match serial compression byte-for-byte, preallocated/
pooled buffers must render identically to fresh ones, the font subset
cache must return identical bytes on repeat renders, and a dense tagged
table must be byte-deterministic across two renders (md5 check, 500 rows
to keep the suite fast).
"""

from __future__ import annotations

import datetime
import hashlib
import unittest

from engine import DocumentBuilder, PageMargins, TableLayout
from engine.doc import Document
from engine.font import _SUBSET_BYTES_CACHE
from engine.tests.helpers import parse_xref

FIXED_CREATED = datetime.datetime(2026, 8, 1, 12, 0, 0)

_PRODUCTS = ["Widget", "Gadget", "Sprocket", "Bolt", "Cable"]
_HEADERS = ["SKU", "Product", "Category", "Price", "Stock", "Region", "Qty", "Status"]


def _dense_rows(n_rows: int) -> list:
    rows = []
    for row in range(n_rows):
        rows.append(
            [
                "SKU-%04d" % row,
                _PRODUCTS[row % len(_PRODUCTS)],
                "A" if row % 2 == 0 else "B",
                "%.2f" % (9.99 + (row % 500) * 1.5),
                str(100 + (row % 900)),
                "EU",
                str((row * 7) % 500),
                "active",
            ]
        )
    return rows


def _dense_document(rows: int, **kwargs) -> bytes:
    builder = DocumentBuilder(
        margins=PageMargins(48, 48, 48, 48),
        created=FIXED_CREATED,
        mode_pdfa4=True,
        mode_pdfua2=True,
        title="Perf test table",
        **kwargs,
    )
    flow = builder.flow()
    flow.table(
        TableLayout(
            col_widths=[60, 70, 70, 70, 60, 60, 60, 49],
            header=_HEADERS,
            rows=_dense_rows(rows),
            size=9,
        )
    )
    return builder.render()


class TestParallelCompression(unittest.TestCase):
    def test_parallel_compress_byte_identical_to_serial(self) -> None:
        parallel = _dense_document(300, parallel_compress=True)
        serial = _dense_document(300, parallel_compress=False)
        self.assertEqual(parallel, serial)
        self.assertGreater(len(parallel), 100_000)

    def test_single_page_document_unchanged(self) -> None:
        parallel = _dense_document(2, parallel_compress=True)
        serial = _dense_document(2, parallel_compress=False)
        self.assertEqual(parallel, serial)

    def test_parallel_document_has_valid_xref(self) -> None:
        data = _dense_document(300)
        offsets = parse_xref(data)
        self.assertGreater(len(offsets), 100)


class TestPreallocatedRender(unittest.TestCase):
    def test_pooled_buffer_render_identical(self) -> None:
        """Rendering twice reuses the pooled buffer; bytes must not change."""
        doc = Document()
        _populate(doc)
        first = doc.render()
        # Corrupt the pooled buffer between renders: a reused buffer must
        # not leak stale bytes into the second render.
        pooled = doc._pooled_buffer
        self.assertIsNotNone(pooled)
        pooled[:] = b"\xff" * len(pooled)
        second = doc.render()
        self.assertEqual(first, second)

    def test_fresh_and_pooled_documents_identical(self) -> None:
        doc_a = Document()
        _populate(doc_a)
        doc_b = Document()
        _populate(doc_b)
        self.assertEqual(doc_a.render(), doc_b.render())
        self.assertEqual(doc_a.render(), doc_b.render())


class TestXrefOffsetListReuse(unittest.TestCase):
    def test_offsets_list_reused_across_renders(self) -> None:
        doc = Document()
        _populate(doc)
        doc.render()
        reused = doc._pooled_offsets
        doc.render()
        self.assertIs(doc._pooled_offsets, reused)
        # The reused offsets must still resolve: object 1 sits right after
        # the two header lines, and the last render's bytes must match a
        # fresh document's bytes exactly.
        first = doc.render()
        self.assertEqual(first[:9], b"%PDF-2.0\n")
        self.assertEqual(reused[1], 15)
        fresh = Document()
        _populate(fresh)
        self.assertEqual(first, fresh.render())


class TestSubsetCache(unittest.TestCase):
    def test_cache_hit_returns_identical_bytes(self) -> None:
        """A repeat subset of the same (font path, chars) returns the same bytes."""
        from engine.font import FontEntry, LIBERATION_FONT_PATHS

        path = LIBERATION_FONT_PATHS["LiberationSans-Regular"]
        chars = frozenset("abcdefghij12345")
        key = (str(path), chars)
        _SUBSET_BYTES_CACHE.pop(key, None)
        try:
            entry_a = FontEntry("F1", "Helvetica", face_name="LiberationSans", path=path)
            entry_a.add_chars("".join(chars))
            entry_a.generate_subset()
            first = entry_a.subset_bytes
            cached = _SUBSET_BYTES_CACHE.get(key)
            self.assertIsNotNone(cached, "generating a subset must populate the cache")
            entry_b = FontEntry("F2", "Helvetica", face_name="LiberationSans", path=path)
            entry_b.add_chars("".join(chars))
            entry_b.generate_subset()
            self.assertIs(entry_b.subset_bytes, cached, "cache hit must serve the cached bytes")
            self.assertEqual(entry_b.subset_bytes, first)
        finally:
            _SUBSET_BYTES_CACHE.pop(key, None)

    def test_document_render_identical_with_warm_cache(self) -> None:
        text = (
            "The quick brown fox jumps over the lazy dog, and the "
            "cache returns identical bytes on the repeat render."
        )
        builder = DocumentBuilder(created=FIXED_CREATED, mode_pdfa4=True, mode_pdfua2=True)
        flow = builder.flow()
        flow.text("Subset cache determinism check", size=14)
        flow.paragraph(text, size=11)
        first = builder.render()
        builder2 = DocumentBuilder(created=FIXED_CREATED, mode_pdfa4=True, mode_pdfua2=True)
        flow2 = builder2.flow()
        flow2.text("Subset cache determinism check", size=14)
        flow2.paragraph(text, size=11)
        self.assertEqual(first, builder2.render())


class TestDenseTableDeterminism(unittest.TestCase):
    def test_500_row_table_md5_identical_across_two_renders(self) -> None:
        first = _dense_document(500)
        second = _dense_document(500)
        self.assertEqual(hashlib.md5(first).hexdigest(), hashlib.md5(second).hexdigest())
        self.assertEqual(first, second)


def _populate(doc: Document) -> None:
    """A tiny two-object document: catalog + pages tree."""
    pages_ref = doc.reserve()
    catalog_ref = doc.reserve()
    doc.set_value(pages_ref, {"/Type": "/Pages", "/Kids": [], "/Count": 0})
    doc.set_value(catalog_ref, {"/Type": "/Catalog", "/Pages": pages_ref})
    doc.set_root(catalog_ref)


if __name__ == "__main__":
    unittest.main()
