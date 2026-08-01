"""Unit tests for the PDF/UA-2 claim wiring (catalog, XMP, namespace, pages).

Builds dual-mode documents (mode_pdfa4 + mode_pdfua2) and verifies, from
the emitted bytes: the catalog entries (/Lang, /MarkInfo, /StructTreeRoot,
/ViewerPreferences, /Metadata), the XMP pdfuaid identification plus the
pdfaExtension schema registration, the PDF 2.0 Namespace object, the
StructTreeRoot dictionary, page /StructParents (present only when the page
has MCIDs) and the untagged path staying free of UA-2 catalog entries.
"""

from __future__ import annotations

import datetime
import re
import unittest

from engine import DocumentBuilder
from engine.tests.helpers import (
    find_object_with,
    object_bytes,
    parse_xref,
    stream_bytes,
    trailer_dict_bytes,
)

FIXED_CREATED = datetime.datetime(2026, 8, 1, 12, 0, 0)

_LANG_RE = rb"/Lang \((.+?)\)"
_MARKINFO_RE = rb"/MarkInfo << /Marked true >>"
_VIEWERPREFS_RE = rb"/ViewerPreferences << /DisplayDocTitle true >>"
_STRUCTTREEROOT_RE = rb"/StructTreeRoot (\d+) 0 R"


def _dual_document(**kwargs) -> bytes:
    builder = DocumentBuilder(
        created=FIXED_CREATED,
        mode_pdfa4=True,
        mode_pdfua2=True,
        title="Dual mode document",
        **kwargs,
    )
    flow = builder.flow()
    flow.text("Dual mode heading", size=16)
    flow.paragraph("Dual mode body paragraph text.", size=11)
    return builder.render()


def _untagged_document() -> bytes:
    builder = DocumentBuilder(created=FIXED_CREATED)
    flow = builder.flow()
    flow.text("plain", size=12)
    return builder.render()


