"""Content-stream operators: text, colour, paths, images, marked content.

:class:`ContentStream` accumulates operator lines and :meth:`ContentStream.render`
emits the stream body bytes.  Phase 1 owned text; phase 2 adds path operators
(``m`` / ``l`` / ``re`` / ``S`` / ``f``), fill and stroke colour (``rg`` /
``RG``), line width, text leading (``TL``) and image placement (``cm`` +
``Do``).  Phase 3 adds CID-font text: with ``cids=True`` a text line is
stored as a deferred run whose UTF-16 hex characters are resolved to subset
glyph IDs at :meth:`render` time through a CID mapper callback.  Phase 5
adds marked content (``BDC`` / ``EMC``) for tagged output and artifact
wrapping; the untagged paths emit no marked content at all.

Phase 6 adds the hot-path operators: :meth:`ContentStream.begin_mcid` emits
the common ``/Tag << /MCID n >> BDC`` in one ``%``-format (no dictionary,
no ``PdfName`` churn per cell), numbers render through the cached
:func:`format_number_bytes`, and CID hex digits reuse a small per-process
cache.
"""

from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple, Union

from .write import encode_dict, encode_string, escape_name, format_number_bytes

__all__ = ["ContentStream"]

# A colour triple in 0..1 scale.
RGB = Tuple[float, float, float]

# Maps (font resource name, character) to the two-byte CID used in the stream.
CidMapper = Callable[[str, str], int]

# Precomputed operator constants for the marked-content hot path (avoid
# rebuilding the same small bytes per cell).
_EMC = b"EMC"

# Bounded cache of the four hex digits for a CID: dense tables re-show the
# same small character set tens of thousands of times, and ``"%04X" % cid``
# is pure, so caching is deterministic.  Bounded at 4096 entries.
_HEX4_CACHE: Dict[int, str] = {}


def _hex4(cid: int) -> str:
    """Four uppercase hex digits for ``cid`` (cached per value)."""
    cached = _HEX4_CACHE.get(cid)
    if cached is None:
        cached = "%04X" % cid
        if len(_HEX4_CACHE) < 4096:
            _HEX4_CACHE[cid] = cached
    return cached


def _num(value: float) -> bytes:
    return format_number_bytes(value)


class _DeferredText:
    """A CID text run resolved to a hex ``Tj`` at render time."""

    __slots__ = ("color", "leading", "resource_name", "size", "text", "x", "y")

    def __init__(
        self,
        text: str,
        x: float,
        y: float,
        resource_name: str,
        size: float,
        color: Optional[RGB],
        leading: Optional[float],
    ) -> None:
        self.text = text
        self.x = x
        self.y = y
        self.resource_name = resource_name
        self.size = size
        self.color = color
        self.leading = leading

    def render(self, cid_mapper: CidMapper) -> bytes:
        """The operator bytes for this run, CIDs mapped per character."""
        resource = self.resource_name
        # Build CID hex without a Python join of thousands of tiny strings
        # when the run is short (typical cell); still correct for long runs.
        mapper = cid_mapper
        text = self.text
        hex_parts = [_hex4(mapper(resource, char)) for char in text]
        hex_digits = "".join(hex_parts).encode("ascii")
        chunks: List[bytes] = []
        if self.color is not None:
            r, g, b = self.color
            chunks.append(_num(r) + b" " + _num(g) + b" " + _num(b) + b" rg")
        chunks.append(b"BT")
        if self.leading is not None:
            chunks.append(_num(self.leading) + b" TL")
        chunks.append(b"/%s %s Tf" % (escape_name(resource), _num(self.size)))
        chunks.append(_num(self.x) + b" " + _num(self.y) + b" Td")
        chunks.append(b"<" + hex_digits + b"> Tj")
        chunks.append(b"ET")
        return b"\n".join(chunks)


