"use strict";

(() => {
  const perPage = 50;
  const example = byId("report-example");
  const fileInput = byId("report-file");
  const side = byId("report-side");
  const updateEquity = lineChart(byId("equity-chart"), (value) => numeric(Math.round(value), 0), (value) => numeric(value, 2), true);
  const exitLabels = {
    stop_at_open: "Stop saat open", target_at_open: "Target saat open", time: "Batas waktu",
    stop_intrabar: "Stop dalam candle", target_intrabar: "Target dalam candle",
    stop_tick: "Stop pada tick", target_tick: "Target pada tick", end_of_data: "Akhir data",
  };
  let request;
  let trades = [];
  let filtered = [];
  let currentPage = 1;

  function tradeRow(trade) {
    const row = node("tr");
    const badge = node("td");
    badge.append(node("span", trade.side, "badge " + trade.side.toLowerCase()));
    const exit = time(trade.exit_at_utc) + (trade.exit_bar_end_utc ? " – " + time(trade.exit_bar_end_utc).slice(-8) : "");
    row.append(badge, node("td", time(trade.signal_at_utc)), node("td", time(trade.entry_at_utc)),
      node("td", numeric(trade.entry_price)), node("td", exit), node("td", exitLabels[trade.exit_reason] ?? trade.exit_reason),
      node("td", numeric(trade.r, 3), trade.r > 0 ? "buy" : trade.r < 0 ? "sell" : ""));
    return row;
  }

  function renderPage() {
    const pages = Math.max(1, Math.ceil(filtered.length / perPage));
    currentPage = Math.min(pages, Math.max(1, currentPage));
    const offset = (currentPage - 1) * perPage;
    const rows = filtered.slice(offset, offset + perPage).map(tradeRow);
    byId("trades").replaceChildren(...(rows.length ? rows : [emptyRow(7, "Tidak ada transaksi untuk filter ini.")]));
    byId("report-pages").hidden = pages <= 1;
    byId("report-prev").disabled = currentPage === 1;
    byId("report-next").disabled = currentPage === pages;
    setText("report-page", "Halaman " + currentPage + " dari " + pages);
    byId("trades").closest(".table-wrap").scrollTop = 0;
  }

  function filterTrades() {
    filtered = side.value === "all" ? trades : trades.filter((trade) => trade.side === side.value);
    currentPage = 1;
    renderPage();
  }

  function showReport(data, label) {
    const stats = data?.summary;
    if (!stats || typeof stats.symbol !== "string" || !Array.isArray(data.trades) ||
        !data.trades.every((trade) => trade && ["BUY", "SELL"].includes(trade.side) && Number.isFinite(trade.r))) {
      throw new Error("Format report.json tidak sesuai.");
    }
    const asR = (value) => Number.isFinite(value) ? numeric(value, 2) + " R" : "—";
    const metrics = [
      ["Simbol", stats.symbol], ["Transaksi", stats.trade_count ?? data.trades.length],
      ["Win rate", Number.isFinite(stats.win_rate) ? numeric(stats.win_rate * 100, 1) + "%" : "—"],
      ["Hasil", asR(stats.total_r)], ["Drawdown maks.", asR(stats.max_drawdown_r)],
      ["Harga", stats.execution_mode === "exness_ticks" ? "Tick Exness" : "Candle"],
      ["Konteks", stats.context_mode === "historical_csv" ? "CSV historis" : "Teknikal"],
    ];
    byId("report-summary").replaceChildren(...metrics.map(([key, value]) => metric(key, value)));
    setText("report-status", label);
    trades = data.trades;
    side.value = "all";
    byId("report-filters").hidden = !trades.length;
    byId("equity-view").hidden = !trades.length;
    if (!trades.length) {
      byId("trades").replaceChildren(emptyRow(7, "Tidak ada transaksi dalam periode ini."));
      return;
    }
    let total = 0;
    const points = [{ x: 0, y: 0 }];
    trades.forEach((trade, index) => {
      total += trade.r;
      points.push({ x: index + 1, y: total });
    });
    updateEquity(points);
    filterTrades();
  }

  async function load(read, label, failure) {
    request?.abort();
    const controller = new AbortController();
    request = controller;
    trades = [];
    filtered = [];
    byId("report-summary").replaceChildren();
    byId("equity-view").hidden = byId("report-filters").hidden = byId("report-pages").hidden = true;
    updateEquity([]);
    setText("report-status", "Memuat laporan…");
    byId("trades").replaceChildren(emptyRow(7, "Belum ada laporan."));
    try {
      const data = await read(controller.signal);
      if (!controller.signal.aborted) showReport(data, label);
    } catch {
      if (!controller.signal.aborted) setText("report-status", failure);
    }
  }

  example.addEventListener("change", () => {
    const selected = example.value;
    if (!selected) return;
    fileInput.value = "";
    load(async (signal) => {
      const response = await fetch("/api/backtest/examples/" + selected, {
        signal: AbortSignal.any([signal, AbortSignal.timeout(12_000)]),
      });
      if (!response.ok) throw new Error("HTTP " + response.status);
      return response.json();
    }, "Contoh " + selected + " · 2–8 Januari 2025 UTC", "Contoh backtest gagal dimuat.");
  });
  fileInput.addEventListener("change", () => {
    const file = fileInput.files[0];
    if (!file) return;
    fileInput.value = "";
    example.value = "";
    load(async () => JSON.parse(await file.text()), file.name, "File tidak bisa dibaca. Pilih report.json dari CLI backtest.");
  });
  side.addEventListener("change", filterTrades);
  byId("report-prev").addEventListener("click", () => { currentPage -= 1; renderPage(); });
  byId("report-next").addEventListener("click", () => { currentPage += 1; renderPage(); });
})();
