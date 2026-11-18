"use strict";

const byId = (id) => document.getElementById(id);
const symbol = byId("symbol");
const dialog = byId("token-dialog");
const tokenKey = "usd-scalper-token";
const instruments = {
  XAUUSD: ["Emas", "Emas / Dolar AS"], EURUSD: ["Euro", "Euro / Dolar AS"],
  GBPUSD: ["Pound", "Pound Inggris / Dolar AS"], USDJPY: ["Yen", "Dolar AS / Yen Jepang"],
  USDCHF: ["Franc", "Dolar AS / Franc Swiss"], USDCAD: ["CAD", "Dolar AS / Dolar Kanada"],
  AUDUSD: ["AUD", "Dolar Australia / Dolar AS"], NZDUSD: ["NZD", "Dolar Selandia Baru / Dolar AS"],
};
const pages = {
  live: ["Pantauan market", "Arah M15 · Keputusan M3"],
  backtest: ["Backtest", "Hasil simulasi dan rincian transaksi"],
  journal: ["Jurnal trade", "Catat entry dan exit aktual dari Exness"],
};
let token = "";
try {
  token = sessionStorage.getItem(tokenKey) || "";
} catch {
  // The token can still be used for the current page.
}
let signals = [];
let watchlistData = {};
let contextEvents = [];
let activeRequest;
let lastSnapshot = "";
let activeTab = "live";

const updateSignalChart = lineChart(byId("signal-chart"),
  (value) => new Date(value).toISOString().slice(5, 16).replace("T", " "),
  (value) => numeric(value, 6));

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

function metric(label, value) {
  const item = node("dl", undefined, "metric");
  item.append(node("dt", label), node("dd", value));
  return item;
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
  if (byId("live").hidden || byId("tradingview-view").hidden) return;
  const params = new URLSearchParams({
    symbol: "OANDA:" + symbol.value, interval: "3", theme: document.documentElement.dataset.theme,
    style: "1", timezone: "Etc/UTC", locale: "en",
    hidesidetoolbar: "1", symboledit: "0", withdateranges: "1", studies: "[]",
  });
  const src = "https://s.tradingview.com/widgetembed/?" + params.toString();
  if (byId("chart").src !== src) byId("chart").src = src;
  byId("chart-link").href = "https://www.tradingview.com/chart/?" + new URLSearchParams({ symbol: params.get("symbol"), interval: "3" });
}

function showChart(external) {
  byId("signal-chart-view").hidden = external;
  byId("tradingview-view").hidden = !external;
  byId("signal-chart-tab").setAttribute("aria-pressed", String(!external));
  byId("tradingview-tab").setAttribute("aria-pressed", String(external));
  chart();
}

function renderSignalChart() {
  const points = signals.map((signal) => ({ x: Date.parse(signal.closed_at_utc), y: signal.reference_price }))
    .filter(({ x, y }) => Number.isFinite(x) && Number.isFinite(y))
    .sort((first, second) => first.x - second.x);
  byId("signal-chart").hidden = !points.length;
  byId("chart-empty").hidden = Boolean(points.length);
  setText("chart-empty-text", token ? "Belum ada harga sinyal untuk instrumen ini." : "Buka akses untuk melihat harga sinyal.");
  updateSignalChart(points);
}

