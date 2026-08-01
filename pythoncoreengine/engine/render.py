"""Zerodha-style contract-note renderer: model -> layout tables -> PDF bytes.

Phase 8 application layer over the **public** engine API only (no private
internals): a :class:`~engine.model.ContractNote` is mapped onto
``DocumentBuilder`` / ``PageFlow`` / ``TableLayout`` with the Zerodha
theme (dark-blue header bar #154360, section rows #21618C, buy/sell cell
colours #27AE60/#E74C3C, alternating row bands #F8F9F9) and the footer +
diagonal watermark, then rendered as PDF/A-4 + PDF/UA-2 (compliant) or
plain PDF 2.0 (``compliant=False``) -- fonts stay embedded in both modes.

"Page X of Y" needs the total page count, which is only known once the
content has flowed, so rendering is two-pass: the content lays out first,
then the footer line and page numbers are stamped onto every finished
page stream.  The stamped text flows through the same font-usage
recording path as the layout (``flow.record_chars``), so the footer /
page-number characters land in the embedded subsets, and it is wrapped in
``/Artifact`` marked content so tagged (UA-2) output stays valid.
"""

from __future__ import annotations

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
from engine.color import ZerodhaTheme
from engine.model import ContractNote, Trade

__all__ = ["FIXED_CREATED", "build_document", "build_trade_table"]

# Fixed creation date: byte-stable output across runs (mirrors engine.fixtures).
FIXED_CREATED = datetime.datetime(2026, 8, 1, 12, 0, 0)

THEME = ZerodhaTheme()

_MARGINS = PageMargins(48, 48, 48, 48)
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
