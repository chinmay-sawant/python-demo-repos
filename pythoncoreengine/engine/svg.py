"""Phase 7.6 -- SVG path data to PDF path operators, as Form XObjects.

Pure-stdlib implementation of the SVG 1.1 path grammar: every command
(``M/m L/l H/h V/v C/c S/s Q/q T/t A/a Z/z``) with implicit repeats,
relative/absolute forms, scientific notation, and the full endpoint-to-
center arc parameterization (SVG 1.1 F.6) with conversion to cubic
Bezier segments.  Style attributes (``fill``, ``stroke``, ``stroke-width``,
``transform``) map to PDF colour/width operators.

The result is a ``/Subtype /Form`` XObject whose stream draws the path in
its ``/BBox [0 0 width height]``; the document builder deduplicates
identical forms, and the page flow wraps placement in ``Figure`` + ``/Alt``
when the structure manager is active.
"""

from __future__ import annotations

import math
import re
import zlib
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

from .write import N, PdfName

# ---------------------------------------------------------------------------
# SVG path tokenizer
# ---------------------------------------------------------------------------

_NUMBER_RE = re.compile(
    r"[MmZzLlHhVvCcSsQqTtAa]|[-+]?(?:\d+\.\d*|\.\d+|\d+)(?:[eE][-+]?\d+)?"
)
# (command letter, number of coordinate values that follow per repetition)
_COMMAND_ARITY = {
    "M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "T": 2, "A": 7,
    "Z": 0,
}

_Point = Tuple[float, float]
_Arc = Tuple[float, float, float, int, int, float, float]


class SVGPathError(ValueError):
    """Raised for malformed SVG path data or unsupported style values."""


def _tokens(d: str) -> List[str]:
    toks = _NUMBER_RE.findall(d)
    if "".join(toks) != re.sub(r"[\s,]+", "", d):
        raise SVGPathError("unparsable characters in path data")
    return toks


def parse_path(d: str) -> List[Tuple[str, Tuple[float, ...]]]:
    """Parse path ``d`` into absolute-space segments.

    Returns a list of ``(command, params)`` tuples with all coordinates
    absolute (relative forms resolved against the current point), implicit
    command repeats expanded, and ``H``/``V`` normalised to ``L``.
    ``S``/``T`` are *not* resolved here (they need the previous control
    point); arcs are *not* converted yet (they need endpoint->center).
    """
    toks = _tokens(d)
    segments: List[Tuple[str, Tuple[float, ...]]] = []
    if not toks:
        raise SVGPathError("empty path data")
    if toks[0] not in ("M", "m"):
        raise SVGPathError("path data must start with a moveto command")
    cur: _Point = (0.0, 0.0)
    subpath_start: _Point = cur
    pending_cmd: Optional[str] = None
    i = 0
    while i < len(toks):
        tok = toks[i]
        if tok.upper() in _COMMAND_ARITY:
            cmd = tok
            pending_cmd = cmd.upper()
            i += 1
        else:
            if pending_cmd is None:
                raise SVGPathError("coordinate without a preceding command")
            cmd = pending_cmd
            if cmd == "Z":
                raise SVGPathError("coordinates after Z with no new command")
        if cmd.upper() == "Z":
            segments.append(("Z", ()))
            cur = subpath_start
            continue
        relative = cmd.islower()
        up = cmd.upper()
        arity = _COMMAND_ARITY[up]
        if i + arity > len(toks):
            raise SVGPathError("truncated command %r" % cmd)
        nums = [float(t) for t in toks[i : i + arity]]
        i += arity
        params: List[float] = []
        if up == "H":
            x = cur[0] + nums[0] if relative else nums[0]
            params = [x, cur[1]]
            cur = (x, cur[1])
        elif up == "V":
            y = cur[1] + nums[0] if relative else nums[0]
            params = [cur[0], y]
            cur = (cur[0], y)
        elif up == "A":
            if nums[0] < 0 or nums[1] < 0:
                raise SVGPathError("arc radii must be non-negative")
            x = cur[0] + nums[5] if relative else nums[5]
            y = cur[1] + nums[6] if relative else nums[6]
            params = [nums[0], nums[1], nums[2], nums[3], nums[4], x, y]
            cur = (x, y)
        elif up == "M":
            x = cur[0] + nums[0] if relative else nums[0]
            y = cur[1] + nums[1] if relative else nums[1]
            params = [x, y]
            cur = (x, y)
            subpath_start = cur
            pending_cmd = "L" if relative else "L"
        elif up == "Z":
            raise AssertionError("unreachable")
        else:
            if relative:
                params = list(nums)
                for j in range(0, len(nums), 2):
                    params[j] += cur[0]
                    params[j + 1] += cur[1]
                if up in ("C", "S"):
                    cur = (cur[0] + nums[4], cur[1] + nums[5])
                elif up == "Q":
                    cur = (cur[0] + nums[2], cur[1] + nums[3])
                else:  # L / T
                    cur = (cur[0] + nums[0], cur[1] + nums[1])
            else:
                params = list(nums)
                if up == "C":
                    cur = (nums[4], nums[5])
                elif up == "Q":
                    cur = (nums[2], nums[3])
                else:  # L
                    cur = (nums[0], nums[1])
        # H/V are normalised to absolute L segments
        segments.append(("L" if up in ("H", "V") else up, tuple(params)))
    return segments


