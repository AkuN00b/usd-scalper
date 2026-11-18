from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch

from scalper.backtest import aggregate, load_candles, simulate
from scalper.context import Event, context_for, evaluate, read_bls
from scalper.server import Alert
from scalper.strategy import Candle, Features, canonical_symbol, feature_stream, technical

START = datetime(2025, 1, 13, tzinfo=UTC)
BUY = Features(103, 102, 104, 101, 102, 101, 60, 1, 102.5, 98, 101, 100, 99)
SELL = Features(97, 98, 99, 96, 98, 99, 40, 1, 102, 97.5, 99, 100, 101)


def make_bars(count, step):
    return [Candle(START + timedelta(minutes=index * step), 100, 101, 99, 100)
            for index in range(count)]


class TestSignals(TestCase):
    def test_both_sides_and_filter_scores(self):
        self.assertEqual((technical(BUY).side, technical(SELL).side), ("BUY", "SELL"))
        weak = Features(**{**BUY.__dict__, "rsi": 90, "open": BUY.close})
        self.assertEqual((technical(weak).side, technical(weak).score), ("WAIT", 75))
        self.assertEqual(canonical_symbol("EXNESS:XAUUSDm"), "XAUUSD")
        with self.assertRaises(ValueError):
            canonical_symbol("USDEUR")

    def test_no_incomplete_htf_and_no_lookahead(self):
        candles = make_bars(251, 3)
        higher = make_bars(51, 15)
        revised = higher[-1]
        higher[-1] = Candle(revised.time, 100, 200, 99, 200)
        features = list(feature_stream(candles, higher))
        self.assertIsNone(features[249])
        self.assertEqual(features[250].trend_close, 100)
        self.assertEqual(len(aggregate(make_bars(14, 1), 1, 15)), 0)

    def test_alert_rejects_htf_that_closes_at_same_time(self):
        from pydantic import ValidationError
        alert = dict(version=1, token="test", symbol="XAUUSD", timeframe="3",
                     closed_at="2025-01-13T00:15:00Z", trend_closed_at="2025-01-13T00:15:00Z",
                     features=BUY.__dict__)
        with self.assertRaises(ValidationError):
            Alert.model_validate(alert)
        alert["trend_closed_at"] = "2025-01-13T00:00:00Z"
        self.assertEqual(Alert.model_validate(alert).features.close, BUY.close)


class TestHistoricalContext(TestCase):
    def test_wait_uses_only_available_and_relevant_information(self):
        scheduled = Event("scheduled", START + timedelta(minutes=12), START - timedelta(days=1),
                          frozenset({"USD"}), "high", "CPI", "BLS")
        unobserved = Event("news", START, START + timedelta(minutes=2),
                           frozenset({"USD"}), "high", "Breaking", "Feed")
        self.assertEqual(context_for((unobserved,), "XAUUSD", START), ([], []))
        decision = evaluate(technical(BUY), "XAUUSD", START, (scheduled, unobserved))
        self.assertEqual(decision["side"], "WAIT")
        self.assertEqual(decision["technical_side"], "BUY")
        self.assertEqual(len(decision["context"]), 1)

    def test_bls_with_eastern_timezone(self):
        feed = b"""BEGIN:VCALENDAR
VERSION:2.0
BEGIN:VEVENT
DTSTART;TZID=America/New_York:20250113T083000
SUMMARY:Consumer Price Index
END:VEVENT
END:VCALENDAR
"""
        event, = read_bls(feed, START)
        self.assertEqual(event.event_at.hour, 13)
        self.assertEqual(event.event_at.minute, 30)


class TestExecution(TestCase):
    def test_mt5_export_and_timestamp(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "mt5.csv"
            path.write_text("<DATE>\t<TIME>\t<OPEN>\t<HIGH>\t<LOW>\t<CLOSE>\n"
                            "2025.01.13\t07:00:00\t5\t7\t3\t6\n")
            self.assertEqual(load_candles(path, 1, "Asia/Jakarta")[0].time, START)
            with self.assertRaises(ValueError):
                load_candles(path, 1, None)

    def test_next_open_sell_uses_ask_exit_and_stop_when_both_hit(self):
        bars = [
            Candle(START, 98, 99, 96, 97),
            Candle(START + timedelta(minutes=3), 97, 100, 93, 97),
        ]
        with patch("scalper.backtest.feature_stream", return_value=iter([SELL, None])):
            stats, decisions, trades = simulate(bars, 3, "XAUUSD", (), 0.1, 0.02, 0.01, "bid")
        trade, = trades
        self.assertEqual(stats["trade_count"], 1)
        self.assertEqual(decisions[0]["outcome"], "filled")
        self.assertEqual(trade["signal_at_utc"], "2025-01-13T00:03:00Z")
        self.assertEqual(trade["entry_at_utc"], "2025-01-13T00:03:00Z")
        self.assertEqual(trade["exit_bar_end_utc"], "2025-01-13T00:06:00Z")
        self.assertEqual(trade["exit_reason"], "stop_intrabar")
        self.assertAlmostEqual(trade["entry_price"], 96.98)
        self.assertAlmostEqual(trade["exit_price"], 98.20)
        self.assertLess(trade["r"], -1)

    def test_gap_aborts_new_entry(self):
        bars = [
            Candle(START, 102, 104, 101, 103),
            Candle(START + timedelta(minutes=6), 109, 110, 108, 109),
        ]
        with patch("scalper.backtest.feature_stream", return_value=iter([BUY, None])):
            _, decisions, trades = simulate(bars, 3, "XAUUSD", (), 0, 0, 0, "bid")
        self.assertEqual(decisions[0]["outcome"], "entry_skipped_missing_bar")
        self.assertEqual(trades, [])

    def test_exness_tick_entry_uses_exact_quote_after_delay(self):
        from scalper.exness import candles_from_ticks, parse_quotes
        with TemporaryDirectory() as directory:
            path = Path(directory) / "ticks.csv"
            path.write_text(
                "Exness,Symbol,Timestamp,Bid,Ask\n"
                "exness,XAUUSD,2025-01-13 00:00:00.500Z,102.0,102.1\n"
                "exness,XAUUSD,2025-01-13 00:01:00.500Z,102.3,102.5\n"
                "exness,XAUUSD,2025-01-13 00:02:58.400Z,103.0,103.1\n"
                "exness,XAUUSD,2025-01-13 00:03:00.800Z,103.0,103.1\n"
                "exness,XAUUSD,2025-01-13 00:03:01.012Z,103.1,103.3\n"
                "exness,XAUUSD,2025-01-13 00:03:27.123Z,105.2,105.4\n"
            )
            candles = candles_from_ticks(parse_quotes([path], "XAUUSD"))
            with patch("scalper.backtest.feature_stream", return_value=iter([BUY])):
                _, decisions, trades = simulate(candles, 1, "XAUUSD", (), None, 0, 0, "bid",
                                                ticks=parse_quotes([path], "XAUUSD"), latency_ms=1000)
        trade, = trades
        self.assertEqual(decisions[0]["entry_at_utc"], "2025-01-13T00:03:01.012000Z")
        self.assertEqual(trade["exit_at_utc"], "2025-01-13T00:03:27.123000Z")
        self.assertEqual(trade["exit_reason"], "target_tick")
        self.assertAlmostEqual(trade["entry_price"], 103.3)
        self.assertAlmostEqual(trade["spread_at_entry"], 0.2)
