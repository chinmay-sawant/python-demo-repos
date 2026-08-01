"""Renderers: Zerodha contract notes + full-format PDF templates.

Phase 8 application layer over the **public** engine API only (no private
internals).

* :func:`build_document` maps a :class:`~engine.model.ContractNote` onto
  ``DocumentBuilder`` / ``PageFlow`` / ``TableLayout`` with the Zerodha
  theme (header #154360, section #21618C, buy/sell colours, alt rows) and
  stamps footer + watermark.
* :func:`build_template_document` maps a :class:`~engine.model.PDFTemplate`
  (full ``config``/``title``/``elements`` JSON) onto
  :class:`~engine.layout.RichTableLayout` cells, honouring per-cell props,
  colours, borders and embedded images.  Compliance follows the template
  config (``pdfaCompliant`` / ``arlingtonCompatible``) unless the caller
  overrides ``compliant=``.

Both paths keep fonts embedded.  "Page X of Y" is two-pass: content
flows first, then footer / page numbers are stamped as ``/Artifact``
marked content so tagged (UA-2) output stays valid.
"""

from __future__ import annotations

import base64
import datetime
import math
from typing import Callable, List, Optional, Sequence, Tuple

from engine import (
    N,
    ContentStream,
    DocumentBuilder,
    PageMargins,
    TableLayout,
    text_width,
)
from engine.color import ZerodhaTheme, hex_to_rgb
from engine.layout import RichTableLayout, StyledCell, parse_props
from engine.model import (
    ContractNote,
    PDFTemplate,
    TableCell,
    TableDef,
    Title,
    Trade,
)

__all__ = [
    "FIXED_CREATED",
    "build_document",
    "build_template_document",
    "build_trade_table",
    "template_wants_compliant",
]

# Fixed creation date: byte-stable output across runs (mirrors engine.fixtures).
FIXED_CREATED = datetime.datetime(2026, 8, 1, 12, 0, 0)

THEME = ZerodhaTheme()

_MARGINS = PageMargins(48, 48, 48, 48)
# Template path uses the gocorepdfengine financial margins (36/40).
_TEMPLATE_MARGINS = PageMargins(36, 36, 40, 40)
_FOOTER_SIZE = 8.0
_FOOTER_BASELINE_GAP = 24.0  # baseline sits this far inside the bottom margin
_SECTION_SIZE = 10.0
_GRID_SIZE = 8.0
_GRID_PADDING = 2.0
_INFO_SIZE = 9.0

# Fixed trade grid columns: Symbol, ISIN, Action, Qty, Price, Total.
_TRADE_WIDTHS: List[float] = [110.0, 90.0, 60.0, 60.0, 80.0, 90.0]
_TRADE_HEADERS = ["Symbol", "ISIN", "Action", "Qty", "Price", "Total"]
_ACTION_COLUMN = 2

_WATERMARK_SIZE = 60.0
_WATERMARK_TEXT = "CONFIDENTIAL"
_WATERMARK_ANGLE = math.radians(45.0)

_LABEL_WIDTH = 110.0
_FIN_LABEL_WIDTH = 160.0

_FINANCIAL_KEYS = ("net_obligation", "stt_tax", "brokerage", "regulatory_charges")


def _money(value: float) -> str:
    """Two-decimal, thousand-separated money string."""
    return "{:,.2f}".format(value)


def _grid_rows(trades: Sequence[Trade]) -> List[List[str]]:
    """The trade grid's string rows (Action kept as BUY/SELL)."""
    return [
        [
            trade.symbol,
            trade.isin or "-",
            trade.action,
            str(trade.qty),
            "%.2f" % trade.price,
            _money(trade.total),
        ]
        for trade in trades
    ]


