"""Domain models: Zerodha contract notes + full-format PDF templates (stdlib only).

Phase 8 application layer on top of the engine.

* :func:`load_json` reads Zerodha domain fixtures under ``sampledata/zerodha/``
  into a :class:`ContractNote`; :func:`expand_trades` grows a note to a
  target trade count with a deterministic per-seed PRNG.
* :func:`load_template` reads the full ``config``/``title``/``table``/
  ``elements``/``footer`` JSON (TEMPLATE_REFERENCE.md) into a
  :class:`PDFTemplate` used by the financial-report path
  (``sampledata/financial/financial_report.json``).

Deliberately engine-free: this module imports nothing from the rest of
``engine`` so the model stays a plain data layer.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

__all__ = [
    "ContractNote",
    "Trade",
    "expand_trades",
    "load_json",
    "PAGE_SIZES",
    "Config",
    "Title",
    "TableDef",
    "TableRow",
    "TableCell",
    "CellImage",
    "Spacer",
    "Element",
    "Footer",
    "PDFTemplate",
    "load_template",
]

# The default footer props string (font:size:style:alignment, TEMPLATE_REFERENCE).
DEFAULT_FOOTER_FONT = "Helvetica:8:000:center"

# Expanded trade counts per workload tier (sampledata/zerodha/README.md).
TARGET_TRADES: Dict[str, int] = {"retail": 2, "active": 40, "hft": 2000}

# Page sizes in points (TEMPLATE_REFERENCE / gocorepdfengine model.PageSizes).
PAGE_SIZES: Dict[str, Tuple[float, float]] = {
    "A3": (842.0, 1191.0),
    "A4": (595.0, 842.0),
    "A5": (420.0, 595.0),
    "LETTER": (612.0, 792.0),
    "LEGAL": (612.0, 1008.0),
}


@dataclass
class Trade:
    """One executed trade on a contract note."""

    symbol: str
    action: str
    qty: int
    price: float
    total: float
    currency: str = ""
    isin: str = ""
    time: str = ""


@dataclass
class ContractNote:
    """The rendered document: client identity, trades, footer, watermark."""

    client_name: str
    client_code: str
    client_pan: str
    trades: List[Trade]
    footer_font: str = DEFAULT_FOOTER_FONT
    footer_text: str = ""
    watermark: bool = False
    title: str = ""
    financials: Dict[str, float] = field(default_factory=dict)


def _parse_trade(raw: Dict[str, Any]) -> Trade:
    """One JSON trade object -> a :class:`Trade` (action normalised to caps)."""
    return Trade(
        symbol=str(raw.get("symbol", "")),
        action=str(raw.get("action", "")).upper(),
        qty=int(raw.get("qty", 0)),
        price=float(raw.get("price", 0.0)),
        total=float(raw.get("total", 0.0)),
        currency=str(raw.get("currency", "")),
        isin=str(raw.get("isin", "")),
        time=str(raw.get("time", "")),
    )


def load_json(path: Union[str, Path]) -> ContractNote:
    """Load one Zerodha domain fixture into a :class:`ContractNote`.

    Maps the domain JSON onto the model: ``client`` -> client fields,
    ``trades[]`` -> trade list, ``footer.font/text`` -> footer, the
    presence of ``features.watermark`` -> watermark flag,
    ``metadata.title`` (or a derived "Contract Note - <code>") -> title
    and ``financials``/``summary`` -> the financials dict.
    """
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)
    client = data.get("client", {})
    footer = data.get("footer", {})
    metadata = data.get("metadata", {})
    features = data.get("features", {})
    financials = data.get("financials") or data.get("summary") or {}
    code = str(client.get("code", ""))
    return ContractNote(
        client_name=str(client.get("name", "")),
        client_code=code,
        client_pan=str(client.get("pan", "")),
        trades=[_parse_trade(trade) for trade in data.get("trades", [])],
        footer_font=str(footer.get("font", DEFAULT_FOOTER_FONT)),
        footer_text=str(footer.get("text", "")),
        watermark="watermark" in features,
        title=str(metadata.get("title") or "Contract Note - " + code),
        financials={
            key: float(value)
            for key, value in financials.items()
            if isinstance(value, (int, float))
        },
    )


def expand_trades(note: ContractNote, target_count: int, seed: int) -> ContractNote:
    """A new note with ``target_count`` trades, deterministic per ``seed``.

    Base trades are cycled in order; each derived trade keeps the source
    symbol/action/ISIN/currency and drifts the price by up to +/-2% and
    the quantity by up to +/-5% through ``random.Random(seed)`` (the
    Mersenne Twister is version-stable across Python releases, so the
    output is reproducible per seed).  ``target_count`` trades with
    ``target_count <= len(note.trades)`` returns the first N base trades
    (no drift needed); the retail tier is never expanded.
    """
    base = note.trades
    if target_count <= len(base):
        trades = list(base[:target_count])
    else:
        rng = random.Random(seed)
        trades = [_derived_trade(base[index % len(base)], rng) for index in range(target_count)]
    return replace(note, trades=trades)


def _derived_trade(source: Trade, rng: random.Random) -> Trade:
    """One expanded trade: cycle ``source`` with seeded price/qty drift."""
    qty_step = max(1, source.qty // 20)
    qty = source.qty + rng.randint(-qty_step, qty_step)
    if qty < 1:
        qty = 1
    price = round(source.price * (1.0 + rng.uniform(-0.02, 0.02)), 2)
    total = round(price * qty, 2)
    return Trade(
        source.symbol,
        source.action,
        qty,
        price,
        total,
        source.currency,
        source.isin,
        source.time,
    )


# Defined after Trade so the module-level fallback can reference it.
_FALLBACK_TRADE = Trade("RELIANCE", "BUY", 100, 2400.0, 240000.0, currency="INR")


# ---------------------------------------------------------------------------
# Full-format PDF template (TEMPLATE_REFERENCE.md / sampledata/financial)
# ---------------------------------------------------------------------------


@dataclass
class Config:
    """Page / compliance flags from the template ``config`` object."""

    page_border: str = "0:0:0:0"
    page: str = "A4"
    page_alignment: int = 1
    watermark: str = ""
    pdf_title: str = ""
    pdfa_compliant: bool = False
    arlington_compatible: bool = False
    embed_fonts: bool = True

    def page_size(self) -> Tuple[float, float]:
        """``(width, height)`` in points for ``self.page`` (A4 fallback)."""
        key = (self.page or "A4").upper()
        return PAGE_SIZES.get(key, PAGE_SIZES["A4"])

    def wants_compliant(self) -> bool:
        """True when JSON asks for PDF/A-4 + PDF/UA-2 (or Arlington)."""
        return bool(self.pdfa_compliant or self.arlington_compatible)


@dataclass
class CellImage:
    """Base64-encoded raster embedded in a table cell."""

    image_name: str = ""
    image_data: str = ""  # base64
    width: float = 0.0
    height: float = 0.0


@dataclass
class TableCell:
    """One cell in a template table row."""

    props: str = ""
    text: str = ""
    bgcolor: str = ""
    textcolor: str = ""
    width: float = 0.0
    height: float = 0.0
    link: str = ""
    dest: str = ""
    image: Optional[CellImage] = None


@dataclass
class TableRow:
    """One table row: ordered cells."""

    row: List[TableCell] = field(default_factory=list)


@dataclass
class TableDef:
    """One table definition (referenced by ``elements``)."""

    maxcolumns: int = 1
    columnwidths: List[float] = field(default_factory=list)
    rowheights: List[float] = field(default_factory=list)
    bgcolor: str = ""
    textcolor: str = ""
    rows: List[TableRow] = field(default_factory=list)


@dataclass
class Title:
    """Document title block (optional nested table for a banner bar)."""

    props: str = ""
    text: str = ""
    table: Optional[TableDef] = None
    bgcolor: str = ""
    textcolor: str = ""
    link: str = ""


@dataclass
class Spacer:
    """Vertical gap between elements."""

    height: float = 0.0


@dataclass
class Element:
    """Ordered element pointer: ``type`` is ``table`` or ``spacer``."""

    type: str = ""
    index: int = 0


@dataclass
class Footer:
    """Page footer font props + text."""

    font: str = DEFAULT_FOOTER_FONT
    text: str = ""
    link: str = ""


@dataclass
class PDFTemplate:
    """Full-format template: config, title, tables, spacers, elements, footer."""

    config: Config = field(default_factory=Config)
    title: Optional[Title] = None
    tables: List[TableDef] = field(default_factory=list)
    spacers: List[Spacer] = field(default_factory=list)
    elements: List[Element] = field(default_factory=list)
    footer: Optional[Footer] = None


def _parse_cell_image(raw: Optional[Dict[str, Any]]) -> Optional[CellImage]:
    if not raw or not isinstance(raw, dict):
        return None
    data = str(raw.get("imagedata") or raw.get("imageData") or "")
    if not data:
        return None
    return CellImage(
        image_name=str(raw.get("imagename") or raw.get("imageName") or ""),
        image_data=data,
        width=float(raw.get("width") or 0.0),
        height=float(raw.get("height") or 0.0),
    )


def _parse_cell(raw: Dict[str, Any]) -> TableCell:
    return TableCell(
        props=str(raw.get("props") or ""),
        text=str(raw.get("text") or ""),
        bgcolor=str(raw.get("bgcolor") or ""),
        textcolor=str(raw.get("textcolor") or ""),
        width=float(raw.get("width") or 0.0),
        height=float(raw.get("height") or 0.0),
        link=str(raw.get("link") or ""),
        dest=str(raw.get("dest") or ""),
        image=_parse_cell_image(raw.get("image")),
    )


def _parse_table(raw: Dict[str, Any]) -> TableDef:
    rows: List[TableRow] = []
    for row_raw in raw.get("rows") or []:
        cells = [_parse_cell(cell) for cell in (row_raw.get("row") or [])]
        rows.append(TableRow(row=cells))
    return TableDef(
        maxcolumns=int(raw.get("maxcolumns") or 1),
        columnwidths=[float(w) for w in (raw.get("columnwidths") or [])],
        rowheights=[float(h) for h in (raw.get("rowheights") or [])],
        bgcolor=str(raw.get("bgcolor") or ""),
        textcolor=str(raw.get("textcolor") or ""),
        rows=rows,
    )


def _parse_title(raw: Optional[Dict[str, Any]]) -> Optional[Title]:
    if not raw or not isinstance(raw, dict):
        return None
    nested = raw.get("table")
    return Title(
        props=str(raw.get("props") or ""),
        text=str(raw.get("text") or ""),
        table=_parse_table(nested) if isinstance(nested, dict) else None,
        bgcolor=str(raw.get("bgcolor") or ""),
        textcolor=str(raw.get("textcolor") or ""),
        link=str(raw.get("link") or ""),
    )


def _parse_config(raw: Optional[Dict[str, Any]]) -> Config:
    if not raw or not isinstance(raw, dict):
        return Config()
    return Config(
        page_border=str(raw.get("pageBorder") or "0:0:0:0"),
        page=str(raw.get("page") or "A4"),
        page_alignment=int(raw.get("pageAlignment") or 1),
        watermark=str(raw.get("watermark") or ""),
        pdf_title=str(raw.get("pdfTitle") or ""),
        pdfa_compliant=bool(raw.get("pdfaCompliant") or False),
        arlington_compatible=bool(raw.get("arlingtonCompatible") or False),
        embed_fonts=bool(raw.get("embedFonts") if "embedFonts" in raw else True),
    )


def _parse_footer(raw: Optional[Dict[str, Any]]) -> Optional[Footer]:
    if not raw or not isinstance(raw, dict):
        return None
    return Footer(
        font=str(raw.get("font") or DEFAULT_FOOTER_FONT),
        text=str(raw.get("text") or ""),
        link=str(raw.get("link") or ""),
    )


def load_template(path: Union[str, Path]) -> PDFTemplate:
    """Load a full-format JSON template (``config``/``title``/``elements``/...).

    Mirrors gocorepdfengine ``model.LoadTemplate``.  Used by the financial
    report sample and :mod:`engine.bench_financial`.
    """
    path = Path(path)
    with open(path, encoding="utf-8") as handle:
        data = json.load(handle)

    tables = [_parse_table(t) for t in (data.get("table") or [])]
    spacers = [
        Spacer(height=float(s.get("height") or 0.0))
        for s in (data.get("spacer") or [])
        if isinstance(s, dict)
    ]
    elements = [
        Element(type=str(e.get("type") or ""), index=int(e.get("index") or 0))
        for e in (data.get("elements") or [])
        if isinstance(e, dict)
    ]
    return PDFTemplate(
        config=_parse_config(data.get("config")),
        title=_parse_title(data.get("title")),
        tables=tables,
        spacers=spacers,
        elements=elements,
        footer=_parse_footer(data.get("footer")),
    )
