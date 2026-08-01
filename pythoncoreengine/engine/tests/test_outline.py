"""Unit tests for the outline (bookmark) tree (engine.outline, engine.doc).

Builds documents through DocumentBuilder with ``outlines=True`` and
verifies, from the emitted bytes: the ``/Outlines`` dictionary (/First,
/Last, /Count), the item dictionaries (Title, Parent, Next, Prev, First,
Last, Count, Dest), the sibling/parent/child wiring of a nested tree, the
catalog /Outlines + /PageMode entries, and the destination forms -- page
destinations (``[page /Fit]`` / ``[page /XYZ ... null]``) in untagged
output vs structure destinations (``[/Sect ...]`` with a ``/Sect``
StructElem target) in tagged output, per PDF/UA-2 clause 8.8.  Also
checks the flags default to off (no outline objects in plain documents).
"""

from __future__ import annotations

import datetime
import re
import unittest

from engine import DocumentBuilder
from engine.fixtures import _phase7_bookmarks_document
from engine.tests.helpers import (
    find_object_with,
    object_bytes,
    parse_xref,
    trailer_dict_bytes,
)

FIXED_CREATED = datetime.datetime(2026, 8, 1, 12, 0, 0)

# Body text that spans two pages so outline items can target page 1.
_BODY = "Outline body paragraph. " * 200 + "Second paragraph words. " * 200

_ITEM_RE = rb"/Title \((.+?)\)"


def _build_tagged(**kwargs) -> bytes:
    builder = DocumentBuilder(
        created=FIXED_CREATED,
        mode_pdfa4=True,
        mode_pdfua2=True,
        title="Outline test",
        outlines=True,
        **kwargs,
    )
    flow = builder.flow()
    flow.text("Heading", size=16)
    flow.paragraph(_BODY, size=11)
    return builder.render()


def _build_untagged(**kwargs) -> bytes:
    builder = DocumentBuilder(created=FIXED_CREATED, outlines=True, **kwargs)
    flow = builder.flow()
    flow.text("Heading", size=16)
    flow.paragraph(_BODY, size=11)
    return builder.render()


def _add_tree(builder) -> None:
    report = builder.add_outline("Report", page_index=0)
    section = builder.add_outline("Section A", page_index=1, y=300, parent=report)
    builder.add_outline("A.1 detail", page_index=1, y=120, parent=section)
    builder.add_outline("Appendix", page_index=1)


def _item_dicts(data: bytes) -> list:
    """All outline item dictionary bodies (objects with a /Title entry)."""
    offsets = parse_xref(data)
    items = []
    for obj_id, offset in sorted(offsets.items()):
        raw = object_bytes(data, offset)
        if b"/Title (" in raw and b"/Dest [" in raw:
            items.append(raw)
    return items


def _item_ids(data: bytes) -> dict:
    """Map outline item title -> its object number (titles are unique here)."""
    offsets = parse_xref(data)
    found = {}
    for obj_id, offset in sorted(offsets.items()):
        raw = object_bytes(data, offset)
        match = re.search(_ITEM_RE, raw)
        if match is not None and b"/Dest [" in raw:
            found[match.group(1).decode()] = obj_id
    return found


class TestOutlineTree(unittest.TestCase):
    def setUp(self) -> None:
        builder = DocumentBuilder(created=FIXED_CREATED, outlines=True)
        flow = builder.flow()
        flow.text("Heading", size=16)
        flow.paragraph(_BODY, size=11)
        _add_tree(builder)
        self.data = builder.render()
        self.offsets = parse_xref(self.data)
        self.outlines_id = find_object_with(self.data, b"/Type /Outlines", self.offsets)
        self.outlines = object_bytes(self.data, self.offsets[self.outlines_id])

    def test_outlines_dict_keys(self) -> None:
        self.assertIn(b"/Type /Outlines", self.outlines)
        match = re.search(rb"/First\s+(\d+)\s+0\s+R", self.outlines)
        self.assertIsNotNone(match, self.outlines)
        last = re.search(rb"/Last\s+(\d+)\s+0\s+R", self.outlines)
        self.assertIsNotNone(last, self.outlines)
        self.assertIn(b"/Count 2", self.outlines)

    def test_first_last_reference_items(self) -> None:
        first = int(re.search(rb"/First\s+(\d+)\s+0\s+R", self.outlines).group(1))
        last = int(re.search(rb"/Last\s+(\d+)\s+0\s+R", self.outlines).group(1))
        for obj_id in (first, last):
            raw = object_bytes(self.data, self.offsets[obj_id])
            self.assertIn(b"/Title (", raw)
            self.assertIn(b"/Parent %d 0 R" % self.outlines_id, raw)

    def test_item_tree_wiring(self) -> None:
        items = _item_dicts(self.data)
        titles = [re.search(_ITEM_RE, raw).group(1).decode() for raw in items]
        self.assertEqual(len(items), 4)
        self.assertEqual(
            sorted(titles), ["A.1 detail", "Appendix", "Report", "Section A"]
        )
        by_title = dict(zip(titles, items))

        report = by_title["Report"]
        self.assertRegex(report, rb"/Next\s+(\d+)\s+0\s+R")  # -> Appendix
        self.assertNotIn(b"/Prev", report)
        section = by_title["Section A"]
        # Section A is the only child of Report: no siblings, one child.
        self.assertNotIn(b"/Prev", section)
        self.assertNotIn(b"/Next", section)
        self.assertRegex(section, rb"/First\s+(\d+)\s+0\s+R")
        self.assertRegex(section, rb"/Last\s+(\d+)\s+0\s+R")
        self.assertIn(b"/Count 1", section)
        appendix = by_title["Appendix"]
        self.assertRegex(appendix, rb"/Prev\s+(\d+)\s+0\s+R")  # -> Report
        self.assertNotIn(b"/Next", appendix)

    def test_parent_chain_nested(self) -> None:
        ids = _item_ids(self.data)
        items = {re.search(_ITEM_RE, raw).group(1).decode(): raw for raw in _item_dicts(self.data)}
        detail = items["A.1 detail"]
        detail_parent = re.search(rb"/Parent\s+(\d+)\s+0\s+R", detail).group(1)
        self.assertEqual(int(detail_parent), ids["Section A"])
        section_parent = re.search(
            rb"/Parent\s+(\d+)\s+0\s+R", items["Section A"]
        ).group(1)
        self.assertEqual(int(section_parent), ids["Report"])

    def test_top_level_parent_is_outlines_dict(self) -> None:
        for title in ("Report", "Appendix"):
            raw = next(
                r for r in _item_dicts(self.data)
                if re.search(_ITEM_RE, r).group(1).decode() == title
            )
            self.assertIn(
                b"/Parent %d 0 R" % self.outlines_id, raw, raw
            )