def _action_color(
    trades: Sequence[Trade],
) -> Callable[[int, int], Optional[Tuple[float, float, float]]]:
    """Cell-colour hook: buy rows green, sell rows red (Action column only)."""

    def pick(
        row_index: int, col_index: int
    ) -> Optional[Tuple[float, float, float]]:
        if col_index != _ACTION_COLUMN:
            return None
        action = trades[row_index].action.upper()
        if action == "BUY":
            return THEME.buy
        if action == "SELL":
            return THEME.sell
        return None

    return pick


def build_trade_table(
    note: ContractNote, col_widths: Optional[Sequence[float]] = None
) -> TableLayout:
    """The fixed-column trade grid for ``note`` (Zerodha theme colours)."""
    widths = list(col_widths) if col_widths is not None else list(_TRADE_WIDTHS)
    return TableLayout(
        col_widths=widths,
        header=list(_TRADE_HEADERS),
        rows=_grid_rows(note.trades),
        header_font="F2",
        header_size=9.0,
        size=_GRID_SIZE,
        cell_padding=_GRID_PADDING,
        header_background=THEME.section_bar,
        header_color=THEME.section_text,
        text_color=THEME.body_text,
        line_color=THEME.grid_line,
        row_background=lambda index: THEME.alt_row_bg if index % 2 == 0 else None,
        cell_text_color=_action_color(note.trades),
    )


def _title_bar(flow, note: ContractNote, width: float) -> None:
    """The full-width #154360 title bar (white bold text)."""
    flow.table(
        TableLayout(
            col_widths=[width],
            header=[note.title or "Contract Note"],
            rows=[],
            header_font="F2",
            header_size=14.0,
            header_background=THEME.header_bar,
            header_color=THEME.header_text,
            grid=False,
        )
    )


def _section_bar(flow, title: str, width: float) -> None:
    """One full-width #21618C section header row."""
    flow.table(
        TableLayout(
            col_widths=[width],
            header=[title],
            rows=[],
            header_font="F2",
            header_size=_SECTION_SIZE,
            header_background=THEME.section_bar,
            header_color=THEME.section_text,
            grid=False,
        )
    )


def _label_rows(pairs: Sequence[Tuple[str, str]]) -> List[List[str]]:
    return [[label, value] for label, value in pairs]


def _info_table(flow, rows: List[List[str]], label_width: float, width: float) -> None:
    """A two-column label/value table with alternating row bands."""
    flow.table(
        TableLayout(
            col_widths=[label_width, width - label_width],
            rows=rows,
            size=_INFO_SIZE,
            text_color=THEME.body_text,
            line_color=THEME.grid_line,
            row_background=lambda index: THEME.alt_row_bg if index % 2 == 0 else None,
        )
    )


def _layout_content(flow, note: ContractNote) -> None:
    """Pass one: flow every content block (page count unknown yet)."""
    width = flow.content_width
    _title_bar(flow, note, width)
    flow.y += 8.0
    _section_bar(flow, "CLIENT INFORMATION", width)
    _info_table(
        flow,
        _label_rows(
            [
                ("Client Name", note.client_name),
                ("Client Code", note.client_code),
                ("PAN", note.client_pan),
            ]
        ),
        _LABEL_WIDTH,
        width,
    )
    flow.y += 8.0
    _section_bar(flow, "TRADE DETAILS", width)
    flow.table(build_trade_table(note))
    flow.y += 8.0
    _section_bar(flow, "FINANCIAL SUMMARY", width)
    pairs: List[Tuple[str, str]] = [
        ("Total Trades", str(len(note.trades))),
        ("Total Turnover", _money(sum(trade.total for trade in note.trades))),
    ]
    for key in _FINANCIAL_KEYS:
        if key in note.financials:
            pairs.append((key.replace("_", " ").title(), _money(note.financials[key])))
    _info_table(flow, _label_rows(pairs), _FIN_LABEL_WIDTH, width)


def _open_artifact(flow, stream: ContentStream, kind: str) -> bool:
    """Open an /Artifact block when tagged; returns whether one was opened.

    Pagination chrome and the watermark must not join the UA-2 structure
    tree, so tagged output wraps them as artifacts (explicitly non-real
    content); untagged output just draws the text.
    """
    if flow.structure_manager() is None:
        return False
    stream.begin_artifact({N("Type"): N(kind)})
    return True


