from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
import csv
import json
import re
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

from icalendar import Calendar

BLS = "https://www.bls.gov/schedule/news_release/bls.ics"
FED = "https://www.federalreserve.gov/feeds/press_monetary.xml"
ECB = "https://www.ecb.europa.eu/rss/press.html"
UN = "https://news.un.org/feed/subscribe/en/news/all/rss.xml"
GDELT = "https://api.gdeltproject.org/api/v2/doc/doc"
FORMAT = ("kind", "event_at", "known_at", "currencies", "impact", "title", "source", "url")
BLS_MARKET = re.compile(r"consumer price index|producer price index|employment situation|employment cost index|job openings", re.I)
RATE = re.compile(r"monetary policy|interest rates?|policy rates?|fomc|rate decision", re.I)
GEO = re.compile(r"strait of hormuz|red sea (shipping|trade|attacks?)|oil (embargo|supply disruption)|missile (strike|attack)|ceasefire|military (strike|escalation)|trade tariffs?|sanctions on (oil|energy)", re.I)
GLOBAL = frozenset({"USD", "XAU"})
REFRESH_SECONDS = 300
FRESH_FOR = timedelta(minutes=25)


@dataclass(frozen=True)
class Event:
    kind: str
    event_at: datetime
    known_at: datetime
    currencies: frozenset[str]
    impact: str
    title: str
    source: str
    url: str = ""

    def row(self) -> dict:
        return dict(zip(FORMAT, (
            self.kind, stamp(self.event_at), stamp(self.known_at), "|".join(sorted(self.currencies)),
            self.impact, self.title, self.source, self.url,
        )))


def stamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def parse_time(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw.strip().replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp harus menyertakan zona waktu: {raw}")
    return parsed.astimezone(UTC)


def read_event(row: dict) -> Event:
    event_at = parse_time(row["event_at"])
    known_at = parse_time(row["known_at"])
    if row["kind"] not in {"scheduled", "news"} or row["impact"] not in {"medium", "high"}:
        raise ValueError("kind harus scheduled/news dan impact harus medium/high.")
    if row["kind"] == "news" and known_at < event_at:
        raise ValueError("known_at untuk berita tidak boleh sebelum event_at.")
    currencies = frozenset(word.strip().upper() for word in row["currencies"].split("|"))
    if not currencies or any(not re.fullmatch(r"[A-Z]{3}", word) for word in currencies):
        raise ValueError("Gunakan kode mata uang seperti USD|EUR.")
    if not row["title"].strip() or not row["source"].strip():
        raise ValueError("title dan source wajib diisi.")
    return Event(row["kind"], event_at, known_at, currencies, row["impact"],
                 row["title"].strip()[:500], row["source"].strip()[:150], (row.get("url") or "")[:1000])


def load_events(path: Path) -> tuple[Event, ...]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        if not reader.fieldnames or not set(FORMAT[:-1]).issubset(reader.fieldnames):
            raise ValueError("Kolom CSV konteks: " + ", ".join(FORMAT))
        events = []
        for row in reader:
            if any(row[field] is None for field in FORMAT[:-1]):
                raise ValueError(f"CSV konteks baris {reader.line_num}: kolom wajib belum diisi.")
            try:
                events.append(read_event(row))
            except ValueError as error:
                raise ValueError(f"CSV konteks baris {reader.line_num}: {error}") from error
        return tuple(events)


def context_for(events: tuple[Event, ...], symbol: str, at: datetime) -> tuple[list, list]:
    currencies = {symbol[:3], symbol[3:]}
    blockers = []
    relevant = []
    seen = set()
    for event in sorted(events, key=lambda row: row.event_at, reverse=True):
        if event.known_at > at or not (currencies & event.currencies):
            continue
        difference = at - event.event_at
        active = (
            -timedelta(minutes=15) <= difference <= timedelta(minutes=15)
            if event.kind == "scheduled" else timedelta(0) <= difference <= timedelta(minutes=25)
        )
        key = (event.kind, event.title.casefold(), event.event_at)
        if active and key not in seen:
            seen.add(key)
            relevant.append(event.row())
            if event.impact == "high":
                blockers.append(f"{event.source}: {event.title}")
    return blockers, relevant[:12]


def fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "USDScalper/1.0 (public market research)"})
    with urllib.request.urlopen(request, timeout=8) as response:
        body = response.read(4_000_001)
        if len(body) > 4_000_000:
            raise ValueError("Ukuran feed melebihi batas.")
        return body


