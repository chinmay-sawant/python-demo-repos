"""End-to-end PDF/A-4 + PDF/UA-2 compliance gate: fixtures must pass veraPDF.

Generates the phase-4 fixtures (minimal text, 3x3 table, image page) and
the phase-5 dual-mode fixtures (minimal text, heading+title, simple table,
multipage table, figure with alt text, link annotation) through the engine
and, when the veraPDF binary is available, runs ``verapdf -f 4`` and
``verapdf -f ua2`` and asserts ``isCompliant="true"`` with zero failed
rules (ISO 19005-4:2020 and ISO 14289-2:2024).  The whole class skips
cleanly when the binary is missing, so the suite stays green on machines
without veraPDF.  Also verifies the fixtures are byte-deterministic across
two generation runs.

The binary path defaults to the repo's bundled veraPDF installation and
can be overridden with the ``VERAPDF_BIN`` environment variable.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from engine.fixtures import (
    _pdfa4_figure_image_document,
    _pdfa4_minimal_text_document,
    _pdfa4_table_simple_document,
    _phase5_figure_alt_document,
    _phase5_heading_title_document,
    _phase5_link_annot_document,
    _phase5_minimal_text_document,
    _phase5_table_multipage_document,
    _phase5_table_simple_document,
    _phase7_bookmarks_document,
    _phase7_form_document,
    _phase7_signed_document,
    _phase7_signed_nocomply_document,
)

VERAPDF_BIN = os.environ.get(
    "VERAPDF_BIN",
    "/home/chinmay/ChinmayPersonalProjects/codehound-python-perf-targets/"
    "pythoncoreengine/verapdf/verapdf",
)
_FIXTURE_BUILDERS = {
    "minimal-text": _pdfa4_minimal_text_document,
    "table-simple": _pdfa4_table_simple_document,
    "figure-image": _pdfa4_figure_image_document,
}
_UA2_FIXTURE_BUILDERS = {
    "minimal-text": _phase5_minimal_text_document,
    "table-simple": _phase5_table_simple_document,
    "table-multipage": _phase5_table_multipage_document,
    "heading-title": _phase5_heading_title_document,
    "figure-alt": _phase5_figure_alt_document,
    "link-annot": _phase5_link_annot_document,
    "bookmarks": _phase7_bookmarks_document,
    "form": _phase7_form_document,
    "signed": _phase7_signed_document,
}


def _verapdf_available() -> bool:
    path = Path(VERAPDF_BIN)
    return path.is_file() and os.access(path, os.X_OK)


class TestPDFA4FixtureDeterminism(unittest.TestCase):
    def test_fixtures_identical_across_two_runs(self) -> None:
        first = {name: build() for name, build in _FIXTURE_BUILDERS.items()}
        second = {name: build() for name, build in _FIXTURE_BUILDERS.items()}
        for name in _FIXTURE_BUILDERS:
            self.assertEqual(first[name], second[name], name)


@unittest.skipUnless(_verapdf_available(), "veraPDF binary not found; skipping compliance gate")
class TestPDFA4Compliance(unittest.TestCase):
    """Run ``verapdf -f 4`` over every phase-4 fixture and require a pass."""

    def _assert_verapdf_compliant(self, fixture_name: str) -> None:
        data = _FIXTURE_BUILDERS[fixture_name]()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / (fixture_name + ".pdf")
            path.write_bytes(data)
            result = subprocess.run(
                [VERAPDF_BIN, "-f", "4", str(path)],
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('isCompliant="true"', result.stdout)
        self.assertIn('failedRules="0"', result.stdout)

    def test_minimal_text_passes_pdfa4(self) -> None:
        self._assert_verapdf_compliant("minimal-text")

    def test_table_simple_passes_pdfa4(self) -> None:
        self._assert_verapdf_compliant("table-simple")

    def test_figure_image_passes_pdfa4(self) -> None:
        self._assert_verapdf_compliant("figure-image")


class TestPhase5FixtureDeterminism(unittest.TestCase):
    def test_dual_mode_fixtures_identical_across_two_runs(self) -> None:
        first = {name: build() for name, build in _UA2_FIXTURE_BUILDERS.items()}
        second = {name: build() for name, build in _UA2_FIXTURE_BUILDERS.items()}
        for name in _UA2_FIXTURE_BUILDERS:
            self.assertEqual(first[name], second[name], name)


@unittest.skipUnless(_verapdf_available(), "veraPDF binary not found; skipping compliance gate")
class TestPDFUA2Compliance(unittest.TestCase):
    """Run ``verapdf -f ua2`` and ``-f 4`` over every phase-5 fixture."""

    def _assert_verapdf_compliant(self, fixture_name: str, flavour: str) -> None:
        data = _UA2_FIXTURE_BUILDERS[fixture_name]()
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / (fixture_name + ".pdf")
            path.write_bytes(data)
            result = subprocess.run(
                [VERAPDF_BIN, "-f", flavour, str(path)],
                capture_output=True,
                text=True,
            )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('isCompliant="true"', result.stdout)
        self.assertIn('failedRules="0"', result.stdout)

    def test_minimal_text_passes_ua2(self) -> None:
        self._assert_verapdf_compliant("minimal-text", "ua2")

    def test_table_simple_passes_ua2(self) -> None:
        self._assert_verapdf_compliant("table-simple", "ua2")

    def test_table_multipage_passes_ua2(self) -> None:
        self._assert_verapdf_compliant("table-multipage", "ua2")

    def test_heading_title_passes_ua2(self) -> None:
        self._assert_verapdf_compliant("heading-title", "ua2")

    def test_figure_alt_passes_ua2(self) -> None:
        self._assert_verapdf_compliant("figure-alt", "ua2")

    def test_link_annot_passes_ua2(self) -> None:
        self._assert_verapdf_compliant("link-annot", "ua2")

    def test_bookmarks_passes_ua2(self) -> None:
        self._assert_verapdf_compliant("bookmarks", "ua2")

    def test_form_passes_ua2(self) -> None:
        self._assert_verapdf_compliant("form", "ua2")

    def test_signed_passes_ua2(self) -> None:
        self._assert_verapdf_compliant("signed", "ua2")

    def test_minimal_text_stays_pdfa4_in_dual_mode(self) -> None:
        self._assert_verapdf_compliant("minimal-text", "4")

    def test_table_simple_stays_pdfa4_in_dual_mode(self) -> None:
        self._assert_verapdf_compliant("table-simple", "4")

    def test_table_multipage_stays_pdfa4_in_dual_mode(self) -> None:
        self._assert_verapdf_compliant("table-multipage", "4")

    def test_heading_title_stays_pdfa4_in_dual_mode(self) -> None:
        self._assert_verapdf_compliant("heading-title", "4")

    def test_figure_alt_stays_pdfa4_in_dual_mode(self) -> None:
        self._assert_verapdf_compliant("figure-alt", "4")

    def test_link_annot_stays_pdfa4_in_dual_mode(self) -> None:
        self._assert_verapdf_compliant("link-annot", "4")

    def test_bookmarks_stays_pdfa4_in_dual_mode(self) -> None:
        self._assert_verapdf_compliant("bookmarks", "4")

    def test_form_stays_pdfa4_in_dual_mode(self) -> None:
        self._assert_verapdf_compliant("form", "4")

    def test_signed_stays_pdfa4_in_dual_mode(self) -> None:
        self._assert_verapdf_compliant("signed", "4")


class TestSignedFixtureStructural(unittest.TestCase):
    """The signed fixtures must stay structurally valid and deterministic."""

    def test_signed_fixture_deterministic(self) -> None:
        first = _phase7_signed_document()
        second = _phase7_signed_document()
        self.assertEqual(first, second)
        self.assertEqual(
            len(first), len(second), "signed fixture length must be stable"
        )

    def test_signed_nocomply_fixture_deterministic(self) -> None:
        first = _phase7_signed_nocomply_document()
        second = _phase7_signed_nocomply_document()
        self.assertEqual(first, second)

    def test_signed_fixture_has_valid_xref(self) -> None:
        from engine.tests.helpers import object_bytes, parse_xref

        data = _phase7_signed_document()
        offsets = parse_xref(data)
        self.assertGreater(len(offsets), 10)
        # Every in-use object still starts with its own object header.
        for obj_id, offset in sorted(offsets.items()):
            header = object_bytes(data, offset).split(b" ", 2)[0]
            self.assertEqual(int(header), obj_id, f"object {obj_id} at {offset}")

    def test_signed_fixture_byte_range_and_contents_sane(self) -> None:
        import re

        from engine.signature import parse_signature_dictionary
        from engine.tests.helpers import find_object_with, object_bytes, parse_xref

        data = _phase7_signed_document()
        offsets = parse_xref(data)
        widget_id = find_object_with(data, b"/FT /Sig", offsets)
        widget = object_bytes(data, offsets[widget_id])
        v_ref = int(re.search(rb"/V\s+(\d+)\s+0\s+R", widget).group(1))
        parsed = parse_signature_dictionary(object_bytes(data, offsets[v_ref]))
        self.assertIsNotNone(parsed)
        br = parsed["byte_range"]
        self.assertEqual(br[0], 0)
        self.assertEqual(br[0] + br[1] + br[2] + br[3], len(data))
        contents = parsed["contents"]
        self.assertGreater(sum(1 for byte in contents if byte), 0)
        self.assertEqual(parsed["m"], "D:20260801120000")


if __name__ == "__main__":
    unittest.main()
