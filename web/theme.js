// Apply browser-local appearance before styles paint; no topology/session changes.
(() => {
  'use strict';
  const key = 'weblab.theme.v1', choices = ['dark', 'light', 'system'];
  const consoleKey = 'weblab.console.theme.v1', consoleChoices = ['workspace', 'dark', 'light'];
  const root = document.documentElement;
  const system = matchMedia('(prefers-color-scheme: dark)');
  function read(key, choices, fallback) {
    try { const saved = localStorage.getItem(key); if (choices.includes(saved)) return saved; } catch (_) {}
    return fallback;
  }
  let preference = read(key, choices, 'dark');
  let consolePreference = read(consoleKey, consoleChoices, 'workspace');

  function apply() {
    const theme = preference === 'system' ? (system.matches ? 'dark' : 'light') : preference;
    const changed = root.dataset.theme !== theme;
    const consoleTheme = consolePreference === 'workspace' ? theme : consolePreference;
    const consoleChanged = root.dataset.consoleTheme !== consoleTheme;
    root.dataset.theme = theme;
    root.dataset.themePreference = preference;
    root.dataset.consoleTheme = consoleTheme;
    root.dataset.consoleThemePreference = consolePreference;
    const control = document.getElementById('theme-preference');
    if (control) control.value = preference;
    const consoleControl = document.getElementById('console-theme-preference');
    if (consoleControl) consoleControl.value = consolePreference;
    if (changed) window.dispatchEvent(new CustomEvent('weblab-theme-change'));
    if (consoleChanged) window.dispatchEvent(new CustomEvent('weblab-console-theme-change'));
  }
  system.addEventListener('change', () => { if (preference === 'system') apply(); });
  window.addEventListener('storage', event => {
    if (![key, consoleKey, null].includes(event.key)) return;
    if (event.key === key || event.key === null) preference = choices.includes(event.newValue) ? event.newValue : 'dark';
    if (event.key === consoleKey || event.key === null) consolePreference = consoleChoices.includes(event.newValue) ? event.newValue : 'workspace';
    apply();
  });
  document.addEventListener('DOMContentLoaded', () => {
    const control = document.getElementById('theme-preference');
    control.addEventListener('change', () => {
      if (!choices.includes(control.value)) return;
      preference = control.value;
      apply();
      try { localStorage.setItem(key, preference); } catch (_) {}
    });
    const consoleControl = document.getElementById('console-theme-preference');
    consoleControl.addEventListener('change', () => {
      if (!consoleChoices.includes(consoleControl.value)) return;
      consolePreference = consoleControl.value;
      apply();
      try { localStorage.setItem(consoleKey, consolePreference); } catch (_) {}
    });
    apply();
  });
  apply();
})();
