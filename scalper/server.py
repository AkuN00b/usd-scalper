from contextlib import asynccontextmanager, closing, suppress
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Literal
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import asyncio
import hmac
import json
import os
import re
import sqlite3

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request as APIRequest, Response
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, ValidationError, model_validator
from starlette.background import BackgroundTask

from .backup import copy_database, daily_copies, save_daily_copy
from .context import (REFRESH_SECONDS, collect_feeds, evaluate, load_events, missing_providers,
                      parse_time, read_event, stamp)
from .exports import export_signals
from .journal import (ExitInput, JournalError, JournalFilters, TradeInput, add_trade, csv_export,
                      initialize_journal, journal_rows, record_exit, summary)
from .overview import overview
from .strategy import Features, canonical_symbol, technical, SYMBOLS

STATIC = Path(__file__).with_name("static")
BACKTEST_EXAMPLES = Path(__file__).resolve().parent.parent / "examples" / "backtests"


@dataclass(frozen=True)
class Config:
    webhook_token: str
    dashboard_token: str
    database: Path
    public_feeds: bool
    context_csv: Path | None
    telegram_token: str
    telegram_chat_id: str
    auto_backup: bool = False

    @classmethod
    def load(cls):
        webhook = os.getenv("WEBHOOK_TOKEN", "")
        dashboard = os.getenv("DASHBOARD_TOKEN", "")
        if not all(re.fullmatch(r"[a-f0-9]{32,128}", token) for token in (webhook, dashboard)) or webhook == dashboard:
            raise RuntimeError("Isi WEBHOOK_TOKEN dan DASHBOARD_TOKEN dengan token hex berbeda sepanjang 32–128 karakter.")
        context = os.getenv("CONTEXT_CSV")
        telegram_token = os.getenv("TELEGRAM_BOT_TOKEN", "")
        telegram_chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
        if bool(telegram_token) != bool(telegram_chat_id):
            raise RuntimeError("Isi TELEGRAM_BOT_TOKEN dan TELEGRAM_CHAT_ID bersamaan.")
        if telegram_token and not re.fullmatch(r"[0-9]+:[A-Za-z0-9_-]+", telegram_token):
            raise RuntimeError("Format TELEGRAM_BOT_TOKEN tidak valid.")
        return cls(webhook, dashboard, Path(os.getenv("DB_PATH", "data/scalper.sqlite3")),
                   os.getenv("ENABLE_PUBLIC_FEEDS", "1") == "1", Path(context) if context else None,
                   telegram_token, telegram_chat_id, os.getenv("AUTO_BACKUP", "0") == "1")


