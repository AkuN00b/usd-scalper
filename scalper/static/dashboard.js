"use strict";

const byId = (id) => document.getElementById(id);
const symbol = byId("symbol");
const dialog = byId("token-dialog");
let token = sessionStorage.getItem("usd-scalper-token") || "";
let signals = [];
let activeRequest;
let lastSnapshot = "";

function node(tag, content, className) {
  const element = document.createElement(tag);
  if (content !== undefined) element.textContent = content;
  if (className) element.className = className;
  return element;
}

function time(value) {
  return value ? value.replace("T", " ").replace(/Z$/, "") : "—";
}

function numeric(value, digits = 6) {
  return Number.isFinite(value) ? value.toLocaleString("id-ID", { maximumFractionDigits: digits }) : "—";
}

function setText(id, text) {
  byId(id).textContent = text;
}

function emptyRow(count, text) {
  const row = node("tr");
  const cell = node("td", text, "muted");
  cell.colSpan = count;
  row.append(cell);
  return row;
}

function safeLink(url) {
  try {
    const target = new URL(url);
    return ["http:", "https:"].includes(target.protocol) ? target.href : "";
  } catch {
    return "";
  }
}

function chart() {
  const params = new URLSearchParams({
    symbol: "OANDA:" + symbol.value, interval: "3", theme: "dark",
    style: "1", timezone: "Etc/UTC", locale: "en",
    hidesidetoolbar: "1", symboledit: "0", withdateranges: "1", studies: "[]",
  });
  byId("chart").src = "https://s.tradingview.com/widgetembed/?" + params.toString();
  byId("chart-link").href = "https://www.tradingview.com/chart/?" + new URLSearchParams({ symbol: params.get("symbol"), interval: "3" });
  setText("market-title", symbol.value);
}

function latest(signal) {
  setText("signal-side", signal?.side ?? "—");
  byId("signal-side").className = "side " + (signal?.side ?? "").toLowerCase();
  setText("signal-time", signal ? time(signal.closed_at_utc) + " UTC" : "");
  setText("signal-score", signal ? "Skor teknikal " + signal.score + "/100 · " + signal.feed_symbol : "Menunggu alert TradingView");
  setText("reference", numeric(signal?.reference_price));
  setText("stop", numeric(signal?.stop));
  setText("target", numeric(signal?.target));
  byId("reasons").replaceChildren(...(signal?.reasons ?? []).map((text) => node("li", text)));
  const record = byId("latest-entry");
  record.hidden = !(signal?.side === "BUY" || signal?.side === "SELL");
  record.onclick = record.hidden ? null : () => window.tradeJournal.openEntry(signal);
}

function history() {
  const rows = signals.filter((signal) => !byId("actionable").checked || signal.side !== "WAIT");
  byId("history").replaceChildren(...(rows.length ? rows.map((signal) => {
    const row = node("tr");
    row.append(node("td", time(signal.closed_at_utc)), node("td", signal.side, signal.side.toLowerCase()),
      node("td", signal.score), node("td", {
        off: "—", sent: "Terkirim", failed: "Gagal", queued: "Antre", sending: "Mengirim",
        unknown: "Tidak terkonfirmasi", expired: "Kedaluwarsa",
      }[signal.telegram] ?? "—"));
    const cell = node("td");
    const details = node("details");
    details.append(node("summary", "Lihat"), node("p", signal.reasons.join(" ")));
    if (signal.side === "BUY" || signal.side === "SELL") {
      const record = node("button", "Catat entry");
      record.type = "button";
      record.addEventListener("click", () => window.tradeJournal.openEntry(signal));
      details.append(record);
    }
    cell.append(details);
    row.append(cell);
    return row;
  }) : [emptyRow(5, "Belum ada sinyal untuk filter ini.")]));
}

function render(data) {
  signals = data.signals;
  latest(signals[0]);
  history();
  const feedList = Object.entries(data.feeds).map(([name, status]) => {
    const label = node("li", name + (status.error ? " · gagal" : " · aktif"), status.error ? "wait" : "buy");
    if (status.error) label.title = status.error;
    return label;
  });
  byId("feeds").replaceChildren(...feedList);
  byId("events").replaceChildren(...(data.events.length ? data.events.map((event) => {
    const item = node("li");
    item.append(node("span", time(event.event_at) + " UTC · " + event.source, "muted"));
    const link = safeLink(event.url);
    if (link) {
      const anchor = node("a", event.title);
      anchor.href = link;
      anchor.target = "_blank";
      anchor.rel = "noopener noreferrer";
      item.append(anchor);
    } else {
      item.append(node("span", event.title));
    }
    return item;
  }) : [node("li", "Tidak ada event relevan dalam rentang pantauan.", "muted")]));
}

function clearData() {
  lastSnapshot = "";
  signals = [];
  latest();
  history();
  byId("feeds").replaceChildren();
  byId("events").replaceChildren(node("li", "Belum ada data.", "muted"));
}

