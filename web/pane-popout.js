// No API requests or new sessions: the opener transfers its existing live pane.
(() => {
  try { sessionStorage.clear(); } catch (_) {}
  try { window.opener?.weblabPopouts?.attach(window); } catch (_) {}
})();
