"""Unit tests for the Zerodha domain model (engine.model).

Covers the JSON -> ContractNote mapping (client fields, trades, footer,
watermark flag, title, financials) and deterministic trade expansion:
same seed -> same trades, different seed -> different trades, and the
expanded trades keep the base symbols/actions with sane qty/price/total.
"""

from __future__ import annotations

import unittest
from pathlib import Path

from engine.model import ContractNote, Trade, expand_trades, load_json

DATA_DIR = Path(__file__).resolve().parents[2] / "sampledata" / "zerodha"


class TestLoadJson(unittest.TestCase):
    def test_retail_note_fields(self) -> None:
        note = load_json(DATA_DIR / "retail_investor.json")
        self.assertIsInstance(note, ContractNote)
        self.assertEqual(note.client_name, "Rahul Sharma")
        self.assertEqual(note.client_code, "RS9988")
        self.assertEqual(note.client_pan, "ABCDE1234F")
        self.assertEqual(len(note.trades), 2)
        self.assertFalse(note.watermark)
        self.assertEqual(note.title, "Contract Note - CN2024001")
        self.assertEqual(note.footer_font, "Helvetica:8:000:center")
        self.assertIn("Zerodha Broking Ltd.", note.footer_text)
        self.assertAlmostEqual(note.financials["net_obligation"], 6795.0)

    def test_retail_trade_fields(self) -> None:
        note = load_json(DATA_DIR / "retail_investor.json")
        trade = note.trades[0]
        self.assertIsInstance(trade, Trade)
        self.assertEqual(trade.symbol, "TATASTEEL")
        self.assertEqual(trade.action, "BUY")
        self.assertEqual(trade.qty, 10)
        self.assertAlmostEqual(trade.price, 145.5)
        self.assertAlmostEqual(trade.total, 1455.0)
        self.assertEqual(trade.isin, "INE081A01012")
        self.assertEqual(trade.currency, "\u20b9")

    def test_active_note_watermark_flag(self) -> None:
        note = load_json(DATA_DIR / "active_trader.json")
        self.assertTrue(note.watermark)
        self.assertEqual(note.client_name, "Priya Venkatesh")
        self.assertEqual(len(note.trades), 4)
        self.assertEqual(note.title, "Contract Note - PV5544")

    def test_hft_note(self) -> None:
        note = load_json(DATA_DIR / "hft_algo.json")
        self.assertEqual(note.client_code, "HFT001")
        self.assertEqual(len(note.trades), 3)
        self.assertFalse(note.watermark)


class TestExpandTrades(unittest.TestCase):
    def setUp(self) -> None:
        self.active = load_json(DATA_DIR / "active_trader.json")
        self.hft = load_json(DATA_DIR / "hft_algo.json")

    def test_expand_active_to_40_deterministic(self) -> None:
        first = expand_trades(self.active, 40, 42)
        second = expand_trades(self.active, 40, 42)
        self.assertEqual(len(first.trades), 40)
        self.assertEqual(first.trades, second.trades)
        self.assertEqual(first.client_code, self.active.client_code)
        self.assertEqual(first.footer_text, self.active.footer_text)

    def test_expand_hft_to_2000_length(self) -> None:
        note = expand_trades(self.hft, 2000, 42)
        self.assertEqual(len(note.trades), 2000)

    def test_different_seeds_diverge(self) -> None:
        a = expand_trades(self.active, 40, 1)
        b = expand_trades(self.active, 40, 2)
        self.assertNotEqual(a.trades, b.trades)

    def test_symbols_and_actions_preserved(self) -> None:
        note = expand_trades(self.active, 40, 42)
        base_symbols = {trade.symbol for trade in self.active.trades}
        base_actions = {trade.action for trade in self.active.trades}
        self.assertTrue({trade.symbol for trade in note.trades} <= base_symbols)
        self.assertTrue({trade.action for trade in note.trades} <= base_actions)

    def test_expanded_trades_are_consistent(self) -> None:
        note = expand_trades(self.hft, 200, 42)
        for trade in note.trades:
            self.assertGreater(trade.qty, 0)
            self.assertGreater(trade.price, 0.0)
            self.assertAlmostEqual(trade.total, round(trade.price * trade.qty, 2))

    def test_retail_target_returns_base_trades(self) -> None:
        note = expand_trades(self.active, 2, 99)
        self.assertEqual([t.symbol for t in note.trades], [t.symbol for t in self.active.trades[:2]])


if __name__ == "__main__":
    unittest.main()