class ContentStream:
    """Accumulates content-stream operators; render() emits the body bytes."""

    def __init__(self) -> None:
        self._operators: List[Union[bytes, _DeferredText]] = []

    # ------------------------------------------------------------------
    # Text
    # ------------------------------------------------------------------

    def begin_text(self) -> None:
        """Append the ``BT`` operator."""
        self._operators.append(b"BT")

    def end_text(self) -> None:
        """Append the ``ET`` operator."""
        self._operators.append(b"ET")

    def set_font(self, resource_name: str, size: float) -> None:
        """Append ``/Name size Tf`` selecting a font from page resources."""
        self._operators.append(
            b"/%s %s Tf" % (escape_name(resource_name), _num(size))
        )

    def set_text_leading(self, leading: float) -> None:
        """Append ``leading TL`` setting the text line leading."""
        self._operators.append(_num(leading) + b" TL")

    def move_text(self, x: float, y: float) -> None:
        """Append ``x y Td`` moving the text line start position."""
        self._operators.append(_num(x) + b" " + _num(y) + b" Td")

    def show_text(self, text: str) -> None:
        """Append ``(text) Tj`` showing one line of text."""
        self._operators.append(encode_string(text) + b" Tj")

    def show_text_cid(self, text: str) -> None:
        """Append ``<UTF16BE> Tj`` showing one line of text for a CID font.

        Identity-H encoding addresses every character as a two-byte CID, so
        the operator argument is a hex string of the UTF-16BE bytes.  For
        embedded subsets use ``text_line(cids=True)`` instead, which maps
        each character to its subset glyph ID.
        """
        hex_digits = text.encode("utf-16-be").hex().encode("ascii")
        self._operators.append(b"<" + hex_digits + b"> Tj")

    def text_line(
        self,
        text: str,
        *,
        x: float,
        y: float,
        resource_name: str = "F1",
        size: float = 12.0,
        color: Optional[RGB] = None,
        leading: Optional[float] = None,
        cids: bool = False,
    ) -> None:
        """Draw a single line of text with optional colour and leading.

        ``color`` is an ``(r, g, b)`` tuple in 0..1 rendered as ``rg`` before
        the text object; ``leading`` emits the ``TL`` operator.  With both
        ``None`` the emitted operators are byte-identical to phase 1.  When
        ``cids`` is True the line is deferred to :meth:`render`, which maps
        every character to a two-byte CID (subset glyph ID) via the CID
        mapper and shows it as a hex ``Tj`` for Identity-H CID fonts.
        """
        if cids:
            self._operators.append(
                _DeferredText(text, x, y, resource_name, size, color, leading)
            )
            return
        if color is not None:
            self.set_color_rgb(*color)
        self.begin_text()
        if leading is not None:
            self.set_text_leading(leading)
        self.set_font(resource_name, size)
        self.move_text(x, y)
        self.show_text(text)
        self.end_text()

    # ------------------------------------------------------------------
    # Marked content (phase 5: tagged output / artifacts)
    # ------------------------------------------------------------------

    def begin_marked_content(
        self, tag: str, properties: Optional[Dict[Any, Any]] = None
    ) -> None:
        """Append ``/Tag << ... >> BDC`` (or bare ``/Tag BDC``).

        ``properties`` is an inline dictionary (``/MCID n``, ``/Alt (...)``);
        it is encoded with the standard value encoders so keys should be
        ``PdfName`` instances via :func:`~engine.write.N`.
        """
        if properties:
            self._operators.append(
                b"/%s %s BDC" % (escape_name(tag), encode_dict(properties))
            )
        else:
            self._operators.append(b"/%s BDC" % escape_name(tag))

    def begin_mcid(self, tag: str, mcid: int) -> None:
        """Append ``/Tag << /MCID n >> BDC`` -- the per-cell hot path (phase 6).

        Emits the most common marked-content form directly with one format
        call, skipping the dictionary/PdfName churn of
        :meth:`begin_marked_content`; output is byte-identical to it.
        """
        # TD/TH dominate dense tables; skip escape_name for plain tags.
        if tag == "TD":
            self._operators.append(b"/TD << /MCID %d >> BDC" % mcid)
        elif tag == "TH":
            self._operators.append(b"/TH << /MCID %d >> BDC" % mcid)
        else:
            self._operators.append(
                b"/%s << /MCID %d >> BDC" % (escape_name(tag), mcid)
            )

    def end_marked_content(self) -> None:
        """Append the ``EMC`` operator closing the current marked-content block."""
        self._operators.append(_EMC)

    def begin_artifact(self, properties: Optional[Dict[Any, Any]] = None) -> None:
        """Append ``/Artifact << ... >> BDC`` for non-structural content.

        Pagination chrome (page numbers, header/footer rules) that must not
        be part of the structure tree wraps itself in an artifact block; the
        UA-2 rule set treats artifact content as explicitly non-real.
        """
        self.begin_marked_content("Artifact", properties)

    # ------------------------------------------------------------------
    # Graphics state
    # ------------------------------------------------------------------

    def save_state(self) -> None:
        """Append the ``q`` operator (push graphics state)."""
        self._operators.append(b"q")

    def restore_state(self) -> None:
        """Append the ``Q`` operator (pop graphics state)."""
        self._operators.append(b"Q")

    def set_color_rgb(self, r: float, g: float, b: float) -> None:
        """Append ``r g b rg`` setting the non-stroking (fill) colour."""
        self._operators.append(_num(r) + b" " + _num(g) + b" " + _num(b) + b" rg")

    def set_stroke_color_rgb(self, r: float, g: float, b: float) -> None:
        """Append ``r g b RG`` setting the stroking colour."""
        self._operators.append(_num(r) + b" " + _num(g) + b" " + _num(b) + b" RG")

    def set_line_width(self, width: float) -> None:
        """Append ``width w`` setting the stroking line width."""
        self._operators.append(_num(width) + b" w")

    def set_matrix(self, a: float, b: float, c: float, d: float, e: float, f: float) -> None:
        """Append ``a b c d e f cm`` concatenating a transformation matrix."""
        nums = b" ".join(_num(v) for v in (a, b, c, d, e, f))
        self._operators.append(nums + b" cm")

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    def move_to(self, x: float, y: float) -> None:
        """Append ``x y m`` starting a new path at ``(x, y)``."""
        self._operators.append(_num(x) + b" " + _num(y) + b" m")

    def line_to(self, x: float, y: float) -> None:
        """Append ``x y l`` extending the path to ``(x, y)``."""
        self._operators.append(_num(x) + b" " + _num(y) + b" l")

    def rect(self, x: float, y: float, width: float, height: float) -> None:
        """Append ``x y width height re`` adding a rectangle to the path."""
        nums = b" ".join(_num(v) for v in (x, y, width, height))
        self._operators.append(nums + b" re")

    def stroke(self) -> None:
        """Append ``S`` stroking the current path."""
        self._operators.append(b"S")

    def fill(self) -> None:
        """Append ``f`` filling the current path (non-zero winding)."""
        self._operators.append(b"f")

    def stroked_rect(self, x: float, y: float, width: float, height: float) -> None:
        """Outline a rectangle: ``... re S``."""
        self.rect(x, y, width, height)
        self.stroke()

    def filled_rect(self, x: float, y: float, width: float, height: float) -> None:
        """Fill a rectangle: ``... re f`` (uses the current fill colour)."""
        self.rect(x, y, width, height)
        self.fill()

    def line(self, x1: float, y1: float, x2: float, y2: float) -> None:
        """Draw a single stroked segment: ``x1 y1 m x2 y2 l S``."""
        self.move_to(x1, y1)
        self.line_to(x2, y2)
        self.stroke()

    # ------------------------------------------------------------------
    # Images
    # ------------------------------------------------------------------

    def draw_xobject(self, resource_name: str) -> None:
        """Append ``/Name Do`` painting the named XObject."""
        self._operators.append(b"/%s Do" % escape_name(resource_name))

    def render(self, cid_mapper: Optional[CidMapper] = None) -> bytes:
        """Return the stream body bytes, one operator per line.

        Deferred CID text runs (``cids=True``) are resolved through
        ``cid_mapper``; passing None with deferred runs still pending raises
        ``ValueError``.
        """
        lines: List[bytes] = []
        for operator in self._operators:
            if isinstance(operator, bytes):
                lines.append(operator)
            else:
                if cid_mapper is None:
                    raise ValueError(
                        "CID text run requires a cid_mapper at render time"
                    )
                lines.append(operator.render(cid_mapper))
        return b"\n".join(lines) + b"\n"
