import type {
  DrawerPayload,
  FacilityIndexEntry,
  FacilityPayload,
  OperatorPayload,
  ResolveFallbackTable,
  ResolveResult,
  Sourced,
  DataManifest,
  DataStatus,
  GridRegionSummary,
  GridRegionSeries,
  SourceStatus,
  PipelineStatus,
  OperatorWaterData,
  FacilityObservations,
} from './types';
import { getRelease, readReleaseJson, releaseSlug } from './release';
import type { FacilityPower, PowerReadings } from './facilityPowerData';
import { facilityAtAddress, geocodeAddress, indexSubregions, nearestFacilities, subregionForPoint } from './geo';
import {
  normaliseDrawer,
  normaliseFacilityIndex,
  normaliseFacilityPayload,
  normaliseOperator,
} from './normalise';

/** All production reads belong to one immutable release. The legacy API is
 * used only by explicit development configuration or a deployment with no
 * production manifest. A failed production record never mixes in old data. */

const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? '/api';
const TIMEOUT_MS = 1800;

export class ApiError extends Error {
  readonly code: 'not_found' | 'offline';
  constructor(code: 'not_found' | 'offline', message: string) {
    super(message);
    this.code = code;
    this.name = 'ApiError';
  }
}

async function getJson<T>(url: string): Promise<T | null> {
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), TIMEOUT_MS);
  try {
    const res = await fetch(url, {
      signal: controller.signal,
      headers: { accept: 'application/json' },
    });
    if (!res.ok) return null;
    const type = res.headers.get('content-type') ?? '';
    if (!type.includes('json')) return null;
    return (await res.json()) as T;
  } catch {
    return null;
  } finally {
    window.clearTimeout(timer);
  }
}

const bundles = new Map<string, Promise<Record<string, unknown>>>();

async function productionRecord(release: DataManifest, path: string): Promise<unknown> {
  const [kind, key, claimFile] = path.split('/');
  const bundlePath = kind === 'card' ? release.cards_path
    : kind === 'operator' ? release.operators_path : kind === 'drawer' ? release.drawers_path : undefined;
  if (!bundlePath) return readReleaseJson(release, path);
  let pending = bundles.get(bundlePath);
  if (!pending) {
    pending = readReleaseJson<Record<string, unknown>>(release, bundlePath.slice(release.base_path.length + 1));
    bundles.set(bundlePath, pending);
  }
  const bundle = await pending;
  const recordKey = (key ?? '').replace(/\.json$/, '');
  const record = bundle[recordKey];
  const value = kind === 'drawer' && record && typeof record === 'object'
    ? (record as Record<string, unknown>)[(claimFile ?? '').replace(/\.json$/, '')]
    : record;
  if (value == null) throw new ApiError('not_found', 'This record is not available in the selected data release.');
  if (typeof value === 'object' && 'release_id' in value && value.release_id !== release.release_id) {
    throw new ApiError('offline', 'This record does not belong to the selected data release.');
  }
  return value;
}

async function load<T>(
  apiPath: string,
  productionPath: string,
  normalise: (raw: unknown) => T | null,
): Promise<Sourced<T>> {
  const release = await getRelease();
  const live = release ? await productionRecord(release, productionPath)
    : await getJson<unknown>(`${API_BASE}${apiPath}`);
  if (live) {
    const normalised = normalise(live);
    if (normalised) return { data: normalised, source: 'api', releaseId: release?.release_id };
  }

  // No fixture fallback. The previous version fell back to committed JSON files
  // after 1.8 s, and those files contained a fabricated "Virginia DEQ" citation
  // with a hand-typed sha256 and water figures for real facilities. A fallback
  // that invents evidence is worse than an error: it renders a confident card
  // built from nothing, and the reader cannot tell.
  throw new ApiError(
    'offline',
    release ? 'The selected release did not provide a usable record.' : `No usable response from ${API_BASE}${apiPath}.`,
  );
}

export function getFacilityPayload(facilityId: string): Promise<Sourced<FacilityPayload>> {
  return load<FacilityPayload>(
    `/v1/facility/${encodeURIComponent(facilityId)}`,
    `card/${releaseSlug(facilityId)}.json`,
    normaliseFacilityPayload,
  );
}

