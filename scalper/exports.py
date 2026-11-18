import csv
import io
import json


def csv_cell(value):
    if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")):
        return "'" + value
    return value


def export_signals(db, symbol: str, start: str, end: str, side: str) -> str:
    file = io.StringIO(newline="")
    writer = csv.writer(file)
    writer.writerow((
        "id", "symbol", "feed_symbol", "closed_at_utc", "decided_at_utc", "side", "score",
        "reference_price", "stop", "target", "telegram", "reasons",
    ))
    for row in db.execute("""
        SELECT id, closed, payload, telegram FROM signals
        WHERE symbol = ? AND closed >= ? AND closed <= ? ORDER BY closed DESC
    """, (symbol, start, end)):
        signal = json.loads(row["payload"])
        decision = signal.get("side")
        if side == "actionable" and decision not in ("BUY", "SELL"):
            continue
        if side not in ("all", "actionable") and side != decision:
            continue
        writer.writerow(map(csv_cell, (
            row["id"], symbol, signal.get("feed_symbol"), row["closed"], signal.get("decided_at_utc"),
            decision, signal.get("score"), signal.get("reference_price"), signal.get("stop"),
            signal.get("target"), row["telegram"], " ".join(signal.get("reasons", [])),
        )))
    return file.getvalue()
