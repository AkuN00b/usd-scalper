from contextlib import closing
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import json
import sqlite3
import csv
import io

from scalper.backup import copy_database, daily_copies, save_daily_copy
from scalper.context import stamp
from scalper.exports import export_signals
from scalper.journal import ExitInput, TradeInput, add_trade, record_exit
from scalper.overview import feed_activity, overview
from scalper.server import connect, initialize

NOW = datetime(2025, 1, 20, 12, tzinfo=UTC)


class OverviewTests(TestCase):
    def setUp(self):
        folder = TemporaryDirectory(prefix="usd scalper tests ")
        self.addCleanup(folder.cleanup)
        self.path = Path(folder.name) / "journal.sqlite3"
        initialize(self.path)

    def test_review_groups_closed_trades_by_monday_and_currency(self):
        with closing(connect(self.path)) as db, db:
            opened = NOW - timedelta(days=10)
            current = NOW - timedelta(hours=1)
            old = NOW - timedelta(days=8)
            db.executemany("INSERT INTO signals(symbol, closed, payload, telegram) VALUES (?, ?, ?, ?)", (
                ("XAUUSD", stamp(current), json.dumps({"side": "BUY", "reference_price": 99.5}), "sent"),
                ("EURUSD", stamp(old), json.dumps({"side": "WAIT"}), "off"),
            ))
            for key, symbol, exited, currency in (
                ("a", "XAUUSD", current, "USD"), ("b", "EURUSD", current - timedelta(days=1), "EUR"),
                ("c", "XAUUSD", old, "USD"), ("d", "XAUUSD", None, None),
            ):
                entry_at = current if key == "a" else opened
                trade = add_trade(db, TradeInput(
                    request_id=key * 32, symbol=symbol, side="BUY", entry=100, stop=98, opened_at=entry_at,
                    signal_id=1 if key == "a" else None,
                ))
                if exited is not None:
                    record_exit(db, trade["id"], ExitInput(
                        exited_at=exited, exit_price=102, gross_pnl=10, costs=2, pnl_currency=currency,
                    ))
            result = overview(db, 7, NOW, public_feeds=False, telegram_enabled=True)
        journal = result["journal"]
        self.assertEqual(journal["open"], 1)
        self.assertEqual(journal["totals"], {"closed": 2, "win_rate": 1, "total_r": 2, "net": {"EUR": 8, "USD": 8}})
        self.assertEqual([week["monday"] for week in journal["weeks"]], ["2025-01-20", "2025-01-13"])
        self.assertEqual(journal["symbols"][1]["mean_slippage"], 0.5)
        sessions = {(session["symbol"], session["start_hour"]): session for session in journal["sessions"]}
        self.assertEqual(sessions[("", 6)]["closed"], 1)
        self.assertIsNone(sessions[("", 6)]["mean_slippage"])
        self.assertEqual(sessions[("XAUUSD", 6)]["mean_slippage"], 0.5)
        self.assertEqual(sessions[("EURUSD", 12)]["total_r"], 1)
        self.assertEqual(result["telegram"], {"sent": 1})
        self.assertEqual(result["signals"][1]["WAIT"], 0)
        self.assertEqual(result["signals"][1]["latest"], stamp(old))
        self.assertEqual(result["feeds"], [])

    def test_feed_cache_age_and_failure_are_reported_separately(self):
        providers = {
            "BLS": {"error": "HTTP 403", "success": None, "last_attempt": stamp(NOW)},
            "ECB": {"error": "Timeout", "success": stamp(NOW - timedelta(minutes=1))},
            "UN": {"error": "", "success": stamp(NOW - timedelta(minutes=26))},
        }
        feeds = {feed["name"]: feed for feed in feed_activity(providers, True, NOW)}
        self.assertEqual((feeds["BLS"]["status"], feeds["BLS"]["blocking"]), ("failed", True))
        self.assertEqual((feeds["ECB"]["status"], feeds["ECB"]["blocking"]), ("cached", False))
        self.assertEqual(feeds["UN"]["status"], "stale")
        self.assertFalse(feeds["GDELT"]["blocking"])

    def test_backup_contains_committed_wal_data_and_keeps_seven_daily_files(self):
        destination = self.path.with_name("download.sqlite3")
        with closing(connect(self.path)) as db:
            db.execute("INSERT INTO feeds VALUES ('BLS', ?, ?, '', '[]')", (stamp(NOW), stamp(NOW)))
            db.commit()
            db.execute("INSERT INTO feeds VALUES ('Fed', ?, ?, '', '[]')", (stamp(NOW), stamp(NOW)))
            copy_database(self.path, destination)
        with closing(sqlite3.connect(destination)) as copy:
            self.assertEqual(copy.execute("SELECT name FROM feeds").fetchall(), [("BLS",)])
        initialize(destination)
        for offset in range(8):
            save_daily_copy(self.path, NOW + timedelta(days=offset))
        newest = daily_copies(self.path)
        self.assertEqual(len(newest), 7)
        self.assertTrue(newest[0].name.endswith("2025-01-27.sqlite3"))
        save_daily_copy(self.path, NOW + timedelta(days=7))
        self.assertEqual(daily_copies(self.path), newest)
        self.assertTrue(destination.is_file())

    def test_signal_csv_filters_symbol_time_and_side_without_display_limit(self):
        start = NOW - timedelta(hours=6)
        with closing(connect(self.path)) as db, db:
            for index in range(65):
                stamp_at = stamp(start + timedelta(minutes=index * 3))
                db.execute("INSERT INTO signals(symbol, closed, payload, telegram) VALUES (?, ?, ?, ?)", (
                    "XAUUSD", stamp_at, json.dumps({"side": "BUY", "reasons": [" =1+1"], "reference_price": 99}), "off",
                ))
            db.executemany("INSERT INTO signals(symbol, closed, payload, telegram) VALUES (?, ?, ?, ?)", (
                ("XAUUSD", stamp(NOW), json.dumps({"side": "WAIT"}), "off"),
                ("XAUUSD", stamp(start - timedelta(minutes=3)), json.dumps({"side": "SELL"}), "sent"),
                ("EURUSD", stamp(NOW), json.dumps({"side": "BUY"}), "off"),
            ))
            data = export_signals(db, "XAUUSD", stamp(start), stamp(NOW), "actionable")
            result = list(csv.DictReader(io.StringIO(data)))
        self.assertEqual(len(result), 65)
        self.assertEqual(result[0]["reasons"], "' =1+1")
        self.assertEqual(result[-1]["closed_at_utc"], stamp(start))
