from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Annotated, Literal
import csv
import io
import json

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

from .context import parse_time
from .strategy import canonical_symbol

Price = Annotated[float, Field(ge=0.000000001, le=1_000_000_000)]
Money = Annotated[float, Field(ge=-1_000_000_000, le=1_000_000_000)]


def journal_time(value: datetime) -> str:
    return value.astimezone(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


class TradeInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)
    request_id: str = Field(pattern=r"^[0-9a-f]{32}$")
    symbol: str = Field(min_length=6, max_length=48)
    side: Literal["BUY", "SELL"]
    opened_at: AwareDatetime
    entry: Price
    stop: Price | None = None
    target: Price | None = None
    signal_id: int | None = Field(default=None, gt=0)
    notes: str = Field(default="", max_length=2000)
    lots: float | None = Field(default=None, gt=0, le=1_000_000)

    @field_validator("symbol")
    @classmethod
    def valid_symbol(cls, symbol: str) -> str:
        return canonical_symbol(symbol)

    @model_validator(mode="after")
    def check_levels(self):
        direction = 1 if self.side == "BUY" else -1
        if self.stop is not None and direction * (self.entry - self.stop) <= 0:
            raise ValueError("SL awal harus berada di sisi rugi dari harga entry.")
        if self.target is not None and direction * (self.target - self.entry) <= 0:
            raise ValueError("TP awal harus berada di sisi untung dari harga entry.")
        check_time(self.opened_at)
        return self


class ExitInput(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False, str_strip_whitespace=True)
    exited_at: AwareDatetime
    exit_price: Price
    exit_notes: str = Field(default="", max_length=2000)
    gross_pnl: Money | None = None
    costs: Money | None = None
    pnl_currency: str | None = Field(default=None, pattern=r"^[A-Z]{3}$")

    @field_validator("exited_at")
    @classmethod
    def check_exit_time(cls, value: datetime) -> datetime:
        return check_time(value)

    @model_validator(mode="after")
    def check_currency(self):
        has_amount = self.gross_pnl is not None or self.costs is not None
        if has_amount != (self.pnl_currency is not None):
            raise ValueError("Isi mata uang akun bersama hasil bruto atau biaya.")
        return self


def check_time(value: datetime) -> datetime:
    if value > datetime.now(UTC) + timedelta(minutes=1):
        raise ValueError("Waktu transaksi lebih dari satu menit di masa depan.")
    return value


class JournalError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class JournalFilters:
    status: Literal["all", "open", "closed"] = "all"
    side: Literal["all", "BUY", "SELL"] = "all"
    start: date | None = None
    end: date | None = None

    def where(self, symbol: str) -> tuple[str, list]:
        clauses = ["t.symbol = ?"]
        values = [symbol]
        if self.status == "open":
            clauses.append("t.exited_at IS NULL")
        if self.status == "closed":
            clauses.append("t.exited_at IS NOT NULL")
        if self.side != "all":
            clauses.append("t.side = ?")
            values.append(self.side)
        if self.start is not None:
            clauses.append("t.opened_at >= ?")
            values.append(self.start.isoformat() + "T00:00:00.000000Z")
        if self.end is not None:
            clauses.append("t.opened_at <= ?")
            values.append(self.end.isoformat() + "T23:59:59.999999Z")
        return " WHERE " + " AND ".join(clauses), values


def initialize_journal(db):
    db.executescript("""
        CREATE TABLE IF NOT EXISTS journal_trades (
            id INTEGER PRIMARY KEY, request_id TEXT UNIQUE NOT NULL,
            symbol TEXT NOT NULL, side TEXT NOT NULL CHECK(side IN ('BUY', 'SELL')),
            opened_at TEXT NOT NULL, entry REAL NOT NULL, stop REAL, target REAL,
            signal_id INTEGER REFERENCES signals(id), notes TEXT NOT NULL,
            exited_at TEXT, exit_price REAL, exit_notes TEXT NOT NULL DEFAULT ''
        );
        CREATE INDEX IF NOT EXISTS journal_symbol_opened ON journal_trades(symbol, opened_at DESC);
    """)
    db.execute("BEGIN IMMEDIATE")
    fields = {row["name"] for row in db.execute("PRAGMA table_info(journal_trades)")}
    for name, kind in (("lots", "REAL"), ("gross_pnl", "REAL"), ("costs", "REAL"), ("pnl_currency", "TEXT")):
        if name not in fields:
            db.execute(f"ALTER TABLE journal_trades ADD COLUMN {name} {kind}")


SELECT_TRADES = """
    SELECT t.*, s.closed AS signal_at, s.payload AS signal_payload
    FROM journal_trades AS t
    LEFT JOIN signals AS s ON s.id = t.signal_id
"""

JOURNAL_FIELDS = (
    "id", "symbol", "side", "opened_at", "entry", "stop", "target", "signal_id", "signal_at",
    "signal_score", "signal_reasons", "notes", "exited_at", "exit_price", "exit_notes",
    "gross_price_move", "gross_r",
    "lots", "gross_pnl", "costs", "pnl_currency", "net_pnl",
    "signal_reference", "entry_slippage_price",
)