# ---------------------------------------------------------------------------
# S/T reflection and arc -> cubic Bezier (SVG 1.1 F.6)
# ---------------------------------------------------------------------------


def _reflect(p: _Point, about: _Point) -> _Point:
    return (2.0 * about[0] - p[0], 2.0 * about[1] - p[1])


def _arc_to_cubics(
    x0: float, y0: float, rx: float, ry: float, rot: float,
    large: int, sweep: int, x1: float, y1: float,
) -> List[Tuple[float, ...]]:
    """Endpoint-to-center parameterization (SVG 1.1 F.6.5) -> Beziers."""
    if x0 == x1 and y0 == y1:
        return []
    if rx == 0.0 or ry == 0.0:
        # Degenerate radii: a straight line to the endpoint (SVG F.6.6).
        return [(x1, y1, x1, y1, x1, y1)]
    phi = math.radians(rot)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx, dy = (x0 - x1) / 2.0, (y0 - y1) / 2.0
    x1p = cos_p * dx + sin_p * dy
    y1p = -sin_p * dx + cos_p * dy
    lam = x1p * x1p / (rx * rx) + y1p * y1p / (ry * ry)
    if lam > 1.0:
        s = math.sqrt(lam)
        rx *= s
        ry *= s
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    co = 0.0 if den == 0.0 else math.sqrt(max(0.0, num / den))
    sign = 1.0 if large != sweep else -1.0
    cxp = sign * co * (rx * y1p / ry)
    cyp = sign * co * (-ry * x1p / rx)
    cx = cos_p * cxp - sin_p * cyp + (x0 + x1) / 2.0
    cy = sin_p * cxp + cos_p * cyp + (y0 + y1) / 2.0
    # Start/end angles via atan2 (SVG 1.1 F.6.5): acos-based variants
    # degenerate to delta == 0 when the chord is a diameter (the vectors
    # are exactly antiparallel and the cross product vanishes).
    ux, uy = (x1p - cxp) / rx, (y1p - cyp) / ry
    vx, vy = (-x1p - cxp) / rx, (-y1p - cyp) / ry
    theta1 = math.atan2(uy, ux)
    delta = math.atan2(vy, vx) - theta1
    if sweep == 0 and delta > 0.0:
        delta -= 2.0 * math.pi
    elif sweep == 1 and delta < 0.0:
        delta += 2.0 * math.pi
    n_segs = max(1, int(math.ceil(abs(delta) / (math.pi / 2.0))))
    step = delta / n_segs
    cubics: List[Tuple[float, ...]] = []
    for k in range(n_segs):
        t1 = theta1 + k * step
        t2 = t1 + step
        sin_t1, cos_t1 = math.sin(t1), math.cos(t1)
        sin_t2, cos_t2 = math.sin(t2), math.cos(t2)
        e1 = math.tan(step / 2.0) * 4.0 / 3.0
        c1x = cx + rx * cos_t1 * cos_p - ry * sin_t1 * sin_p
        c1y = cy + rx * cos_t1 * sin_p + ry * sin_t1 * cos_p
        c2x = cx + rx * cos_t2 * cos_p - ry * sin_t2 * sin_p
        c2y = cy + rx * cos_t2 * sin_p + ry * sin_t2 * cos_p
        ex = cx + rx * math.cos(t2) * cos_p - ry * math.sin(t2) * sin_p
        ey = cy + rx * math.cos(t2) * sin_p + ry * math.sin(t2) * cos_p
        cubics.append(
            (
                c1x + e1 * (ex - c1x),
                c1y + e1 * (ey - c1y),
                c2x + e1 * (ex - c2x),
                c2y + e1 * (ey - c2y),
                ex,
                ey,
            )
        )
        _ = (sin_t1, sin_t2, cos_t1, cos_t2)  # angle bookkeeping (F.6.5)
    return cubics