function renderWatchlist() {
  const favorites = dashboardPreferences.state.favorites;
  const selected = favorites.includes(symbol.value);
  const favorite = byId("favorite");
  favorite.setAttribute("aria-pressed", String(selected));
  favorite.title = selected ? "Hapus " + symbol.value + " dari favorit" : "Tambahkan " + symbol.value + " ke favorit";
  const items = byId("watchlist-items");
  const focused = items.contains(document.activeElement) ? document.activeElement.dataset.symbol : "";
  const buttons = favorites.map((name) => {
    const button = node("button", name);
    button.type = "button";
    button.dataset.symbol = name;
    button.setAttribute("aria-pressed", String(name === symbol.value));
    const current = watchlistData[name];
    if (current) {
      button.classList.add("with-signal");
      const side = current.side ?? "—";
      button.append(node("span", side, "badge " + side.toLowerCase()));
      const details = current.closed_at_utc
        ? numeric(current.reference_price) + " · " + time(current.closed_at_utc).slice(5, 16) + " UTC"
        : "Belum ada sinyal";
      button.append(node("span", details, "watchlist-meta"));
      if (current.missing.length) {
        const status = node("span", "Feed ditunggu", "watchlist-meta wait");
        status.title = current.missing.join(", ");
        button.append(status);
      }
    } else {
      button.append(node("span", instruments[name][0], "symbol-code"));
    }
    button.addEventListener("click", () => {
      if (symbol.value === name) return;
      symbol.value = name;
      selectInstrument();
    });
    return button;
  });
  items.replaceChildren(...(buttons.length ? buttons : [node("p", "Pilih instrumen lalu klik Favorit.", "muted")]));
  buttons.find((button) => button.dataset.symbol === focused)?.focus();
}

