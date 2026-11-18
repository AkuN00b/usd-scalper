from collections import Counter, defaultdict
from datetime import UTC, datetime, time, timedelta

from .context import FRESH_FOR, missing_providers, parse_time, stamp
from .journal import SELECT_TRADES, journal_time, trade_view
from .strategy import SYMBOLS


def signal_activity(db, since: datetime, now: datetime) -> tuple[list, dict]:
    slots = {
        symbol: dict(symbol=symbol, BUY=0, SELL=0, WAIT=0, latest=None)
        for symbol in SYMBOLS
    }
    telegram = Counter()
    for row in db.execute(
        """SELECT symbol, json_extract(payload, '$.side') AS side, telegram, count(*) AS total
           FROM signals WHERE closed >= ? AND closed <= ? GROUP BY symbol, side, telegram""",
        (stamp(since), stamp(now.replace(microsecond=0))),
    ):
        side = row["side"]
        if row["symbol"] in slots and side in {"BUY", "SELL", "WAIT"}:
            slots[row["symbol"]][side] += row["total"]
        telegram[row["telegram"]] += row["total"]
    for symbol, slot in slots.items():
        row = db.execute("SELECT max(closed) FROM signals WHERE symbol = ?", (symbol,)).fetchone()
        slot["latest"] = row[0]
    return list(slots.values()), dict(telegram)


def journal_activity(db, since: datetime, now: datetime) -> dict:
    open_positions = db.execute(
        "SELECT count(*) FROM journal_trades WHERE exited_at IS NULL"
    ).fetchone()[0]
    weeks = defaultdict(Counter)
    week_net = defaultdict(Counter)
    instruments = defaultdict(Counter)
    sessions = defaultdict(Counter)
    totals = Counter()
    total_net = Counter()
    for row in db.execute(
        SELECT_TRADES + " WHERE t.exited_at >= ? AND t.exited_at <= ?",
        (journal_time(since), journal_time(now)),
    ):
        trade = trade_view(row)
        day = parse_time(trade["exited_at"]).date()
        week_date = (day - timedelta(days=day.weekday())).isoformat()
        week = weeks[week_date]
        symbol = trade["symbol"]
        instrument = instruments[symbol]
        start_hour = parse_time(trade["opened_at"]).astimezone(UTC).hour // 6 * 6
        symbol_session = sessions[(symbol, start_hour)]
        for period in (week, instrument, totals, sessions[("", start_hour)], symbol_session):
            period["closed"] += 1
            period["wins"] += trade["gross_price_move"] > 0
            if trade["gross_r"] is not None:
                period["r_count"] += 1
                period["total_r"] += trade["gross_r"]
        if trade["entry_slippage_price"] is not None:
            for period in (instrument, symbol_session):
                period["slippage_count"] += 1
                period["total_slippage"] += trade["entry_slippage_price"]
        if trade["net_pnl"] is not None:
            week_net[week_date][trade["pnl_currency"]] += trade["net_pnl"]
            total_net[trade["pnl_currency"]] += trade["net_pnl"]

    def rollup(values: Counter) -> dict:
        return {
            "closed": values["closed"],
            "win_rate": values["wins"] / values["closed"] if values["closed"] else None,
            "total_r": values["total_r"] if values["r_count"] else None,
        }

    def with_slippage(values: Counter) -> dict:
        count = values["slippage_count"]
        return dict(rollup(values), slippage_count=count, mean_slippage=values["total_slippage"] / count if count else None)

    return {
        "open": open_positions,
        "totals": dict(rollup(totals), net=dict(sorted(total_net.items()))),
        "weeks": [
            dict(rollup(stats), monday=week, net=dict(sorted(week_net[week].items())))
            for week, stats in sorted(weeks.items(), reverse=True)
        ],
        "symbols": [
            dict(with_slippage(stats), symbol=symbol) for symbol, stats in sorted(instruments.items())
        ],
        "sessions": [
            dict(with_slippage(stats), symbol=symbol, start_hour=start_hour)
            for (symbol, start_hour), stats in sorted(sessions.items())
        ],
    }


def feed_activity(providers: dict, enabled: bool, now: datetime) -> list[dict]:
    if not enabled:
        return []
    missing = {name for symbol in SYMBOLS for name in missing_providers(providers, symbol, now)}
    result = []
    for name in ("BLS", "Fed", "UN", "ECB", "GDELT"):
        feed = providers.get(name, {})
        success = feed.get("success")
        error = feed.get("error")
        fresh = success is not None and now - parse_time(success) <= FRESH_FOR
        if fresh:
            status = "cached" if error else "ready"
        elif error:
            status = "failed"
        else:
            status = "stale" if success else "pending"
        result.append({
            "name": name, "status": status, "success": success, "error": error,
            "last_attempt": feed.get("last_attempt"), "blocking": name in missing,
        })
    return result


def overview(db, days: int, now: datetime, *, public_feeds: bool, telegram_enabled: bool) -> dict:
    start_date = now.date() - timedelta(days=days - 1)
    since = datetime.combine(start_date, time.min, tzinfo=UTC)
    providers = {row["name"]: dict(row) for row in db.execute(
        "SELECT name, last_attempt, success, error FROM feeds"
    )}
    signals, telegram = signal_activity(db, since, now)
    return {
        "since": stamp(since), "through": stamp(now),
        "public_feeds": public_feeds, "feeds": feed_activity(providers, public_feeds, now),
        "telegram_enabled": telegram_enabled, "telegram": telegram,
        "signals": signals, "journal": journal_activity(db, since, now),
    }