def flatten_path(
    segments: Sequence[Tuple[str, Tuple[float, ...]]],
) -> List[Tuple[str, Tuple[float, ...]]]:
    """Resolve S/T reflections and convert arcs to cubic Beziers."""
    out: List[Tuple[str, Tuple[float, ...]]] = []
    cur: _Point = (0.0, 0.0)
    prev_ctrl: Optional[_Point] = None
    prev_cmd: Optional[str] = None
    for cmd, params in segments:
        if cmd == "M":
            cur = (params[0], params[1])
            prev_ctrl, prev_cmd = None, "M"
            out.append(("M", params))
        elif cmd == "L":
            cur = (params[0], params[1])
            prev_ctrl, prev_cmd = None, "L"
            out.append(("L", params))
        elif cmd == "Z":
            prev_ctrl, prev_cmd = None, "Z"
            out.append(("Z", ()))
        elif cmd == "C":
            cur = (params[4], params[5])
            prev_ctrl = (params[2], params[3])
            prev_cmd = "C"
            out.append(("C", params))
        elif cmd == "S":
            if prev_cmd == "C":
                c1 = _reflect(prev_ctrl, cur)
            else:
                c1 = cur
            c2 = (params[0], params[1])
            end = (params[2], params[3])
            prev_ctrl = c2
            prev_cmd = "C"
            cur = end
            out.append(("C", (c1[0], c1[1], c2[0], c2[1], end[0], end[1])))
        elif cmd == "Q":
            c1 = (params[0], params[1])
            end = (params[2], params[3])
            # Quadratic -> cubic: c1 = q0 + 2/3(q1-q0), c2 = q2 + 2/3(q1-q2)
            cc1 = (cur[0] + 2.0 / 3.0 * (c1[0] - cur[0]),
                   cur[1] + 2.0 / 3.0 * (c1[1] - cur[1]))
            cc2 = (end[0] + 2.0 / 3.0 * (c1[0] - end[0]),
                   end[1] + 2.0 / 3.0 * (c1[1] - end[1]))
            prev_ctrl = cc2
            prev_cmd = "C"
            cur = end
            out.append(("C", (cc1[0], cc1[1], cc2[0], cc2[1], end[0], end[1])))
        elif cmd == "T":
            if prev_cmd in ("C", "Q", "T"):
                q1 = _reflect(prev_ctrl, cur)
            else:
                q1 = cur
            end = (params[0], params[1])
            cc1 = (cur[0] + 2.0 / 3.0 * (q1[0] - cur[0]),
                   cur[1] + 2.0 / 3.0 * (q1[1] - cur[1]))
            cc2 = (end[0] + 2.0 / 3.0 * (q1[0] - end[0]),
                   end[1] + 2.0 / 3.0 * (q1[1] - end[1]))
            prev_ctrl = cc2
            prev_cmd = "C"
            cur = end
            out.append(("C", (cc1[0], cc1[1], cc2[0], cc2[1], end[0], end[1])))
        elif cmd == "A":
            rx, ry, rot, large, sweep, x, y = params
            for cubic in _arc_to_cubics(cur[0], cur[1], rx, ry, rot, large,
                                        sweep, x, y):
                out.append(("C", cubic))
                cur = (cubic[4], cubic[5])
            prev_ctrl, prev_cmd = None, "A"
        else:
            raise SVGPathError("unsupported command %r" % cmd)
    return out