export function getDrawer(
  facilityId: string,
  claimId: string,
): Promise<Sourced<DrawerPayload>> {
  return load<DrawerPayload>(
    `/v1/facility/${encodeURIComponent(facilityId)}/drawer/${encodeURIComponent(claimId)}`,
    `drawer/${releaseSlug(facilityId)}/${releaseSlug(claimId)}.json`,
    (raw) => normaliseDrawer(raw, facilityId, claimId),
  );
}

export function getOperatorPayload(facilityId: string): Promise<Sourced<OperatorPayload>> {
  return load<OperatorPayload>(
    `/v1/operator/scenario?facility_id=${encodeURIComponent(facilityId)}`,
    `operator/${releaseSlug(facilityId)}.json`,
    normaliseOperator,
  );
}

export async function getFacilityIndex(): Promise<Sourced<FacilityIndexEntry[]>> {
  const release = await getRelease();
  const raw = release ? await readReleaseJson<unknown>(release, 'facilities.json')
    : await getJson<unknown>(`${API_BASE}/v1/facilities`);
  const entries = raw ? normaliseFacilityIndex(raw) : null;
  if (!entries) throw new ApiError('offline', 'The facility index is unavailable.');
  const metadata = raw && typeof raw === 'object' && !Array.isArray(raw)
    ? raw as Record<string, unknown> : {};
  return {
    data: entries,
    source: 'api',
    truncated: metadata.truncated === true,
    indexAsOf: typeof metadata.as_of === 'string' ? metadata.as_of : null,
    releaseId: release?.release_id,
    sourceStatus: Array.isArray(metadata.source_status) ? metadata.source_status as SourceStatus[] : undefined,
  };
}

/** Current collector health is operational metadata, separate from the pinned
 * facility and grid facts. A failed health request does not replace a release. */
export async function getPipelineStatus(): Promise<PipelineStatus | null> {
  if (!await getRelease()) return null;
  const response = await fetch('/data/pipeline-status.json', { cache: 'no-store', headers: { accept: 'application/json' }, signal: AbortSignal.timeout(10000) });
  if (response.status === 404) return null;
  if (!response.ok || !(response.headers.get('content-type') ?? '').includes('json')) throw new Error('The latest collection status could not be reached.');
  const raw = await response.json() as PipelineStatus;
  if (typeof raw.status !== 'string') throw new Error('The latest collection status is invalid.');
  return raw;
}

export async function getDataStatus(): Promise<DataStatus> {
  const release = await getRelease();
  if (!release) throw new ApiError('not_found', 'Source status is unavailable for this legacy snapshot.');
  const status = await readReleaseJson<DataStatus>(release, 'data-status.json');
  if (!status || !Array.isArray(status.sources)) throw new ApiError('offline', 'The source status report could not be read.');
  return status;
}

/** Optional evidence remains release-pinned and is never requested from older releases. */
export async function getOperatorWater(): Promise<OperatorWaterData | null> {
  const release = await getRelease();
  if (!release || !Object.prototype.hasOwnProperty.call(release.objects ?? {}, 'operator-water.json')) return null;
  const data = await readReleaseJson<OperatorWaterData>(release, 'operator-water.json');
  if (!data || !Array.isArray(data.observations) || !Array.isArray(data.aggregates) || !data.source) throw new ApiError('offline', 'Operator location water evidence could not be read from this release.');
  return data;
}

export async function getGridIndex(): Promise<{ release_id: string; as_of: string; regions: GridRegionSummary[] }> {
  const release = await getRelease();
  if (!release) throw new ApiError('not_found', 'The regional grid feed is unavailable for this legacy snapshot.');
  const value = await readReleaseJson<{ release_id: string; as_of: string; regions: GridRegionSummary[] }>(release, 'grid/index.json');
  if (!value || !Array.isArray(value.regions)) throw new ApiError('offline', 'The grid region index could not be read.');
  return value;
}

export async function getGridRegion(code: string): Promise<GridRegionSeries> {
  const release = await getRelease();
  if (!release) throw new ApiError('not_found', 'The regional grid feed is unavailable for this legacy snapshot.');
  const value = await readReleaseJson<GridRegionSeries>(release, `grid/${releaseSlug(code)}.json`);
  if (!value || !Array.isArray(value.readings)) throw new ApiError('offline', 'The grid observations could not be read.');
  return value;
}

/* ------------------------------------------------------------------ resolve */

