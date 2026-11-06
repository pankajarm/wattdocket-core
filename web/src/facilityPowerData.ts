export interface PowerSite {
  site_id: string; name: string; operator?: string | null;
  address: {street?: string; city?: string; state?: string; postal_code?: string; country?: string};
  reporting_boundary: string;
  facility_type?: string;
  identity_evidence: {url: string; quote?: string; source_fields?: Record<string, unknown>; locator: string}[];
}
export interface PowerProvenance {
  source_url: string; source_locator: string; source_sha256: string;
  retrieved_at: string; checked_at: string; collection_status: string;
}
export interface PowerDemand extends PowerProvenance {
  demand_id: string; site_id: string; value: number; unit: 'kW' | 'MW'; reporting_year: number;
  measurement_boundary: string; whole_facility: boolean; note: string;
}
export interface PowerSeries extends PowerProvenance {
  series_id: string; site_id: string; label: string; unit: 'kW';
  measurement_basis: string; measurement_boundary: string; whole_facility: boolean;
  interval_minutes: number | null; time_basis: string; methodology_note: string;
  observed_start: string; observed_end: string; measured_count: number; missing_count: number;
  readings_path: string; license: string | Record<string, unknown>;
}
export type PowerReading = [string, number | null, string | number | null, string | null, string | null, ...unknown[]];
export interface PowerReadings extends PowerSeries { readings: PowerReading[]; }
export interface FacilityPower {
  release_id: string; built_at: string; sites: PowerSite[]; series: PowerSeries[];
  demands: PowerDemand[]; coverage: Record<string, number>;
  excluded: {source_id: string; reason?: string; [key: string]: unknown}[];
}

/** Seven UTC calendar days, preserving explicit missing observations. */
export function powerWindow(readings: PowerReading[], endDay: string): PowerReading[] {
  const end = Date.parse(`${endDay}T00:00:00Z`) + 86400000;
  if (!Number.isFinite(end)) return [];
  const start = end - 7 * 86400000;
  return readings.filter(r => { const time = Date.parse(r[0]); return time >= start && time < end; });
}

/** Break lines at absent readings; no zero fill or interpolation across gaps. */
export function powerPaths(readings: PowerReading[], width: number, height: number): string[] {
  if (readings.length < 2) return [];
  const start = Date.parse(readings[0]![0]), span = Date.parse(readings[readings.length - 1]![0]) - start;
  const max = Math.max(1, ...readings.map(r => r[1] ?? 0));
  const paths: string[] = [];
  let path = '';
  for (const row of readings) {
    if (row[1] === null) { if (path) paths.push(path); path = ''; continue; }
    const x = (Date.parse(row[0]) - start) / span * width, y = height - row[1] / max * height;
    path += `${path ? 'L' : 'M'}${x.toFixed(2)},${y.toFixed(2)} `;
  }
  if (path) paths.push(path);
  return paths;
}
