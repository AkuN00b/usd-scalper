from dataclasses import dataclass
from datetime import datetime, timedelta
import re

SYMBOLS = ("XAUUSD", "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "USDCAD", "AUDUSD", "NZDUSD")
MIN_SCORE = 85
STOP_ATR = 1.2
REWARD_RISK = 1.5
MAX_HOLD_BARS = 10


def canonical_symbol(raw: str) -> str:
    ticker = raw.rsplit(":", 1)[-1].strip().upper()
    if ticker == "USDEUR":
        raise ValueError("Gunakan harga EURUSD asli. USD/EUR adalah kuotasi terbalik.")
    for symbol in SYMBOLS:
        if ticker == symbol or re.fullmatch(rf"{symbol}(?:M|C|Z|[._-][A-Z0-9]+)", ticker):
            return symbol
    raise ValueError(f"Simbol belum didukung: {raw}")


@dataclass(frozen=True)
class Candle:
    time: datetime
    open: float
    high: float
    low: float
    close: float


@dataclass(frozen=True)
class Features:
    close: float
    open: float
    high: float
    low: float
    ema9: float
    ema21: float
    rsi: float
    atr: float
    previous_high: float
    previous_low: float
    trend_close: float
    trend_ema20: float
    trend_ema50: float


@dataclass(frozen=True)
class Setup:
    side: str
    score: int
    reasons: tuple[str, ...]
    stop_distance: float
    target_distance: float

    def levels(self, entry: float) -> tuple[float, float]:
        sign = 1 if self.side == "BUY" else -1
        return entry - sign * self.stop_distance, entry + sign * self.target_distance


def technical(f: Features) -> Setup:
    up = f.trend_ema20 > f.trend_ema50 and f.trend_close > f.trend_ema20
    down = f.trend_ema20 < f.trend_ema50 and f.trend_close < f.trend_ema20
    if not (up or down):
        return Setup("WAIT", 0, ("Tren M15 belum searah.",), 0, 0)

    buy = up
    bias = "naik" if buy else "turun"
    alignment = f.close > f.ema9 > f.ema21 if buy else f.close < f.ema9 < f.ema21
    momentum = 52 <= f.rsi <= 72 if buy else 28 <= f.rsi <= 48
    breakout = f.close > f.previous_high if buy else f.close < f.previous_low
    body = f.close - f.open if buy else f.open - f.close
    impulse = f.atr > 0 and 0.25 * f.atr <= body <= 1.5 * f.atr
    bools = (alignment, momentum, breakout, impulse)
    scores = (20, 15, 15, 10)
    descriptions = (
        "harga dan EMA 9/21 M3 searah",
        f"RSI M3 {f.rsi:.1f} dalam rentang",
        "harga menembus ekstrem candle M3 sebelumnya",
        "badan candle sesuai filter ATR",
    )
    missing = ("EMA M3", "RSI M3", "breakout M3", "ukuran candle")
    score = 40 + sum(weight for condition, weight in zip(bools, scores) if condition)
    reasons = [f"Tren M15 {bias}."]
    reasons.extend(f"Konfirmasi: {name}." for ok, name in zip(bools, descriptions) if ok)
    if not all(bools):
        reasons.append("Belum terpenuhi: " + ", ".join(name for ok, name in zip(bools, missing) if not ok) + ".")
    if not (alignment and breakout and score >= MIN_SCORE and f.atr > 0):
        return Setup("WAIT", score, tuple(reasons), 0, 0)
    distance = STOP_ATR * f.atr
    return Setup("BUY" if buy else "SELL", score, tuple(reasons), distance, distance * REWARD_RISK)


class EMA:
    def __init__(self, length: int):
        self.alpha = 2 / (length + 1)
        self.value = None

    def push(self, value: float) -> float:
        self.value = value if self.value is None else self.alpha * value + (1 - self.alpha) * self.value
        return self.value


class RMA:
    def __init__(self, length: int):
        self.length = length
        self.seed = []
        self.value = None

    def push(self, value: float) -> float | None:
        if self.value is not None:
            self.value = (value + (self.length - 1) * self.value) / self.length
        else:
            self.seed.append(value)
            if len(self.seed) == self.length:
                self.value = sum(self.seed) / self.length
        return self.value


def feature_stream(m3: list[Candle], m15: list[Candle]):
    trend_fast, trend_slow = EMA(20), EMA(50)
    fast, slow, atr = EMA(9), EMA(21), RMA(14)
    gains, losses = RMA(14), RMA(14)
    previous = None
    index = 0
    trend = None
    for candle in m3:
        while index < len(m15) and m15[index].time + timedelta(minutes=15) <= candle.time:
            higher = m15[index]
            trend = (higher.close, trend_fast.push(higher.close), trend_slow.push(higher.close))
            index += 1
        ema9, ema21 = fast.push(candle.close), slow.push(candle.close)
        true_range = candle.high - candle.low
        if previous is not None:
            true_range = max(true_range, abs(candle.high - previous.close), abs(candle.low - previous.close))
            change = candle.close - previous.close
            gain, loss = gains.push(max(0, change)), losses.push(max(0, -change))
        else:
            gain = loss = None
        average_range = atr.push(true_range)
        if (
            index >= 50 and candle.time - m15[index - 1].time <= timedelta(minutes=27)
            and gain is not None and loss is not None and average_range is not None
            and previous is not None and candle.time - previous.time == timedelta(minutes=3)
        ):
            rsi = 100 if loss == 0 else 0 if gain == 0 else 100 - 100 / (1 + gain / loss)
            yield Features(candle.close, candle.open, candle.high, candle.low, ema9, ema21, rsi,
                           average_range, previous.high, previous.low, *trend)
        else:
            yield None
        previous = candle
