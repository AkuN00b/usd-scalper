from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import argparse
import csv
import json
import math
from zipfile import BadZipFile

from .context import evaluate, load_events, parse_time, stamp
from .exness import candles_from_ticks, minute_groups, parse_quotes
from .strategy import Candle, MAX_HOLD_BARS, canonical_symbol, feature_stream, technical


def local_time(raw: str, zone: ZoneInfo | None) -> datetime:
    if len(raw) >= 10 and raw[4] == ".":
        raw = raw[:10].replace(".", "-") + raw[10:]
    value = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if value.tzinfo is not None:
        return value.astimezone(UTC)
    if zone is None:
        raise ValueError("Pakai --tz untuk timestamp tanpa offset.")
    candidates = {
        value.replace(tzinfo=zone, fold=fold).astimezone(UTC) for fold in (0, 1)
    }
    valid = [candidate for candidate in candidates if candidate.astimezone(zone).replace(tzinfo=None) == value]
    if len(valid) != 1:
        raise ValueError("Timestamp lokal ambigu atau tidak ada. Gunakan offset eksplisit atau UTC.")
    return valid[0]


def load_candles(path: Path, minutes: int, timezone: str | None) -> list[Candle]:
    zone = ZoneInfo(timezone) if timezone else None
    candles = []
    with path.open(newline="", encoding="utf-8-sig") as handle:
        sample = handle.read(4096)
        handle.seek(0)
        reader = csv.DictReader(handle, dialect=csv.Sniffer().sniff(sample, delimiters=",;\t"))
        for row in reader:
            row = {key.strip().strip("<>").lower(): (value or "").strip()
                   for key, value in row.items() if key is not None}
            try:
                raw_time = row.get("timestamp") or (
                    row["date"] + "T" + row["time"] if "date" in row else row["time"]
                )
                when = local_time(raw_time, zone)
                prices = [float(row[key]) for key in ("open", "high", "low", "close")]
                if not all(math.isfinite(price) and price > 0 for price in prices):
                    raise ValueError("Harga harus positif dan finite.")
                candle = Candle(when, *prices)
                if not (candle.low <= min(candle.open, candle.close)
                        <= max(candle.open, candle.close) <= candle.high):
                    raise ValueError("OHLC tidak konsisten.")
                if when.minute % minutes != 0 or when.second or when.microsecond:
                    raise ValueError(f"Waktu open harus sejajar dengan interval M{minutes} dalam UTC.")
                candles.append(candle)
            except (KeyError, ValueError) as error:
                raise ValueError(f"Baris {reader.line_num}: {error}") from error
    candles.sort(key=lambda candle: candle.time)
    if not candles or any(a.time == b.time for a, b in zip(candles, candles[1:])):
        raise ValueError("CSV harga kosong atau berisi timestamp duplikat.")
    return candles