/**
 * SPEC §6 `GET /v1/resolve`, done in the browser for addresses the index does
 * not hold. There is no server route: the geocoder is the Census Bureau's, the
 * polygons and annual regional factor tables are static files, and the lookup is
 * deterministic. Both files load lazily, only when someone asks for an address
 * that missed the index — the polygons are 4 MB.
 */
let fallbackTable: Promise<ResolveFallbackTable> | null = null;
let subregionIndex: Promise<ReturnType<typeof indexSubregions>> | null = null;

async function staticJson<T>(path: string): Promise<T> {
  const res = await fetch(path, { headers: { accept: 'application/json' } });
  if (!res.ok) throw new ApiError('offline', `${path} answered ${res.status}.`);
  return (await res.json()) as T;
}

function loadFallbackTable(): Promise<ResolveFallbackTable> {
  fallbackTable ??= getRelease().then((release) => release
    ? readReleaseJson<ResolveFallbackTable>(release, 'resolve-fallback.json').then((table) => ({ ...table, release_id:release.release_id, built_at:release.built_at }))
    : staticJson<ResolveFallbackTable>('./data/resolve-fallback.json')).catch((e) => {
    fallbackTable = null;
    throw e;
  });
  return fallbackTable;
}

function loadSubregionIndex() {
  subregionIndex ??= getRelease().then((release) => release
    ? readReleaseJson<unknown>(release, 'egrid-subregions.json')
    : staticJson<unknown>('./data/egrid-subregions.json'))
    .then(indexSubregions)
    .catch((e) => {
      subregionIndex = null;
      throw e;
    });
  return subregionIndex;
}

export async function resolveAddress(
  query: string,
  entries: FacilityIndexEntry[],
): Promise<ResolveResult> {
  // Start the static downloads while the geocoder thinks.
  // Observe every promise immediately: if a static download fails before the
  // geocoder returns, delaying Promise.all can produce an unhandled rejection.
  const [geocode, table, index] = await Promise.all([
    geocodeAddress(query), loadFallbackTable(), loadSubregionIndex(),
  ]);

  const subregion = subregionForPoint(index, geocode.lat, geocode.lon);
  const binding: ResolveResult['binding'] = 'point_in_polygon';
  const reference = subregion ? table.subregions[subregion] : undefined;
  if (!subregion || !reference) {
    throw new ApiError(
      'not_found',
      `A unique grid region could not be established for ${geocode.matched_address}. It may be near a boundary, in an overlapping area, or outside the available coverage. A reference scenario is withheld.`,
    );
  }

  return {
    query,
    geocode,
    subregion,
    binding,
    reference,
    table,
    nearest: nearestFacilities(entries, geocode.lat, geocode.lon),
    at_address: facilityAtAddress(entries, geocode),
  };
}

/* ------------------------------------------------------------------ explain */

/** The endpoint resolves facts from the same immutable release being displayed.
 * Only the release and subject identity leave the client. Calculator inputs,
 * filtered tables, chart windows and local fallback text are never model facts.
 */
const EXPLAIN_URL =
  (import.meta.env.VITE_EXPLAIN_URL as string | undefined) ??
  '';

export interface ExplainSubject {
  kind: 'facility' | 'disclosure_location' | 'power_site' | 'permit_document';
  id: string;
}

export interface ExplainRequest { release_id: string; subject: ExplainSubject; }
export interface ExplainCitation {
  fact_id: string; label: string; url: string; locator: string; sha256: string;
}
export interface ExplainFact {
  id: string; text: string; scope: string; required_qualifiers?: string[];
}
export interface ExplainResult {
  text: string;
  source: 'model' | 'template';
  degraded: boolean;
  reason?: string;
  release_id?: string;
  subject?: ExplainSubject;
  citations?: ExplainCitation[];
  qualifiers?: string[];
  fact_ids?: string[];
  cited_facts?: ExplainFact[];
  paragraphs?: {text: string; fact_ids: string[]}[];
  validation?: { level: 'numerals_citations_and_qualifiers' | 'trusted_fallback'; label: string };
}

/** Whether this build can ask Muse Spark for a plain-language explanation. */
export function explainAvailable(): boolean {
  return Boolean(EXPLAIN_URL);
}

