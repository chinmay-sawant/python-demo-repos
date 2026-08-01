"""Layout primitives: margins, text metrics, wrapping, page flow, tables.

Phase 2 owns everything between raw content operators and document assembly:
the content box (:class:`PageMargins`), the Helvetica width table used for
word wrapping (:func:`wrap_text`), :class:`PageFlow` (a cursor-based drawing
context that breaks pages through a host callback) and :class:`TableLayout`
(a fixed-column grid with borders and header styling).

Phase 5 adds the tagged path: when the host exposes a structure manager,
``PageFlow`` wraps each drawn text run in ``BDC``/``EMC`` marked content
with a per-page MCID and creates the owning ``/StructElem`` (heading -> H1,
paragraph -> P, image -> Figure, link -> Link), and ``TableLayout`` builds
Table/TR/TD/TH elements with the decorative graphics (fills, borders, grid)
wrapped as artifacts.  Layout still deliberately knows nothing about
compliance; the host object it draws through is responsible for all
document-level bookkeeping.

Phase 6 adds the table hot path: cell marked content goes through
:meth:`ContentStream.begin_mcid` (one format call, no per-cell dictionary)
and cells are created through the structure manager's combined
:meth:`~engine.structure.StructureManager.begin_cell`, which allocates the
StructElem and its MCID in one call.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, List, Optional, Protocol, Sequence, Set, Tuple

from .content import ContentStream, RGB
from .page import A4_POINTS
from .structure import StructElem, StructureManager
from .write import N, ObjectId

__all__ = [
    "Align",
    "CellProps",
    "FlowHost",
    "PageFlow",
    "PageMargins",
    "RichTableLayout",
    "StyledCell",
    "TableLayout",
    "parse_props",
    "text_width",
    "wrap_text",
]

DEFAULT_MARGIN = 72.0
_LINE_HEIGHT_FACTOR = 1.2


def _cell_text_baseline(
    y_top: float,
    height: float,
    line_index: int,
    n_lines: int,
    leading: float,
) -> float:
    """Top-down Y of the PDF text baseline for one line inside a cell.

    PDF ``Tj`` positions the *baseline*, not the top of the glyph.  Using
    ``y_top + padding`` as the baseline therefore draws the body of the
    letter into the top border (the classic "text too high" look).  We
    vertically centre an ``n_lines``-line block of height
    ``n_lines * leading`` inside the cell and put each baseline at the
    bottom of its line box -- the same placement as gocorepdfengine
    (``startY = y - H + (H - fontSize*1.2)/2`` for a single line).
    """
    lines = max(int(n_lines), 1)
    text_block = lines * leading
    return y_top + (height - text_block) / 2.0 + (line_index + 1) * leading


# Text alignment for template props / rich cells (TEMPLATE_REFERENCE.md).
Align = str  # "left" | "center" | "right"


@dataclass(frozen=True)
class CellProps:
    """Parsed ``font:size:style:align:L:R:T:B`` props string."""

    font_name: str = "Helvetica"
    font_size: float = 10.0
    bold: bool = False
    italic: bool = False
    underline: bool = False
    align: str = "left"
    border: Tuple[bool, bool, bool, bool] = (False, False, False, False)


def parse_props(s: str) -> CellProps:
    """Parse an 8-field props string (TEMPLATE_REFERENCE / gocorepdfengine).

    Format: ``FontName:Size:BIU:align:left:right:top:bottom`` where BIU is
    a 3-digit bold/italic/underline mask and border flags are ``0``/``1``.
    Malformed input returns safe defaults rather than raising.
    """
    if not s:
        return CellProps()
    parts = s.split(":")
    if len(parts) != 8:
        return CellProps(font_name=parts[0] if parts else "Helvetica")
    try:
        size = float(parts[1])
    except ValueError:
        size = 10.0
    style = parts[2]
    bold = len(style) >= 1 and style[0] == "1"
    italic = len(style) >= 2 and style[1] == "1"
    underline = len(style) >= 3 and style[2] == "1"
    align = parts[3] if parts[3] in ("left", "center", "right") else "left"
    border = tuple(parts[4 + i] == "1" for i in range(4))  # type: ignore[assignment]
    return CellProps(
        font_name=parts[0] or "Helvetica",
        font_size=size,
        bold=bold,
        italic=italic,
        underline=underline,
        align=align,
        border=border,  # type: ignore[arg-type]
    )


@dataclass
class StyledCell:
    """One richly-styled table cell (template / financial-report path)."""

    text: str = ""
    font: str = "F1"
    size: float = 10.0
    text_color: Optional[RGB] = None
    fill_color: Optional[RGB] = None
    align: str = "left"
    border: Tuple[bool, bool, bool, bool] = (False, False, False, False)
    min_height: float = 0.0
    image_data: Optional[bytes] = None
    image_w: float = 0.0
    image_h: float = 0.0


def _is_dark(color: RGB) -> bool:
    """Perceptual luminance below 50%: white text reads best over it."""
    r, g, b = color
    return 0.299 * r + 0.587 * g + 0.114 * b < 0.5

# Helvetica glyph widths in 1/1000 em (Adobe AFM-style values), indexed by
# byte value 0..255.  Only the printable ASCII range has real metrics; every
# other byte falls back to the average width 556.
_HELVETICA_WIDTHS: List[int] = [556] * 256
_HELVETICA_WIDTHS[0x20:0x7F] = [
    278, 278, 355, 556, 556, 889, 667, 191, 333, 333, 389, 584, 278, 333,
    278, 278, 556, 556, 556, 556, 556, 556, 556, 556, 556, 556, 278, 278,
    584, 584, 584, 556, 1015, 667, 667, 722, 722, 667, 611, 778, 722, 278,
    500, 667, 556, 833, 722, 778, 667, 778, 722, 667, 611, 722, 667, 944,
    667, 667, 611, 278, 278, 278, 469, 556, 333, 556, 556, 500, 556, 556,
    278, 556, 556, 222, 222, 500, 222, 833, 556, 556, 556, 556, 333, 500,
    278, 556, 500, 722, 500, 500, 500, 334, 260, 334, 584,
]


def _width_of_char(ch: str) -> int:
    code = ord(ch)
    if code >= 256:
        return 556
    return _HELVETICA_WIDTHS[code]


def text_width(text: str, size: float = 12.0) -> float:
    """Width of ``text`` in points at ``size`` (Helvetica metrics)."""
    return sum(_width_of_char(ch) for ch in text) * size / 1000.0


def _longest_prefix(word: str, max_width: float, size: float) -> Tuple[str, str]:
    """Split ``word`` into the longest fitting prefix and the remainder."""
    for index in range(len(word), 0, -1):
        if text_width(word[:index], size) <= max_width:
            return word[:index], word[index:]
    return word[0], word[1:]


def wrap_text(text: str, max_width: float, size: float = 12.0) -> List[str]:
    """Greedy word wrap into lines that fit ``max_width`` points.

    Words longer than the line width are hard-split on character boundaries;
    explicit ``\\n`` in the input forces line breaks.  Returns a list of
    lines (possibly empty for empty input).
    """
    if not text:
        return []
    lines: List[str] = []
    for raw_line in text.split("\n"):
        current = ""
        for word in raw_line.split(" "):
            while word:
                candidate = word if not current else current + " " + word
                if text_width(candidate, size) <= max_width:
                    current = candidate
                    break
                if current:
                    lines.append(current)
                    current = ""
                if text_width(word, size) <= max_width:
                    current = word
                    break
                head, word = _longest_prefix(word, max_width, size)
                lines.append(head)
        if current:
            lines.append(current)
    return lines


class PageMargins:
    """The content box inset on every page, in points."""

    def __init__(
        self,
        left: float = DEFAULT_MARGIN,
        right: float = DEFAULT_MARGIN,
        top: float = DEFAULT_MARGIN,
        bottom: float = DEFAULT_MARGIN,
    ) -> None:
        self.left = left
        self.right = right
        self.top = top
        self.bottom = bottom


class FlowHost(Protocol):
    """The object a :class:`PageFlow` draws through (implemented by the document builder).

    ``new_page`` starts a fresh page and returns its content stream;
    ``font_ref`` and ``image_ref`` return (and lazily register) resource
    names referenced by the current page.  ``font_is_cid`` tells the flow
    whether a font resource is emitted as a CID font (Identity-H hex text);
    ``record_font_usage`` lets the host collect used characters per font for
    subsetting.  Under tagging the host also exposes ``structure_manager``
    (None when untagged), ``page_ref`` (for ``/Pg`` on structure elements)
    and ``add_link_annotation`` (registers a link annotation on a page).
    """

    def new_page(self) -> ContentStream: ...

    def font_ref(self, name: str) -> ObjectId: ...

    def image_ref(self, data: bytes) -> str: ...

    def svg_ref(
        self,
        svg: str,
        *,
        width: float,
        height: float,
        transform: Optional[str] = None,
        fill: Optional[str] = "#000000",
        stroke: Optional[str] = None,
        stroke_width: float = 1.0,
    ) -> str: ...

    def font_is_cid(self, name: str) -> bool: ...

    def record_font_usage(self, name: str, text: str) -> None: ...

    def structure_manager(self) -> Optional[StructureManager]: ...

    def page_ref(self, index: int) -> ObjectId: ...

    def add_link_annotation(
        self, page_index: int, rect: Sequence[float], uri: str
    ) -> ObjectId: ...


class PageFlow:
    """Cursor-based drawing context with automatic page breaks.

    Coordinates are top-down: ``x`` is the distance right of the left margin
    and ``y`` the distance below the top margin.  Whenever content overflows
    the content box, :meth:`ensure_space` calls the host's ``new_page``,
    which returns a fresh content stream; the flow then owns the new page's
    cursor and resource usage records.
    """

    def __init__(
        self,
        *,
        host: FlowHost,
        page_size: Tuple[float, float] = A4_POINTS,
        margins: Optional[PageMargins] = None,
    ) -> None:
        self._host = host
        self.page_size = page_size
        self.margins = margins if margins is not None else PageMargins()
        self.content_width = page_size[0] - self.margins.left - self.margins.right
        self._usable_height = page_size[1] - self.margins.top - self.margins.bottom
        self.y = 0.0
        self._cur_fonts: Set[str] = set()
        self._cur_xobjects: Set[str] = set()
        self.stream = host.new_page()
        self._streams: List[ContentStream] = [self.stream]
        self._page_fonts: List[Set[str]] = [self._cur_fonts]
        self._page_xobjects: List[Set[str]] = [self._cur_xobjects]

    # ------------------------------------------------------------------
    # Page bookkeeping (read by the document builder at render time)
    # ------------------------------------------------------------------

    @property
    def page_count(self) -> int:
        """The number of pages flowed so far."""
        return len(self._streams)

    @property
    def streams(self) -> List[ContentStream]:
        """One content stream per page, in page order."""
        return self._streams

    @property
    def page_fonts(self) -> List[Set[str]]:
        """Font resource names used per page, aligned with ``streams``."""
        return self._page_fonts

    @property
    def page_xobjects(self) -> List[Set[str]]:
        """Image resource names used per page, aligned with ``streams``."""
        return self._page_xobjects

    def _start_page(self) -> None:
        """Close the current page's bookkeeping and open a fresh one."""
        self.stream = self._host.new_page()
        self._streams.append(self.stream)
        self._cur_fonts = set()
        self._cur_xobjects = set()
        self._page_fonts.append(self._cur_fonts)
        self._page_xobjects.append(self._cur_xobjects)
        self.y = 0.0

    def pdf_y(self, top_down: float) -> float:
        """Convert a top-down cursor position to PDF user-space y."""
        return self.page_size[1] - self.margins.top - top_down

    # ------------------------------------------------------------------
    # Cursor control
    # ------------------------------------------------------------------

    def ensure_space(self, height: float) -> None:
        """Start a new page when ``height`` does not fit below the cursor.

        A block taller than the whole content box is left where it is
        (drawn partially off-page) instead of looping forever.
        """
        if height <= self._usable_height and self.y + height > self._usable_height + 1e-6:
            self._start_page()

    def use_font(self, name: str) -> None:
        """Record ``name`` as a font resource of the current page."""
        self._cur_fonts.add(name)
        self._host.font_ref(name)

    def use_xobject(self, name: str) -> None:
        """Record ``name`` as an image resource of the current page."""
        self._cur_xobjects.add(name)

    def record_chars(self, font: str, text: str) -> None:
        """Report characters drawn with ``font`` to the host (for subsetting)."""
        self._host.record_font_usage(font, text)

    def font_is_cid(self, name: str) -> bool:
        """True when ``name`` is emitted as a CID font (Identity-H text)."""
        return self._host.font_is_cid(name)

    def structure_manager(self) -> Optional[StructureManager]:
        """The host's structure manager, or None when output is untagged.

        Hosts without a ``structure_manager`` method (test stubs) are treated
        as untagged, keeping the phase-2 byte paths intact.
        """
        getter = getattr(self._host, "structure_manager", None)
        return getter() if getter is not None else None

    @property
    def page_index(self) -> int:
        """The zero-based index of the page currently being drawn."""
        return len(self._streams) - 1

    # ------------------------------------------------------------------
    # Drawing helpers
    # ------------------------------------------------------------------

    def _draw_text_line(
        self,
        text: str,
        *,
        x: float,
        size: float,
        font: str,
        color: Optional[RGB],
        leading: float,
    ) -> None:
        """Draw one raw line at the cursor; advance the cursor by ``leading``.

        ``x`` is relative to the left margin; the line baseline sits at the
        current cursor position.  No structure is attached here; the tagged
        wrappers (``text`` / ``paragraph`` / cell rendering) place the
        BDC/EMC around this call.
        """
        self.use_font(font)
        self.record_chars(font, text)
        self.stream.text_line(
            text,
            x=self.margins.left + x,
            y=self.pdf_y(self.y),
            resource_name=font,
            size=size,
            color=color,
            cids=self.font_is_cid(font),
        )
        self.y += leading

    def text(
        self,
        text: str,
        *,
        x: float = 0.0,
        size: float = 12.0,
        font: str = "F1",
        color: Optional[RGB] = None,
        leading: Optional[float] = None,
    ) -> None:
        """Draw one line at the cursor; advance the cursor by the leading.

        Under tagging the line is a heading (``H1``): one BDC block carrying
        a fresh MCID, owned by a new ``H1`` StructElem under the Document.
        """
        line_leading = leading if leading is not None else size * _LINE_HEIGHT_FACTOR
        manager = self.structure_manager()
        if manager is not None:
            elem = manager.create_element("H1", parent=manager.document_element())
            mcid = manager.begin_content(elem, self.page_index)
            self.stream.begin_mcid("H1", mcid)
            self._draw_text_line(
                text, x=x, size=size, font=font, color=color, leading=line_leading
            )
            self.stream.end_marked_content()
        else:
            self._draw_text_line(
                text, x=x, size=size, font=font, color=color, leading=line_leading
            )

    def paragraph(
        self,
        text: str,
        *,
        x: float = 0.0,
        size: float = 12.0,
        font: str = "F1",
        color: Optional[RGB] = None,
        leading: Optional[float] = None,
        max_width: Optional[float] = None,
    ) -> None:
        """Draw a wrapped paragraph from the cursor, breaking pages as needed.

        Under tagging every line lands inside a ``P`` marked-content block;
        when the paragraph crosses a page boundary the block is closed and a
        fresh ``P`` StructElem (owning only the new page's MCIDs, so its
        ``/Pg`` stays correct) continues the text.
        """
        width = self.content_width - x if max_width is None else max_width
        line_height = leading if leading is not None else size * _LINE_HEIGHT_FACTOR
        manager = self.structure_manager()
        elem = None
        for line in wrap_text(text, width, size):
            if elem is not None and self.y + line_height > self._usable_height + 1e-6:
                self.stream.end_marked_content()
                elem = None
            self.ensure_space(line_height)
            if manager is not None and elem is None:
                elem = manager.create_element("P", parent=manager.document_element())
                mcid = manager.begin_content(elem, self.page_index)
                self.stream.begin_mcid("P", mcid)
            self._draw_text_line(
                line, x=x, size=size, font=font, color=color, leading=line_height
            )
        if elem is not None:
            self.stream.end_marked_content()

    def image(
        self,
        data: bytes,
        *,
        x: float,
        y: float,
        width: float,
        height: float,
        alt: Optional[str] = None,
    ) -> str:
        """Draw a JPEG or PNG at top-down ``(x, y)`` scaled to ``width x height``.

        The image bytes are registered (deduplicated) with the host, which
        returns the resource name the ``/ImN Do`` operator references.
        Under tagging the placement is a ``Figure`` with the alternative
        text in the BDC properties and on the StructElem (``alt`` defaults
        to ``"Image"``).
        """
        name = self._host.image_ref(data)
        self.use_xobject(name)
        manager = self.structure_manager()
        if manager is not None:
            alt_text = alt if alt is not None else "Image"
            elem = manager.create_element(
                "Figure", parent=manager.document_element(), alt=alt_text
            )
            mcid = manager.begin_content(elem, self.page_index)
            self.stream.begin_marked_content(
                "Figure", {N("MCID"): mcid, N("Alt"): alt_text}
            )
            self.stream.save_state()
            self.stream.set_matrix(
                width, 0, 0, height, self.margins.left + x, self.pdf_y(y + height)
            )
            self.stream.draw_xobject(name)
            self.stream.restore_state()
            self.stream.end_marked_content()
        else:
            self.stream.save_state()
            self.stream.set_matrix(
                width, 0, 0, height, self.margins.left + x, self.pdf_y(y + height)
            )
            self.stream.draw_xobject(name)
            self.stream.restore_state()
        return name

    def svg(
        self,
        d: str,
        *,
        x: float,
        y: float,
        width: float,
        height: float,
        alt: Optional[str] = None,
        transform: Optional[str] = None,
        fill: Optional[str] = "#000000",
        stroke: Optional[str] = None,
        stroke_width: float = 1.0,
    ) -> str:
        """Draw an SVG path as a Form XObject at top-down ``(x, y)``.

        Mirrors :meth:`image`: the path is registered (deduplicated) with
        the host, which returns the resource name for ``/ImN Do``.  Under
        tagging the placement is a ``Figure`` with the alternative text in
        the BDC properties and on the StructElem (``alt`` defaults to
        ``"Figure"``).
        """
        name = self._host.svg_ref(
            d,
            width=width,
            height=height,
            transform=transform,
            fill=fill,
            stroke=stroke,
            stroke_width=stroke_width,
        )
        self.use_xobject(name)
        manager = self.structure_manager()
        if manager is not None:
            alt_text = alt if alt is not None else "Figure"
            elem = manager.create_element(
                "Figure", parent=manager.document_element(), alt=alt_text
            )
            mcid = manager.begin_content(elem, self.page_index)
            self.stream.begin_marked_content(
                "Figure", {N("MCID"): mcid, N("Alt"): alt_text}
            )
            self.stream.save_state()
            self.stream.set_matrix(
                width, 0, 0, height, self.margins.left + x, self.pdf_y(y + height)
            )
            self.stream.draw_xobject(name)
            self.stream.restore_state()
            self.stream.end_marked_content()
        else:
            self.stream.save_state()
            self.stream.set_matrix(
                width, 0, 0, height, self.margins.left + x, self.pdf_y(y + height)
            )
            self.stream.draw_xobject(name)
            self.stream.restore_state()
        return name

    def link(
        self,
        text: str,
        uri: str,
        *,
        x: float = 0.0,
        size: float = 12.0,
        font: str = "F1",
        color: Optional[RGB] = None,
    ) -> None:
        """Draw one link line: tagged text plus a ``/Link`` annotation.

        Under tagging the line is wrapped in a ``Link`` marked-content block
        whose StructElem carries the MCID and an ``/OBJR`` reference to a
        link annotation (``/Subtype /Link``, ``/Rect`` covering the text,
        ``/A << /S /URI /URI (uri) >>``) registered on the page.  Untagged,
        the line is drawn as plain text.
        """
        line_leading = size * _LINE_HEIGHT_FACTOR
        self.use_font(font)
        self.record_chars(font, text)
        manager = self.structure_manager()
        if manager is not None:
            elem = manager.create_element("Link", parent=manager.document_element())
            mcid = manager.begin_content(elem, self.page_index)
            self.stream.begin_mcid("Link", mcid)
            self.stream.text_line(
                text,
                x=self.margins.left + x,
                y=self.pdf_y(self.y),
                resource_name=font,
                size=size,
                color=color,
                cids=self.font_is_cid(font),
            )
            self.stream.end_marked_content()
            width = text_width(text, size)
            baseline = self.pdf_y(self.y)
            rect = [
                self.margins.left + x,
                baseline - size * 0.25,
                self.margins.left + x + width,
                baseline + size * 0.85,
            ]
            annot_ref = self._host.add_link_annotation(self.page_index, rect, uri)
            manager.add_objr(elem, annot_ref, self._host.page_ref(self.page_index))
        else:
            self.stream.text_line(
                text,
                x=self.margins.left + x,
                y=self.pdf_y(self.y),
                resource_name=font,
                size=size,
                color=color,
                cids=self.font_is_cid(font),
            )
        self.y += line_leading

    def table(self, table: "TableLayout") -> None:
        """Draw a table from the current cursor, breaking pages as needed."""
        table.emit(self)