def aggregate(candles: list[Candle], source: int, target: int) -> list[Candle]:
    if source == target:
        return candles
    grouped = {}
    for candle in candles:
        start = candle.time.replace(minute=candle.time.minute // target * target)
        grouped.setdefault(start, []).append(candle)
    return [
        Candle(start, bars[0].open, max(bar.high for bar in bars),
               min(bar.low for bar in bars), bars[-1].close)
        for start, bars in grouped.items() if len(bars) == target // source
    ]


def finite_positive(raw: str) -> float:
    value = float(raw)
    if not math.isfinite(value) or value < 0:
        raise argparse.ArgumentTypeError("Angka harus finite dan minimal nol.")
    return value


def simulate(candles, minutes, symbol, events, spread, slippage, commission, quotes, ticks=None, latency_ms=0):
    decisions, trades = [], []
    m3, m15 = aggregate(candles, minutes, 3), aggregate(candles, minutes, 15)
    features = {
        bar.time + timedelta(minutes=3): (bar, technical(feature))
        for bar, feature in zip(m3, feature_stream(m3, m15)) if feature is not None
    }
    if not features:
        raise ValueError("Belum cukup data utuh untuk warm-up. Pakai beberapa hari data M1 atau M3.")
    duration = timedelta(minutes=minutes)
    latency = timedelta(milliseconds=latency_ms)
    pending = position = None
    context_cache = tuple(events)
    tick_minutes = minute_groups(ticks) if ticks is not None else None
    last_quote = None

    def executable(price, buy):
        if quotes == "bid":
            return price + (spread if buy else 0)
        return price + (1 if buy else -1) * spread / 2

    def enter(setup, signal, at, price, observed_spread):
        nonlocal position
        buy = setup.side == "BUY"
        entry = price + (slippage if buy else -slippage)
        stop, target = setup.levels(entry)
        signal["outcome"] = "filled"
        signal["entry_at_utc"] = stamp(at)
        position = (buy, stop, target, entry, at, observed_spread, signal)

    def close(price, at, reason, bar_end=None):
        nonlocal position
        buy, stop, target, entry, entered, entry_spread, signal = position
        pnl = (price - entry) * (1 if buy else -1) - commission
        trades.append({
            "symbol": symbol, "side": signal["side"], "score": signal["score"],
            "signal_at_utc": signal["signal_at_utc"], "entry_at_utc": stamp(entered),
            "entry_price": entry, "spread_at_entry": entry_spread,
            "stop": stop, "target": target, "exit_price": price,
            "exit_at_utc": stamp(at), "exit_bar_end_utc": stamp(bar_end) if bar_end else "",
            "exit_reason": reason, "pnl_price_per_unit": pnl, "r": pnl / abs(entry - stop),
            "signal_reasons": " ".join(signal["reasons"]),
        })
        position = None

    def check_quoted_exit(price, at, resolution):
        buy, stop, target, _, entered, _, _ = position
        adverse = -slippage if buy else slippage
        if (price <= stop if buy else price >= stop):
            close(price + adverse, at, "stop_" + resolution)
        elif (price >= target if buy else price <= target):
            close(target, at, "target_" + resolution)
        elif at - entered >= timedelta(minutes=3 * MAX_HOLD_BARS):
            close(price + adverse, at, "time")

    for candle in candles:
        if pending and candle.time != pending[2]:
            pending[1]["outcome"] = "entry_skipped_missing_bar"
            pending = None
        end = candle.time + duration

        if tick_minutes is not None:
            at, group = next(tick_minutes)
            if at != candle.time:
                raise ValueError("Data tick berubah saat backtest sedang berjalan.")
            for quote in group:
                last_quote = quote
                if pending and quote.time >= pending[2] + latency:
                    setup, signal, _ = pending
                    price = quote.ask if setup.side == "BUY" else quote.bid
                    enter(setup, signal, quote.time, price, quote.ask - quote.bid)
                    pending = None
                if position:
                    check_quoted_exit(quote.bid if position[0] else quote.ask, quote.time, "tick")
            if pending:
                pending[1]["outcome"] = "entry_skipped_no_quote_after_latency"
                pending = None
        else:
            if pending:
                setup, signal, _ = pending
                enter(setup, signal, candle.time, executable(candle.open, setup.side == "BUY"), spread)
                pending = None
            if position:
                check_quoted_exit(executable(candle.open, not position[0]), candle.time, "at_open")
            if position:
                buy, stop, target, _, _, _, _ = position
                adverse = -slippage if buy else slippage
                exit_low = executable(candle.low, not buy)
                exit_high = executable(candle.high, not buy)
                if (exit_low <= stop if buy else exit_high >= stop):
                    close(stop + adverse, candle.time, "stop_intrabar", end)
                elif (exit_high >= target if buy else exit_low <= target):
                    close(target, candle.time, "target_intrabar", end)

        if end in features:
            bar, setup = features[end]
            decision = evaluate(setup, symbol, end, context_cache)
            decision.update(symbol=symbol, signal_at_utc=stamp(end), reference_price=bar.close)
            decision["outcome"] = "wait" if decision["side"] == "WAIT" else "position_already_open" if position else "pending"
            decisions.append(decision)
            if decision["outcome"] == "pending":
                pending = (setup, decision, end)

    if pending:
        pending[1]["outcome"] = "entry_skipped_end_of_data"
    if position:
        buy = position[0]
        adverse = -slippage if buy else slippage
        if last_quote is not None:
            close((last_quote.bid if buy else last_quote.ask) + adverse, last_quote.time, "end_of_data")
        else:
            last = candles[-1]
            close(executable(last.close, not buy) + adverse, last.time + duration, "end_of_data")

    cumulative = peak = drawdown = 0.0
    for trade in trades:
        cumulative += trade["r"]
        peak = max(peak, cumulative)
        drawdown = max(drawdown, peak - cumulative)
    wins = sum(trade["r"] > 0 for trade in trades)
    stats = {
        "symbol": symbol, "input_minutes": minutes,
        "execution_mode": "exness_ticks" if ticks is not None else "candle", "first_at_utc": stamp(candles[0].time),
        "last_at_utc": stamp(candles[-1].time + duration), "decision_count": len(decisions),
        "trade_count": len(trades), "win_rate": wins / len(trades) if trades else None,
        "total_r": cumulative, "max_drawdown_r": drawdown, "spread": spread,
        "slippage": slippage, "commission_price_per_unit": commission, "quotes": quotes,
        "entry_latency_ms": latency_ms if ticks is not None else None,
        "context_event_count": len(events),
        "blocked_by_context": sum(d["technical_side"] != "WAIT" and d["side"] == "WAIT" for d in decisions),
    }
    return stats, decisions, trades


def write_csv(path: Path, rows: list[dict], fields: tuple[str, ...]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value, ensure_ascii=False)
                             if isinstance(value, (list, dict)) else value for key, value in row.items()})


