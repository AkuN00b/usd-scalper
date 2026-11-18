"use strict";

(() => {
  const perPage = 50;
  const side = byId("report-side");
  const exit = byId("report-exit");
  const timeBand = byId("report-time");
  const exportButton = byId("report-export");
  const updateEquity = lineChart(byId("equity-chart"), (value) => numeric(Math.round(value), 0), (value) => numeric(value, 2), true);
  const exitLabels = {
    stop_at_open: "Stop saat open", target_at_open: "Target saat open", time: "Batas waktu",
    stop_intrabar: "Stop dalam candle", target_intrabar: "Target dalam candle",
    stop_tick: "Stop pada tick", target_tick: "Target pada tick", end_of_data: "Akhir data",
  };
  let primaryReport;
  let secondaryReport;
  let filtered = [];
  let currentPage = 1;
  let chartMode = "points";
  entryHours.forEach((start) => timeBand.add(new Option(entryHoursLabel(start), start)));

  function exitLabel(reason) {
    return Object.hasOwn(exitLabels, reason) ? exitLabels[reason] : reason || "—";
  }

  function tradeRow(trade) {
    const row = node("tr");
    const badge = node("td");
    badge.append(node("span", trade.side, "badge " + trade.side.toLowerCase()));
    const exit = time(trade.exit_at_utc) + (trade.exit_bar_end_utc ? " – " + time(trade.exit_bar_end_utc).slice(-8) : "");
    row.append(badge, node("td", time(trade.signal_at_utc)), node("td", time(trade.entry_at_utc)),
      node("td", numeric(trade.entry_price)), node("td", exit), node("td", exitLabel(trade.exit_reason)),
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
    const trades = primaryReport.trades;
    filtered = trades.filter((trade) =>
      (side.value === "all" || trade.side === side.value) &&
      (!exit.value || trade.exit_reason === exit.value) &&
      (!timeBand.value || String(trade.entryHour) === timeBand.value));
    currentPage = 1;
    setText("report-match", filtered.length + " / " + trades.length + " transaksi");
    const active = side.value !== "all" || exit.value !== "" || timeBand.value !== "";
    const summary = byId("report-filter-stats");
    summary.hidden = !active;
    if (active) {
      const stats = measure(filtered);
      summary.replaceChildren(
        metric("Total R · Filter", asR(filtered.length ? stats.total : null)),
        metric("Win rate · Filter", winRate(stats.wins, filtered.length)),
        metric("R per trade · Filter", asR(filtered.length ? stats.total / filtered.length : null)),
      );
    } else {
      summary.replaceChildren();
    }
    exportButton.disabled = !filtered.length;
    renderPage();
  }

  function asR(value) {
    return Number.isFinite(value) ? numeric(value, 2) + " R" : "—";
  }

  function winRate(wins, count) {
    return count ? numeric(wins * 100 / count, 1) + "%" : "—";
  }

  function measure(trades, chart = false) {
    let total = 0;
    let peak = 0;
    let gains = 0;
    let losses = 0;
    let wins = 0;
    let lossRun = 0;
    let maxLossRun = 0;
    const points = chart && trades.length ? [{ x: 0, y: 0 }] : [];
    const drawdown = [...points];
    trades.forEach((trade, index) => {
      total += trade.r;
      peak = Math.max(peak, total);
      gains += Math.max(0, trade.r);
      losses -= Math.min(0, trade.r);
      wins += Number(trade.r > 0);
      lossRun = trade.r < 0 ? lossRun + 1 : 0;
      maxLossRun = Math.max(maxLossRun, lossRun);
      if (chart) {
        points.push({ x: index + 1, y: total });
        drawdown.push({ x: index + 1, y: total - peak });
      }
    });
    return { total, gains, losses, wins, maxLossRun, points, drawdown };
  }

  function bucket(timestamp) {
    if (typeof timestamp !== "string" || !/(?:Z|[+-]\d{2}:\d{2})$/i.test(timestamp)) return null;
    const hour = new Date(timestamp).getUTCHours();
    return Number.isFinite(hour) ? Math.floor(hour / 6) * 6 : null;
  }

  function renderTimeBands(trades) {
    const rows = entryHours.map((start) => {
      const selected = trades.filter((trade) => trade.entryHour === start);
      const count = selected.length;
      const stats = measure(selected);
      const row = node("tr");
      const cell = node("td");
      const button = node("button", entryHoursLabel(start));
      button.type = "button";
      button.setAttribute("aria-label", "Filter jam entry " + entryHoursLabel(start) + " UTC");
      button.addEventListener("click", () => {
        timeBand.value = String(start);
        filterTrades();
      });
      cell.append(button);
      row.append(cell, node("td", count), node("td", winRate(stats.wins, count)),
        node("td", asR(count ? stats.total : null)), node("td", asR(count ? stats.total / count : null)));
      return row;
    });
    byId("report-time-rows").replaceChildren(...rows);
  }

  function parseReport(data) {
    const stats = data?.summary;
    const texts = ["exit_reason", "signal_at_utc", "entry_at_utc", "exit_at_utc", "exit_bar_end_utc"];
    if (!stats || typeof stats.symbol !== "string" || !Array.isArray(data.trades) ||
        !data.trades.every((trade) => trade && ["BUY", "SELL"].includes(trade.side) && Number.isFinite(trade.r) &&
          texts.every((key) => trade[key] == null || typeof trade[key] === "string"))) {
      throw new Error("Format report.json tidak sesuai.");
    }
    const trades = data.trades.map((trade) => ({ ...trade, entryHour: bucket(trade.entry_at_utc) }));
    const { total, gains, losses, maxLossRun, points, drawdown } = measure(trades, true);
    const period = [stats.first_at_utc, stats.last_at_utc]
      .filter((value) => typeof value === "string").map((value) => value.slice(0, 10)).join(" – ");
    const metrics = [
      ["Simbol", stats.symbol], ["Periode UTC", period || "—"], ["Transaksi", stats.trade_count ?? trades.length],
      ["Win rate", Number.isFinite(stats.win_rate) ? numeric(stats.win_rate * 100, 1) + "%" : "—"],
      ["Hasil", asR(stats.total_r)], ["Drawdown maks.", asR(stats.max_drawdown_r)],
      ["R per trade", trades.length ? asR(total / trades.length) : "—"],
      ["Profit factor", losses > 0 ? numeric(gains / losses, 2) : gains > 0 ? "∞" : "—"],
      ["Rugi beruntun maks.", trades.length ? maxLossRun : "—"],
      ["Harga", stats.execution_mode === "exness_ticks" ? "Tick Exness" : "Candle"],
      ["Konteks", stats.context_mode === "historical_csv" ? "CSV historis" : "Teknikal"],
    ];
    return { metrics, trades, points, drawdown, symbol: stats.symbol };
  }

  function renderChart() {
    const primary = primaryReport?.[chartMode] ?? [];
    const secondary = primaryReport ? secondaryReport?.[chartMode] ?? [] : [];
    byId("equity-view").hidden = !primary.length && !secondary.length;
    byId("equity-legend").hidden = !primary.length || !secondary.length;
    const cumulative = chartMode === "points";
    byId("chart-equity-mode").setAttribute("aria-pressed", String(cumulative));
    byId("chart-drawdown-mode").setAttribute("aria-pressed", String(!cumulative));
    const title = cumulative ? "R kumulatif per urutan trade." : "Drawdown dari puncak sebelumnya, dalam R per urutan trade.";
    setText("equity-note", title);
    byId("equity-chart").setAttribute("aria-label", title);
    updateEquity(primary, secondary);
  }

  function renderComparison() {
    const both = Boolean(primaryReport && secondaryReport);
    byId("compare-table").hidden = !both;
    byId("compare-clear").hidden = !secondaryReport;
    renderChart();
    if (both) {
      byId("compare-rows").replaceChildren(...primaryReport.metrics.map(([key, value], index) => {
        const row = node("tr");
        const label = node("th", key);
        label.scope = "row";
        row.append(label, node("td", value), node("td", secondaryReport.metrics[index][1]));
        return row;
      }));
    } else {
      byId("compare-rows").replaceChildren();
    }
  }

  function showReport(report, label) {
    primaryReport = report;
    const { metrics, trades } = report;
    byId("report-summary").replaceChildren(...metrics.map(([key, value]) => metric(key, value)));
    setText("report-status", label);
    byId("report-compare").hidden = false;
    side.value = "all";
    timeBand.value = "";
    const reasons = [...new Set(trades.map((trade) => trade.exit_reason).filter((reason) => typeof reason === "string" && reason))];
    const choices = reasons.sort().map((reason) => new Option(exitLabel(reason), reason));
    exit.replaceChildren(new Option("Semua", ""), ...choices);
    byId("report-filters").hidden = !trades.length;
    byId("report-time-view").hidden = !trades.length;
    byId("report-filter-stats").hidden = true;
    renderComparison();
    if (!trades.length) {
      byId("trades").replaceChildren(emptyRow(7, "Tidak ada transaksi dalam periode ini."));
      return;
    }
    renderTimeBands(trades);
    filterTrades();
  }

  function clearPrimary() {
    primaryReport = undefined;
    byId("report-summary").replaceChildren();
    for (const id of ["report-compare", "report-filters", "report-pages", "report-time-view", "report-filter-stats"]) {
      byId(id).hidden = true;
    }
    byId("trades").replaceChildren(emptyRow(7, "Belum ada laporan."));
    renderComparison();
  }

  function wireLoader(prefix, clearReport, renderReport) {
    const example = byId(prefix + "-example");
    const fileInput = byId(prefix + "-file");
    const status = prefix + "-status";
    let request;

    function reset() {
      request?.abort();
      clearReport();
      setText(status, "");
    }

    async function load(read, label, failure) {
      reset();
      const controller = new AbortController();
      request = controller;
      setText(status, "Memuat laporan…");
      try {
        const data = await read(controller.signal);
        if (!controller.signal.aborted) renderReport(parseReport(data), label);
      } catch {
        if (!controller.signal.aborted) {
          clearReport();
          setText(status, failure);
        }
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
    return () => {
      example.value = "";
      reset();
    };
  }

  wireLoader("report", clearPrimary, showReport);
  const clearComparison = wireLoader("compare", () => {
    secondaryReport = undefined;
    renderComparison();
  }, (report, label) => {
    secondaryReport = report;
    setText("compare-status", label);
    renderComparison();
  });
  byId("compare-clear").addEventListener("click", clearComparison);
  side.addEventListener("change", filterTrades);
  exit.addEventListener("change", filterTrades);
  timeBand.addEventListener("change", filterTrades);
  byId("chart-equity-mode").addEventListener("click", () => { chartMode = "points"; renderChart(); });
  byId("chart-drawdown-mode").addEventListener("click", () => { chartMode = "drawdown"; renderChart(); });
  exportButton.addEventListener("click", () => {
    const fields = [
      "symbol", "signal_at_utc", "entry_at_utc", "side", "score", "entry_price", "spread_at_entry", "stop", "target",
      "exit_at_utc", "exit_bar_end_utc", "exit_price", "exit_reason", "pnl_price_per_unit", "r", "signal_reasons",
    ];
    function csvCell(value) {
      if (Number.isFinite(value)) return String(value);
      if (typeof value !== "string") return "";
      const safe = /^[=+\-@]/.test(value.trimStart()) ? "'" + value : value;
      return '"' + safe.replaceAll('"', '""') + '"';
    }
    const rows = filtered.map((trade) => fields.map((field) => csvCell(trade[field])).join(",") + "\r\n");
    const csv = new Blob(["\uFEFF", fields.join(",") + "\r\n", ...rows], { type: "text/csv;charset=utf-8" });
    const name = primaryReport.symbol.replace(/[^A-Za-z0-9_-]/g, "_");
    downloadBlob(csv, "backtest-" + name + "-filtered.csv");
  });
  byId("report-prev").addEventListener("click", () => { currentPage -= 1; renderPage(); });
  byId("report-next").addEventListener("click", () => { currentPage += 1; renderPage(); });
})();
