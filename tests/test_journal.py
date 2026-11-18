from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
import json
import sqlite3

from pydantic import ValidationError

from scalper.journal import (
    ExitInput, JournalError, TradeInput, add_trade, csv_export, journal_rows, record_exit, summary,
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
                "2025-01-13T08:00:00Z", json.dumps({"side": "SELL", "score": 95, "reasons": ["Tren M15 turun."]}),
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
