"use strict";

const dashboardPreferences = (() => {
  const key = "usd-scalper-preferences";
  const defaults = { theme: "light", accent: "violet", compact: false, showContext: true };
  const darkMode = window.matchMedia("(prefers-color-scheme: dark)");
  const root = document.documentElement;
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem(key)) || {};
  } catch {
    // Use defaults when browser storage is unavailable or invalid.
  }
  const state = {
    theme: ["light", "dark", "system"].includes(saved.theme) ? saved.theme : defaults.theme,
    accent: ["violet", "blue", "teal"].includes(saved.accent) ? saved.accent : defaults.accent,
    compact: saved.compact === true,
    showContext: saved.showContext !== false,
    symbol: typeof saved.symbol === "string" ? saved.symbol : "XAUUSD",
    favorites: Array.isArray(saved.favorites) ? saved.favorites : ["XAUUSD", "EURUSD"],
  };

  function updateAppearance() {
    const theme = state.theme === "system" ? (darkMode.matches ? "dark" : "light") : state.theme;
    root.dataset.theme = theme;
    root.dataset.accent = state.accent;
    root.dataset.compact = String(state.compact);
    document.querySelector('meta[name="theme-color"]').content = theme === "dark" ? "#101726" : "#f4f6fa";
    const context = document.getElementById("context-panel");
    if (context) context.hidden = !state.showContext;
    window.dispatchEvent(new Event("appearancechange"));
  }

  function save(changes) {
    Object.assign(state, changes);
    try {
      localStorage.setItem(key, JSON.stringify(state));
    } catch {
      // Keep preferences active for the current page.
    }
  }

  function setup() {
    const modal = document.getElementById("appearance-dialog");
    const fields = [
      ["theme", "theme", "value"], ["accent", "accent", "value"],
      ["compact", "compact", "checked"], ["show-context", "showContext", "checked"],
    ];
    function renderFields() {
      for (const [id, setting, property] of fields) {
        document.getElementById(id)[property] = state[setting];
      }
    }
    for (const [id, setting, property] of fields) {
      document.getElementById(id).addEventListener("change", (event) => {
        save({ [setting]: event.target[property] });
        updateAppearance();
      });
    }
    document.getElementById("customize").addEventListener("click", () => {
      renderFields();
      modal.showModal();
    });
    document.getElementById("reset-preferences").addEventListener("click", () => {
      save(defaults);
      renderFields();
      updateAppearance();
    });
    if (!state.showContext) document.getElementById("context-panel").hidden = true;
  }

  updateAppearance();
  darkMode.addEventListener("change", () => {
    if (state.theme === "system") updateAppearance();
  });
  document.addEventListener("DOMContentLoaded", setup);
  return { state, save };
})();
