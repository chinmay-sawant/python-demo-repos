"""Unit tests for the tagged structure tree (engine.structure, engine.layout).

Builds tagged documents through DocumentBuilder (mode_pdfa4 + mode_pdfua2)
and verifies, from the emitted bytes: BDC/EMC marked content with per-page
MCIDs, the ParentTree number tree (page -> MCID-indexed element refs), the
StructElem parent chain (Document -> H1), TH vs TD ownership (cells own the
MCIDs, not the TR), /Pg on leaf elements, the multi-page TR /Pg consistency,
the header attributes (/ID, /Scope, /Headers) and the untagged path staying
free of all structure objects.
"""

from __future__ import annotations

import datetime
import re
import unittest
import zlib

from engine import DocumentBuilder, PageMargins, TableLayout
from engine.fixtures import _gradient_png
from engine.tests.helpers import (
    all_objects_with,
    find_object_with,
    inflate_stream,
    object_bytes,
    parse_xref,
)

FIXED_CREATED = datetime.datetime(2026, 8, 1, 12, 0, 0)

_MCID_BDC_RE = re.compile(rb"/\w+ << /MCID (\d+) >> BDC")
_ARTIFACT_BDC_RE = re.compile(rb"/Artifact << /Type /Layout >> BDC")


def _tagged_document(**kwargs) -> bytes:
    builder = DocumentBuilder(
        created=FIXED_CREATED, mode_pdfa4=True, mode_pdfua2=True, **kwargs
    )
    flow = builder.flow()
    flow.text("Structure test heading", size=16)
    flow.paragraph(
        "A body paragraph with enough words to wrap onto a couple of lines "
        "and exercise the per-page MCID bookkeeping.",
        size=11,
    )
    return builder.render()


def _tagged_table_document(n_rows: int = 60, **kwargs) -> bytes:
    builder = DocumentBuilder(
        created=FIXED_CREATED,
        mode_pdfa4=True,
        mode_pdfua2=True,
        margins=PageMargins(48, 48, 48, 48),
        **kwargs,
    )
    flow = builder.flow()
    header = ["SKU", "Product", "Category", "Price", "Stock"]
    rows = [
        ["SKU-%03d" % i, "Widget", "A", "%.2f" % (9.99 + i), str(100 + i)]
        for i in range(n_rows)
    ]
    flow.table(
        TableLayout(col_widths=[60, 90, 90, 90, 60], header=header, rows=rows, size=9)
    )
    return builder.render()


def _page_streams(data: bytes) -> list:
    """One inflated content-stream body per page, in page order."""
    offsets = parse_xref(data)
    streams = []
    for page_id in all_objects_with(data, offsets, b"/Type /Page /"):
        body = object_bytes(data, offsets[page_id])
        match = re.search(rb"/Contents\s+(\d+)\s+0\s+R", body)
        if match is not None:
            streams.append(inflate_stream(data, offsets[int(match.group(1))]))
    return streams


def _structure_objects(data: bytes) -> dict:
    """Map /S type -> list of element object bodies (e.g. {"H1": [...]})."""
    offsets = parse_xref(data)
    found = {}
    for obj_id, offset in sorted(offsets.items()):
        raw = object_bytes(data, offset)
        match = re.search(rb"/Type /StructElem ", raw)
        if match is None:
            continue
        type_match = re.search(rb"/S /(\w+) ", raw)
        if type_match is not None:
            found.setdefault(type_match.group(1).decode(), []).append(raw)
    return found


