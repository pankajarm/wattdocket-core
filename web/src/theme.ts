export type ThemePreference = 'system' | 'light' | 'dark';
export const THEME_KEY = 'wattdocket-theme';

export function readThemePreference(): ThemePreference {
  try {
    const value = window.localStorage.getItem(THEME_KEY);
    return value === 'light' || value === 'dark' ? value : 'system';
  } catch {
    return 'system';
  }
}

export function resolveTheme(preference: ThemePreference, systemDark: boolean): 'light' | 'dark' {
  return preference === 'system' ? (systemDark ? 'dark' : 'light') : preference;
}

export function applyTheme(preference: ThemePreference) {
  if (typeof document === 'undefined') return;
  const theme = resolveTheme(preference, window.matchMedia?.('(prefers-color-scheme: dark)').matches ?? false);
  document.documentElement.dataset.theme = theme;
  document.documentElement.style.colorScheme = theme;
  document.querySelector('meta[name="theme-color"]')?.setAttribute('content', theme === 'dark' ? '#0d1720' : '#f4f8fa');
}

export function saveThemePreference(preference: ThemePreference) {
  try { window.localStorage.setItem(THEME_KEY, preference); } catch { /* Still works in this session. */ }
  applyTheme(preference);
}