class TableLayout:
    """A fixed-column-width table grid with borders and header styling.

    Rows are page-breakable between rows only; when a row moves to a fresh
    page the header row (if any) is redrawn at the top of that page.
    """

    def __init__(
        self,
        *,
        col_widths: Sequence[float],
        rows: Sequence[Sequence[str]],
        header: Optional[Sequence[str]] = None,
        font: str = "F1",
        header_font: str = "F2",
        size: float = 10.0,
        header_size: float = 10.0,
        text_color: Optional[RGB] = None,
        header_color: Optional[RGB] = None,
        header_background: Optional[RGB] = (0.13, 0.26, 0.38),
        cell_padding: float = 4.0,
        line_width: float = 0.5,
        line_color: RGB = (0.4, 0.4, 0.4),
        grid: bool = True,
        cell_borders: bool = False,
        border_skip: Optional[Callable[[int, int], bool]] = None,
        min_row_height: Optional[float] = None,
        row_background: Optional[Callable[[int], Optional[RGB]]] = None,
        cell_text_color: Optional[Callable[[int, int], Optional[RGB]]] = None,
    ) -> None:
        self.col_widths = list(col_widths)
        self.rows = [list(row) for row in rows]
        self.header = list(header) if header is not None else None
        self.font = font
        self.header_font = header_font
        self.size = size
        self.header_size = header_size
        self.text_color = text_color
        self.header_color = header_color
        self.header_background = header_background
        self.cell_padding = cell_padding
        self.line_width = line_width
        self.line_color = line_color
        self.grid = grid
        self.cell_borders = cell_borders
        self.border_skip = border_skip
        self.min_row_height = min_row_height
        # Phase 8: optional per-row/per-cell styling hooks (None = off).
        # ``row_background(row_index)`` returns a band fill colour for a
        # data row or None; ``cell_text_color(row_index, col_index)``
        # returns a per-cell text colour (e.g. buy/sell highlighting) or
        # None.  Both leave the phase-2/5/6 paths byte-identical when unset.
        self.row_background = row_background
        self.cell_text_color = cell_text_color

    # ------------------------------------------------------------------
    # Layout math
    # ------------------------------------------------------------------

    def _row_height(self, cells: Sequence[str], size: float) -> float:
        inner = size * _LINE_HEIGHT_FACTOR
        lines = 1
        for cell, width in zip(cells, self.col_widths):
            count = len(wrap_text(cell, width - 2 * self.cell_padding, size))
            lines = max(lines, count)
        height = lines * inner + 2 * self.cell_padding
        if self.min_row_height is not None:
            height = max(height, self.min_row_height)
        return height

    def _column_x(self, x0: float, index: int) -> float:
        return x0 + sum(self.col_widths[:index])

    # ------------------------------------------------------------------
    # Emission
    # ------------------------------------------------------------------

    def emit(self, flow: PageFlow) -> None:
        """Draw the whole table into ``flow`` from its current cursor."""
        total = sum(self.col_widths)
        if total > flow.content_width + 1e-6:
            raise ValueError(
                f"table width {total} exceeds content width {flow.content_width}"
            )
        x0 = flow.margins.left
        x1 = x0 + total
        column_x = [self._column_x(x0, i) for i in range(len(self.col_widths) + 1)]

        manager = flow.structure_manager()
        table_elem = (
            manager.create_element("Table", parent=manager.document_element())
            if manager is not None
            else None
        )

        def draw_header() -> None:
            height = self._row_height(self.header, self.header_size)
            flow.ensure_space(height)
            row_elem = (
                manager.create_element("TR", parent=table_elem, page=flow.page_index)
                if manager is not None
                else None
            )
            self._draw_row(flow, self.header, flow.y, self.header_size, column_x, x1, row_elem, -1)
            flow.y += height
            self._draw_grid(flow, flow.y - height, flow.y, column_x)

        prev_pages = flow.page_count
        if self.header is not None:
            draw_header()
            prev_pages = flow.page_count
        for row_index, row in enumerate(self.rows):
            # Break pages *before* drawing the row, then redraw the header at
            # the top of the fresh page (checking the page count before the
            # break would draw the header below the first row of the page).
            height = self._row_height(row, self.size)
            flow.ensure_space(height)
            if self.header is not None and flow.page_count != prev_pages:
                prev_pages = flow.page_count
                draw_header()
            row_elem = (
                manager.create_element("TR", parent=table_elem, page=flow.page_index)
                if manager is not None
                else None
            )
            self._draw_row(flow, row, flow.y, self.size, column_x, x1, row_elem, row_index)
            flow.y += height
            self._draw_grid(flow, flow.y - height, flow.y, column_x)

    def _draw_row(
        self,
        flow: PageFlow,
        cells: Sequence[str],
        y_top: float,
        size: float,
        column_x: List[float],
        x1: float,
        row_elem: Optional[StructElem] = None,
        row_index: int = -1,
    ) -> None:
        """Draw one row band: cell backgrounds, wrapped text, cell outlines.

        Under tagging the row's decorative graphics (background fill, cell
        borders) are wrapped in an ``/Artifact`` marked-content block, each
        cell's text lines are wrapped in a ``TD``/``TH`` block with a fresh
        MCID owned by a new cell StructElem under the row's ``TR``.

        Phase 6: cells go through :meth:`StructureManager.begin_cell` and
        :meth:`ContentStream.begin_mcid` (no per-cell dictionary churn) and
        ``row_index`` is passed in by the emitter instead of rescanned.
        """
        is_header = cells is self.header
        font = self.header_font if is_header else self.font
        if is_header:
            background = self.header_background
        elif self.row_background is not None:
            background = self.row_background(row_index)
        else:
            background = None
        # Cell text must always get an explicit fill colour: without one the
        # text inherits whatever fill colour is current in the graphics
        # state -- typically the band's background fill -- leaving the text
        # invisible against the band (and every later row dimmed, since the
        # fill colour persists until changed).  Headers over a dark band
        # render white, everything else black.
        text_color = self.header_color if is_header else self.text_color
        if text_color is None:
            text_color = (1.0, 1.0, 1.0) if (
                background is not None and _is_dark(background)
            ) else (0.0, 0.0, 0.0)
        leading = size * _LINE_HEIGHT_FACTOR
        height = self._row_height(cells, size)

        flow.use_font(font)
        manager = flow.structure_manager()
        if manager is not None and background is not None:
            flow.stream.begin_artifact({N("Type"): N("Layout")})
            flow.stream.set_color_rgb(*background)
            flow.stream.filled_rect(
                flow.margins.left, flow.pdf_y(y_top + height), x1 - flow.margins.left, height
            )
            flow.stream.end_marked_content()
        elif background is not None:
            flow.stream.set_color_rgb(*background)
            flow.stream.filled_rect(
                flow.margins.left, flow.pdf_y(y_top + height), x1 - flow.margins.left, height
            )
        flow.stream.set_color_rgb(*text_color)

        tag = "TH" if is_header else "TD"
        for col, cell in enumerate(cells):
            width = self.col_widths[col]
            border = self.cell_borders and not self._skip_border(row_index, col)
            if border and manager is not None:
                flow.stream.begin_artifact({N("Type"): N("Layout")})
                flow.stream.stroked_rect(
                    column_x[col], flow.pdf_y(y_top + height), width, height
                )
                flow.stream.end_marked_content()
            elif border:
                flow.stream.stroked_rect(
                    column_x[col], flow.pdf_y(y_top + height), width, height
                )
            lines = wrap_text(cell, width - 2 * self.cell_padding, size)
            if not is_header and self.cell_text_color is not None and lines:
                cell_color = self.cell_text_color(row_index, col)
                if cell_color is not None:
                    flow.stream.set_color_rgb(*cell_color)
            if manager is not None and lines:
                # TH cells carry /ID + /Scope; TD cells reference the header
                # of their column via /Headers (ISO 32000-2 14.8.5.7), so the
                # header association survives page breaks and repeats.
                header_id = "H%d" % col
                cell_elem, mcid = manager.begin_cell(
                    tag,
                    parent=row_elem,
                    page_index=flow.page_index,
                    scope="Column" if tag == "TH" else None,
                    struct_id=header_id if tag == "TH" else None,
                    headers=[header_id] if tag == "TD" else None,
                )
                flow.stream.begin_mcid(tag, mcid)
            for line_index, line in enumerate(lines):
                flow.record_chars(font, line)
                flow.stream.text_line(
                    line,
                    x=column_x[col] + self.cell_padding,
                    y=flow.pdf_y(
                        _cell_text_baseline(
                            y_top, height, line_index, len(lines), leading
                        )
                    ),
                    resource_name=font,
                    size=size,
                    cids=flow.font_is_cid(font),
                )
            if manager is not None and lines:
                flow.stream.end_marked_content()

    def _skip_border(self, row_index: int, col_index: int) -> bool:
        if self.border_skip is None:
            return False
        return self.border_skip(row_index, col_index)

    def _draw_grid(
        self, flow: PageFlow, y_top: float, y_bottom: float, column_x: List[float]
    ) -> None:
        """Stroke the row band's grid lines (shared separators)."""
        if not self.grid:
            return
        stream = flow.stream
        manager = flow.structure_manager()
        if manager is not None:
            stream.begin_artifact({N("Type"): N("Layout")})
        stream.set_stroke_color_rgb(*self.line_color)
        stream.set_line_width(self.line_width)
        top = flow.pdf_y(y_top)
        bottom = flow.pdf_y(y_bottom)
        stream.line(column_x[0], top, column_x[-1], top)
        stream.line(column_x[0], bottom, column_x[-1], bottom)
        for x in column_x[1:-1]:
            stream.line(x, top, x, bottom)
        if manager is not None:
            stream.end_marked_content()


