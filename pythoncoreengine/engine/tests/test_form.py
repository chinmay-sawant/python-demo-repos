"""Unit tests for the AcroForm / widget machinery (engine.form, engine.doc).

Builds documents through DocumentBuilder with ``forms=True`` and verifies,
from the emitted bytes: the catalog ``/AcroForm`` dictionary (/Fields,
/NeedAppearances false, /DA, /DR with the embedded font), the merged
field/widget annotation keys (/Subtype /Widget, /FT, /Rect, /T, /V, /P,
/F, /DA, /AP, /Contents), the appearance streams (a text widget's /N
stream inflates to BT/Tf/Td/Tj/ET and contains the field value's CID
glyphs; a checkbox's /N is a subdictionary with /Yes and /Off state
streams), the tagged-path wiring (widget /StructParent keys resolve in
the ParentTree to ``/Form`` StructElems that own the widget via /OBJR)
and the untagged path staying free of all structure entries.
"""

from __future__ import annotations

import datetime
import re
import unittest

from engine import DocumentBuilder
from engine.fixtures import _phase7_form_document
from engine.tests.helpers import (
    find_object_with,
    inflate_stream,
    object_bytes,
    parse_xref,
)

FIXED_CREATED = datetime.datetime(2026, 8, 1, 12, 0, 0)

_WIDGET_RE = rb"/Subtype /Widget"


def _build_form(checked: bool = True, **kwargs) -> bytes:
    builder = DocumentBuilder(created=FIXED_CREATED, forms=True, **kwargs)
    flow = builder.flow()
    flow.text("Form", size=16)
    flow.paragraph("Fill in the fields below.", size=11)
    builder.add_text_field(
        "ClientName", "Jane Doe", page_index=0, x=100, y=150, width=200, height=18, size=10
    )
    builder.add_checkbox(
        "Consent", page_index=0, x=100, y=180, width=12, height=12, checked=checked
    )
    return builder.render()


def _widgets(data: bytes) -> list:
    """All widget annotation bodies in object order."""
    offsets = parse_xref(data)
    return [
        object_bytes(data, offset)
        for obj_id, offset in sorted(offsets.items())
        if b"/Subtype /Widget" in object_bytes(data, offset)
    ]