async function refresh() {
  activeRequest?.abort();
  if (!token) return;
  const controller = new AbortController();
  activeRequest = controller;
  try {
    const res = await fetch("/api/dashboard?" + new URLSearchParams({ symbol: symbol.value }), {
      headers: { Authorization: "Bearer " + token }, signal: controller.signal, cache: "no-store",
    });
    if (res.status === 401) {
      token = "";
      sessionStorage.removeItem("usd-scalper-token");
      clearData();
      window.tradeJournal?.refresh();
      setText("connection", "Token dashboard tidak valid");
      return;
    }
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    if (controller.signal.aborted) return;
    const snapshot = JSON.stringify(data);
    if (snapshot !== lastSnapshot) render(data);
    lastSnapshot = snapshot;
    setText("connection", data.missing.length ? "Feed wajib ditunggu · " + data.missing.join(", ") :
      data.feed_status === "public" ? "Feed wajib siap" : "Mode teknikal / CSV");
  } catch (error) {
    if (error.name !== "AbortError") setText("connection", "Dashboard gagal mengambil data");
  }
}

function showTab(tab) {
  for (const name of ["live", "backtest", "journal"]) {
    byId(name).hidden = name !== tab;
    byId(name + "-tab").setAttribute("aria-pressed", String(name === tab));
  }
  if (tab === "journal") window.tradeJournal.refresh();
}

function report(data) {
  const stats = data.summary;
  if (!stats || !Array.isArray(data.trades) || typeof stats.symbol !== "string") {
    throw new Error("Format report.json tidak sesuai.");
  }
  const metrics = [
    ["Simbol", stats.symbol], ["Transaksi", stats.trade_count],
    ["Win rate", stats.win_rate === null ? "—" : numeric(stats.win_rate * 100, 1) + "%"],
    ["Hasil", numeric(stats.total_r, 2) + " R"], ["Drawdown", numeric(stats.max_drawdown_r, 2) + " R"],
    ["Harga", stats.execution_mode === "exness_ticks" ? "Tick Exness" : "Candle"],
    ["Konteks", stats.context_mode === "historical_csv" ? "CSV historis" : "Teknikal"],
  ];
  byId("report-summary").replaceChildren(...metrics.map(([key, value]) => {
    const item = node("dl", undefined, "metric");
    item.append(node("dt", key), node("dd", value));
    return item;
  }));
  const rows = data.trades.slice(0, 1000).map((trade) => {
    const row = node("tr");
    const exit = time(trade.exit_at_utc) + (trade.exit_bar_end_utc ? " – " + time(trade.exit_bar_end_utc).slice(-8) : "");
    const labels = { stop_at_open: "Stop saat open", target_at_open: "Target saat open", time: "Batas waktu",
      stop_intrabar: "Stop dalam candle", target_intrabar: "Target dalam candle",
      stop_tick: "Stop pada tick", target_tick: "Target pada tick", end_of_data: "Akhir data" };
    row.append(node("td", trade.side), node("td", time(trade.signal_at_utc)), node("td", time(trade.entry_at_utc)),
      node("td", numeric(trade.entry_price)), node("td", exit), node("td", labels[trade.exit_reason] ?? trade.exit_reason),
      node("td", numeric(trade.r, 3)));
    return row;
  });
  if (data.trades.length > 1000) rows.push(emptyRow(7, "Tampilan dibatasi 1.000 transaksi. Riwayat lengkap ada di trades.csv."));
  byId("trades").replaceChildren(...(rows.length ? rows : [emptyRow(7, "Tidak ada transaksi dalam periode ini.")]));
}

byId("access").addEventListener("click", () => dialog.showModal());
byId("cancel").addEventListener("click", () => dialog.close());
byId("token-form").addEventListener("submit", (event) => {
  event.preventDefault();
  token = byId("dashboard-token").value.trim();
  sessionStorage.setItem("usd-scalper-token", token);
  byId("dashboard-token").value = "";
  dialog.close();
  clearData();
  refresh();
  if (!byId("journal").hidden) window.tradeJournal.refresh();
});
symbol.addEventListener("change", () => {
  chart();
  clearData();
  refresh();
  if (!byId("journal").hidden) window.tradeJournal.refresh();
});
byId("actionable").addEventListener("change", history);
for (const name of ["live", "backtest", "journal"]) {
  byId(name + "-tab").addEventListener("click", () => showTab(name));
}
byId("report-file").addEventListener("change", async (event) => {
  const file = event.target.files[0];
  if (!file) return;
  try {
    report(JSON.parse(await file.text()));
  } catch {
    setText("report-summary", "File tidak bisa dibaca. Pilih report.json dari CLI backtest.");
    byId("trades").replaceChildren(emptyRow(7, "Belum ada laporan."));
  }
});
chart();
refresh();
setInterval(refresh, 15000);