def read_bls(body: bytes, observed: datetime) -> list[Event]:
    calendar = Calendar.from_ical(body)
    events = []
    components = calendar.walk("VEVENT")
    if not components:
        raise ValueError("Kalender BLS kosong.")
    for entry in components:
        title = str(entry.get("SUMMARY", ""))
        when = entry.decoded("DTSTART")
        if BLS_MARKET.search(title) and isinstance(when, datetime) and when.tzinfo is not None:
            when = when.astimezone(UTC)
            if observed - timedelta(hours=1) <= when <= observed + timedelta(days=14):
                events.append(Event("scheduled", when, observed, GLOBAL, "high", title, "BLS", BLS))
    return events


def recent_news(title: str, link: str, when: datetime, observed: datetime, source: str, rule, currencies) -> Event | None:
    if rule.search(title) and observed - timedelta(hours=4) <= when <= observed:
        return Event("news", when, observed, currencies, "high", title[:500], source, link[:1000])
    return None


def read_rss(body: bytes, observed: datetime, source: str, rule, currencies) -> list[Event]:
    root = ET.fromstring(body)
    items = root.findall("./channel/item")
    if not items:
        raise ValueError("RSS tidak memiliki item.")
    events = []
    for item in items:
        title, raw_date = item.findtext("title", ""), item.findtext("pubDate", "")
        try:
            when = parsedate_to_datetime(raw_date)
            if when.tzinfo is None:
                continue
        except (TypeError, ValueError):
            continue
        event = recent_news(title, item.findtext("link", ""), when, observed, source, rule, currencies)
        if event:
            events.append(event)
    return events


def read_gdelt(body: bytes, observed: datetime) -> list[Event]:
    payload = json.loads(body)
    articles = payload["articles"]
    results = []
    for article in articles:
        when = datetime.strptime(article["seendate"], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        event = recent_news(article["title"], article["url"], when, observed, "GDELT",
                            GEO, GLOBAL)
        if event:
            results.append(event)
    return results


def collect_feeds() -> list[tuple[str, datetime, list[Event] | None, str]]:
    query = urllib.parse.urlencode({
        "query": '("strait of hormuz" OR "red sea shipping" OR "oil embargo" OR "trade tariffs") sourcelang:english',
        "mode": "ArtList", "format": "json", "maxrecords": 30, "timespan": "3h",
    })
    providers = [
        ("BLS", BLS, read_bls, ()),
        ("Fed", FED, read_rss, ("Fed", RATE, GLOBAL)),
        ("ECB", ECB, read_rss, ("ECB", RATE, frozenset({"EUR"}))),
        ("UN", UN, read_rss, ("UN News", GEO, GLOBAL)),
        ("GDELT", GDELT + "?" + query, read_gdelt, ()),
    ]

    def collect(provider):
        name, url, parser, args = provider
        try:
            body = fetch(url)
            observed = datetime.now(UTC)
            return name, observed, parser(body, observed, *args), ""
        except (OSError, ValueError, KeyError, TypeError, ET.ParseError) as error:
            message = f"{type(error).__name__}: {error}"
            return name, datetime.now(UTC), None, message[:250]

    with ThreadPoolExecutor(max_workers=len(providers)) as pool:
        return list(pool.map(collect, providers))


def missing_providers(states: dict, symbol: str, at: datetime) -> list[str]:
    required = ["BLS", "Fed", "UN"]
    if "EUR" in symbol:
        required.append("ECB")
    return [
        name for name in required
        if not states.get(name, {}).get("success")
        or at - parse_time(states[name]["success"]) > FRESH_FOR
    ]


def evaluate(setup, symbol: str, at: datetime, events: tuple[Event, ...],
             missing: list[str] | tuple[str, ...] = ()) -> dict:
    blockers, relevant = context_for(events, symbol, at)
    reasons = list(setup.reasons)
    if missing:
        reasons.append("Feed wajib belum siap: " + ", ".join(missing) + ".")
    reasons.extend("Jeda entry karena konteks: " + blocker + "." for blocker in blockers[:3])
    side = "WAIT" if missing or blockers else setup.side
    return {"side": side, "technical_side": setup.side, "score": setup.score, "reasons": reasons,
            "context": relevant, "context_missing": list(missing)}