export async function getExplain(
  input: FacilityPayload | ExplainRequest,
  template: string,
  options: {signal?: AbortSignal} = {},
): Promise<ExplainResult> {
  const request: ExplainRequest = 'subject' in input ? input : {
    release_id: input.release_id ?? '', subject: {kind: 'facility', id: input.facility.id},
  };
  const fallback = (reason: string): ExplainResult => ({text: template, source: 'template', degraded: true,
    reason, release_id: request.release_id, subject: request.subject, citations: [], qualifiers: [], fact_ids: []});
  if (!/^[a-zA-Z0-9._-]+$/.test(request.release_id) || !request.subject.id ||
      !['facility', 'disclosure_location', 'power_site', 'permit_document'].includes(request.subject.kind)) return fallback('RELEASE_UNAVAILABLE');
  if (options.signal?.aborted) return fallback('CANCELLED');
  if (!EXPLAIN_URL) return fallback('MODEL_UNCONFIGURED');
  const controller = new AbortController();
  const timer = window.setTimeout(() => controller.abort(), 40000);
  const cancel = () => controller.abort();
  options.signal?.addEventListener('abort', cancel, {once: true});
  try {
    const res = await fetch(EXPLAIN_URL, {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({release_id: request.release_id, subject: {kind: request.subject.kind, id: request.subject.id}}),
      signal: controller.signal,
    });
    if (!res.ok) {
      let reason = `HTTP ${res.status}`;
      try {
        const errorBody = (await res.json()) as { reason?: unknown };
        if (typeof errorBody?.reason === 'string') reason = errorBody.reason;
      } catch {
        // Keep the status-based reason when the error body is not JSON.
      }
      return fallback(reason);
    }
    const data = (await res.json()) as ExplainResult;
    if (data?.release_id !== request.release_id || data.subject?.kind !== request.subject.kind || data.subject?.id !== request.subject.id) {
      return fallback('EVIDENCE_MISMATCH');
    }
    const safeCitation = (citation: ExplainCitation) => {
      try {
        const url = new URL(citation.url);
        return url.protocol === 'https:' && !url.username && !url.password &&
          typeof citation.fact_id === 'string' && typeof citation.label === 'string' &&
          typeof citation.locator === 'string' && /^[a-f0-9]{64}$/.test(citation.sha256);
      } catch { return false; }
    };
    if (typeof data.text !== 'string' || !data.text.trim() || !['model', 'template'].includes(data.source) ||
        typeof data.degraded !== 'boolean' || !Array.isArray(data.citations) || !data.citations.every(safeCitation) ||
        !Array.isArray(data.qualifiers) || !data.qualifiers.every(value => typeof value === 'string') ||
        !Array.isArray(data.fact_ids) || !data.fact_ids.every(value => typeof value === 'string') ||
        (data.source === 'model' && (data.validation?.level !== 'numerals_citations_and_qualifiers' || !data.fact_ids.length ||
          !Array.isArray(data.cited_facts) || !data.cited_facts.length ||
          !data.cited_facts.every(fact => fact && typeof fact.id === 'string' && typeof fact.text === 'string' && data.fact_ids!.includes(fact.id)) ||
          !data.fact_ids.every(id => data.cited_facts!.some(fact => fact.id === id)) ||
          !data.citations.every(citation => data.fact_ids!.includes(citation.fact_id))))) return fallback('INVALID_RESPONSE');
    return data;
  } catch {
    return fallback(options.signal?.aborted ? 'CANCELLED' : 'unreachable');
  } finally {
    window.clearTimeout(timer);
    options.signal?.removeEventListener('abort', cancel);
  }
}

export async function getFacilityObservations(): Promise<FacilityObservations | null> {
  const release = await getRelease();
  if (!release?.objects?.['facility-observations.json']) return null;
  return readReleaseJson<FacilityObservations>(release, 'facility-observations.json');
}

export async function getFacilityPower(): Promise<FacilityPower | null> {
  const release = await getRelease();
  return release?.objects?.['facility-power.json'] ? readReleaseJson<FacilityPower>(release, 'facility-power.json') : null;
}

export async function getPowerReadings(path: string): Promise<PowerReadings> {
  const release = await getRelease();
  if (!release?.objects?.[path] || !/^power\/[a-z0-9_-]+\.json$/.test(path)) throw new Error('Meter series is unavailable in this release.');
  return readReleaseJson<PowerReadings>(release, path);
}
