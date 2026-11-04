import type { Claim, SourceStatus, FacilityIndexEntry } from './types';

export function dateLabel(value: string | null | undefined, time = false): string {
  if (!value) return 'Not supplied';
  const date = new Date(value);
  if (!Number.isFinite(date.getTime())) return value;
  return new Intl.DateTimeFormat('en-US', {
    month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC',
    ...(time ? { hour: 'numeric', minute: '2-digit', timeZoneName: 'short' } as const : {}),
  }).format(date);
}

export function sourceState(source: SourceStatus, now = Date.now()): SourceStatus['status'] {
  const at = source.last_success_at ?? source.retrieved_at;
  if (source.status === 'current' && source.stale_after_seconds != null && at &&
      Number.isFinite(Date.parse(at)) && now - Date.parse(at) > source.stale_after_seconds * 1000) return 'stale';
  return source.status;
}

export const SOURCE_STATE_LABEL: Record<SourceStatus['status'], string> = {
  current: 'Collection current', stale: 'Update delayed', failed: 'Collection failed', not_configured: 'Not connected',
};

export function observationLabel(source: Pick<SourceStatus, 'observation_start' | 'observation_end' | 'data_vintage'>): string {
  if (source.data_vintage && (/^\d{4}$/.test(source.data_vintage) || /^eGRID/i.test(source.data_vintage))) return source.data_vintage;
  if (source.observation_start && source.observation_end) return `${source.data_vintage ? source.data_vintage + ' · ' : ''}${dateLabel(source.observation_start, true)} – ${dateLabel(source.observation_end, true)}`;
  if (source.data_vintage) return source.data_vintage;
  if (source.observation_start && source.observation_end && source.observation_start.slice(0, 10) !== source.observation_end.slice(0, 10)) {
    return `${dateLabel(source.observation_start)} – ${dateLabel(source.observation_end)}`;
  }
  return source.observation_end ? dateLabel(source.observation_end) : source.observation_start ? dateLabel(source.observation_start) : 'Reporting period not supplied';
}

export function claimDateLabel(claim: Claim): string {
  if (claim.data_vintage) return `Data: ${claim.data_vintage}`;
  if (claim.observation_end) return `Data through ${dateLabel(claim.observation_end)}`;
  if (claim.retrieved_at) return `Retrieved ${dateLabel(claim.retrieved_at)}`;
  return claim.as_of_utc ? `Calculated ${dateLabel(claim.as_of_utc)}` : 'Source date not supplied';
}

export function safeSourceUrl(value: string | null | undefined): string | undefined {
  if (!value) return undefined;
  if (/^https?:\/\//i.test(value)) return value;
  return /^\/data\/releases\/[a-zA-Z0-9._-]+\/[a-zA-Z0-9_./-]+\.json$/.test(value) && !value.includes('..') ? value : undefined;
}

export function coverageLabel(value: unknown): string {
  if (typeof value === 'string') return value;
  if (!value || typeof value !== 'object' || Array.isArray(value)) return '';
  return Object.entries(value).filter(([, v]) => ['string', 'number', 'boolean'].includes(typeof v))
    .map(([k, v]) => `${k.replace(/_/g, ' ')}: ${typeof v === 'number' ? v.toLocaleString('en-US') : String(v)}`).join(' · ');
}

/** Match the map's location gate; records with withheld points remain searchable. */
export function mapLocationCounts(entries: FacilityIndexEntry[]) {
  const shown = entries.filter((entry) => entry.location_quality?.map_eligible !== false &&
    typeof entry.lat === 'number' && typeof entry.lon === 'number' && Number.isFinite(entry.lat) && Number.isFinite(entry.lon) &&
    Math.abs(entry.lat) <= 90 && Math.abs(entry.lon) <= 180 && !(entry.lat === 0 && entry.lon === 0)).length;
  return { shown, review: entries.length - shown };
}