class TestAcroFormCatalog(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _build_form()
        self.offsets = parse_xref(self.data)
        self.acroform = object_bytes(
            self.data,
            self.offsets[find_object_with(self.data, b"/Type /AcroForm", self.offsets)],
        )

    def test_acroform_keys(self) -> None:
        self.assertIn(b"/Type /AcroForm", self.acroform)
        fields_match = re.search(rb"/Fields \[\s*(\d+)\s+0\s+R\s+(\d+)\s+0\s+R\s*\]", self.acroform)
        self.assertIsNotNone(fields_match, self.acroform)
        self.assertIn(b"/NeedAppearances false", self.acroform)
        self.assertIn(b"/DA (/F1 10 Tf 0 g)", self.acroform)
        self.assertIn(b"/DR << /Font << /F1", self.acroform)

    def test_fields_reference_widgets(self) -> None:
        catalog_id = find_object_with(self.data, b"/Type /Catalog", self.offsets)
        catalog = object_bytes(self.data, self.offsets[catalog_id])
        acroform_id = int(re.search(rb"/AcroForm\s+(\d+)\s+0\s+R", catalog).group(1))
        self.assertEqual(
            acroform_id, find_object_with(self.data, b"/Type /AcroForm", self.offsets)
        )
        for widget in _widgets(self.data):
            self.assertIn(b"/Type /Annot", widget)

    def test_no_acroform_when_flag_off(self) -> None:
        builder = DocumentBuilder(created=FIXED_CREATED)
        flow = builder.flow()
        flow.text("plain", size=12)
        data = builder.render()
        self.assertNotIn(b"/AcroForm", data)
        self.assertNotIn(b"/Subtype /Widget", data)


class TestTextWidget(unittest.TestCase):
    def setUp(self) -> None:
        # Embed mode: the appearance stream draws the value as subset CIDs.
        self.data = _build_form(mode_embed_fonts=True)
        self.offsets = parse_xref(self.data)
        self.widgets = _widgets(self.data)

    def test_text_widget_keys(self) -> None:
        text_widget = next(w for w in self.widgets if b"/FT /Tx" in w)
        self.assertIn(b"/Subtype /Widget", text_widget)
        self.assertIn(b"/FT /Tx", text_widget)
        self.assertIn(b"/Rect [100 ", text_widget)
        self.assertIn(b"/T (ClientName)", text_widget)
        self.assertIn(b"/V (Jane Doe)", text_widget)
        self.assertRegex(text_widget, rb"/P\s+(\d+)\s+0\s+R")
        self.assertIn(b"/F 4", text_widget)
        self.assertIn(b"/DA (/F1 10 Tf 0 g)", text_widget)
        self.assertIn(b"/Contents (ClientName)", text_widget)
        self.assertRegex(text_widget, rb"/AP << /N (\d+) 0 R >>")

    def test_text_widget_page_reference(self) -> None:
        text_widget = next(w for w in self.widgets if b"/FT /Tx" in w)
        page_ref = int(re.search(rb"/P\s+(\d+)\s+0\s+R", text_widget).group(1))
        self.assertIn(
            b"/Type /Page ",
            object_bytes(self.data, self.offsets[page_ref]),
        )

    def test_text_appearance_stream_parses_and_shows_value(self) -> None:
        text_widget = next(w for w in self.widgets if b"/FT /Tx" in w)
        ap_ref = int(re.search(rb"/N (\d+) 0 R", text_widget).group(1))
        ap_body = object_bytes(self.data, self.offsets[ap_ref])
        self.assertIn(b"/Type /XObject", ap_body)
        self.assertIn(b"/Subtype /Form", ap_body)
        self.assertIn(b"/BBox [0 0 200 18]", ap_body)
        self.assertRegex(ap_body, rb"/Resources << /Font << /F1 (\d+) 0 R >> >>")
        ops = inflate_stream(self.data, self.offsets[ap_ref])
        self.assertIn(b"BT\n/F1 10 Tf\n2 2 Td\n", ops)
        self.assertIn(b" Tj\nET", ops)
        # The value's glyphs are drawn as Identity-H hex CIDs.
        self.assertRegex(ops, rb"<[0-9A-F]{32}> Tj")

    def test_value_glyphs_land_in_embedded_subset(self) -> None:
        text_widget = next(w for w in self.widgets if b"/FT /Tx" in w)
        ap_ref = int(re.search(rb"/N (\d+) 0 R", text_widget).group(1))
        ops = inflate_stream(self.data, self.offsets[ap_ref])
        hex_digits = re.search(rb"<([0-9A-F]{32})> Tj", ops).group(1)
        self.assertGreater(int(hex_digits, 16), 0)

    def test_plain_mode_appearance_uses_literal_text(self) -> None:
        data = _build_form()
        text_widget = next(w for w in _widgets(data) if b"/FT /Tx" in w)
        ap_ref = int(re.search(rb"/N (\d+) 0 R", text_widget).group(1))
        offsets = parse_xref(data)
        ops = inflate_stream(data, offsets[ap_ref])
        self.assertIn(b"(Jane Doe) Tj", ops)


class TestCheckboxWidget(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _build_form(checked=True)
        self.offsets = parse_xref(self.data)
        self.widgets = _widgets(self.data)

    def test_checkbox_widget_keys(self) -> None:
        btn = next(w for w in self.widgets if b"/FT /Btn" in w)
        self.assertIn(b"/FT /Btn", btn)
        self.assertIn(b"/T (Consent)", btn)
        self.assertIn(b"/V /Yes", btn)
        self.assertIn(b"/AS /Yes", btn)
        self.assertIn(b"/Contents (Consent)", btn)
        self.assertIn(b"/F 4", btn)
        self.assertRegex(btn, rb"/AP << /N << /Yes (\d+) 0 R /Off (\d+) 0 R >> >>")

    def test_checkbox_state_appearances(self) -> None:
        btn = next(w for w in self.widgets if b"/FT /Btn" in w)
        yes_ref = int(re.search(rb"/Yes (\d+) 0 R", btn).group(1))
        off_ref = int(re.search(rb"/Off (\d+) 0 R", btn).group(1))
        yes_ops = inflate_stream(self.data, self.offsets[yes_ref])
        off_ops = inflate_stream(self.data, self.offsets[off_ref])
        self.assertIn(b"re\nf", yes_ops)  # filled box
        self.assertIn(b"re\nS", off_ops)  # outlined box
        for ref in (yes_ref, off_ref):
            body = object_bytes(self.data, self.offsets[ref])
            self.assertIn(b"/Subtype /Form", body)
            self.assertIn(b"/BBox [0 0 12 12]", body)

    def test_unchecked_checkbox_value_is_off(self) -> None:
        data = _build_form(checked=False)
        btn = next(w for w in _widgets(data) if b"/FT /Btn" in w)
        self.assertIn(b"/V /Off", btn)
        self.assertIn(b"/AS /Off", btn)


class TestFormTagging(unittest.TestCase):
    def setUp(self) -> None:
        self.data = _build_form(mode_pdfa4=True, mode_pdfua2=True, title="Form")
        self.offsets = parse_xref(self.data)
        self.widgets = _widgets(self.data)
        root_id = find_object_with(self.data, b"/Type /StructTreeRoot", self.offsets)
        root = object_bytes(self.data, self.offsets[root_id])
        parent_tree_id = int(re.search(rb"/ParentTree\s+(\d+)\s+0\s+R", root).group(1))
        self.parent_tree = object_bytes(self.data, self.offsets[parent_tree_id])

    def test_widgets_have_struct_parent_keys(self) -> None:
        for widget in self.widgets:
            self.assertRegex(widget, rb"/StructParent (\d+)")

    def test_struct_parents_resolve_to_form_elements(self) -> None:
        for widget in self.widgets:
            key = int(re.search(rb"/StructParent (\d+)", widget).group(1))
            # The ParentTree maps key -> the enclosing /Form StructElem.
            match = re.search(
                rb"%d\s+(\d+)\s+0\s+R" % key, self.parent_tree
            )
            self.assertIsNotNone(match, (key, self.parent_tree))
            elem = object_bytes(self.data, self.offsets[int(match.group(1))])
            self.assertIn(b"/Type /StructElem", elem)
            self.assertIn(b"/S /Form", elem)
            widget_num = int(re.search(rb"/P\s+(\d+)\s+0\s+R", widget).group(1))
            # The Form element owns exactly this widget through /OBJR.
            self.assertIn(b"/OBJR", elem)
            self.assertIn(b"/Pg %d 0 R" % widget_num, elem)

    def test_annotation_keys_do_not_collide_with_page_keys(self) -> None:
        page_keys = set()
        for key in re.findall(rb"(\d+)\s+\[\s*\d+ 0 R", self.parent_tree):
            page_keys.add(int(key))
        for widget in self.widgets:
            key = int(re.search(rb"/StructParent (\d+)", widget).group(1))
            self.assertNotIn(key, page_keys)

    def test_page_has_annots_and_tabs(self) -> None:
        page_id = find_object_with(self.data, b"/Type /Page /", self.offsets)
        page = object_bytes(self.data, self.offsets[page_id])
        self.assertIn(b"/Annots [", page)
        self.assertIn(b"/Tabs /S", page)

    def test_untagged_form_has_no_structure(self) -> None:
        data = _build_form()
        for widget in _widgets(data):
            self.assertNotIn(b"/StructParent", widget)
        self.assertNotIn(b"/S /Form", data)

    def test_untagged_form_still_valid_acroform(self) -> None:
        data = _build_form()
        acroform = object_bytes(
            data,
            parse_xref(data)[
                find_object_with(data, b"/Type /AcroForm", parse_xref(data))
            ],
        )
        self.assertIn(b"/NeedAppearances false", acroform)


class TestFormFixture(unittest.TestCase):
    def test_fixture_deterministic(self) -> None:
        first = _phase7_form_document()
        second = _phase7_form_document()
        self.assertEqual(first, second)

    def test_fixture_widgets_and_appearances(self) -> None:
        data = _phase7_form_document()
        self.assertEqual(len(_widgets(data)), 2)
        for widget in _widgets(data):
            self.assertIn(b"/AP << /N", widget)
            self.assertRegex(widget, rb"/StructParent (\d+)")


if __name__ == "__main__":
    unittest.main()
