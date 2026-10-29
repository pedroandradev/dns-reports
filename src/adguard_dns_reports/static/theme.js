/* One browser preference for every report, settings page and sign-in screen. */
(() => {
  "use strict";
  const key = "dnsreports.theme";
  const root = document.documentElement;
  const system = window.matchMedia("(prefers-color-scheme: dark)");
  let preference = null;
  try {
    const saved = window.localStorage.getItem(key);
    if (saved === "light" || saved === "dark") preference = saved;
  } catch (_) { /* Storage can be unavailable in restricted browsers. */ }

  const apply = (theme) => {
    root.dataset.theme = theme;
    const dark = theme === "dark";
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      const label = dark ? "Ativar tema claro" : "Ativar tema escuro";
      button.setAttribute("aria-label", label);
      button.setAttribute("title", label);
      button.setAttribute("aria-pressed", String(dark));
      const text = button.querySelector("[data-theme-label]");
      if (text) text.textContent = dark ? "Tema claro" : "Tema escuro";
    });
  };
  const currentTheme = () => preference || (system.matches ? "dark" : "light");
  apply(currentTheme()); // Run before the stylesheet to avoid a flash of the other theme.

  document.addEventListener("DOMContentLoaded", () => {
    apply(currentTheme());
    document.querySelectorAll("[data-theme-toggle]").forEach((button) => {
      button.addEventListener("click", () => {
        preference = root.dataset.theme === "dark" ? "light" : "dark";
        try { window.localStorage.setItem(key, preference); } catch (_) {}
        apply(preference);
      });
    });
  });
  system.addEventListener("change", () => { if (!preference) apply(currentTheme()); });
  window.addEventListener("storage", (event) => {
    if (event.key === key || event.key === null) {
      preference = event.newValue === "light" || event.newValue === "dark" ? event.newValue : null;
      apply(currentTheme());
    }
  });
})();