def trade_view(row) -> dict:
    record = dict(row)
    del record["request_id"]
    snapshot = record.pop("signal_payload")
    signal = json.loads(snapshot) if snapshot is not None else {}
    direction = 1 if record["side"] == "BUY" else -1
    reference = signal.get("reference_price")
    slippage = direction * (record["entry"] - reference) if isinstance(reference, (int, float)) else None
    move = None
    result = None
    if record["exit_price"] is not None:
        move = direction * (record["exit_price"] - record["entry"])
        if record["stop"] is not None:
            result = move / abs(record["entry"] - record["stop"])
    gross, costs = record["gross_pnl"], record["costs"]
    return dict(record, signal_score=signal.get("score"), signal_reasons=signal.get("reasons", []),
                gross_price_move=move, gross_r=result,
                net_pnl=gross - costs if gross is not None and costs is not None else None,
                signal_reference=reference, entry_slippage_price=slippage)


def read_trade(db, trade_id: int) -> dict:
    row = db.execute(SELECT_TRADES + " WHERE t.id = ?", (trade_id,)).fetchone()
    if row is None:
        raise JournalError(404, "Trade tidak ditemukan.")
    return trade_view(row)


def add_trade(db, trade: TradeInput) -> dict:
    values = trade.model_dump()
    values["opened_at"] = journal_time(trade.opened_at)
    if trade.signal_id is not None:
        source = db.execute("SELECT symbol, closed, payload FROM signals WHERE id=?", (trade.signal_id,)).fetchone()
        if (source is None or source["symbol"] != trade.symbol
                or json.loads(source["payload"])["side"] != trade.side):
            raise JournalError(422, "Sinyal harus punya simbol dan sisi BUY/SELL yang sama dengan trade.")
        if trade.opened_at < parse_time(source["closed"]):
            raise JournalError(422, "Waktu entry tidak boleh sebelum sinyal yang ditautkan.")
    added = db.execute("""
        INSERT INTO journal_trades(request_id, symbol, side, opened_at, entry, stop, target, signal_id, notes, lots)
        VALUES (:request_id, :symbol, :side, :opened_at, :entry, :stop, :target, :signal_id, :notes, :lots)
        ON CONFLICT(request_id) DO NOTHING RETURNING id
    """, values).fetchone()
    if added is not None:
        return read_trade(db, added["id"])
    existing = db.execute("SELECT * FROM journal_trades WHERE request_id = ?", (trade.request_id,)).fetchone()
    if any(existing[key] != value for key, value in values.items()):
        raise JournalError(409, "ID penyimpanan sudah dipakai untuk trade lain.")
    return read_trade(db, existing["id"])


def record_exit(db, trade_id: int, exit: ExitInput) -> dict:
    values = (journal_time(exit.exited_at), exit.exit_price, exit.exit_notes, exit.gross_pnl, exit.costs, exit.pnl_currency)
    updated = db.execute("""
        UPDATE journal_trades SET exited_at = ?, exit_price = ?, exit_notes = ?,
            gross_pnl = ?, costs = ?, pnl_currency = ?
        WHERE id = ? AND exited_at IS NULL AND opened_at <= ?
    """, (*values, trade_id, values[0])).rowcount
    trade = read_trade(db, trade_id)
    recorded = (trade["exited_at"], trade["exit_price"], trade["exit_notes"],
                trade["gross_pnl"], trade["costs"], trade["pnl_currency"])
    if not updated and values != recorded:
        if trade["exited_at"] is not None:
            raise JournalError(409, "Trade ini sudah punya exit.")
        raise JournalError(422, "Waktu exit tidak boleh sebelum entry.")
    return trade


def journal_rows(db, symbol: str, limit: int | None = None, offset: int = 0,
                 filters: JournalFilters = JournalFilters()):
    where, parameters = filters.where(symbol)
    query = SELECT_TRADES + where + " ORDER BY t.opened_at DESC, t.id DESC"
    if limit is not None:
        query += " LIMIT ? OFFSET ?"
        parameters.extend((limit, offset))
    return (trade_view(row) for row in db.execute(query, parameters))


def summary(db, symbol: str, filters: JournalFilters = JournalFilters()) -> dict:
    where, parameters = filters.where(symbol)
    row = db.execute(f"""
        WITH moves AS (
            SELECT exited_at, stop, entry,
                CASE side WHEN 'BUY' THEN exit_price-entry ELSE entry-exit_price END AS move
            FROM journal_trades AS t{where}
        )
        SELECT count(*) AS total, count(exited_at) AS closed,
            count(CASE WHEN move > 0 THEN 1 END) AS wins,
            count(CASE WHEN move IS NOT NULL AND stop IS NOT NULL THEN 1 END) AS r_count,
            avg(move / abs(entry-stop)) AS mean_gross_r,
            sum(move / abs(entry-stop)) AS total_gross_r
        FROM moves
    """, parameters).fetchone()
    stats = dict(row)
    stats["gross_win_rate"] = stats["wins"] / stats["closed"] if stats["closed"] else None
    stats["net_by_currency"] = [
        dict(row) for row in db.execute(f"""
            SELECT pnl_currency AS currency, count(*) AS trades, sum(gross_pnl - costs) AS net
            FROM journal_trades AS t{where} AND gross_pnl IS NOT NULL AND costs IS NOT NULL
            GROUP BY pnl_currency ORDER BY pnl_currency
        """, parameters)
    ]
    return stats


def csv_export(rows) -> str:
    file = io.StringIO(newline="")
    writer = csv.DictWriter(file, fieldnames=JOURNAL_FIELDS)
    writer.writeheader()
    for trade in rows:
        row = {**trade, "signal_reasons": " ".join(trade["signal_reasons"])}
        for field in ("notes", "exit_notes", "signal_reasons"):
            if row[field].lstrip().startswith(("=", "+", "-", "@")):
                row[field] = "'" + row[field]
        writer.writerow(row)
    return file.getvalue()