class TestCatalogUAEntries(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _dual_document()
        self.offsets = parse_xref(self.data)
        catalog_id = find_object_with(self.data, b"/Type /Catalog", self.offsets)
        self.catalog = object_bytes(self.data, self.offsets[catalog_id])

    def test_lang_present(self) -> None:
        self.assertRegex(self.catalog, _LANG_RE)

    def test_markinfo_marked_true(self) -> None:
        self.assertIn(b"/MarkInfo << /Marked true >>", self.catalog)

    def test_struct_tree_root_reference(self) -> None:
        match = re.search(_STRUCTTREEROOT_RE, self.catalog)
        self.assertIsNotNone(match)
        root_id = int(match.group(1))
        root = object_bytes(self.data, self.offsets[root_id])
        self.assertIn(b"/Type /StructTreeRoot", root)

    def test_viewer_preferences_display_doc_title(self) -> None:
        self.assertRegex(self.catalog, _VIEWERPREFS_RE)

    def test_metadata_present(self) -> None:
        self.assertRegex(self.catalog, rb"/Metadata (\d+) 0 R")

    def test_pdfa_entries_kept_in_dual_mode(self) -> None:
        self.assertRegex(self.catalog, rb"/OutputIntents \[\s*\d+ 0 R\s*\]")

    def test_untagged_catalog_has_no_ua_entries(self) -> None:
        data = _untagged_document()
        offsets = parse_xref(data)
        catalog_id = find_object_with(data, b"/Type /Catalog", offsets)
        catalog = object_bytes(data, offsets[catalog_id])
        self.assertNotIn(b"/Lang", catalog)
        self.assertNotIn(b"/MarkInfo", catalog)
        self.assertNotIn(b"/StructTreeRoot", catalog)
        self.assertNotIn(b"/ViewerPreferences", catalog)
        self.assertNotIn(b"/Metadata", catalog)


class TestXMPUAIdentification(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _dual_document()
        self.offsets = parse_xref(self.data)
        catalog_id = find_object_with(self.data, b"/Type /Catalog", self.offsets)
        catalog = object_bytes(self.data, self.offsets[catalog_id])
        metadata_id = int(re.search(rb"/Metadata (\d+) 0 R", catalog).group(1))
        # The metadata stream is stored uncompressed (no /Filter).
        self.xmp = stream_bytes(self.data, self.offsets[metadata_id])

    def test_pdfuaid_part_2_rev_2024(self) -> None:
        self.assertIn(b"<pdfuaid:part>2</pdfuaid:part>", self.xmp)
        self.assertIn(b"<pdfuaid:rev>2024</pdfuaid:rev>", self.xmp)

    def test_pdfuaid_namespace_declared(self) -> None:
        self.assertIn(
            b'xmlns:pdfuaid="http://www.aiim.org/pdfua/ns/id/"', self.xmp
        )

    def test_pdfa_identification_kept_in_dual_mode(self) -> None:
        self.assertIn(b"<pdfaid:part>4</pdfaid:part>", self.xmp)
        self.assertIn(b"<pdfaid:rev>2020</pdfaid:rev>", self.xmp)

    def test_pdfa_extension_registers_pdfuaid_schema(self) -> None:
        self.assertIn(
            b'xmlns:pdfaExtension="http://www.aiim.org/pdfa/ns/extension/"', self.xmp
        )
        self.assertIn(
            b"<pdfaExtension:namespaceURI>http://www.aiim.org/pdfua/ns/id/"
            b"</pdfaExtension:namespaceURI>",
            self.xmp,
        )
        self.assertIn(b"<pdfaExtension:prefix>pdfuaid</pdfaExtension:prefix>", self.xmp)
        self.assertIn(b"<pdfaExtension:name>part</pdfaExtension:name>", self.xmp)
        self.assertIn(b"<pdfaExtension:name>rev</pdfaExtension:name>", self.xmp)
        self.assertIn(b"<pdfaExtension:valueType>Integer</pdfaExtension:valueType>", self.xmp)

    def test_dc_title_present_as_lang_alt(self) -> None:
        self.assertIn(b"<dc:title><rdf:Alt>", self.xmp)
        self.assertIn(b'<rdf:li xml:lang="x-default">Dual mode document</rdf:li>', self.xmp)

    def test_ua_only_document_omits_pdfaid(self) -> None:
        builder = DocumentBuilder(
            created=FIXED_CREATED, mode_pdfua2=True, title="UA only"
        )
        flow = builder.flow()
        flow.text("UA only heading", size=16)
        data = builder.render()
        offsets = parse_xref(data)
        catalog_id = find_object_with(data, b"/Type /Catalog", offsets)
        catalog = object_bytes(data, offsets[catalog_id])
        metadata_id = int(re.search(rb"/Metadata (\d+) 0 R", catalog).group(1))
        xmp = stream_bytes(data, offsets[metadata_id])
        self.assertIn(b"<pdfuaid:part>2</pdfuaid:part>", xmp)
        self.assertNotIn(b"<pdfaid:part>4</pdfaid:part>", xmp)


class TestNamespaceAndStructTreeRoot(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _dual_document()
        self.offsets = parse_xref(self.data)
        catalog_id = find_object_with(self.data, b"/Type /Catalog", self.offsets)
        self.catalog = object_bytes(self.data, self.offsets[catalog_id])

    def test_namespace_object(self) -> None:
        ns_id = find_object_with(self.data, b"/Type /Namespace", self.offsets)
        ns = object_bytes(self.data, self.offsets[ns_id])
        self.assertIn(b"/Type /Namespace", ns)
        self.assertIn(b"/NS (http://iso.org/pdf2/ssn)", ns)

    def test_struct_tree_root_keys(self) -> None:
        match = re.search(_STRUCTTREEROOT_RE, self.catalog)
        root = object_bytes(self.data, self.offsets[int(match.group(1))])
        self.assertIn(b"/Type /StructTreeRoot", root)
        self.assertRegex(root, rb"/K\s+\d+\s+0\s+R")
        self.assertRegex(root, rb"/ParentTree\s+\d+\s+0\s+R")
        ns_id = find_object_with(self.data, b"/Type /Namespace", self.offsets)
        self.assertIn(b"/Namespaces [%d 0 R]" % ns_id, root)

    def test_document_element_references_namespace(self) -> None:
        match = re.search(_STRUCTTREEROOT_RE, self.catalog)
        root = object_bytes(self.data, self.offsets[int(match.group(1))])
        doc_id = int(re.search(rb"/K\s+(\d+)\s+0\s+R", root).group(1))
        doc = object_bytes(self.data, self.offsets[doc_id])
        self.assertIn(b"/S /Document ", doc)
        ns_id = find_object_with(self.data, b"/Type /Namespace", self.offsets)
        self.assertIn(b"/NS %d 0 R" % ns_id, doc)


class TestPageStructParents(unittest.TestCase):
    def test_struct_parents_present_when_mcids_exist(self) -> None:
        data = _dual_document()
        offsets = parse_xref(data)
        page_id = find_object_with(data, b"/Type /Page /", offsets)
        page = object_bytes(data, offsets[page_id])
        self.assertIn(b"/StructParents 0", page)

    def test_struct_parents_absent_when_no_mcids(self) -> None:
        builder = DocumentBuilder(
            created=FIXED_CREATED, mode_pdfa4=True, mode_pdfua2=True
        )
        builder.flow()  # tagged document with no drawn content
        data = builder.render()
        offsets = parse_xref(data)
        for obj_id, offset in sorted(offsets.items()):
            raw = object_bytes(data, offset)
            if b"/Type /Page /" in raw:
                self.assertNotIn(b"/StructParents", raw)

    def test_untagged_pages_have_no_struct_parents(self) -> None:
        data = _untagged_document()
        self.assertNotIn(b"/StructParents", data)

    def test_multipage_pages_carry_per_page_keys(self) -> None:
        builder = DocumentBuilder(
            created=FIXED_CREATED,
            mode_pdfa4=True,
            mode_pdfua2=True,
            margins=__import__("engine").PageMargins(48, 48, 48, 48),
        )
        flow = builder.flow()
        flow.paragraph("\n".join(["Word " * 40 + "end"] * 60), size=11)
        data = builder.render()
        offsets = parse_xref(data)
        keys = []
        for obj_id, offset in sorted(offsets.items()):
            raw = object_bytes(data, offset)
            match = re.search(rb"/StructParents (\d+)", raw)
            if b"/Type /Page /" in raw and match is not None:
                keys.append(int(match.group(1)))
        self.assertEqual(keys, sorted(keys))
        self.assertEqual(len(keys), len(set(keys)))


class TestTrailerInDualMode(unittest.TestCase):
    def test_no_info_in_dual_mode_trailer(self) -> None:
        data = _dual_document()
        self.assertNotIn(b"/Info", trailer_dict_bytes(data))
        self.assertIn(b"/Root", trailer_dict_bytes(data))


if __name__ == "__main__":
    unittest.main()
