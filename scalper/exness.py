from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import groupby
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.request import Request, urlopen
from zipfile import BadZipFile, ZipFile
import argparse
import csv
import io
import math
import re
import shutil

from .strategy import Candle, canonical_symbol

BASE = "https://ticks.ex2archive.com/ticks"


@dataclass(frozen=True, slots=True)
class Quote:
    time: datetime
    bid: float
    ask: float


def download(symbol: str, month: str, directory: Path) -> Path:
    symbol = canonical_symbol(symbol)
    if not re.fullmatch(r"[0-9]{4}-(0[1-9]|1[0-2])", month):
        raise ValueError("Gunakan bulan dalam format YYYY-MM.")
    year, number = month.split("-")
    filename = f"Exness_{symbol}_{year}_{number}.zip"
    url = f"{BASE}/{symbol}/{year}/{number}/{filename}"
    directory.mkdir(parents=True, exist_ok=True)
    output = directory / filename
    if output.exists():
        raise ValueError(f"File sudah ada: {output}")
    temporary = None
    try:
        request = Request(url, headers={"User-Agent": "USDScalper/1.0 (historical backtest)"})
        with urlopen(request, timeout=45) as response, NamedTemporaryFile(
                dir=directory, prefix=filename + ".", delete=False) as handle:
            temporary = Path(handle.name)
            shutil.copyfileobj(response, handle)
        with ZipFile(temporary) as archive:
            tick_member(archive)
        temporary.rename(output)
        return output
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


def tick_member(archive: ZipFile):
    files = [item for item in archive.infolist() if not item.is_dir() and item.filename.lower().endswith(".csv")]
    if len(files) != 1 or files[0].file_size > 2_000_000_000:
        raise ValueError("ZIP Exness harus berisi satu CSV tick hingga 2 GB.")
    return files[0]


@contextmanager
def open_ticks(path: Path):
    if path.suffix.lower() == ".zip":
        with ZipFile(path) as archive, archive.open(tick_member(archive)) as buffer:
            with io.TextIOWrapper(buffer, encoding="utf-8-sig", newline="") as handle:
                yield handle
    else:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            yield handle


def parse_quotes(paths, symbol, start=None, end=None):
    previous = None
    for path in paths:
        with open_ticks(path) as handle:
            rows = csv.reader(handle)
            header = next(rows, [])
            labels = {column.strip().lower(): index for index, column in enumerate(header)}
            if not {"symbol", "timestamp", "bid", "ask"} <= labels.keys():
                raise ValueError("Tick Exness perlu kolom Symbol, Timestamp, Bid, Ask.")
            symbol_column, date_column, bid_column, ask_column = (
                labels[key] for key in ("symbol", "timestamp", "bid", "ask")
            )
            first_symbol = None
            for row in rows:
                try:
                    if row[symbol_column] != first_symbol:
                        if canonical_symbol(row[symbol_column]) != symbol:
                            raise ValueError("Simbol dalam tick berbeda dari --symbol.")
                        first_symbol = row[symbol_column]
                    at = datetime.fromisoformat(row[date_column])
                    if at.tzinfo is None:
                        raise ValueError("Tick harus menyertakan offset waktu.")
                    at = at.astimezone(UTC)
                    if previous and at < previous:
                        raise ValueError("Urutkan arsip dan tick secara kronologis.")
                    previous = at
                    if end and at >= end:
                        return
                    if start and at < start:
                        continue
                    bid, ask = float(row[bid_column]), float(row[ask_column])
                    if not (math.isfinite(bid) and math.isfinite(ask) and 0 < bid <= ask):
                        raise ValueError("Harga bid/ask tidak valid.")
                    yield Quote(at, bid, ask)
                except (IndexError, ValueError) as error:
                    raise ValueError(f"{path.name}, baris {rows.line_num}: {error}") from error


def minute_of(quote: Quote) -> datetime:
    return quote.time.replace(second=0, microsecond=0)


def minute_groups(quotes):
    return groupby(quotes, minute_of)


def candles_from_ticks(quotes) -> list[Candle]:
    candles = []
    for at, group in minute_groups(quotes):
        first = last = next(group).bid
        high = low = first
        for quote in group:
            last = quote.bid
            if last > high:
                high = last
            if last < low:
                low = last
        candles.append(Candle(at, first, high, low, last))
    return candles


def main():
    parser = argparse.ArgumentParser(description="Unduh ZIP tick bulanan dari arsip publik Exness.")
    parser.add_argument("--symbol", required=True)
    parser.add_argument("--month", required=True)
    parser.add_argument("--output", type=Path, default=Path("data"))
    args = parser.parse_args()
    try:
        print(download(args.symbol, args.month, args.output))
    except (ValueError, OSError, EOFError, BadZipFile) as error:
        parser.exit(2, f"Unduhan gagal: {error}\n")


if __name__ == "__main__":
    main()