# ---------------------------------------------------------------------------
# Transforms
# ---------------------------------------------------------------------------

_Matrix = Tuple[float, float, float, float, float, float]


def _apply_matrix(m: _Matrix, p: _Point) -> _Point:
    a, b, c, d, e, f = m
    return (a * p[0] + c * p[1] + e, b * p[0] + d * p[1] + f)


def parse_transform(attr: Optional[str]) -> Optional[_Matrix]:
    """Parse an SVG ``transform`` list into a single affine matrix."""
    if not attr or not attr.strip():
        return None
    matrices: List[_Matrix] = []
    for fn in re.finditer(
        r"(translate|scale|rotate|matrix)\s*\(([^)]*)\)", attr
    ):
        name, body = fn.group(1), fn.group(2)
        nums = [float(x) for x in re.findall(r"[-+]?(?:\d+\.\d*|\.\d+|\d+)", body)]
        if name == "translate":
            tx, ty = (nums[0], 0.0) if len(nums) == 1 else (nums[0], nums[1])
            matrices.append((1.0, 0.0, 0.0, 1.0, tx, ty))
        elif name == "scale":
            sx, sy = (nums[0], nums[0]) if len(nums) == 1 else (nums[0], nums[1])
            matrices.append((sx, 0.0, 0.0, sy, 0.0, 0.0))
        elif name == "rotate":
            if len(nums) == 1:
                a = math.radians(nums[0])
                matrices.append((math.cos(a), math.sin(a),
                                 -math.sin(a), math.cos(a), 0.0, 0.0))
            else:
                a, cx, cy = math.radians(nums[0]), nums[1], nums[2]
                t = (1.0, 0.0, 0.0, 1.0, cx, cy)
                r = (math.cos(a), math.sin(a), -math.sin(a), math.cos(a), 0.0, 0.0)
                t_inv = (1.0, 0.0, 0.0, 1.0, -cx, -cy)
                matrices.append(_multiply(_multiply(t, r), t_inv))
        elif name == "matrix":
            if len(nums) != 6:
                raise SVGPathError("matrix transform needs 6 numbers")
            matrices.append(tuple(nums))  # type: ignore[arg-type]
        else:
            raise SVGPathError("unknown transform %r" % name)
    if not matrices:
        raise SVGPathError("unparseable transform %r" % attr)
    result: _Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for m in matrices:
        result = _multiply(result, m)
    return result


def _multiply(a: _Matrix, b: _Matrix) -> _Matrix:
    a1, a2, a3, a4, a5, a6 = a
    b1, b2, b3, b4, b5, b6 = b
    return (
        a1 * b1 + a3 * b2,
        a2 * b1 + a4 * b2,
        a1 * b3 + a3 * b4,
        a2 * b3 + a4 * b4,
        a1 * b5 + a3 * b6 + a5,
        a2 * b5 + a4 * b6 + a6,
    )


# ---------------------------------------------------------------------------
# Style + PDF emission
# ---------------------------------------------------------------------------


def _hex_color(value: str) -> Tuple[float, float, float]:
    v = value.strip()
    if v.startswith("#"):
        v = v[1:]
        if len(v) == 3:
            v = "".join(ch * 2 for ch in v)
        if len(v) != 6:
            raise SVGPathError("bad hex color %r" % value)
        try:
            rgb = tuple(int(v[i : i + 2], 16) for i in (0, 2, 4))
        except ValueError:
            raise SVGPathError("bad hex color %r" % value) from None
        return tuple(round(c / 255.0, 4) for c in rgb)  # type: ignore[return-value]
    raise SVGPathError("unsupported color %r (use #RGB/#RRGGBB or 'none')"
                       % value)


def _fmt(v: float) -> str:
    s = "%.2f" % v
    s = s.rstrip("0").rstrip(".")
    return "-0" if s == "-0" else s


