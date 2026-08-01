"""Zerodha-style domain model: JSON contract notes, trade expansion (stdlib only).

Phase 8 application layer on top of the engine.  :func:`load_json` reads
the domain fixtures under ``sampledata/zerodha/`` into a
:class:`ContractNote`; :func:`expand_trades` grows a note to a target
trade count with a deterministic per-seed PRNG (stdlib ``random``),
cycling the base trades with slight price/quantity variation.  Same seed
-> same trades every time; different seed -> different trades.

Deliberately engine-free: this module imports nothing from ``engine`` so
the model stays a plain data layer.
"""

from __future__ import annotations

import json
import random
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any, Dict, List, Union

__all__ = ["ContractNote", "Trade", "expand_trades", "load_json"]

# The default footer props string (font:size:style:alignment, TEMPLATE_REFERENCE).
DEFAULT_FOOTER_FONT = "Helvetica:8:000:center"

# Expanded trade counts per workload tier (sampledata/zerodha/README.md).
TARGET_TRADES: Dict[str, int] = {"retail": 2, "active": 40, "hft": 2000}


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