class RichTableLayout:
    """Fixed-column table with per-cell styling (template financial path).

    Unlike :class:`TableLayout` (uniform string rows + optional hooks), each
    cell carries its own font, size, colours, alignment, borders and optional
    embedded image.  Used by the full-format JSON template renderer.
    """

    def __init__(
        self,
        *,
        col_widths: Sequence[float],
        rows: Sequence[Sequence[StyledCell]],
        cell_padding: float = 4.0,
        line_width: float = 0.5,
        line_color: RGB = (0.3, 0.3, 0.3),
        default_text_color: RGB = (0.0, 0.0, 0.0),
    ) -> None:
        self.col_widths = list(col_widths)
        self.rows = [list(row) for row in rows]
        self.cell_padding = cell_padding
        self.line_width = line_width
        self.line_color = line_color
        self.default_text_color = default_text_color

    def _row_height(self, cells: Sequence[StyledCell]) -> float:
        height = 0.0
        for cell, width in zip(cells, self.col_widths):
            if cell.image_data is not None and cell.image_h > 0:
                height = max(height, cell.image_h + 2 * self.cell_padding)
            if cell.min_height > 0:
                height = max(height, cell.min_height)
            size = cell.size if cell.size > 0 else 10.0
            lines = wrap_text(
                cell.text, max(1.0, width - 2 * self.cell_padding), size
            ) if cell.text else []
            text_h = max(1, len(lines)) * size * _LINE_HEIGHT_FACTOR + 2 * self.cell_padding
            height = max(height, text_h)
        return height if height > 0 else 25.0

    def emit(self, flow: PageFlow) -> None:
        """Draw the table into ``flow`` from its current cursor."""
        total = sum(self.col_widths)
        if total > flow.content_width + 1e-6:
            raise ValueError(
                f"table width {total} exceeds content width {flow.content_width}"
            )
        x0 = flow.margins.left
        column_x = [x0 + sum(self.col_widths[:i]) for i in range(len(self.col_widths) + 1)]

        manager = flow.structure_manager()
        table_elem = (
            manager.create_element("Table", parent=manager.document_element())
            if manager is not None
            else None
        )

        for row in self.rows:
            # Pad short rows so column count matches.
            cells = list(row)
            while len(cells) < len(self.col_widths):
                cells.append(StyledCell())
            height = self._row_height(cells)
            flow.ensure_space(height)
            row_elem = (
                manager.create_element("TR", parent=table_elem, page=flow.page_index)
                if manager is not None
                else None
            )
            self._draw_row(flow, cells, flow.y, height, column_x, row_elem)
            flow.y += height

    def _draw_row(
        self,
        flow: PageFlow,
        cells: Sequence[StyledCell],
        y_top: float,
        height: float,
        column_x: List[float],
        row_elem: Optional[StructElem],
    ) -> None:
        manager = flow.structure_manager()
        stream = flow.stream
        for col, cell in enumerate(cells):
            if col >= len(self.col_widths):
                break
            width = self.col_widths[col]
            x = column_x[col]
            pdf_bottom = flow.pdf_y(y_top + height)

            # Background fill (artifact under tagging).
            if cell.fill_color is not None:
                if manager is not None:
                    stream.begin_artifact({N("Type"): N("Layout")})
                stream.set_color_rgb(*cell.fill_color)
                stream.filled_rect(x, pdf_bottom, width, height)
                if manager is not None:
                    stream.end_marked_content()

            # Optional cell image (centered in the cell box).
            if cell.image_data is not None:
                iw = cell.image_w if cell.image_w > 0 else width - 2 * self.cell_padding
                ih = cell.image_h if cell.image_h > 0 else height - 2 * self.cell_padding
                iw = min(iw, width - 2 * self.cell_padding)
                ih = min(ih, height - 2 * self.cell_padding)
                ix = (width - iw) / 2.0
                iy = y_top + (height - ih) / 2.0
                # flow.image expects top-down coords relative to content box.
                flow.image(
                    cell.image_data,
                    x=x - flow.margins.left + ix,
                    y=iy,
                    width=iw,
                    height=ih,
                    alt="Image",
                )

            # Text (always emit a TD under tagging so tables stay regular).
            size = cell.size if cell.size > 0 else 10.0
            leading = size * _LINE_HEIGHT_FACTOR
            font = cell.font or "F1"
            text_color = cell.text_color if cell.text_color is not None else self.default_text_color
            lines = wrap_text(
                cell.text, max(1.0, width - 2 * self.cell_padding), size
            ) if cell.text else []
            # Always emit a TD under tagging so every row has the same column count
            # (PDF/UA-2 table regularity), including empty and image cells.
            if manager is not None and row_elem is not None:
                _cell_elem, mcid = manager.begin_cell(
                    "TD",
                    parent=row_elem,
                    page_index=flow.page_index,
                )
                stream.begin_mcid("TD", mcid)
            if lines:
                flow.use_font(font)
                for line_index, line in enumerate(lines):
                    flow.record_chars(font, line)
                    tw = text_width(line, size)
                    if cell.align == "center":
                        tx = x + (width - tw) / 2.0
                    elif cell.align == "right":
                        tx = x + width - self.cell_padding - tw
                    else:
                        tx = x + self.cell_padding
                    stream.text_line(
                        line,
                        x=tx,
                        y=flow.pdf_y(
                            _cell_text_baseline(
                                y_top, height, line_index, len(lines), leading
                            )
                        ),
                        resource_name=font,
                        size=size,
                        color=text_color,
                        cids=flow.font_is_cid(font),
                    )
            if manager is not None and row_elem is not None:
                stream.end_marked_content()

            # Per-side borders (artifact under tagging).
            bl, br, bt, bb = cell.border
            if bl or br or bt or bb:
                if manager is not None:
                    stream.begin_artifact({N("Type"): N("Layout")})
                stream.set_stroke_color_rgb(*self.line_color)
                stream.set_line_width(self.line_width)
                top = flow.pdf_y(y_top)
                bottom = pdf_bottom
                if bl:
                    stream.line(x, top, x, bottom)
                if br:
                    stream.line(x + width, top, x + width, bottom)
                if bt:
                    stream.line(x, top, x + width, top)
                if bb:
                    stream.line(x, bottom, x + width, bottom)
                if manager is not None:
                    stream.end_marked_content()
