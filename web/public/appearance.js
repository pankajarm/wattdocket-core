/* Shared with the app: retain a visitor's day, night, or system preference. */
(function () {
  var key = 'wattdocket-theme';
  var system = window.matchMedia('(prefers-color-scheme: dark)');
  var preference = readPreference();
  function readPreference() {
    try {
      var saved = window.localStorage.getItem(key);
      return saved === 'light' || saved === 'dark' ? saved : 'system';
    } catch (_) { return 'system'; }
  }
  function apply() {
    var theme = preference === 'system' ? (system.matches ? 'dark' : 'light') : preference;
    document.documentElement.dataset.theme = theme;
    document.documentElement.style.colorScheme = theme;
    var meta = document.querySelector('meta[name="theme-color"]');
    if (!meta) {
      meta = document.createElement('meta');
      meta.name = 'theme-color';
      document.head.appendChild(meta);
    }
    meta.content = theme === 'dark' ? '#0d1720' : '#f4f8fa';
    var select = document.querySelector('[data-theme-preference]');
    if (select) select.value = preference;
  }
  // Runs in the head, before the page is painted.
  apply();
  system.addEventListener('change', apply);
  window.addEventListener('storage', function (event) {
    if (event.key === key || event.key === null) {
      preference = readPreference();
      apply();
    }
  });
  document.addEventListener('DOMContentLoaded', function () {
    var select = document.querySelector('[data-theme-preference]');
    if (!select) return;
    select.value = preference;
    select.addEventListener('change', function () {
      preference = select.value === 'light' || select.value === 'dark' ? select.value : 'system';
      try { window.localStorage.setItem(key, preference); } catch (_) { /* Session-only when storage is unavailable. */ }
      apply();
    });
  });
})();