def _stamp_pagination(flow, note: ContractNote) -> None:
    """Pass two: footer line + "Page X of Y" on every finished page stream."""
    total = flow.page_count
    for index, stream in enumerate(flow.streams):
        page_label = "Page %d of %d" % (index + 1, total)
        flow.use_font("F1")
        flow.record_chars("F1", note.footer_text)
        flow.record_chars("F1", page_label)
        baseline = flow.pdf_y(
            flow.page_size[1] - flow.margins.top - flow.margins.bottom
            + _FOOTER_BASELINE_GAP
        )
        opened = _open_artifact(flow, stream, "Pagination")
        stream.text_line(
            note.footer_text,
            x=flow.margins.left,
            y=baseline,
            resource_name="F1",
            size=_FOOTER_SIZE,
            color=THEME.body_text,
            cids=flow.font_is_cid("F1"),
        )
        label_x = (
            flow.margins.left + flow.content_width - text_width(page_label, _FOOTER_SIZE)
        )
        stream.text_line(
            page_label,
            x=label_x,
            y=baseline,
            resource_name="F1",
            size=_FOOTER_SIZE,
            color=THEME.body_text,
            cids=flow.font_is_cid("F1"),
        )
        if opened:
            stream.end_marked_content()


def _stamp_watermark(flow, note: ContractNote) -> None:
    """Diagonal grey watermark on every page (note.watermark flag)."""
    if not note.watermark:
        return
    cos_a, sin_a = math.cos(_WATERMARK_ANGLE), math.sin(_WATERMARK_ANGLE)
    center_x = flow.margins.left + flow.content_width / 2.0
    center_y = flow.page_size[1] / 2.0
    half = text_width(_WATERMARK_TEXT, _WATERMARK_SIZE) / 2.0
    for stream in flow.streams:
        flow.use_font("F1")
        flow.record_chars("F1", _WATERMARK_TEXT)
        opened = _open_artifact(flow, stream, "Layout")
        stream.save_state()
        stream.set_matrix(cos_a, sin_a, -sin_a, cos_a, center_x, center_y)
        stream.text_line(
            _WATERMARK_TEXT,
            x=-half,
            y=0.0,
            resource_name="F1",
            size=_WATERMARK_SIZE,
            color=THEME.watermark,
            cids=flow.font_is_cid("F1"),
        )
        stream.restore_state()
        if opened:
            stream.end_marked_content()


def build_document(note: ContractNote, *, compliant: bool = True) -> bytes:
    """Render ``note`` to PDF bytes.

    ``compliant=True`` (default) builds PDF/A-4 + PDF/UA-2 with embedded
    fonts, XMP metadata, ICC profiles/OutputIntent and the tagged
    structure tree; ``compliant=False`` emits plain PDF 2.0 (no XMP/ICC/
    structure, /Info present) but keeps fonts embedded so text renders
    offline.  Deterministic: fixed creation date, seeded expansion, no
    wall-clock inputs.
    """
    builder = DocumentBuilder(
        margins=_MARGINS,
        created=FIXED_CREATED,
        mode_pdfa4=compliant,
        mode_pdfua2=compliant,
        mode_embed_fonts=True,
        title=note.title,
    )
    flow = builder.flow()
    _layout_content(flow, note)
    _stamp_pagination(flow, note)
    _stamp_watermark(flow, note)
    return builder.render()


# ---------------------------------------------------------------------------
# Full-format template renderer (financial report JSON)
# ---------------------------------------------------------------------------


def template_wants_compliant(template: PDFTemplate) -> bool:
    """Whether the template config asks for PDF/A-4 + PDF/UA-2."""
    return template.config.wants_compliant()


