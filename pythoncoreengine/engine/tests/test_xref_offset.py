"""Unit tests for classic xref offset integrity.

Every referenced offset in the xref section must land on a valid object
header (``N 0 obj``), offsets must be unique and ascending, and no object
body may overlap the next object's header.
"""

from __future__ import annotations

import unittest

from engine import generate_minimal_pdf
from engine.tests.helpers import object_bytes, parse_obj_header, parse_xref


class TestXrefOffsets(unittest.TestCase):
    def setUp(self) -> None:
        self.data = generate_minimal_pdf(text="xref integrity")
        self.offsets = parse_xref(self.data)

    def test_every_offset_lands_on_object_header(self) -> None:
        for obj_id, offset in sorted(self.offsets.items()):
            number, generation = parse_obj_header(self.data, offset)
            self.assertEqual(number, obj_id)
            self.assertEqual(generation, 0)
            self.assertEqual(self.data[offset + len(b"%d 0 obj" % obj_id)], 0x0A)

    def test_object_header_bytes_are_exact(self) -> None:
        for obj_id, offset in sorted(self.offsets.items()):
            header = self.data[offset:offset + len(b"%d 0 obj\n" % obj_id)]
            self.assertEqual(header, b"%d 0 obj\n" % obj_id)

    def test_offsets_unique_and_ascending(self) -> None:
        positions = [self.offsets[i] for i in sorted(self.offsets)]
        self.assertEqual(len(positions), len(set(positions)))
        self.assertEqual(positions, sorted(positions))

    def test_no_object_overlap(self) -> None:
        ids = sorted(self.offsets)
        for previous, following in zip(ids, ids[1:]):
            body = object_bytes(self.data, self.offsets[previous])
            body_start = self.offsets[previous]
            body_end = self.data.index(b"endobj", body_start) + len(b"endobj\n")
            self.assertLessEqual(body_end, self.offsets[following])

    def test_all_offsets_within_file(self) -> None:
        for obj_id, offset in self.offsets.items():
            self.assertLess(offset, len(self.data) - 8)
            self.assertLess(offset, self.data.index(b"xref\n"))

    def test_single_page_document_has_six_objects(self) -> None:
        self.assertEqual(sorted(self.offsets), [1, 2, 3, 4, 5, 6])


if __name__ == "__main__":
    unittest.main()
