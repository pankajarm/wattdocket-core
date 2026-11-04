import type { Badge, Metric } from './types';

const nf0 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 0 });
const nf1 = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 });

/** Compact display for a chip: 154k t, 84 MW, 6.2 MW. */
export function compact(value: number, metric: Metric): string {
  const abs = Math.abs(value);
  // Sub-10 values keep a decimal: a water band is 0.9–1.4, never 1–1.
  if (abs < 10) return trim(nf1.format(value));
  if (metric === 'carbon_t' || metric === 'water_gal' || metric === 'land_acres') {
    if (abs >= 1_000_000) return `${trim(nf1.format(value / 1_000_000))}M`;
    if (abs >= 10_000) return `${nf0.format(Math.round(value / 1000))}k`;
    if (abs >= 1000) return `${trim(nf1.format(value / 1000))}k`;
    return nf0.format(value);
  }
  return trim(nf1.format(value));
}

/** Exact value for the drawer headline. Mass is tonnes with thousands separators. */
export function exact(value: number, metric: Metric): string {
  if (metric === 'carbon_t') return nf0.format(value);
  if (metric === 'power_mw' || metric === 'diesel_mw') return trim(nf1.format(value));
  return nf0.format(value);
}

function trim(s: string): string {
  return s.replace(/\.0$/, '');
}

/** Range text for a chip: "115k – 200k" — same scale on both ends. */
export function rangeText(p10: number, p90: number, metric: Metric): string {
  return `${compact(p10, metric)} – ${compact(p90, metric)}`;
}

export function isoDate(iso: string): string {
  return iso.slice(0, 10);
}

export function isoDateTime(iso: string): string {
  return `${iso.slice(0, 10)} ${iso.slice(11, 16)}Z`;
}

export const BADGE_LABEL: Record<Badge, string> = {
  MEASURED: 'measured',
  REPORTED: 'reported',
  MODELED: 'modeled',
  ESTIMATED: 'estimated',
};

export function badgeClass(badge: Badge): string {
  switch (badge) {
    case 'MEASURED':
      return 'badge badge--measured';
    case 'REPORTED':
      return 'badge badge--reported';
    case 'MODELED':
      return 'badge badge--modeled';
    case 'ESTIMATED':
      return 'badge badge--estimated';
  }
}

/** Truncate a sha256 for a receipt line without lying about its length. */
export function shortHash(sha: string): string {
  if (sha.length <= 12) return sha;
  return `${sha.slice(0, 4)}…${sha.slice(-4)}`;
}

export function fmtHour(h: number): string {
  return `${String(h).padStart(2, '0')}:00`;
}
