import { useEffect, useState } from 'react';
import { applyTheme, readThemePreference, saveThemePreference, THEME_KEY, type ThemePreference } from '../theme';

export function ThemeControl() {
  const [preference, setPreference] = useState<ThemePreference>(readThemePreference);
  useEffect(() => {
    applyTheme(preference);
    const media = window.matchMedia?.('(prefers-color-scheme: dark)');
    const updateSystem = () => applyTheme(preference);
    const updateStored = (event: StorageEvent) => {
      if (event.key === THEME_KEY || event.key === null) setPreference(readThemePreference());
    };
    media?.addEventListener('change', updateSystem);
    window.addEventListener('storage', updateStored);
    return () => {
      media?.removeEventListener('change', updateSystem);
      window.removeEventListener('storage', updateStored);
    };
  }, [preference]);

  return <label className="theme-control">
    <svg className="theme-control__sun" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true">
      <circle cx="12" cy="12" r="4" /><path d="M12 2v2m0 16v2M2 12h2m16 0h2M5 5l1.5 1.5m11 11L19 19M5 19l1.5-1.5m11-11L19 5" />
    </svg>
    <svg className="theme-control__moon" width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.6" aria-hidden="true"><path d="M20 15.3A8.7 8.7 0 0 1 8.7 4 8.8 8.8 0 1 0 20 15.3Z" /></svg>
    <select aria-label="Color theme" title="Auto follows your device appearance" value={preference} onChange={(event) => {
      const next = event.target.value as ThemePreference;
      saveThemePreference(next);
      setPreference(next);
    }}>
      <option value="system">Auto</option>
      <option value="light">Day</option>
      <option value="dark">Night</option>
    </select>
  </label>;
}
