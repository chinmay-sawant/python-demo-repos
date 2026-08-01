"""Phase 6 pooling tests: buffer and offset-list reuse must not alias.

Renders documents through pooled buffers (a document hands its bytearray
back to itself on every :meth:`Document.render` call) and asserts that
mutating one document's pooled buffer never changes another document's
output, and that a reused writer buffer always yields exactly the bytes
written (stale bytes past the used length never leak).
"""

from __future__ import annotations

import datetime
import unittest

from engine import DocumentBuilder, PageMargins, TableLayout
from engine.doc import Document
from engine.write import ByteWriter, encode_xref_section

FIXED_CREATED = datetime.datetime(2026, 8, 1, 12, 0, 0)


def _minimal_document() -> bytes:
    builder = DocumentBuilder(created=FIXED_CREATED)
    flow = builder.flow()
    flow.text("Pooling isolation check", size=14)
    return builder.render()


def _populated_document() -> Document:
    doc = Document()
    pages_ref = doc.reserve()
    catalog_ref = doc.reserve()
    doc.set_value(pages_ref, {"/Type": "/Pages", "/Kids": [], "/Count": 0})
    doc.set_value(catalog_ref, {"/Type": "/Catalog", "/Pages": pages_ref})
    doc.set_root(catalog_ref)
    return doc


class TestBufferPoolNoAliasing(unittest.TestCase):
    def test_two_documents_do_not_share_buffers(self) -> None:
        doc_a = _populated_document()
        doc_b = _populated_document()
        data_a = doc_a.render()
        data_b = doc_b.render()
        self.assertIsNot(doc_a._pooled_buffer, doc_b._pooled_buffer)
        # Mutate the first document's pooled buffer; the second document's
        # bytes are untouched (they came from its own buffer).
        doc_a._pooled_buffer[:] = b"\xaa" * len(doc_a._pooled_buffer)
        self.assertEqual(data_b, doc_b.render())

    def test_reused_buffer_never_leaks_stale_bytes(self) -> None:
        doc = _populated_document()
        first = doc.render()
        buffer = doc._pooled_buffer
        self.assertGreater(len(buffer), len(first))  # capacity >= used
        buffer[:] = b"\x55" * len(buffer)
        second = doc.render()
        self.assertEqual(first, second)

    def test_pooled_buffer_grows_when_document_grows(self) -> None:
        doc = _populated_document()
        small = doc.render()
        # Attach a stream body that pushes the next render past the pooled
        # capacity: the writer must grow the reused buffer, not truncate.
        extra_ref = doc.reserve()
        doc.set_stream(extra_ref, b"BT\nET\n" * 500)
        large = doc.render()
        self.assertGreater(len(large), len(small))
        self.assertIn(b"stream\n", large)
        self.assertEqual(large.count(b"endobj\n"), 3)

    def test_builder_render_twice_after_mutation(self) -> None:
        # DocumentBuilder.render() on a fresh builder is single-shot, but a
        # second render of a *different* builder must not see the first's
        # pooled state.
        one = _minimal_document()
        two = _minimal_document()
        self.assertEqual(one, two)


class TestByteWriterReuse(unittest.TestCase):
    def test_writer_reuses_external_buffer(self) -> None:
        buffer = bytearray(1024)
        writer = ByteWriter(buffer)
        writer.write(b"abc")
        self.assertEqual(writer.getvalue(), b"abc")
        writer.write(b"def")
        self.assertEqual(writer.getvalue(), b"abcdef")

    def test_take_buffer_round_trip(self) -> None:
        writer = ByteWriter()
        writer.write(b"hello")
        pooled = writer.take_buffer()
        writer2 = ByteWriter(pooled)
        writer2.write(b"HELLO WORLD")
        # A reused buffer starts at offset 0: stale bytes are overwritten,
        # never appended after, so the output is exactly what was written.
        self.assertEqual(writer2.getvalue(), b"HELLO WORLD")

    def test_prealloc_writer_matches_plain_writer(self) -> None:
        plain = ByteWriter()
        prealloc = ByteWriter(prealloc=4096)
        for chunk in (b"x", b"y" * 100, b"z" * 5000):
            plain.write(chunk)
            prealloc.write(chunk)
        self.assertEqual(plain.getvalue(), prealloc.getvalue())

    def test_offsets_reported_are_positions(self) -> None:
        writer = ByteWriter()
        writer.write(b"%PDF-2.0\n")
        start = writer.write(b"abc")
        self.assertEqual(start, 9)
        self.assertEqual(writer.tell(), 12)


class TestXrefOffsetListPath(unittest.TestCase):
    def test_sequence_offsets_encode_like_dict(self) -> None:
        as_dict = encode_xref_section({1: 15, 2: 40}, 3)
        as_list = encode_xref_section([0, 15, 40], 3)
        self.assertEqual(as_dict, as_list)

    def test_sequence_missing_offset_raises(self) -> None:
        with self.assertRaises(ValueError):
            encode_xref_section([0, 0, 40], 3)

    def test_document_reuses_offset_list(self) -> None:
        doc = _populated_document()
        doc.render()
        offsets = doc._pooled_offsets
        doc.render()
        self.assertIs(doc._pooled_offsets, offsets)
        # Header: "%PDF-2.0\n" (9) + binary comment line (6) = 15.
        self.assertEqual(offsets[1], 15)
        self.assertEqual(
            offsets[2],
            15 + len(b"1 0 obj\n<< /Type (/Pages) /Kids [] /Count 0 >>\nendobj\n"),
        )


if __name__ == "__main__":
    unittest.main()