class TestDestinations(unittest.TestCase):
    def test_untagged_dests_point_at_pages(self) -> None:
        data = _build_untagged()
        offsets = parse_xref(data)
        for raw in _item_dicts(data):
            target = int(re.search(rb"/Dest \[\s*(\d+)\s+0\s+R", raw).group(1))
            page = object_bytes(data, offsets[target])
            self.assertIn(b"/Type /Page ", page, raw)

    def test_tagged_dests_point_at_sect_elements(self) -> None:
        data = _build_tagged()
        offsets = parse_xref(data)
        for raw in _item_dicts(data):
            target = int(re.search(rb"/Dest \[\s*(\d+)\s+0\s+R", raw).group(1))
            elem = object_bytes(data, offsets[target])
            self.assertIn(b"/Type /StructElem", elem, raw)
            self.assertIn(b"/S /Sect", elem, raw)
            # The /Sect element knows the page it navigates to.
            self.assertRegex(elem, rb"/Pg\s+(\d+)\s+0\s+R")

    def test_xyz_dest_with_null_zoom_and_fit_dest(self) -> None:
        builder = DocumentBuilder(created=FIXED_CREATED, outlines=True)
        flow = builder.flow()
        flow.text("Heading", size=16)
        flow.paragraph(_BODY, size=11)
        builder.add_outline("Top", page_index=0, y=400)
        builder.add_outline("Fit", page_index=1)
        data = builder.render()
        items = _item_dicts(data)
        by_title = {
            re.search(_ITEM_RE, raw).group(1).decode(): raw for raw in items
        }
        self.assertIn(b"/Dest [", by_title["Top"])
        self.assertRegex(by_title["Top"], rb"/XYZ\s+0\s+441\.89\s+null")
        self.assertRegex(by_title["Fit"], rb"/Dest \[\s*\d+\s+0\s+R /Fit\]")


class TestCatalogEntries(unittest.TestCase):
    def test_catalog_outlines_and_page_mode(self) -> None:
        data = _build_tagged(page_mode="UseOutlines")
        offsets = parse_xref(data)
        catalog_id = find_object_with(data, b"/Type /Catalog", offsets)
        catalog = object_bytes(data, offsets[catalog_id])
        self.assertRegex(catalog, rb"/Outlines\s+(\d+)\s+0\s+R")
        self.assertIn(b"/PageMode /UseOutlines", catalog)

    def test_no_page_mode_when_omitted(self) -> None:
        data = _build_tagged()
        offsets = parse_xref(data)
        catalog_id = find_object_with(data, b"/Type /Catalog", offsets)
        catalog = object_bytes(data, offsets[catalog_id])
        self.assertIn(b"/Outlines", catalog)
        self.assertNotIn(b"/PageMode", catalog)

    def test_outlines_off_by_default(self) -> None:
        builder = DocumentBuilder(created=FIXED_CREATED)
        flow = builder.flow()
        flow.text("plain", size=12)
        data = builder.render()
        self.assertNotIn(b"/Outlines", data)
        self.assertNotIn(b"/PageMode", data)
        self.assertNotIn(b"/Type /Outlines", data)

    def test_fixture_deterministic(self) -> None:
        first = _phase7_bookmarks_document()
        second = _phase7_bookmarks_document()
        self.assertEqual(first, second)


class TestBuilderErrors(unittest.TestCase):
    def test_add_outline_requires_flag(self) -> None:
        builder = DocumentBuilder(created=FIXED_CREATED)
        builder.flow()
        with self.assertRaises(ValueError):
            builder.add_outline("No", page_index=0)

    def test_add_outline_rejects_missing_page(self) -> None:
        builder = DocumentBuilder(created=FIXED_CREATED, outlines=True)
        builder.flow()
        with self.assertRaises(ValueError):
            builder.add_outline("No", page_index=3)


class TestNoComplyVariant(unittest.TestCase):
    def test_nocomply_keeps_info_and_page_dests(self) -> None:
        builder = DocumentBuilder(created=FIXED_CREATED, outlines=True)
        flow = builder.flow()
        flow.text("Heading", size=16)
        flow.paragraph(_BODY, size=11)
        _add_tree(builder)
        data = builder.render()
        self.assertIn(b"/Info", trailer_dict_bytes(data))
        offsets = parse_xref(data)
        for raw in _item_dicts(data):
            target = int(re.search(rb"/Dest \[\s*(\d+)\s+0\s+R", raw).group(1))
            self.assertIn(b"/Type /Page ", object_bytes(data, offsets[target]))


if __name__ == "__main__":
    unittest.main()