class AlertFeatures(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    close: float = Field(gt=0)
    open: float = Field(gt=0)
    high: float = Field(gt=0)
    low: float = Field(gt=0)
    ema9: float = Field(gt=0)
    ema21: float = Field(gt=0)
    rsi: float = Field(ge=0, le=100)
    atr: float = Field(ge=0)
    previous_high: float = Field(gt=0)
    previous_low: float = Field(gt=0)
    trend_close: float = Field(gt=0)
    trend_ema20: float = Field(gt=0)
    trend_ema50: float = Field(gt=0)

    @model_validator(mode="after")
    def prices(self):
        if (not self.low <= min(self.open, self.close) <= max(self.open, self.close) <= self.high
                or self.previous_low > self.previous_high):
            raise ValueError("Rentang harga tidak konsisten.")
        return self


class Alert(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: int = Field(ge=1, le=1)
    token: str = Field(min_length=1, max_length=128)
    symbol: str = Field(min_length=6, max_length=48)
    timeframe: str = Field(pattern=r"^3$")
    closed_at: AwareDatetime
    trend_closed_at: AwareDatetime
    features: AlertFeatures

    @model_validator(mode="after")
    def time_alignment(self):
        signal = self.closed_at.astimezone(UTC)
        trend = self.trend_closed_at.astimezone(UTC)
        if signal.minute % 3 or signal.second or signal.microsecond:
            raise ValueError("closed_at harus sejajar dengan interval M3.")
        if trend.minute % 15 or trend.second or trend.microsecond:
            raise ValueError("trend_closed_at harus sejajar dengan interval M15.")
        if not timedelta(minutes=3) <= signal - trend <= timedelta(minutes=15):
            raise ValueError("Butuh M15 final yang tersedia sejak awal candle M3.")
        return self


def connect(path):
    connection = sqlite3.connect(path, timeout=0.8)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    return connection


def initialize(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    with closing(connect(path)) as db, db:
        db.executescript("""
            PRAGMA journal_mode = WAL;
            CREATE TABLE IF NOT EXISTS signals (
                id INTEGER PRIMARY KEY, symbol TEXT NOT NULL, closed TEXT NOT NULL,
                payload TEXT NOT NULL, telegram TEXT NOT NULL, UNIQUE(symbol, closed)
            );
            CREATE INDEX IF NOT EXISTS signals_closed ON signals(closed);
            CREATE TABLE IF NOT EXISTS feeds (
                name TEXT PRIMARY KEY, last_attempt TEXT NOT NULL,
                success TEXT, error TEXT NOT NULL, events TEXT NOT NULL
            );
        """)
        initialize_journal(db)
        db.execute("UPDATE signals SET telegram='unknown' WHERE telegram='sending'")


def state(path):
    with closing(connect(path)) as db:
        rows = list(db.execute("SELECT * FROM feeds ORDER BY name"))
    providers = {
        row["name"]: {"last_attempt": row["last_attempt"], "success": row["success"], "error": row["error"]}
        for row in rows
    }
    events = tuple(read_event(item) for row in rows for item in json.loads(row["events"]))
    return providers, events


def update_feeds(path):
    results = collect_feeds()
    with closing(connect(path)) as db, db:
        for name, at, events, error in results:
            if events is None:
                db.execute("""
                    INSERT INTO feeds VALUES (?, ?, NULL, ?, '[]')
                    ON CONFLICT(name) DO UPDATE SET last_attempt=excluded.last_attempt, error=excluded.error
                """, (name, stamp(at), error))
                continue
            # Retain first-seen time across refreshes so an event remains auditable.
            existing = db.execute("SELECT events FROM feeds WHERE name=?", (name,)).fetchone()
            known = {
                (item["kind"], item["title"], item["event_at"]): item["known_at"]
                for item in json.loads(existing["events"])
            } if existing else {}
            rows = [event.row() for event in events]
            for item in rows:
                key = (item["kind"], item["title"], item["event_at"])
                item["known_at"] = known.get(key, item["known_at"])
            db.execute("""
                INSERT INTO feeds VALUES (?, ?, ?, '', ?)
                ON CONFLICT(name) DO UPDATE SET last_attempt=excluded.last_attempt,
                    success=excluded.success, error='', events=excluded.events
            """, (name, stamp(at), stamp(at), json.dumps(rows)))


def notify_telegram(config):
    with closing(connect(config.database)) as db, db:
        record = db.execute("""
            UPDATE signals SET telegram='sending'
            WHERE id=(SELECT id FROM signals WHERE telegram='queued' ORDER BY id LIMIT 1)
            RETURNING id,payload
        """).fetchone()
    if not record:
        return
    signal = json.loads(record["payload"])
    if datetime.now(UTC) - parse_time(signal["closed_at_utc"]) > timedelta(minutes=3):
        status = "expired"
    else:
        message = (
            f"{signal['symbol']} {signal['side']} · skor teknikal {signal['score']}/100\n"
            f"Candle tutup: {signal['closed_at_utc']}\n"
            f"Referensi: {signal['reference_price']:.8g} | SL: {signal['stop']:.8g} | TP: {signal['target']:.8g}\n"
            + "\n".join(signal["reasons"])
        )
        body = urlencode({"chat_id": config.telegram_chat_id, "text": message[:3900]}).encode()
        try:
            with urlopen(Request(f"https://api.telegram.org/bot{config.telegram_token}/sendMessage",
                                 data=body), timeout=8) as response:
                status = "sent" if json.loads(response.read()).get("ok") else "failed"
        except (OSError, ValueError):
            status = "failed"
    with closing(connect(config.database)) as db, db:
        db.execute("UPDATE signals SET telegram=? WHERE id=?", (status, record["id"]))


async def recurring(work, delay):
    while True:
        try:
            await asyncio.to_thread(work)
        except sqlite3.Error as error:
            print(f"Operasi cache ditunda: {type(error).__name__}", flush=True)
        await asyncio.sleep(delay)


def run_backup(app):
    try:
        save_daily_copy(app.state.config.database, datetime.now(UTC))
        app.state.backup_error = False
    except (OSError, sqlite3.Error) as error:
        app.state.backup_error = True
        print(f"Backup SQLite gagal: {type(error).__name__}", flush=True)


@asynccontextmanager
async def lifespan(app):
    config = Config.load()
    initialize(config.database)
    app.state.config = config
    app.state.manual_events = load_events(config.context_csv) if config.context_csv else ()
    app.state.backup_error = False
    tasks = []
    if config.public_feeds:
        tasks.append(asyncio.create_task(recurring(partial(update_feeds, config.database), REFRESH_SECONDS)))
    if config.telegram_token:
        tasks.append(asyncio.create_task(recurring(partial(notify_telegram, config), 3)))
    if config.auto_backup:
        tasks.append(asyncio.create_task(recurring(partial(run_backup, app), 900)))
    try:
        yield
    finally:
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task


app = FastAPI(title="USD Scalper", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


def access(request: APIRequest, authorization: str = Header(default="")):
    expected = ("Bearer " + request.app.state.config.dashboard_token).encode()
    if not hmac.compare_digest(authorization.encode(), expected):
        raise HTTPException(401, "Token dashboard diperlukan.")


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-store"})


@app.get("/api/backtest/examples/{symbol}")
def backtest_example(symbol: str):
    if symbol not in {"XAUUSD", "EURUSD"}:
        raise HTTPException(404, "Contoh backtest tidak tersedia.")
    path = BACKTEST_EXAMPLES / symbol.lower() / "report.json"
    if not path.is_file():
        raise HTTPException(404, "Contoh backtest tidak tersedia.")
    return FileResponse(path, media_type="application/json")


@app.get("/health")
def health():
    return {"status": "ok"}


@app.get("/api/overview", dependencies=[Depends(access)])
def dashboard_overview(request: APIRequest, response: Response, days: int = Query(7, ge=1, le=90)):
    response.headers["Cache-Control"] = "no-store"
    config = request.app.state.config
    with closing(connect(config.database)) as db, db:
        db.execute("BEGIN")
        data = overview(db, days, datetime.now(UTC), public_feeds=config.public_feeds,
                        telegram_enabled=bool(config.telegram_token))
    backups = {"enabled": config.auto_backup, "latest": None, "error": request.app.state.backup_error}
    try:
        files = daily_copies(config.database)
        if files:
            backups["latest"] = stamp(datetime.fromtimestamp(files[0].stat().st_mtime, UTC))
    except OSError:
        backups["error"] = True
    return dict(data, backups=backups)


@app.get("/api/maintenance/backup", dependencies=[Depends(access)])
def download_backup(request: APIRequest):
    folder = TemporaryDirectory(prefix="usd-scalper-backup-")
    filename = f"usd-scalper-{datetime.now(UTC):%Y%m%d-%H%M%S}-utc.sqlite3"
    path = Path(folder.name) / filename
    try:
        copy_database(request.app.state.config.database, path)
    except (OSError, sqlite3.Error):
        folder.cleanup()
        raise HTTPException(503, "Backup SQLite gagal dibuat. Coba lagi.") from None
    return FileResponse(
        path, filename=filename, media_type="application/vnd.sqlite3",
        headers={"Cache-Control": "no-store"}, background=BackgroundTask(folder.cleanup),
    )


def symbol_or_422(symbol: str) -> str:
    try:
        return canonical_symbol(symbol)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None


@app.get("/api/dashboard", dependencies=[Depends(access)])
def dashboard(request: APIRequest, response: Response, symbol: str = "XAUUSD"):
    response.headers["Cache-Control"] = "no-store"
    config = request.app.state.config
    symbol = symbol_or_422(symbol)
    providers, events = state(config.database)
    if not config.public_feeds:
        providers, events = {}, ()
    now = datetime.now(UTC)
    timeline = [
        event.row() for event in (*events, *request.app.state.manual_events)
        if {symbol[:3], symbol[3:]} & event.currencies
        and now - timedelta(hours=2) <= event.event_at <= now + timedelta(days=3)
    ]
    timeline.sort(key=lambda item: item["event_at"])
    with closing(connect(config.database)) as db:
        rows = db.execute(
            "SELECT id, payload, telegram FROM signals WHERE symbol=? ORDER BY closed DESC LIMIT 50", (symbol,)
        ).fetchall()
        watchlist = {}
        for name in SYMBOLS:
            last = rows[0] if rows and name == symbol else db.execute(
                "SELECT payload FROM signals WHERE symbol=? ORDER BY closed DESC LIMIT 1", (name,)
            ).fetchone()
            signal = json.loads(last["payload"]) if last else {}
            watchlist[name] = {
                key: signal.get(key) for key in ("side", "closed_at_utc", "reference_price")
            } | {"missing": missing_providers(providers, name, now) if config.public_feeds else []}
    return {
        "symbols": SYMBOLS, "symbol": symbol, "feeds": providers,
        "feed_status": "public" if config.public_feeds else "technical_with_optional_csv",
        "missing": missing_providers(providers, symbol, now) if config.public_feeds else [],
        "events": timeline[:20],
        "watchlist": watchlist,
        "signals": [dict(json.loads(row["payload"]), id=row["id"], telegram=row["telegram"]) for row in rows],
    }


@app.get("/api/signals/export.csv", dependencies=[Depends(access)])
def signal_csv(request: APIRequest, symbol: str = "XAUUSD", days: int = Query(7, ge=1, le=90),
               side: Literal["all", "actionable", "BUY", "SELL", "WAIT"] = "all"):
    symbol = symbol_or_422(symbol)
    now = datetime.now(UTC).replace(microsecond=0)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)
    with closing(connect(request.app.state.config.database)) as db:
        content = export_signals(db, symbol, stamp(today - timedelta(days=days - 1)), stamp(now), side)
    return Response(content, media_type="text/csv", headers={
        "Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="signals-{symbol}-{days}d.csv"',
    })


def journal_filters(status: Literal["all", "open", "closed"] = "all", side: Literal["all", "BUY", "SELL"] = "all",
                    start: date | None = None, end: date | None = None) -> JournalFilters:
    if start is not None and end is not None and start > end:
        raise HTTPException(422, "Tanggal awal harus sebelum atau sama dengan tanggal akhir.")
    return JournalFilters(status, side, start, end)


@app.get("/api/journal", dependencies=[Depends(access)])
def journal(request: APIRequest, response: Response, symbol: str = "XAUUSD", page: int = Query(1, ge=1),
            filters: JournalFilters = Depends(journal_filters)):
    symbol = symbol_or_422(symbol)
    response.headers["Cache-Control"] = "no-store"
    with closing(connect(request.app.state.config.database)) as db, db:
        db.execute("BEGIN")
        stats = summary(db, symbol, filters)
        page_size = 100
        pages = max(1, (stats["total"] + page_size - 1) // page_size)
        page = min(page, pages)
        trades = list(journal_rows(db, symbol, page_size, (page - 1) * page_size, filters))
        return {"symbol": symbol, "summary": stats, "trades": trades, "page": page, "pages": pages}


@app.get("/api/journal/export.csv", dependencies=[Depends(access)])
def export_journal(request: APIRequest, symbol: str = "XAUUSD", filters: JournalFilters = Depends(journal_filters)):
    symbol = symbol_or_422(symbol)
    with closing(connect(request.app.state.config.database)) as db:
        content = csv_export(journal_rows(db, symbol, filters=filters))
    return Response(content, media_type="text/csv", headers={
        "Cache-Control": "no-store", "Content-Disposition": f'attachment; filename="journal-{symbol}.csv"',
    })


@app.post("/api/journal", status_code=201, dependencies=[Depends(access)])
def log_trade(request: APIRequest, trade: TradeInput):
    try:
        with closing(connect(request.app.state.config.database)) as db, db:
            return add_trade(db, trade)
    except JournalError as error:
        raise HTTPException(error.status, str(error)) from None
    except sqlite3.OperationalError:
        raise HTTPException(503, "Jurnal sementara tidak tersedia. Coba simpan lagi.") from None


@app.put("/api/journal/{trade_id}/exit", dependencies=[Depends(access)])
def log_exit(request: APIRequest, trade_id: int, trade_exit: ExitInput):
    try:
        with closing(connect(request.app.state.config.database)) as db, db:
            return record_exit(db, trade_id, trade_exit)
    except JournalError as error:
        raise HTTPException(error.status, str(error)) from None
    except sqlite3.OperationalError:
        raise HTTPException(503, "Jurnal sementara tidak tersedia. Coba simpan lagi.") from None


def handle_alert(request: APIRequest, message: Alert):
    config = request.app.state.config
    if not hmac.compare_digest(message.token.encode(), config.webhook_token.encode()):
        raise HTTPException(401, "Token webhook invalid.")
    try:
        symbol = canonical_symbol(message.symbol)
    except ValueError as error:
        raise HTTPException(422, str(error)) from None
    closed = message.closed_at.astimezone(UTC)
    now = datetime.now(UTC)
    closed_text = stamp(closed)
    with closing(connect(config.database)) as db:
        old = db.execute("SELECT id FROM signals WHERE symbol=? AND closed=?", (symbol, closed_text)).fetchone()
    if old:
        return {"accepted": True, "duplicate": True, "id": old["id"]}
    if closed > now + timedelta(seconds=10) or now - closed > timedelta(seconds=120):
        raise HTTPException(422, "Alert kedaluwarsa atau waktu candle berada di masa depan.")
    providers, live_events = state(config.database) if config.public_feeds else ({}, ())
    required = missing_providers(providers, symbol, now) if config.public_feeds else []
    features = Features(**message.features.model_dump())
    setup = technical(features)
    decision = evaluate(setup, symbol, now, (*live_events, *request.app.state.manual_events), required)
    stop, target = setup.levels(features.close) if decision["side"] != "WAIT" else (None, None)
    decision.update(
        symbol=symbol, feed_symbol=message.symbol, closed_at_utc=closed_text,
        trend_closed_at_utc=stamp(message.trend_closed_at), decided_at_utc=stamp(now),
        reference_price=features.close, stop=stop, target=target,
        mode="public_feeds" if config.public_feeds else "technical_with_optional_csv",
    )
    notification = "queued" if config.telegram_token and decision["side"] != "WAIT" else "off"
    with closing(connect(config.database)) as db, db:
        saved = db.execute("""
            INSERT INTO signals(symbol, closed, payload, telegram) VALUES (?, ?, ?, ?)
            ON CONFLICT(symbol, closed) DO NOTHING RETURNING id
        """, (symbol, closed_text, json.dumps(decision), notification)).fetchone()
        record = saved or db.execute(
            "SELECT id FROM signals WHERE symbol=? AND closed=?", (symbol, closed_text)
        ).fetchone()
    return {"accepted": True, "duplicate": saved is None, "id": record["id"]}


@app.post("/webhooks/tradingview", status_code=202)
async def tradingview(request: APIRequest):
    body = bytearray()
    async for chunk in request.stream():
        body.extend(chunk)
        if len(body) > 8192:
            raise HTTPException(413, "Payload terlalu besar.")
    try:
        message = Alert.model_validate_json(body)
    except ValidationError:
        raise HTTPException(422, "JSON TradingView tidak sesuai skema.") from None
    try:
        return await asyncio.to_thread(handle_alert, request, message)
    except sqlite3.Error:
        raise HTTPException(503, "Database sementara tidak tersedia.") from None
