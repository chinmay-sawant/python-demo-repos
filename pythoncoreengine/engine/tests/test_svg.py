"""Phase 7.6 tests: SVG path parsing, arcs, Form XObjects, Figure/Alt."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine import DocumentBuilder
from engine.svg import (  # noqa: E402
    SVGPathError,
    flatten_path,
    parse_path,
    path_ops,
    parse_transform,
    svg_form_xobject,
)

STAR = (
    "M90 5 L116.5 52.3 L169.4 60.9 L133.5 96 L143.2 148.7 "
    "L90 125.5 L36.8 148.7 L46.5 96 L10.6 60.9 L63.5 52.3 Z"
)


class TestTokenizer(unittest.TestCase):
    def test_command_forms_absolute(self) -> None:
        segs = parse_path("M0 0 L10 10 H20 V20 C1 2 3 4 5 6 S7 8 9 10 "
                          "Q11 12 13 14 T15 16 A5 6 0 0 1 20 20 Z")
        self.assertEqual(segs[0], ("M", (0.0, 0.0)))
        self.assertEqual(segs[1], ("L", (10.0, 10.0)))
        self.assertEqual(segs[2], ("L", (20.0, 10.0)))  # H -> L
        self.assertEqual(segs[3], ("L", (20.0, 20.0)))  # V -> L
        self.assertEqual(segs[4], ("C", (1.0, 2.0, 3.0, 4.0, 5.0, 6.0)))
        self.assertEqual(segs[5], ("S", (7.0, 8.0, 9.0, 10.0)))
        self.assertEqual(segs[6], ("Q", (11.0, 12.0, 13.0, 14.0)))
        self.assertEqual(segs[7], ("T", (15.0, 16.0)))
        self.assertEqual(segs[8], ("A", (5.0, 6.0, 0.0, 0.0, 1.0, 20.0, 20.0)))
        self.assertEqual(segs[9], ("Z", ()))

    def test_relative_commands(self) -> None:
        segs = parse_path("m10 10 l5 5 h5 v5 c1 1 2 2 3 3 q1 1 2 2 t1 1 "
                          "a2 2 0 0 1 3 3 z")
        self.assertEqual(segs[0], ("M", (10.0, 10.0)))
        self.assertEqual(segs[1], ("L", (15.0, 15.0)))
        self.assertEqual(segs[2], ("L", (20.0, 15.0)))
        self.assertEqual(segs[3], ("L", (20.0, 20.0)))
        # c from (20,20), q from (23,23), t from (25,25), a from (26,26)
        self.assertEqual(segs[4], ("C", (21.0, 21.0, 22.0, 22.0, 23.0, 23.0)))
        self.assertEqual(segs[5], ("Q", (24.0, 24.0, 25.0, 25.0)))
        self.assertEqual(segs[6], ("T", (26.0, 26.0)))
        self.assertEqual(segs[7], ("A", (2.0, 2.0, 0.0, 0.0, 1.0, 29.0, 29.0)))
        self.assertEqual(segs[8], ("Z", ()))

    def test_implicit_command_repeats(self) -> None:
        # After M/m the first implicit pair is L; repeated pairs continue L.
        segs = parse_path("M0 0 10 0 20 0")
        self.assertEqual(segs, [("M", (0.0, 0.0)),
                                ("L", (10.0, 0.0)),
                                ("L", (20.0, 0.0))])

    def test_scientific_notation_and_glued_signs(self) -> None:
        segs = parse_path("M1e2-2.5e1 L.5-1.25E1")
        self.assertEqual(segs, [("M", (100.0, -25.0)),
                                ("L", (0.5, -12.5))])

    def test_comma_and_whitespace_separators(self) -> None:
        segs = parse_path("M 0,0 L 10 , 20")
        self.assertEqual(segs, [("M", (0.0, 0.0)), ("L", (10.0, 20.0))])

    def test_malformed_raises(self) -> None:
        for bad in ("", "L10 10", "M0 0 L", "M0 0 10", "M0 0 A-1 2 0 0 1 5 5",
                    "M0 0 X10 10"):
            with self.assertRaises(SVGPathError, msg=bad):
                parse_path(bad)


class TestFlatten(unittest.TestCase):
    def test_smooth_reflects_control_points(self) -> None:
        flat = flatten_path(parse_path("M0 0 C 10 0 20 0 30 0 S 50 0 60 0"))
        self.assertEqual(len(flat), 3)  # M + two cubics
        # Second cubic's first control is the reflection of (20,0) about (30,0).
        self.assertEqual(flat[2][1][:2], (40.0, 0.0))

    def test_quadratic_to_cubic(self) -> None:
        flat = flatten_path(parse_path("M0 0 Q 10 20 20 0"))
        self.assertEqual(flat[1][0], "C")
        x1, y1, x2, y2, x3, y3 = flat[1][1]
        self.assertAlmostEqual(x1, 20.0 / 3.0)  # 2/3 of the Q control delta
        self.assertAlmostEqual(y1, 40.0 / 3.0)
        self.assertAlmostEqual(x2, 40.0 / 3.0)
        self.assertAlmostEqual(y2, 40.0 / 3.0)
        self.assertEqual((x3, y3), (20.0, 0.0))

    def test_semicircle_arc(self) -> None:
        # (50,50)->(100,50), r=25, center (75,50); sweep 0 = upper half.
        flat = flatten_path(parse_path("M50 50 A25 25 0 1 0 100 50"))
        self.assertTrue(all(seg[0] == "C" for seg in flat[1:]))
        end = flat[-1][1][4:]
        self.assertAlmostEqual(end[0], 100.0, places=4)
        self.assertAlmostEqual(end[1], 50.0, places=4)
        self.assertGreater(len(flat), 2)  # > 90 degrees -> multiple segments

    def test_full_circle_via_two_arcs(self) -> None:
        flat = flatten_path(
            parse_path("M50 50 A25 25 0 1 0 100 50 A25 25 0 1 0 50 50")
        )
        end = flat[-1][1][4:]
        self.assertAlmostEqual(end[0], 50.0, places=4)
        self.assertAlmostEqual(end[1], 50.0, places=4)
        self.assertEqual(len(flat), 5)  # M + 2 arcs x 2 cubics each

    def test_degenerate_arc_is_lineto(self) -> None:
        flat = flatten_path(parse_path("M0 0 A0 5 0 0 0 10 0"))
        self.assertEqual(flat[1], ("C", (10.0, 0.0, 10.0, 0.0, 10.0, 0.0)))
        # equal endpoints -> nothing
        flat = flatten_path(parse_path("M5 5 A5 5 0 0 0 5 5"))
        self.assertEqual(len(flat), 1)  # just the moveto


class TestTransforms(unittest.TestCase):
    def test_parse_and_compose(self) -> None:
        m = parse_transform("translate(10,20) scale(2)")
        self.assertAlmostEqual(m[4], 10.0)
        self.assertAlmostEqual(m[5], 20.0)
        self.assertAlmostEqual(m[0], 2.0)
        self.assertAlmostEqual(m[3], 2.0)
        m = parse_transform("rotate(90, 5, 5)")
        self.assertAlmostEqual(m[0], 0.0, places=9)
        self.assertAlmostEqual(m[1], 1.0, places=9)

    def test_bad_transform_raises(self) -> None:
        with self.assertRaises(SVGPathError):
            parse_transform("skew(10)")
        with self.assertRaises(SVGPathError):
            parse_transform("matrix(1,2,3)")


class TestPathOps(unittest.TestCase):
    def test_square_with_y_flip(self) -> None:
        ops = path_ops("M0 0 L100 0 L100 100 L0 100 Z",
                       width=100, height=100, fill="#ff0000")
        self.assertEqual(ops,
                         b"0 100 m 100 100 l 100 0 l 0 0 l h 1 0 0 rg f")

    def test_stroke_only(self) -> None:
        ops = path_ops("M0 0 L100 0", width=100, height=100,
                       stroke="#000000", stroke_width=2)
        self.assertTrue(ops.endswith(b"0 0 0 RG 2 w S"))

    def test_fill_and_stroke_uses_B(self) -> None:
        ops = path_ops("M0 0 L10 0 L10 10 Z", width=10, height=10,
                       fill="#123456", stroke="#abcdef", stroke_width=1)
        self.assertIn(b" B", ops)

    def test_bad_color_raises(self) -> None:
        with self.assertRaises(SVGPathError):
            path_ops("M0 0 L10 0", width=10, height=10, fill="red")
        with self.assertRaises(SVGPathError):
            path_ops("M0 0 L10 0", width=10, height=10, fill="#12")

    def test_no_paint_raises(self) -> None:
        with self.assertRaises(SVGPathError):
            path_ops("M0 0 L10 0", width=10, height=10)

    def test_transform_applied(self) -> None:
        ops = path_ops("M20 20 L80 20", width=100, height=100,
                       stroke="#000000",
                       transform="translate(10,5) rotate(30)")
        self.assertTrue(ops.startswith(b"17.32"))

    def test_hex_three_digit(self) -> None:
        ops = path_ops("M0 0 L10 0 L10 10 Z", width=10, height=10, fill="#f00")
        self.assertIn(b"1 0 0 rg", ops)


class TestFormXObject(unittest.TestCase):
    def test_form_dict_keys(self) -> None:
        data, extra = svg_form_xobject(STAR, width=180, height=160)
        self.assertEqual(extra["/Subtype"], "/Form")
        self.assertEqual(extra["/Type"], "/XObject")
        self.assertEqual(extra["/BBox"], [0, 0, 180, 160])
        self.assertEqual(extra["/Resources"], {})
        import zlib
        body = zlib.decompress(data)
        self.assertIn(b" m ", body)
        self.assertIn(b" rg ", body)
        self.assertIn(b"h", body)

    def test_dedupes_identical_shapes(self) -> None:
        builder = DocumentBuilder(created=__import__("datetime").datetime(2026, 8, 1))
        flow = builder.flow()
        flow.svg(STAR, x=0, y=0, width=180, height=160)
        flow.svg(STAR, x=0, y=200, width=180, height=160)
        pdf = builder.render()
        self.assertEqual(pdf.count(b"/Subtype /Form"), 1)

    def test_different_shapes_do_not_dedupe(self) -> None:
        builder = DocumentBuilder(created=__import__("datetime").datetime(2026, 8, 1))
        flow = builder.flow()
        flow.svg(STAR, x=0, y=0, width=180, height=160)
        flow.svg("M0 0 L10 0 L10 10 Z", x=0, y=0, width=180, height=160)
        pdf = builder.render()
        self.assertEqual(pdf.count(b"/Subtype /Form"), 2)


class TestTaggedIntegration(unittest.TestCase):
    def test_figure_alt_when_tagged(self) -> None:
        builder = DocumentBuilder(
            created=__import__("datetime").datetime(2026, 8, 1),
            mode_pdfa4=True,
            mode_pdfua2=True,
            title="svg",
        )
        flow = builder.flow()
        flow.svg(STAR, x=0, y=0, width=180, height=160, alt="A star")
        pdf = builder.render()
        self.assertIn(b"/StructTreeRoot", pdf)
        self.assertIn(b"/Alt (A star)", pdf)
        self.assertIn(b"/Figure", pdf)
        self.assertIn(b"/StructParents", pdf)

    def test_no_structure_when_untagged(self) -> None:
        builder = DocumentBuilder(created=__import__("datetime").datetime(2026, 8, 1))
        flow = builder.flow()
        flow.svg(STAR, x=0, y=0, width=180, height=160)
        pdf = builder.render()
        self.assertNotIn(b"/StructTreeRoot", pdf)
        self.assertNotIn(b"/Figure", pdf)

    def test_compliance_dual_mode(self) -> None:
        verapdf = (
            Path(__file__).resolve().parents[3]
            / "verapdf" / "verapdf"
        )
        if not verapdf.exists():
            self.skipTest("veraPDF binary not installed")
        import subprocess
        from engine.fixtures import _phase7_svg_document_dual
        pdf = _phase7_svg_document_dual()
        tmp = Path(__file__).parent / "fixtures" / "_tmp_svg.pdf"
        tmp.write_bytes(pdf)
        try:
            for flavour in ("4", "ua2"):
                out = subprocess.run(
                    [str(verapdf), "-f", flavour, str(tmp)],
                    capture_output=True, text=True, timeout=300,
                )
                self.assertEqual(out.returncode, 0, out.stdout)
                self.assertIn('isCompliant="true"', out.stdout)
                self.assertIn('failedRules="0"', out.stdout)
        finally:
            tmp.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