function selectInstrument() {
  dashboardPreferences.save({ symbol: symbol.value });
  setText("market-title", symbol.value);
  setText("market-name", instruments[symbol.value][1]);
  renderWatchlist();
  chart();
  clearData();
  refresh();
  if (activeTab === "journal") window.tradeJournal.refresh();
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
  const filter = byId("signal-filter").value;
  const rows = signals.filter((signal) => filter === "all" || signal.side === filter ||
    (filter === "actionable" && signal.side !== "WAIT"));
  byId("history").replaceChildren(...(rows.length ? rows.map((signal) => {
    const row = node("tr");
    const side = node("td");
    side.append(node("span", signal.side, "badge " + signal.side.toLowerCase()));
    row.append(node("td", time(signal.closed_at_utc)), side,
      node("td", signal.score), node("td", numeric(signal.reference_price)), node("td", {
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
  }) : [emptyRow(6, token ? "Belum ada sinyal untuk filter ini." : "Buka akses untuk melihat riwayat sinyal.")]));
}

function render(data) {
  signals = data.signals;
  watchlistData = data.watchlist ?? {};
  contextEvents = data.events;
  renderWatchlist();
  latest(signals[0]);
  history();
  renderSignalChart();
  const feedList = Object.entries(data.feeds).map(([name, status]) => {
    const label = node("li", name + (status.error ? " · gagal" : " · aktif"), "badge " + (status.error ? "wait" : "buy"));
    if (status.error) label.title = status.error;
    return label;
  });
  byId("feeds").replaceChildren(...feedList);
  renderEvents();
}

function renderEvents() {
  const filter = byId("event-filter").value;
  const events = contextEvents.filter((event) => filter === "all" || event.kind === filter || event.impact === filter);
  byId("events").replaceChildren(...(events.length ? events.map((event) => {
    const item = node("li");
    const info = node("div");
    info.append(node("span", time(event.event_at) + " UTC · " + event.source, "muted"));
    if (event.impact === "high") info.append(" ", node("span", "Dampak tinggi", "badge wait"));
    item.append(info);
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
  }) : [node("li", token ? "Tidak ada event untuk filter ini." : "Buka akses untuk melihat kalender dan berita.", "muted")]));
}

function clearData() {
  lastSnapshot = "";
  signals = [];
  contextEvents = [];
  latest();
  history();
  renderSignalChart();
  byId("feeds").replaceChildren();
  renderEvents();
}

function connection(text, state = "") {
  const status = byId("connection");
  if (status.textContent !== text) status.textContent = text;
  status.dataset.state = state;
}

function setToken(value) {
  token = value;
  watchlistData = {};
  try {
    if (value) sessionStorage.setItem(tokenKey, value);
    else sessionStorage.removeItem(tokenKey);
  } catch {
    // Authentication continues in memory for the current page.
  }
  byId("access").textContent = token ? "Kelola akses" : "Akses data";
  byId("lock").hidden = !token;
  byId("refresh").hidden = !token || activeTab === "backtest";
  renderWatchlist();
  clearData();
}

async function refresh() {
  activeRequest?.abort();
  if (!token) return;
  const controller = new AbortController();
  activeRequest = controller;
  try {
    const res = await fetch("/api/dashboard?" + new URLSearchParams({ symbol: symbol.value }), {
      headers: { Authorization: "Bearer " + token },
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(12_000)]), cache: "no-store",
    });
    if (controller.signal.aborted) return;
    if (res.status === 401) {
      setToken("");
      window.tradeJournal?.refresh();
      connection("Token dashboard tidak valid", "error");
      return;
    }
    if (!res.ok) throw new Error("HTTP " + res.status);
    const data = await res.json();
    if (controller.signal.aborted) return;
    const snapshot = JSON.stringify(data);
    if (snapshot !== lastSnapshot) render(data);
    lastSnapshot = snapshot;
    if (data.missing.length) connection("Feed wajib ditunggu · " + data.missing.join(", "), "notice");
    else connection(data.feed_status === "public" ? "Feed wajib siap" : "Mode teknikal / CSV", "ready");
  } catch (error) {
    if (!controller.signal.aborted) connection("Dashboard gagal mengambil data", "error");
  }
}

function showTab(tab) {
  activeTab = tab;
  for (const name of Object.keys(pages)) {
    byId(name).hidden = name !== tab;
    byId(name + "-tab").setAttribute("aria-pressed", String(name === tab));
  }
  setText("page-title", pages[tab][0]);
  setText("page-description", pages[tab][1]);
  byId("refresh").hidden = !token || tab === "backtest";
  if (tab === "live") {
    chart();
    refresh();
  }
  if (tab === "journal") window.tradeJournal.refresh();
}

byId("access").addEventListener("click", () => dialog.showModal());
byId("cancel").addEventListener("click", () => dialog.close());
byId("lock").addEventListener("click", () => {
  activeRequest?.abort();
  setToken("");
  window.tradeJournal.refresh();
  connection("Akses data diperlukan");
  dialog.close();
});
byId("token-form").addEventListener("submit", (event) => {
  event.preventDefault();
  const value = byId("dashboard-token").value.trim();
  if (!value) return;
  setToken(value);
  byId("dashboard-token").value = "";
  dialog.close();
  connection("Memuat data…");
  refresh();
  if (activeTab === "journal") window.tradeJournal.refresh();
});
byId("refresh").addEventListener("click", () => {
  connection("Memuat data…");
  refresh();
  if (activeTab === "journal") window.tradeJournal.refresh();
});
byId("favorite").addEventListener("click", () => {
  const favorites = dashboardPreferences.state.favorites;
  dashboardPreferences.save({ favorites: favorites.includes(symbol.value)
    ? favorites.filter((name) => name !== symbol.value) : [...favorites, symbol.value] });
  renderWatchlist();
});
symbol.addEventListener("change", selectInstrument);
byId("signal-filter").addEventListener("change", history);
byId("event-filter").addEventListener("change", renderEvents);
byId("signal-chart-tab").addEventListener("click", () => showChart(false));
byId("tradingview-tab").addEventListener("click", () => showChart(true));
window.addEventListener("appearancechange", chart);
for (const name of Object.keys(pages)) {
  byId(name + "-tab").addEventListener("click", () => showTab(name));
}
document.addEventListener("visibilitychange", () => {
  if (document.hidden) activeRequest?.abort();
  else if (activeTab === "live") refresh();
});
setInterval(() => {
  if (activeTab === "live" && !document.hidden) refresh();
}, 15000);

const preferences = dashboardPreferences.state;
if (Object.hasOwn(instruments, preferences.symbol)) symbol.value = preferences.symbol;
preferences.favorites = [...new Set(preferences.favorites.filter((name) => typeof name === "string" && Object.hasOwn(instruments, name)))];
setToken(token);
selectInstrument();