def _safe_hex(value: str) -> Optional[Tuple[float, float, float]]:
    if not value:
        return None
    try:
        return hex_to_rgb(value)
    except ValueError:
        return None


def _font_resource(props_font: str, bold: bool) -> str:
    """Map Helvetica(+Bold) template faces onto F1/F2 registry names."""
    name = (props_font or "Helvetica").lower()
    if bold or "bold" in name:
        return "F2"
    return "F1"


def _cell_to_styled(
    cell: TableCell,
    *,
    default_bg: str = "",
    default_tc: str = "",
) -> StyledCell:
    props = parse_props(cell.props)
    fill = _safe_hex(cell.bgcolor) or _safe_hex(default_bg)
    text_color = _safe_hex(cell.textcolor) or _safe_hex(default_tc)
    image_data = None
    image_w = 0.0
    image_h = 0.0
    if cell.image is not None and cell.image.image_data:
        try:
            image_data = base64.b64decode(cell.image.image_data)
        except Exception:
            image_data = None
        image_w = cell.image.width
        image_h = cell.image.height
    return StyledCell(
        text=cell.text,
        font=_font_resource(props.font_name, props.bold),
        size=props.font_size if props.font_size > 0 else 10.0,
        text_color=text_color,
        fill_color=fill,
        align=props.align,
        border=props.border,
        min_height=cell.height,
        image_data=image_data,
        image_w=image_w,
        image_h=image_h,
    )


def _scale_col_widths(weights: Sequence[float], content_w: float) -> List[float]:
    values = list(weights) if weights else [1.0]
    total = sum(values)
    if total <= 0:
        n = len(values)
        return [content_w / n] * n
    factor = content_w / total
    return [w * factor for w in values]


def _table_def_to_layout(td: TableDef, content_w: float) -> RichTableLayout:
    n_cols = td.maxcolumns if td.maxcolumns > 0 else 1
    weights = list(td.columnwidths) if td.columnwidths else [1.0] * n_cols
    while len(weights) < n_cols:
        weights.append(1.0)
    col_widths = _scale_col_widths(weights[:n_cols], content_w)
    rows: List[List[StyledCell]] = []
    for row_index, row in enumerate(td.rows):
        styled: List[StyledCell] = []
        for cell in row.row:
            sc = _cell_to_styled(
                cell, default_bg=td.bgcolor, default_tc=td.textcolor
            )
            if row_index < len(td.rowheights) and td.rowheights[row_index] > sc.min_height:
                sc.min_height = td.rowheights[row_index]
            styled.append(sc)
        while len(styled) < n_cols:
            styled.append(StyledCell())
        rows.append(styled)
    return RichTableLayout(col_widths=col_widths, rows=rows)


def _title_to_layout(title: Title, content_w: float) -> Optional[RichTableLayout]:
    if title.table is not None:
        return _table_def_to_layout(title.table, content_w)
    if not title.text:
        return None
    props = parse_props(title.props)
    row_h = max(36.0, props.font_size * 2 + 12)
    cell = StyledCell(
        text=title.text,
        font=_font_resource(props.font_name, props.bold),
        size=props.font_size if props.font_size > 0 else 18.0,
        text_color=_safe_hex(title.textcolor),
        fill_color=_safe_hex(title.bgcolor),
        align=props.align or "center",
        border=props.border,
        min_height=row_h,
    )
    return RichTableLayout(col_widths=[content_w], rows=[[cell]])


def _spacer_layout(height: float, content_w: float) -> RichTableLayout:
    return RichTableLayout(
        col_widths=[content_w],
        rows=[[StyledCell(min_height=height)]],
    )


def _layout_template(flow, template: PDFTemplate) -> None:
    content_w = flow.content_width
    if template.title is not None:
        layout = _title_to_layout(template.title, content_w)
        if layout is not None:
            layout.emit(flow)
    for elem in template.elements:
        if elem.type == "table":
            if 0 <= elem.index < len(template.tables):
                _table_def_to_layout(template.tables[elem.index], content_w).emit(flow)
        elif elem.type == "spacer":
            if 0 <= elem.index < len(template.spacers):
                h = template.spacers[elem.index].height
                if h > 0:
                    _spacer_layout(h, content_w).emit(flow)