class TestMarkedContent(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _tagged_document()
        self.streams = _page_streams(self.data)

    def test_bdc_emc_with_mcids_emitted(self) -> None:
        self.assertEqual(len(self.streams), 1)
        ops = self.streams[0]
        self.assertIn(b"/H1 << /MCID 0 >> BDC", ops)
        self.assertIn(b"/P << /MCID 1 >> BDC", ops)
        self.assertEqual(ops.count(b"BDC"), ops.count(b"EMC"))
        self.assertIn(b"EMC\n", ops)

    def test_mcid_counter_resets_per_page(self) -> None:
        data = _tagged_table_document(n_rows=40)
        for stream in _page_streams(data):
            mcids = [int(m) for m in _MCID_BDC_RE.findall(stream)]
            self.assertEqual(mcids, sorted(mcids))
            self.assertEqual(min(mcids), 0)
            self.assertEqual(set(mcids), set(range(len(mcids))))

    def test_untagged_document_has_no_marked_content(self) -> None:
        builder = DocumentBuilder(created=FIXED_CREATED)
        flow = builder.flow()
        flow.text("plain", size=12)
        data = builder.render()
        for stream in _page_streams(data):
            self.assertNotIn(b"BDC", stream)
            self.assertNotIn(b"EMC", stream)
            self.assertNotIn(b"/StructElem", data)

    def test_table_decorations_are_artifacts(self) -> None:
        data = _tagged_table_document(n_rows=4)
        ops = b"\n".join(_page_streams(data))
        self.assertGreater(len(_ARTIFACT_BDC_RE.findall(ops)), 0)


class TestParentTree(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _tagged_table_document(n_rows=40)
        self.offsets = parse_xref(self.data)
        root_id = find_object_with(self.data, b"/Type /StructTreeRoot", self.offsets)
        self.root = object_bytes(self.data, self.offsets[root_id])
        match = re.search(rb"/ParentTree\s+(\d+)\s+0\s+R", self.root)
        self.parent_tree_id = int(match.group(1))

    def test_nums_maps_pages_to_mcid_indexed_refs(self) -> None:
        nums = object_bytes(self.data, self.offsets[self.parent_tree_id])
        match = re.search(rb"/Nums \[\s*(.*?)\]\s*>>", nums, re.S)
        self.assertIsNotNone(match, nums)
        body = match.group(1)
        self.assertTrue(body.startswith(b"0 ["), body[:40])
        self.assertIn(b"] 1 [", body)
        for entry in re.finditer(rb"(\d+)\s*\[\s*((?:\d+ 0 R\s*)+)\]", body):
            page_key, refs = int(entry.group(1)), entry.group(2)
            self.assertIn(page_key, (0, 1))

    def test_parent_tree_points_at_td_th_not_tr(self) -> None:
        nums = object_bytes(self.data, self.offsets[self.parent_tree_id])
        for ref in re.findall(rb"(\d+) 0 R", nums):
            raw = object_bytes(self.data, self.offsets[int(ref)])
            self.assertRegex(raw, rb"/S /(TD|TH) ")
            self.assertNotIn(b"/S /TR ", raw)


class TestStructElemChain(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _tagged_document()
        self.offsets = parse_xref(self.data)
        root_id = find_object_with(self.data, b"/Type /StructTreeRoot", self.offsets)
        self.root = object_bytes(self.data, self.offsets[root_id])

    def test_document_element_parents_to_tree_root(self) -> None:
        match = re.search(rb"/K\s+(\d+)\s+0\s+R", self.root)
        doc_id = int(match.group(1))
        doc = object_bytes(self.data, self.offsets[doc_id])
        self.assertIn(b"/S /Document ", doc)
        self.assertIn(b"/NS ", doc)
        parent_match = re.search(rb"/P\s+(\d+)\s+0\s+R", doc)
        root_obj_id = find_object_with(self.data, b"/Type /StructTreeRoot", self.offsets)
        self.assertEqual(int(parent_match.group(1)), root_obj_id)

    def test_h1_parent_chain_document_to_tree_root(self) -> None:
        elements = _structure_objects(self.data)
        self.assertEqual(len(elements["H1"]), 1)
        h1 = elements["H1"][0]
        doc_id = int(re.search(rb"/P\s+(\d+)\s+0\s+R", h1).group(1))
        doc = object_bytes(self.data, self.offsets[doc_id])
        self.assertIn(b"/S /Document ", doc)
        self.assertIn(b"/NS ", doc)

    def test_h1_owns_mcid_and_page(self) -> None:
        elements = _structure_objects(self.data)
        h1 = elements["H1"][0]
        self.assertIn(b"/K [0]", h1)
        self.assertRegex(h1, rb"/Pg\s+(\d+)\s+0\s+R")
        page_id = int(re.search(rb"/Pg\s+(\d+)\s+0\s+R", h1).group(1))
        page = object_bytes(self.data, self.offsets[page_id])
        self.assertIn(b"/Type /Page ", page)
        self.assertIn(b"/StructParents 0", page)


class TestTableStructure(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _tagged_table_document(n_rows=40)
        self.offsets = parse_xref(self.data)
        self.elements = _structure_objects(self.data)

    def test_th_td_distinction(self) -> None:
        self.assertTrue(any(b"/S /TH " in raw for raw in self.elements["TH"]))
        self.assertTrue(any(b"/S /TD " in raw for raw in self.elements["TD"]))
        # Header row cells are TH, body cells are TD.
        for raw in self.elements["TH"]:
            self.assertIn(b"/Scope /Column", raw)
        for raw in self.elements["TD"]:
            self.assertNotIn(b"/Scope", raw)

    def test_cells_reference_header_ids(self) -> None:
        th_ids = {
            re.search(rb"/ID \((\w+)\)", raw).group(1).decode()
            for raw in self.elements["TH"]
        }
        self.assertTrue(th_ids)
        for raw in self.elements["TD"]:
            header_match = re.search(rb"/Headers \[\s*\((\w+)\)\s*\]", raw)
            self.assertIsNotNone(header_match, raw)
            self.assertIn(header_match.group(1).decode(), th_ids)

    def test_pg_on_leaves(self) -> None:
        for tag in ("TH", "TD", "H1", "P"):
            for raw in self.elements.get(tag, []):
                self.assertRegex(raw, rb"/Pg\s+\d+\s+0\s+R", raw)

    def test_multipage_tr_pg_consistent_with_child_td(self) -> None:
        for raw in self.elements["TR"]:
            tr_page = re.search(rb"/Pg\s+(\d+)\s+0\s+R", raw)
            self.assertIsNotNone(tr_page, raw)
            tr_page = int(tr_page.group(1))
            for kid in re.findall(rb"(\d+) 0 R", raw):
                kid_raw = object_bytes(self.data, self.offsets[int(kid)])
                if b"/S /TD " not in kid_raw and b"/S /TH " not in kid_raw:
                    continue
                td_page = int(re.search(rb"/Pg\s+(\d+)\s+0\s+R", kid_raw).group(1))
                self.assertEqual(tr_page, td_page)

    def test_table_and_tr_hierarchy(self) -> None:
        tables = self.elements["Table"]
        self.assertEqual(len(tables), 1)
        kids_match = re.search(rb"/K \[\s*(.*?)\s*\]", tables[0], re.S)
        table_kids = [int(r) for r in re.findall(rb"(\d+) 0 R", kids_match.group(1))]
        self.assertGreater(len(table_kids), 1)
        for kid in table_kids:
            self.assertIn(b"/S /TR ", object_bytes(self.data, self.offsets[kid]))


class TestNamespaceObject(unittest.TestCase):
    def test_namespace_object_present(self) -> None:
        data = _tagged_document()
        offsets = parse_xref(data)
        ns_id = find_object_with(data, b"/Type /Namespace", offsets)
        ns = object_bytes(data, offsets[ns_id])
        self.assertIn(b"/NS (http://iso.org/pdf2/ssn)", ns)
        root_id = find_object_with(data, b"/Type /StructTreeRoot", offsets)
        root = object_bytes(data, offsets[root_id])
        self.assertIn(b"/Namespaces [%d 0 R]" % ns_id, root)


class TestLinkStructure(unittest.TestCase):
    def test_link_has_objr_with_annotation_and_page(self) -> None:
        builder = DocumentBuilder(
            created=FIXED_CREATED, mode_pdfa4=True, mode_pdfua2=True
        )
        flow = builder.flow()
        flow.text("Links", size=16)
        flow.link("Visit example", "https://example.com/", size=11)
        data = builder.render()
        offsets = parse_xref(data)
        links = _structure_objects(data)["Link"]
        self.assertEqual(len(links), 1)
        self.assertIn(b"/OBJR", links[0])
        self.assertRegex(links[0], rb"/Obj\s+(\d+)\s+0\s+R")
        self.assertRegex(links[0], rb"/Pg\s+(\d+)\s+0\s+R")
        annot_id = int(re.search(rb"/Obj\s+(\d+)\s+0\s+R", links[0]).group(1))
        annot = object_bytes(data, offsets[annot_id])
        self.assertIn(b"/Subtype /Link", annot)
        self.assertIn(b"/A << /S /URI /URI (https://example.com/)", annot)
        self.assertIn(b"/Rect [", annot)
        page_id = find_object_with(data, b"/Type /Page /", offsets)
        page = object_bytes(data, offsets[page_id])
        self.assertRegex(page, rb"/Annots \[\s*%d\s+0\s+R\s*\]" % annot_id)
        self.assertIn(b"/Tabs /S", page)


class TestArtifactHelper(unittest.TestCase):
    def test_content_stream_artifact_helpers(self) -> None:
        from engine.content import ContentStream
        from engine.write import N

        stream = ContentStream()
        stream.begin_artifact({N("Type"): N("Pagination")})
        stream.begin_text()
        stream.end_text()
        stream.end_marked_content()
        ops = stream.render()
        self.assertIn(b"/Artifact << /Type /Pagination >> BDC", ops)
        self.assertIn(b"EMC", ops)
        bare = ContentStream()
        bare.begin_artifact()
        self.assertIn(b"/Artifact BDC", bare.render())


if __name__ == "__main__":
    unittest.main()
