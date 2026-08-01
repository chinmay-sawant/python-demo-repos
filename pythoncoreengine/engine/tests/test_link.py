"""Unit tests for link annotations (phase 5 surface, phase 7.2 verification).

Phase 5 already ships ``/Subtype /Link`` annotations through
``flow.link`` (tagged ``Link`` StructElems with ``/OBJR`` kids, page
``/Annots`` and ``/Tabs /S``).  Phase 7.2 confirms the annotation
dictionary contract (/Subtype /Link, /Rect, /Border, /A << /S /URI
/URI (...) >>, /F 4), exercises multiple links on one page and verifies
the phase-5 link fixture is byte-unaffected by the phase-7 additions
(the fixture regenerates to exactly the same bytes as the checked-in
file, which is produced by the same dual-mode code path).
"""

from __future__ import annotations

import datetime
import re
import unittest

from engine import DocumentBuilder
from engine.fixtures import (
    DEFAULT_OUTPUT_DIR,
    _phase5_link_annot_document,
    generate_fixtures,
)
from engine.tests.helpers import (
    find_object_with,
    inflate_stream,
    object_bytes,
    parse_xref,
)

FIXED_CREATED = datetime.datetime(2026, 8, 1, 12, 0, 0)

_URI_ACTION_RE = rb"/A << /S /URI /URI \((.+?)\) >>"


def _build_links(n_links: int = 1, **kwargs) -> bytes:
    """A page with ``n_links`` link annotations via the builder API.

    ``flow.link`` only emits the ``/Link`` annotation under tagging
    (phase 5: untagged links are plain text); the builder's
    ``add_link_annotation`` is the annotation path that works in every
    mode.
    """
    builder = DocumentBuilder(created=FIXED_CREATED, **kwargs)
    flow = builder.flow()
    flow.text("Links", size=16)
    flow.paragraph("Body text giving the page real content.", size=11)
    for index in range(n_links):
        builder.add_link_annotation(
            0,
            [100.0, 700.0 - index * 20.0, 300.0, 716.0 - index * 20.0],
            "https://example.com/%d" % index,
        )
    return builder.render()


def _link_annotations(data: bytes) -> list:
    offsets = parse_xref(data)
    found = []
    for obj_id, offset in sorted(offsets.items()):
        raw = object_bytes(data, offset)
        if b"/Subtype /Link" in raw:
            found.append(raw)
    return found


class TestLinkAnnotationDict(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _build_links()
        self.offsets = parse_xref(self.data)
        self.annots = _link_annotations(self.data)

    def test_link_annotation_keys(self) -> None:
        self.assertEqual(len(self.annots), 1)
        annot = self.annots[0]
        self.assertIn(b"/Type /Annot", annot)
        self.assertIn(b"/Subtype /Link", annot)
        self.assertIn(b"/Rect [", annot)
        self.assertIn(b"/Border [0 0 0]", annot)
        self.assertIn(b"/F 4", annot)
        match = re.search(_URI_ACTION_RE, annot)
        self.assertIsNotNone(match, annot)
        self.assertEqual(match.group(1), b"https://example.com/0")
        self.assertIn(b"/S /URI", annot)

    def test_page_annots_and_tabs(self) -> None:
        page_id = find_object_with(self.data, b"/Type /Page /", self.offsets)
        page = object_bytes(self.data, self.offsets[page_id])
        annot_num = next(
            obj_id
            for obj_id, offset in sorted(self.offsets.items())
            if b"/Subtype /Link" in object_bytes(self.data, offset)
        )
        self.assertRegex(page, rb"/Annots \[\s*%d\s+0\s+R\s*\]" % annot_num)
        self.assertIn(b"/Tabs /S", page)
        self.assertIn(b"/Rect [", self.annots[0])

    def test_untagged_link_has_no_structure(self) -> None:
        self.assertNotIn(b"/StructParent", self.annots[0])
        self.assertNotIn(b"/Type /StructElem", self.data)


class TestMultipleLinks(unittest.TestCase):
    def test_two_links_on_one_page(self) -> None:
        data = _build_links(n_links=2)
        annots = _link_annotations(data)
        self.assertEqual(len(annots), 2)
        uris = [re.search(_URI_ACTION_RE, a).group(1) for a in annots]
        self.assertEqual(sorted(uris), [b"https://example.com/0", b"https://example.com/1"])
        offsets = parse_xref(data)
        page_id = find_object_with(data, b"/Type /Page /", offsets)
        page = object_bytes(data, offsets[page_id])
        nums = [int(m) for m in re.findall(rb"(\d+) 0 R", page)]
        annot_nums = [
            int(m)
            for m in re.findall(rb"(\d+) 0 R", b" ".join(annots))
        ]
        for num in annot_nums:
            self.assertIn(num, nums)

    def test_two_tagged_links_get_separate_elements(self) -> None:
        builder = DocumentBuilder(
            created=FIXED_CREATED, mode_pdfa4=True, mode_pdfua2=True, title="L"
        )
        flow = builder.flow()
        flow.text("Links", size=16)
        flow.link("Visit example 0", "https://example.com/0", size=11)
        flow.link("Visit example 1", "https://example.com/1", size=11)
        data = builder.render()
        self.assertIn(b"/Type /StructElem", data)
        self.assertEqual(len(re.findall(rb"/S /Link ", data)), 2)
        self.assertEqual(len(_link_annotations(data)), 2)
        self.assertEqual(data.count(b"/OBJR"), 2)


class TestPhase5LinkFixtureUnaffected(unittest.TestCase):
    def test_fixture_file_matches_regeneration(self) -> None:
        generate_fixtures(DEFAULT_OUTPUT_DIR)
        path = DEFAULT_OUTPUT_DIR / "phase5_link_annot.pdf"
        self.assertEqual(path.read_bytes(), _phase5_link_annot_document())

    def test_fixture_carries_link_contract(self) -> None:
        data = _phase5_link_annot_document()
        annot = _link_annotations(data)[0]
        self.assertIn(b"/Subtype /Link", annot)
        self.assertIn(b"/Border [0 0 0]", annot)
        self.assertRegex(
            annot, rb"/A << /S /URI /URI \(https://example\.com/pythoncoreengine\)"
        )
        self.assertIn(b"/Tabs /S", data)
        self.assertIn(b"/Annots [", data)

    def test_phase5_page_stream_still_inflates(self) -> None:
        data = _phase5_link_annot_document()
        offsets = parse_xref(data)
        page_id = find_object_with(data, b"/Type /Page /", offsets)
        page = object_bytes(data, offsets[page_id])
        content_id = int(re.search(rb"/Contents\s+(\d+)\s+0\s+R", page).group(1))
        ops = inflate_stream(data, offsets[content_id])
        self.assertIn(b"/Link << /MCID", ops)


if __name__ == "__main__":
    unittest.main()
