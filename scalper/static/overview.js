"use strict";

window.systemOverview = (() => {
  const days = byId("overview-days");
  const backup = byId("overview-backup");
  const resultTables = ["weeks", "instruments", "signals", "feeds"];
  let pending;
  let download;
  let savedDays;
  try {
    savedDays = localStorage.getItem("usd-scalper-overview-days");
  } catch {
    // Keep the default period when browser storage is unavailable.
  }
  if (["7", "30", "90"].includes(savedDays)) days.value = savedDays;

  function clear(message = "Buka akses dashboard untuk melihat ringkasan.") {
    pending?.abort();
    download?.abort();
    backup.disabled = false;
    days.disabled = !token;
    byId("overview-panels").hidden = true;
    for (const id of [...resultTables, "totals", "telegram"]) byId("overview-" + id).replaceChildren();
    for (const id of ["telegram-state", "backup-config", "backup-status"]) setText("overview-" + id, "");
    setText("overview-status", message);
  }

  function row(...cells) {
    const result = node("tr");
    result.append(...cells.map((cell) => cell instanceof HTMLElement ? cell : node("td", cell)));
    return result;
  }

  function linkedInstrument(name, tab) {
    const cell = node("td");
    const button = node("button", name);
    button.type = "button";
    button.addEventListener("click", () => {
      symbol.value = name;
      selectInstrument();
      showTab(tab);
    });
    cell.append(button);
    return cell;
  }

  function percentage(value) {
    return Number.isFinite(value) ? numeric(value * 100, 1) + "%" : "—";
  }

  function seconds(value) {
    return time(value).slice(0, 19);
  }

  function signed(value, digits = 2) {
    return node("td", numeric(value, digits), value > 0 ? "buy" : value < 0 ? "sell" : "");
  }

  function money(net, currency) {
    return numeric(net, 4) + " " + currency;
  }

  function totals(journal, signals) {
    const closed = journal.totals;
    const decisions = signals.reduce((sum, signal) => sum + signal.BUY + signal.SELL + signal.WAIT, 0);
    byId("overview-totals").replaceChildren(
      metric("Keputusan M3", decisions), metric("Posisi terbuka", journal.open),
      metric("Trade ditutup", closed.closed), metric("Win rate bruto", percentage(closed.win_rate)),
      metric("R bruto", numeric(closed.total_r, 2)),
      ...Object.entries(closed.net).map(([currency, net]) => metric("P/L bersih", money(net, currency))),
    );
  }

  function renderJournal(journal) {
    const weeks = journal.weeks.map((week) => row(
      week.monday, week.closed, percentage(week.win_rate), signed(week.total_r),
      Object.entries(week.net).map(([code, net]) => money(net, code)).join(" · ") || "—",
    ));
    byId("overview-weeks").replaceChildren(...(weeks.length ? weeks : [emptyRow(5, "Belum ada trade yang ditutup dalam periode ini.")]));
    const symbols = journal.symbols.map((item) => {
      const slippage = node("td", item.slippage_count ? numeric(item.mean_slippage, 8) + " · " + item.slippage_count + " fill" : "—");
      return row(linkedInstrument(item.symbol, "journal"), item.closed, percentage(item.win_rate), signed(item.total_r), slippage);
    });
    byId("overview-instruments").replaceChildren(...(symbols.length ? symbols : [emptyRow(5, "Belum ada trade yang ditutup dalam periode ini.")]));
  }

  function statusCell(feed) {
    const labels = { ready: "Siap", cached: "Cache masih segar", failed: "Gagal", stale: "Kedaluwarsa", pending: "Menunggu" };
    const cell = node("td", undefined, "feed-detail");
    cell.append(node("span", labels[feed.status], "badge " + (feed.status === "ready" ? "buy" : "wait")));
    if (feed.blocking) cell.append(" ", node("span", "Feed wajib", "badge sell"));
    if (feed.error) {
      const detail = node("details");
      detail.append(node("summary", "Lihat pesan"), node("p", feed.error));
      cell.append(detail);
    }
    return cell;
  }

  function render(data) {
    totals(data.journal, data.signals);
    renderJournal(data.journal);
    byId("overview-signals").replaceChildren(...data.signals.map((item) =>
      row(linkedInstrument(item.symbol, "live"), time(item.latest), item.BUY, item.SELL, item.WAIT)));
    const feeds = data.feeds.map((feed) => row(feed.name, statusCell(feed), seconds(feed.success), seconds(feed.last_attempt)));
    byId("overview-feeds").replaceChildren(...(feeds.length ? feeds : [emptyRow(4, "Mode teknikal / CSV. Feed publik dimatikan.")]));
    setText("overview-telegram-state", data.telegram_enabled ? "Terhubung di konfigurasi" : "Belum dikonfigurasi");
    byId("overview-telegram").replaceChildren(
      metric("Terkirim", data.telegram.sent ?? 0),
      metric("Antre / mengirim", (data.telegram.queued ?? 0) + (data.telegram.sending ?? 0)),
      metric("Gagal", data.telegram.failed ?? 0), metric("Kedaluwarsa", data.telegram.expired ?? 0),
      metric("Tidak terkonfirmasi", data.telegram.unknown ?? 0),
    );
    let config = data.backups.enabled ? "Backup harian aktif." : "Aktifkan backup harian dengan AUTO_BACKUP=1.";
    if (data.backups.latest) config += " File otomatis terbaru dibuat " + seconds(data.backups.latest) + " UTC.";
    if (data.backups.error) config += " Pemeriksaan backup otomatis terakhir gagal.";
    setText("overview-backup-config", config);
    setText("overview-status", data.since.slice(0, 10) + " sampai " + seconds(data.through) + " UTC");
    byId("overview-panels").hidden = false;
  }

  async function request(path, controller, timeout) {
    const response = await fetch(path, {
      headers: { Authorization: "Bearer " + token }, cache: "no-store",
      signal: AbortSignal.any([controller.signal, AbortSignal.timeout(timeout)]),
    });
    if (response.status === 401) throw new Error("Buka kembali akses dengan token dashboard.");
    if (response.status === 404) throw new Error("Fitur ini belum tersedia di server. Mulai ulang Uvicorn setelah pembaruan.");
    if (!response.ok) throw new Error("Permintaan gagal. Coba lagi.");
    return response;
  }

  async function refresh() {
    if (!token) {
      clear();
      return;
    }
    pending?.abort();
    const controller = new AbortController();
    pending = controller;
    days.disabled = false;
    setText("overview-status", "Memuat ringkasan…");
    try {
      const response = await request("/api/overview?days=" + days.value, controller, 15_000);
      const data = await response.json();
      if (!controller.signal.aborted) render(data);
    } catch (error) {
      if (!controller.signal.aborted) clear(error.message);
    }
  }

  backup.addEventListener("click", async () => {
    if (!token || backup.disabled) return;
    const controller = new AbortController();
    download = controller;
    backup.disabled = true;
    setText("overview-backup-status", "Menyiapkan backup…");
    try {
      const response = await request("/api/maintenance/backup", controller, 35_000);
      const blob = await response.blob();
      if (controller.signal.aborted) return;
      const name = response.headers.get("Content-Disposition")?.match(/filename="([^"]+)"/)?.[1];
      downloadBlob(blob, name ?? "usd-scalper.sqlite3");
      setText("overview-backup-status", "Backup siap disimpan.");
    } catch (error) {
      if (!controller.signal.aborted) setText("overview-backup-status", error.message);
    } finally {
      if (download === controller) backup.disabled = false;
    }
  });
  days.addEventListener("change", () => {
    try {
      localStorage.setItem("usd-scalper-overview-days", days.value);
    } catch {
      // Keep this period for the current page.
    }
    refresh();
  });
  return { clear, refresh };
})();
