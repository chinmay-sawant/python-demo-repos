"""Unit tests for the full-format financial template path.

Covers JSON load, compliant vs nocomply bytes, template-cache equivalence
(cached vs uncached bench runs emit the same PDF md5), warm-up write, and
basic props parsing used by the rich-table renderer.
"""

from __future__ import annotations

import hashlib
import tempfile
import unittest
from pathlib import Path

from engine.bench_financial import (
    _repo_root,
    run_bench,
    write_warmup_pdf,
)
from engine.layout import parse_props
from engine.model import load_template
from engine.render import build_template_document, template_wants_compliant
from engine.tests.helpers import find_object_with, object_bytes, parse_xref

DATA_DIR = _repo_root() / "sampledata" / "financial"
FIXTURE = DATA_DIR / "financial_report.json"


@unittest.skipUnless(FIXTURE.is_file(), "financial_report.json missing")
class TestLoadTemplate(unittest.TestCase):
    def test_loads_sections(self) -> None:
        t = load_template(FIXTURE)
        self.assertTrue(template_wants_compliant(t))
        self.assertEqual(t.config.page.upper(), "A4")
        self.assertIsNotNone(t.title)
        self.assertGreaterEqual(len(t.tables), 1)
        self.assertGreaterEqual(len(t.elements), 1)
        self.assertIsNotNone(t.footer)
        self.assertIn("TECHCORP", t.footer.text)  # type: ignore[union-attr]


@unittest.skipUnless(FIXTURE.is_file(), "financial_report.json missing")
class TestTemplateRender(unittest.TestCase):
    def test_compliant_pdf_header_and_structure(self) -> None:
        t = load_template(FIXTURE)
        data = build_template_document(t, compliant=True)
        self.assertTrue(data.startswith(b"%PDF-2.0"))
        self.assertGreater(len(data), 10_000)
        offsets = parse_xref(data)
        catalog = object_bytes(
            data, offsets[find_object_with(data, b"/Type /Catalog", offsets)]
        )
        self.assertIn(b"/StructTreeRoot", catalog)
        self.assertIn(b"/OutputIntents", catalog)

    def test_nocomply_differs(self) -> None:
        t = load_template(FIXTURE)
        compliant = build_template_document(t, compliant=True)
        plain = build_template_document(t, compliant=False)
        self.assertNotEqual(compliant, plain)
        offsets = parse_xref(plain)
        catalog = object_bytes(
            plain, offsets[find_object_with(plain, b"/Type /Catalog", offsets)]
        )
        self.assertNotIn(b"/StructTreeRoot", catalog)
        self.assertNotIn(b"/OutputIntents", catalog)

    def test_deterministic(self) -> None:
        t = load_template(FIXTURE)
        a = build_template_document(t, compliant=True)
        b = build_template_document(t, compliant=True)
        self.assertEqual(a, b)
        self.assertEqual(hashlib.md5(a).hexdigest(), hashlib.md5(b).hexdigest())


@unittest.skipUnless(FIXTURE.is_file(), "financial_report.json missing")
class TestFinancialBench(unittest.TestCase):
    def test_cached_and_uncached_identical_md5(self) -> None:
        cached = run_bench(3, cached=True, data_dir=DATA_DIR)
        uncached = run_bench(3, cached=False, data_dir=DATA_DIR)
        self.assertEqual(cached["md5"], uncached["md5"])
        self.assertEqual(cached["output_bytes"], uncached["output_bytes"])
        self.assertGreater(cached["jobs_per_sec"], 0.0)

    def test_warmup_write(self) -> None:
        t = load_template(FIXTURE)
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp)
            path = write_warmup_pdf(t, out, compliant=True)
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, 1000)
            plain = write_warmup_pdf(t, out, compliant=False)
            self.assertTrue(plain.is_file())
            self.assertNotEqual(path.read_bytes(), plain.read_bytes())


class TestParseProps(unittest.TestCase):
    def test_full_props(self) -> None:
        p = parse_props("Helvetica:12:100:center:1:1:1:1")
        self.assertEqual(p.font_name, "Helvetica")
        self.assertEqual(p.font_size, 12.0)
        self.assertTrue(p.bold)
        self.assertFalse(p.italic)
        self.assertEqual(p.align, "center")
        self.assertEqual(p.border, (True, True, True, True))

    def test_empty_defaults(self) -> None:
        p = parse_props("")
        self.assertEqual(p.font_name, "Helvetica")
        self.assertEqual(p.align, "left")


if __name__ == "__main__":
    unittest.main()