def path_ops(
    d: str,
    *,
    width: float,
    height: float,
    transform: Optional[str] = None,
    fill: Optional[str] = None,
    stroke: Optional[str] = None,
    stroke_width: float = 1.0,
) -> bytes:
    """Build the PDF content operators for an SVG path.

    Coordinates are transformed to PDF space: the SVG origin (top-left,
    y down) maps to the form's bottom-left with the y axis flipped, so the
    result draws correctly inside a ``/BBox [0 0 width height]`` form.
    """
    if fill is None and stroke is None:
        raise SVGPathError("path needs a fill or a stroke")
    m = parse_transform(transform)
    flat = flatten_path(parse_path(d))

    def _pt(p: _Point) -> _Point:
        p = _apply_matrix(m, p) if m is not None else p
        return (p[0], height - p[1])

    ops: List[str] = []
    cur: _Point = (0.0, 0.0)
    first_of_subpath: Optional[_Point] = None
    for cmd, params in flat:
        if cmd == "M":
            cur = _pt((params[0], params[1]))
            first_of_subpath = cur
            ops.append("%s %s m" % (_fmt(cur[0]), _fmt(cur[1])))
        elif cmd == "L":
            cur = _pt((params[0], params[1]))
            ops.append("%s %s l" % (_fmt(cur[0]), _fmt(cur[1])))
        elif cmd == "C":
            p = [_pt((params[0], params[1])),
                 _pt((params[2], params[3])),
                 _pt((params[4], params[5]))]
            cur = p[2]
            ops.append("%s %s %s %s %s %s c"
                       % (_fmt(p[0][0]), _fmt(p[0][1]),
                          _fmt(p[1][0]), _fmt(p[1][1]),
                          _fmt(p[2][0]), _fmt(p[2][1])))
        elif cmd == "Z":
            if first_of_subpath is not None and cur != first_of_subpath:
                ops.append("h")
                cur = first_of_subpath
        else:
            raise SVGPathError("unexpected command %r" % cmd)

    color_ops: List[str] = []
    if fill is not None and fill.strip().lower() != "none":
        r, g, b = _hex_color(fill)
        color_ops.append("%s %s %s rg" % (_fmt(r), _fmt(g), _fmt(b)))
    if stroke is not None and stroke.strip().lower() != "none":
        r, g, b = _hex_color(stroke)
        color_ops.append("%s %s %s RG" % (_fmt(r), _fmt(g), _fmt(b)))
        color_ops.append("%s w" % _fmt(stroke_width))
    fill_on = fill is not None and fill.strip().lower() != "none"
    stroke_on = stroke is not None and stroke.strip().lower() != "none"
    if fill_on and stroke_on:
        color_ops.append("B")
    elif fill_on:
        color_ops.append("f")
    elif stroke_on:
        color_ops.append("S")
    else:
        color_ops.append("h")
    return " ".join(ops + color_ops).encode("ascii")


# ---------------------------------------------------------------------------
# Form XObject
# ---------------------------------------------------------------------------


def svg_form_xobject(
    svg: str,
    *,
    width: float,
    height: float,
    transform: Optional[str] = None,
    fill: Optional[str] = "#000000",
    stroke: Optional[str] = None,
    stroke_width: float = 1.0,
) -> Tuple[bytes, Dict[PdfName, object]]:
    """Turn SVG path data into a compressed Form XObject body + dict.

    Returns ``(stream_data, extra_dict)`` suitable for the document
    builder's stream reservation (``/Filter /FlateDecode`` applied by the
    caller via ``compressed_stream`` is the builder's default).
    """
    body = path_ops(
        svg,
        width=width,
        height=height,
        transform=transform,
        fill=fill,
        stroke=stroke,
        stroke_width=stroke_width,
    )
    extra: Dict[PdfName, object] = {
        N("Type"): N("XObject"),
        N("Subtype"): N("Form"),
        N("BBox"): [0, 0, width, height],
        N("Resources"): {},
        N("Filter"): N("FlateDecode"),
    }
    return zlib.compress(body), extra


@dataclass(frozen=True)
class SVGShape:
    """A reusable parsed SVG path with style attributes."""

    d: str
    width: float
    height: float
    transform: Optional[str] = None
    fill: Optional[str] = "#000000"
    stroke: Optional[str] = None
    stroke_width: float = 1.0

    def content_bytes(self) -> bytes:
        """The uncompressed PDF operators for this shape."""
        return path_ops(
            self.d,
            width=self.width,
            height=self.height,
            transform=self.transform,
            fill=self.fill,
            stroke=self.stroke,
            stroke_width=self.stroke_width,
        )