def main() -> None:
    parser = argparse.ArgumentParser(description="Backtest M15/M3 dengan CSV harga Exness/MT5.")
    prices = parser.add_mutually_exclusive_group(required=True)
    prices.add_argument("--csv", type=Path)
    prices.add_argument("--ticks", type=Path, nargs="+", help="Arsip atau CSV tick Exness urut dari yang terlama.")
    parser.add_argument("--from-utc", type=parse_time, help="Mulai inklusif, format ISO dengan offset.")
    parser.add_argument("--to-utc", type=parse_time, help="Berakhir eksklusif, format ISO dengan offset.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--input-minutes", type=int, choices=(1, 3), default=1)
    parser.add_argument("--tz", help="Zona waktu sumber, misalnya UTC atau Asia/Jakarta.")
    parser.add_argument("--spread", type=finite_positive, help="Spread penuh dalam satuan harga, wajib untuk CSV candle.")
    parser.add_argument("--slippage", type=finite_positive, default=0.0, help="Per eksekusi dalam satuan harga.")
    parser.add_argument("--commission", type=finite_positive, default=0.0, help="Biaya pulang pergi per unit harga.")
    parser.add_argument("--latency-ms", type=int, default=0, help="Delay entry pada mode tick, 0–59.999 ms.")
    parser.add_argument("--quotes", choices=("bid", "mid"), default="bid")
    parser.add_argument("--context", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    try:
        symbol = canonical_symbol(args.symbol)
        start, end = args.from_utc, args.to_utc
        if start and end and end <= start:
            raise ValueError("--to-utc harus setelah --from-utc.")
        for limit in (start, end):
            if limit and (limit.minute % 15 or limit.second or limit.microsecond):
                raise ValueError("Batas UTC harus sejajar dengan M15, misalnya 13:15Z.")
        if not 0 <= args.latency_ms < 60000:
            raise ValueError("--latency-ms harus berada dalam 0–59.999.")
        if args.ticks:
            if args.spread is not None or args.tz or args.input_minutes != 1 or args.quotes != "bid":
                raise ValueError("Mode tick sudah punya spread dan zona waktu. Hapus opsi candle.")
            candles = candles_from_ticks(parse_quotes(args.ticks, symbol, start, end))
            ticks = parse_quotes(args.ticks, symbol, start, end)
        else:
            if args.spread is None:
                raise ValueError("--spread wajib untuk mode CSV candle.")
            if args.latency_ms:
                raise ValueError("--latency-ms hanya tersedia dalam mode tick.")
            candles = [
                candle for candle in load_candles(args.csv, args.input_minutes, args.tz)
                if (start is None or candle.time >= start) and (end is None or candle.time < end)
            ]
            ticks = None
        events = load_events(args.context) if args.context else ()
        stats, decisions, trades = simulate(
            candles, args.input_minutes, symbol, events, args.spread,
            args.slippage, args.commission, args.quotes, ticks=ticks, latency_ms=args.latency_ms,
        )
        stats["context_mode"] = "historical_csv" if args.context else "technical_only"
        args.output.mkdir(parents=True, exist_ok=True)
        report = {"summary": stats, "trades": trades}
        (args.output / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        write_csv(args.output / "decisions.csv", decisions, (
            "symbol", "signal_at_utc", "entry_at_utc", "side", "technical_side", "score",
            "reference_price", "outcome", "reasons", "context",
        ))
        write_csv(args.output / "trades.csv", trades, (
            "symbol", "signal_at_utc", "entry_at_utc", "side", "score", "entry_price",
            "spread_at_entry", "stop", "target", "exit_at_utc", "exit_bar_end_utc", "exit_price",
            "exit_reason", "pnl_price_per_unit", "r", "signal_reasons",
        ))
    except (ValueError, OSError, KeyError, csv.Error, BadZipFile) as error:
        parser.exit(2, f"Backtest gagal: {error}\n")
    print(json.dumps(stats, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