def _stamp_template_pagination(flow, footer_text: str) -> None:
    """Footer line + Page X of Y on every page (artifact under tagging)."""
    total = flow.page_count
    for index, stream in enumerate(flow.streams):
        page_label = "Page %d of %d" % (index + 1, total)
        flow.use_font("F1")
        if footer_text:
            flow.record_chars("F1", footer_text)
        flow.record_chars("F1", page_label)
        baseline = flow.pdf_y(
            flow.page_size[1] - flow.margins.top - flow.margins.bottom
            + _FOOTER_BASELINE_GAP
        )
        opened = _open_artifact(flow, stream, "Pagination")
        if footer_text:
            stream.text_line(
                footer_text,
                x=flow.margins.left,
                y=baseline,
                resource_name="F1",
                size=_FOOTER_SIZE,
                color=THEME.body_text,
                cids=flow.font_is_cid("F1"),
            )
        label_x = (
            flow.margins.left + flow.content_width - text_width(page_label, _FOOTER_SIZE)
        )
        stream.text_line(
            page_label,
            x=label_x,
            y=baseline,
            resource_name="F1",
            size=_FOOTER_SIZE,
            color=THEME.body_text,
            cids=flow.font_is_cid("F1"),
        )
        if opened:
            stream.end_marked_content()


def _stamp_template_watermark(flow, text: str) -> None:
    if not text:
        return
    cos_a, sin_a = math.cos(_WATERMARK_ANGLE), math.sin(_WATERMARK_ANGLE)
    center_x = flow.margins.left + flow.content_width / 2.0
    center_y = flow.page_size[1] / 2.0
    half = text_width(text, _WATERMARK_SIZE) / 2.0
    for stream in flow.streams:
        flow.use_font("F1")
        flow.record_chars("F1", text)
        opened = _open_artifact(flow, stream, "Layout")
        stream.save_state()
        stream.set_matrix(cos_a, sin_a, -sin_a, cos_a, center_x, center_y)
        stream.text_line(
            text,
            x=-half,
            y=0.0,
            resource_name="F1",
            size=_WATERMARK_SIZE,
            color=THEME.watermark,
            cids=flow.font_is_cid("F1"),
        )
        stream.restore_state()
        if opened:
            stream.end_marked_content()


def build_template_document(
    template: PDFTemplate,
    *,
    compliant: Optional[bool] = None,
) -> bytes:
    """Render a full-format :class:`PDFTemplate` to PDF bytes.

    ``compliant=None`` (default) follows the template config
    (``pdfaCompliant`` or ``arlingtonCompatible``).  Pass ``True``/``False``
    to force PDF/A-4+UA-2 or plain PDF 2.0.  Fonts stay embedded either way.
    Deterministic: fixed creation date, no wall-clock inputs.
    """
    if compliant is None:
        compliant = template_wants_compliant(template)
    page_w, page_h = template.config.page_size()
    # Landscape when pageAlignment == 2.
    if template.config.page_alignment == 2 and page_w < page_h:
        page_w, page_h = page_h, page_w

    doc_title = template.config.pdf_title
    if not doc_title and template.title is not None:
        doc_title = template.title.text

    builder = DocumentBuilder(
        page_size=(page_w, page_h),
        margins=_TEMPLATE_MARGINS,
        created=FIXED_CREATED,
        mode_pdfa4=compliant,
        mode_pdfua2=compliant,
        mode_embed_fonts=True,
        title=doc_title or None,
    )
    flow = builder.flow()
    _layout_template(flow, template)
    footer_text = template.footer.text if template.footer is not None else ""
    _stamp_template_pagination(flow, footer_text)
    _stamp_template_watermark(flow, template.config.watermark)
    return builder.render()
