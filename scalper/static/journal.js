"use strict";

window.tradeJournal = (() => {
  const entryDialog = byId("entry-dialog");
  const exitDialog = byId("exit-dialog");
  let chosenSignal = null;
  let chosenTrade = null;
  let entryRequestId = "";
  let listRequest;
  let currentInstrument = "";
  let currentPage = 1;
  let appliedFilters = {};

  const zone = Intl.DateTimeFormat().resolvedOptions().timeZone;
  document.querySelectorAll(".form-zone").forEach((label) => { label.textContent = "(" + zone + ")"; });

  function localTime(date = new Date()) {
    return new Date(date.getTime() - date.getTimezoneOffset() * 60_000).toISOString().slice(0, 19);
  }

  function utcTime(input) {
    const raw = byId(input).value;
    const date = new Date(raw);
    if (!Number.isFinite(date.getTime()) || !localTime(date).startsWith(raw)) {
      throw new Error("Masukkan waktu lokal yang valid.");
    }
    return date.toISOString();
  }

  function journalTime(value) {
    return time(value?.replace(/\.0+Z$/, "Z"));
  }

  function numberField(input) {
    const field = byId(input);
    return field.value === "" ? null : field.valueAsNumber;
  }

  function money(value, currency) {
    return Number.isFinite(value) ? numeric(value, 4) + " " + currency : "—";
  }

  async function request(path, options = {}) {
    if (!token) throw new Error("Buka akses dashboard terlebih dahulu.");
    const response = await fetch(path, {
      ...options,
      headers: { Authorization: "Bearer " + token, ...(options.body ? { "Content-Type": "application/json" } : {}) },
      cache: "no-store", signal: options.signal || AbortSignal.timeout(12_000),
    });
    if (!response.ok) {
      const payload = await response.json().catch(() => ({}));
      const detail = payload.detail;
      if (typeof detail === "string") throw new Error(detail);
      if (Array.isArray(detail) && detail[0]?.msg) {
        throw new Error(detail[0].msg.replace(/^Value error, /, ""));
      }
      throw new Error("Jurnal gagal dimuat atau disimpan.");
    }
    return response;
  }

  function renderRow(trade) {
    const row = node("tr");
    row.append(
      node("td", journalTime(trade.opened_at)), node("td", trade.side, trade.side.toLowerCase()),
      node("td", numeric(trade.entry, 9)), node("td", journalTime(trade.exited_at)),
      node("td", numeric(trade.exit_price, 9)), node("td", numeric(trade.gross_r, 2)),
      node("td", money(trade.net_pnl, trade.pnl_currency), trade.net_pnl > 0 ? "buy" : trade.net_pnl < 0 ? "sell" : ""),
    );
    const info = node("td");
    const reference = trade.signal_at ? "Sinyal " + journalTime(trade.signal_at) + " UTC · skor " + trade.signal_score : "";
    const details = node("details");
    const initial = [];
    if (trade.stop !== null) initial.push("SL awal " + numeric(trade.stop, 9));
    if (trade.target !== null) initial.push("TP awal " + numeric(trade.target, 9));
    if (trade.lots != null) initial.push("Lot " + numeric(trade.lots, 8));
    if (Number.isFinite(trade.entry_slippage_price)) {
      initial.push("Referensi " + numeric(trade.signal_reference, 9),
        "Selisih fill " + numeric(trade.entry_slippage_price, 9) + " (positif berarti fill kurang menguntungkan)");
    }
    const amounts = [];
    if (trade.gross_pnl != null) amounts.push("P/L sebelum biaya " + money(trade.gross_pnl, trade.pnl_currency));
    if (trade.costs != null) amounts.push("Biaya " + money(trade.costs, trade.pnl_currency));
    const notes = [
      reference, trade.signal_reasons.join(" "), initial.join(" · "), amounts.join(" · "),
      trade.notes && "Entry · " + trade.notes, trade.exit_notes && "Exit · " + trade.exit_notes,
    ].filter(Boolean);
    if (notes.length) {
      details.append(node("summary", "Lihat"), ...notes.map((note) => node("p", note)));
      info.append(details);
    } else {
      info.textContent = "—";
    }

    const action = node("td");
    if (trade.exited_at) {
      action.textContent = "Ditutup";
    } else {
      const exit = node("button", "Catat exit");
      exit.type = "button";
      exit.addEventListener("click", () => openExit(trade));
      action.append(exit);
    }
    row.append(info, action);
    return row;
  }

  function resetList(message) {
    byId("journal-export").disabled = true;
    byId("journal-pages").hidden = true;
    byId("journal-summary").replaceChildren();
    setText("journal-status", message);
    byId("journal-rows").replaceChildren(emptyRow(9, "Belum ada data untuk ditampilkan."));
  }

  async function refresh() {
    listRequest?.abort();
    byId("journal-filters").hidden = !token;
    if (!token) {
      resetList("Buka akses dashboard untuk melihat jurnal.");
      return;
    }
    const controller = new AbortController();
    listRequest = controller;
    resetList("Memuat jurnal…");
    try {
      if (currentInstrument !== symbol.value) {
        currentInstrument = symbol.value;
        currentPage = 1;
      }
      const params = new URLSearchParams({ symbol: currentInstrument, page: currentPage, ...appliedFilters });
      const response = await request("/api/journal?" + params, {
        signal: AbortSignal.any([controller.signal, AbortSignal.timeout(12_000)]),
      });
      const { summary, trades, page, pages } = await response.json();
      if (controller.signal.aborted) return;
      const win = summary.gross_win_rate === null ? "—" : numeric(summary.gross_win_rate * 100, 1) + "%";
      byId("journal-summary").replaceChildren(
        metric("Posisi terbuka", summary.total - summary.closed), metric("Trade ditutup", summary.closed),
        metric("Win rate bruto", win), metric("R bruto rata-rata", numeric(summary.mean_gross_r, 2)),
        metric("R bruto total", numeric(summary.total_gross_r, 2)),
        ...(summary.net_by_currency ?? []).map(({ currency, net, trades }) =>
          metric("P/L bersih · " + trades + " trade", money(net, currency))),
      );
      const message = [summary.total + " trade sesuai filter"];
      if (summary.r_count) message.push("R dari " + summary.r_count + " trade dengan SL awal");
      setText("journal-status", message.join(" · "));
      currentPage = page;
      byId("journal-pages").hidden = pages <= 1;
      byId("journal-prev").disabled = page <= 1;
      byId("journal-next").disabled = page >= pages;
      setText("journal-page", "Halaman " + page + " dari " + pages);
      byId("journal-rows").replaceChildren(...(trades.length ? trades.map(renderRow)
        : [emptyRow(9, "Tidak ada trade sesuai filter.")]));
      byId("journal-export").disabled = !summary.total;
    } catch (error) {
      if (!controller.signal.aborted) resetList(error.message);
    }
  }

  function openEntry(signal = null) {
    if (!token) {
      dialog.showModal();
      return;
    }
    byId("entry-form").reset();
    chosenSignal = signal;
    entryRequestId = Array.from(crypto.getRandomValues(new Uint8Array(16)), (byte) => byte.toString(16).padStart(2, "0")).join("");
    byId("entry-symbol").value = signal?.symbol ?? symbol.value;
    byId("entry-side").disabled = Boolean(signal);
    byId("entry-side").value = signal?.side ?? "BUY";
    byId("entry-at").value = localTime();
    byId("entry-signal").hidden = !signal;
    if (signal) setText("entry-signal", "Sinyal " + journalTime(signal.closed_at_utc) + " UTC · referensi " + numeric(signal.reference_price, 9));
    setText("entry-error", "");
    entryDialog.showModal();
  }

  function openExit(trade) {
    byId("exit-form").reset();
    byId("exit-costs").closest("details").open = false;
    chosenTrade = trade;
    byId("exit-at").value = localTime();
    setText("exit-trade", trade.symbol + " · " + trade.side + " · entry " + numeric(trade.entry, 9));
    setText("exit-error", "");
    exitDialog.showModal();
  }

  function wireForm(name, modal, submit) {
    const form = byId(name + "-form");
    const cancel = byId(name + "-cancel");
    const save = form.querySelector('[type="submit"]');
    cancel.addEventListener("click", () => modal.close());
    modal.addEventListener("cancel", (event) => { if (save.disabled) event.preventDefault(); });
    form.addEventListener("submit", async (event) => {
      event.preventDefault();
      if (save.disabled) return;
      save.disabled = cancel.disabled = true;
      setText(name + "-error", "");
      try {
        await submit();
        modal.close();
        if (name === "entry") currentPage = 1;
        showTab("journal");
      } catch (error) {
        setText(name + "-error", error.name === "TimeoutError" ? "Permintaan habis waktu. Coba simpan lagi." : error.message);
      } finally {
        save.disabled = cancel.disabled = false;
      }
    });
  }

  wireForm("entry", entryDialog, () => request("/api/journal", {
    method: "POST", body: JSON.stringify({
      request_id: entryRequestId, symbol: byId("entry-symbol").value, side: byId("entry-side").value,
      opened_at: utcTime("entry-at"), entry: numberField("entry-price"),
      stop: numberField("entry-stop"), target: numberField("entry-target"), lots: numberField("entry-lots"),
      signal_id: chosenSignal?.id ?? null, notes: byId("entry-notes").value,
    }),
  }));
  wireForm("exit", exitDialog, () => request("/api/journal/" + chosenTrade.id + "/exit", {
    method: "PUT", body: JSON.stringify({
      exited_at: utcTime("exit-at"), exit_price: numberField("exit-price"), exit_notes: byId("exit-notes").value,
      gross_pnl: numberField("exit-gross"), costs: numberField("exit-costs"),
      pnl_currency: byId("exit-currency").value.trim().toUpperCase() || null,
    }),
  }));

  byId("journal-prev").addEventListener("click", () => { currentPage -= 1; refresh(); });
  byId("journal-next").addEventListener("click", () => { currentPage += 1; refresh(); });
  byId("journal-new").addEventListener("click", () => openEntry());
  byId("journal-filters").addEventListener("submit", (event) => {
    event.preventDefault();
    const start = byId("journal-start").value;
    const end = byId("journal-end").value;
    if (start && end && start > end) {
      setText("journal-status", "Tanggal awal harus sebelum atau sama dengan tanggal akhir.");
      return;
    }
    appliedFilters = { status: byId("journal-state").value, side: byId("journal-side").value };
    if (start) appliedFilters.start = start;
    if (end) appliedFilters.end = end;
    currentPage = 1;
    refresh();
  });
  byId("journal-filters").addEventListener("reset", () => {
    appliedFilters = {};
    currentPage = 1;
    refresh();
  });
  byId("journal-export").addEventListener("click", async () => {
    const instrument = symbol.value;
    try {
      const response = await request("/api/journal/export.csv?" + new URLSearchParams({ symbol: instrument, ...appliedFilters }));
      const url = URL.createObjectURL(await response.blob());
      const link = node("a");
      link.href = url;
      link.download = "journal-" + instrument + ".csv";
      document.body.append(link);
      link.click();
      link.remove();
      setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (error) {
      setText("journal-status", error.message);
    }
  });

  return { openEntry, refresh };
})();
