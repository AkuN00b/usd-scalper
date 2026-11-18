from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import json
import sqlite3

from pydantic import ValidationError

from scalper.journal import (
    ExitInput, JournalError, JournalFilters, TradeInput, add_trade, csv_export, journal_rows, record_exit, summary,
)
from scalper.server import connect, initialize

ENTRY = datetime(2025, 1, 13, 8, tzinfo=UTC)
AFTER = datetime(2025, 1, 13, 8, 12, tzinfo=UTC)
BEFORE = datetime(2025, 1, 13, 7, 59, tzinfo=UTC)


class JournalTests(TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.database = Path(directory.name) / "scalper.sqlite3"
        with closing(sqlite3.connect(self.database)) as db, db:
            db.execute("""
                CREATE TABLE signals (
                    id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, closed TEXT NOT NULL,
                    payload TEXT NOT NULL, telegram TEXT NOT NULL, UNIQUE(symbol,closed)
                )
            """)
            db.execute("INSERT INTO signals VALUES (1, 'XAUUSD', ?, ?, 'off')", (
                "2025-01-13T08:00:00Z",
                json.dumps({"side": "SELL", "score": 95, "reasons": ["Tren M15 turun."], "reference_price": 2701}),
            ))
        initialize(self.database)

    @staticmethod
    def entry(**overrides):
        return TradeInput(**({
            "request_id": "a" * 32, "symbol": "EXNESS:XAUUSDm", "side": "SELL",
            "opened_at": ENTRY, "entry": 2700, "stop": 2702, "target": 2697,
            "signal_id": 1, "notes": "Sebelum sesi London",
        } | overrides))

    def test_preserves_old_signal_and_persists_closed_trade_on_restart(self):
        with closing(connect(self.database)) as db, db:
            trade = add_trade(db, self.entry())
            closed = record_exit(
                db, trade["id"], ExitInput(exited_at=AFTER, exit_price=2697, exit_notes="Target"),
            )
        initialize(self.database)
        with closing(connect(self.database)) as db:
            row, = journal_rows(db, "XAUUSD")
            self.assertEqual(closed, row)
            self.assertEqual(row["signal_reasons"], ["Tren M15 turun."])
            self.assertEqual((row["signal_reference"], row["entry_slippage_price"]), (2701, 1))
            self.assertEqual(row["gross_r"], 1.5)
            self.assertEqual(summary(db, "XAUUSD")["gross_win_rate"], 1)

    def test_concurrent_retry_yields_single_trade_and_exit_is_idempotent(self):
        def save():
            with closing(connect(self.database)) as db, db:
                return add_trade(db, self.entry())["id"]

        with ThreadPoolExecutor(max_workers=2) as pool:
            ids = list(pool.map(lambda _: save(), range(2)))
        self.assertEqual(ids[0], ids[1])

        with closing(connect(self.database)) as db, db:
            exit_trade = ExitInput(exited_at=AFTER, exit_price=2702)
            self.assertEqual(record_exit(db, ids[0], exit_trade), record_exit(db, ids[0], exit_trade))
            with self.assertRaises(JournalError) as conflict:
                record_exit(db, ids[0], ExitInput(exited_at=AFTER, exit_price=2701))
            self.assertEqual(conflict.exception.status, 409)
            with self.assertRaises(JournalError) as changed:
                add_trade(db, self.entry(notes="Berubah"))
            self.assertEqual(changed.exception.status, 409)
            self.assertEqual(summary(db, "XAUUSD")["total"], 1)

    def test_rejects_bad_timestamps_and_wrong_signal(self):
        with self.assertRaises(ValidationError):
            self.entry(stop=2699)
        with closing(connect(self.database)) as db, db:
            with self.assertRaises(JournalError):
                add_trade(db, self.entry(symbol="EURUSD"))
            with self.assertRaises(JournalError):
                add_trade(db, self.entry(opened_at=BEFORE))
            trade = add_trade(db, self.entry())
            with self.assertRaises(JournalError):
                record_exit(db, trade["id"], ExitInput(exited_at=BEFORE, exit_price=2698))
            self.assertEqual(summary(db, "XAUUSD")["closed"], 0)

    def test_csv_includes_actual_trade_with_optional_stop_and_preserves_notes(self):
        with closing(connect(self.database)) as db, db:
            trade = add_trade(db, self.entry(signal_id=None, stop=None, notes="=1+1"))
            record_exit(db, trade["id"], ExitInput(exited_at=AFTER, exit_price=2699))
            self.assertEqual(summary(db, "XAUUSD")["r_count"], 0)
            self.assertEqual(summary(db, "XAUUSD")["gross_win_rate"], 1)
            exported = csv_export(journal_rows(db, "XAUUSD"))
            self.assertIn("'=1+1", exported)
            row, = journal_rows(db, "XAUUSD")
            self.assertEqual(row["notes"], "=1+1")

    def test_migrates_existing_journal_and_keeps_old_trade_values(self):
        with closing(connect(self.database)) as db, db:
            db.execute("DROP TABLE journal_trades")
            db.execute("""
                CREATE TABLE journal_trades (
                    id INTEGER PRIMARY KEY, request_id TEXT UNIQUE NOT NULL, symbol TEXT NOT NULL,
                    side TEXT NOT NULL, opened_at TEXT NOT NULL, entry REAL NOT NULL, stop REAL,
                    target REAL, signal_id INTEGER, notes TEXT NOT NULL, exited_at TEXT,
                    exit_price REAL, exit_notes TEXT NOT NULL DEFAULT ''
                )
            """)
            db.execute("""
                INSERT INTO journal_trades(id, request_id, symbol, side, opened_at, entry, notes)
                VALUES (12, ?, 'XAUUSD', 'BUY', '2025-01-13T08:00:00.000000Z', 2700, 'Data lama')
            """, ("a" * 32,))
        initialize(self.database)
        initialize(self.database)
        with closing(connect(self.database)) as db, db:
            old, = journal_rows(db, "XAUUSD")
            self.assertEqual((old["id"], old["notes"], old["lots"], old["net_pnl"]), (12, "Data lama", None, None))
            recorded = record_exit(db, old["id"], ExitInput(exited_at=AFTER, exit_price=2701))
            self.assertIsNone(recorded["net_pnl"])

    def test_filtered_rows_totals_and_csv_keep_account_currencies_separate(self):
        with self.assertRaises(ValidationError):
            ExitInput(exited_at=AFTER, exit_price=2698, costs=1)
        with closing(connect(self.database)) as db, db:
            for key, currency, gross, costs in (("a", "USD", 10, 1.5), ("b", "EUR", -2, 0), ("c", "USD", 10, None)):
                trade = add_trade(db, self.entry(request_id=key * 32, lots=0.05))
                exit = ExitInput(exited_at=AFTER, exit_price=2698, gross_pnl=gross, costs=costs, pnl_currency=currency)
                recorded = record_exit(db, trade["id"], exit)
                self.assertEqual(record_exit(db, trade["id"], exit), recorded)
                self.assertEqual(recorded["net_pnl"], gross - costs if costs is not None else None)
            add_trade(db, self.entry(request_id="d" * 32, signal_id=None, side="BUY", stop=2698, target=2704))
            filters = JournalFilters(status="closed", side="SELL", start=date(2025, 1, 13), end=date(2025, 1, 13))
            results = summary(db, "XAUUSD", filters)
            self.assertEqual(results["total_gross_r"], 3)
            self.assertEqual(results["net_by_currency"], [
                {"currency": "EUR", "trades": 1, "net": -2.0},
                {"currency": "USD", "trades": 1, "net": 8.5},
            ])
            rows = list(journal_rows(db, "XAUUSD", filters=filters))
            self.assertEqual([row["lots"] for row in rows], [0.05, 0.05, 0.05])
            self.assertIn("USD,8.5", csv_export(rows))
            self.assertFalse(list(journal_rows(db, "XAUUSD", filters=JournalFilters(start=date(2025, 1, 14)))))
