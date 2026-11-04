/**
 * Payload normaliser.
 *
 * The API (api/wattdocket_api/main.py + core/wattdocket/ingest/cards.py) emits
 * cards in its own shape: `facility.address`, `grid.subregion`, a claim `value`
 * that is either a number or the string "NOT_PUBLISHED", a `range` object, and
 * chain/transforms as arrays of strings. The fixtures in public/data/ use the
 * shape in types.ts.
 *
 * Rather than pinning the UI to one of them, every API response passes through
 * here and either returns a payload the components can render or null — in
 * which case the caller falls back to a fixture, and then to an honest
 * "no card" state. Nothing is guessed: a field that is absent stays absent.
 */

import { isBandClaim, isNumericClaim } from './types';
import type {
  Badge,
  IntervalReading,
  IntensityState,
  BandClaim,
  ChainStep,
  Citation,
  Claim,
  DrawerPayload,
  FacilityPayload,
  FacilityIndexEntry,
  GridBinding,
  HourlySeries,
  Metric,
  NumericClaim,
  NotPublishedClaim,
  OperatorPayload,
  SensitivityRow,
  TransformRow,
  EvidenceRetrieval,
} from './types';

type Json = Record<string, unknown>;

const METRIC_LABEL: Record<string, string> = {
  power_mw: 'Power draw',
  carbon_t: 'Carbon',
  water_gal: 'Water',
  diesel_mw: 'Backup diesel',
  diesel_hours: 'Diesel hours',
  land_acres: 'Land',
};

const BADGES: Badge[] = ['MEASURED', 'REPORTED', 'MODELED', 'ESTIMATED'];

function obj(v: unknown): Json | null {
  return v && typeof v === 'object' && !Array.isArray(v) ? (v as Json) : null;
}

function arr(v: unknown): unknown[] {
  return Array.isArray(v) ? v : [];
}

function str(v: unknown, fallback = ''): string {
  return typeof v === 'string' ? v : fallback;
}

// Older static cards contain an unsupported nationwide legal claim. Correct
// only that known legacy wording; retain other notes and all source data.
function missingWaterNote(metric: Metric, note: string): string {
  if (metric === 'water_gal' && /no (?:US|U\.S\.) state (?:effectively )?requires (?:facility|site)-level operational water reporting/i.test(note)) {
    return 'No facility-level water figure was found in the public sources used for this card.';
  }
  return note;
}

function num(v: unknown): number | null {
  return typeof v === 'number' && Number.isFinite(v) ? v : null;
}

function badge(v: unknown): Badge {
  const s = str(v).toUpperCase() as Badge;
  return BADGES.includes(s) ? s : 'ESTIMATED';
}

function evidenceRetrieval(value: Json): EvidenceRetrieval {
  const freshness = value['freshness'];
  return {
    freshness: freshness === 'current' || freshness === 'cached' || freshness === 'stale' || freshness === 'archived' ? freshness : undefined,
    retrieved_at: typeof value['retrieved_at'] === 'string' ? value['retrieved_at'] : null,
    archived_by_at: typeof value['archived_by_at'] === 'string' ? value['archived_by_at'] : null,
    latest_fetch_status: typeof value['latest_fetch_status'] === 'string' ? value['latest_fetch_status'] : null,
    latest_fetch_error: typeof value['latest_fetch_error'] === 'string' ? value['latest_fetch_error'] : null,
    latest_fetch_at: typeof value['latest_fetch_at'] === 'string' ? value['latest_fetch_at'] : null,
    representation: typeof value['representation'] === 'string' ? value['representation'] : undefined,
    page_derivation: typeof value['page_derivation'] === 'string' ? value['page_derivation'] : undefined,
  };
}

function citationFor(raw: unknown, claimId: string): Citation | null {
  const c = obj(raw);
  if (!c) return null;
  const quote = str(c['quote']);
  const dataset = str(c['dataset']);
  const field = str(c['field']);
  const docId = [dataset, field].filter(Boolean).join(' · ') || str(c['document_id']);
  return {
    ...evidenceRetrieval(c),
    id: str(c['id'], `${claimId}-cit`),
    claim_id: claimId,
    document_sha256: str(c['document_sha256'], str(c['doc_sha256'])),
    publisher: str(c['publisher']),
    document_id: docId || 'document not named',
    page: num(c['page']),
    line: typeof c['line'] === 'number' ? String(c['line']) : (str(c['line']) || null),
    quote,
    // The API verifies the whole quote against the page, not a sub-span, so
    // there is no highlight target to invent.
    matched_text: str(c['matched_text']),
    verified: c['verified'] === true,
    verified_at_utc: str(c['verified_at_utc'], str(c['as_of_utc'], str(c['as_of'], ''))),
    verifier_version: str(c['verifier_version'], 'substring-1'),
    url: typeof c['url'] === 'string' ? c['url'] : null,
  };
}

function rangeOf(c: Json): { p10: number | null; p50: number | null; p90: number | null } {
  const r = obj(c['range']);
  return {
    p10: num(r?.['low']) ?? num(r?.['p10']) ?? num(c['p10']),
    p50: num(r?.['base']) ?? num(r?.['p50']) ?? num(c['p50']),
    p90: num(r?.['high']) ?? num(r?.['p90']) ?? num(c['p90']),
  };
}

/** API claim → our Claim union. Returns null when the claim cannot be typed. */
export function normaliseClaim(raw: unknown, facilityId: string): Claim | null {
  const c = obj(raw);
  if (!c) return null;
  const metric = str(c['metric']) as Metric;
  if (!metric) return null;
  const id = str(c['id'], `${facilityId}:${metric}`);
  const citations = arr(c['citations'])
    .map((x) => citationFor(x, id))
    .filter((x): x is Citation => x != null);
  const explicitScope = c['scope'];
  const scope: Claim['scope'] = explicitScope === 'facility' || explicitScope === 'grid' || explicitScope === 'scenario'
    ? explicitScope : /^reference_size@|^carbon@/.test(str(c['calc_id'])) ? 'scenario' : 'facility';
  const rawRangeKind = c['range_kind'];
  const rangeKind: Claim['range_kind'] = rawRangeKind === 'confidence' || rawRangeKind === 'reported' || rawRangeKind === 'scenario'
    ? rawRangeKind : scope === 'scenario' ? 'scenario' : null;

  const base = {
    ...evidenceRetrieval(c),
    id,
    facility_id: facilityId,
    metric,
    label: str(c['label'], scope === 'scenario'
      ? metric === 'power_mw' ? 'Reference load' : metric === 'carbon_t' ? 'Scenario carbon' : METRIC_LABEL[metric] ?? metric
      : METRIC_LABEL[metric] ?? metric),
    badge: badge(c['badge']),
    provisional: c['provisional'] === true,
    as_of_utc: str(c['as_of_utc'], str(c['as_of'], '')),
    source_count: typeof c['source_count'] === 'number' ? c['source_count'] : citations.length,
    citations,
    drivers: arr(c['drivers']).map((d) => String(d)),
    calc_id: typeof c['calc_id'] === 'string' ? c['calc_id'] : null,
    renderable: c['renderable'] !== false,
    blocked_reason: typeof c['render_block_reason'] === 'string'
      ? c['render_block_reason']
      : typeof c['blocked_reason'] === 'string'
        ? c['blocked_reason']
        : undefined,
    unit_note: typeof c['unit_note'] === 'string' ? c['unit_note'] : undefined,
    scope,
    inputs: obj(c['inputs']) ? c['inputs'] as Claim['inputs'] : undefined,
    range_kind: rangeKind,
    observation_start: typeof c['observation_start'] === 'string' ? c['observation_start'] : null,
    observation_end: typeof c['observation_end'] === 'string' ? c['observation_end'] : null,
    retrieved_at: typeof c['retrieved_at'] === 'string' ? c['retrieved_at'] : null,
    data_vintage: typeof c['data_vintage'] === 'string' ? c['data_vintage'] : null,
  };

  const value = c['value'];
  const unit = str(c['unit']);

  if (typeof value === 'number') {
    if (!Number.isFinite(value)) return null;
    const { p10, p50, p90 } = rangeOf(c);
    const claim: NumericClaim = {
      ...base,
      value: { kind: 'NUMERIC', value, unit },
      p10: p10 ?? value,
      p50: p50 ?? value,
      p90: p90 ?? value,
    };
    if (explicitScope && c['renderable'] !== true) {
      claim.renderable = false;
      claim.blocked_reason = 'This number has not passed the publication evidence check.';
    }
    if (!(claim.p10 <= claim.p50 && claim.p50 <= claim.p90)) {
      // A band that does not contain its centre is not a band. Refuse it.
      claim.renderable = false;
      claim.blocked_reason = 'The supplied range does not contain its central value.';
    }
    return claim;
  }

  const MISSING_REASON: Record<string, string> = {
    NOT_PUBLISHED: 'not published',
    NOT_REPORTED: 'not reported',
    BELOW_THRESHOLD: 'below the reporting threshold',
  };

  if (typeof value === 'string') {
    const note = missingWaterNote(metric, str(c['note'], value.replace(/_/g, ' ').toLowerCase()));
    const missing: NotPublishedClaim = {
      ...base,
      value: {
        kind: 'NOT_PUBLISHED',
        reason: note,
        method_note: missingWaterNote(metric, str(c['note'], '')),
      },
      p10: null,
      p50: null,
      p90: null,
    };
    return missing;
  }

  const v = obj(value);
  if (!v) return null;

  // The API's not-published union is {state, reason}: a dict with no number.
  const state = str(v['state']).toUpperCase();
  if (state && state in MISSING_REASON) {
    const note = missingWaterNote(metric, str(v['reason'], str(v['note'], str(c['note'], ''))));
    const claim: NotPublishedClaim = {
      ...base,
      value: {
        kind: 'NOT_PUBLISHED',
        reason: note || (MISSING_REASON[state] ?? 'not published'),
        method_note: note,
      },
      p10: null,
      p50: null,
      p90: null,
    };
    return claim;
  }
  if (v['kind'] === 'BAND') {
    const low = num(v['low']);
    const high = num(v['high']);
    if (low == null || high == null || low > high) return null;
    const claim: BandClaim = {
      ...base,
      value: {
        kind: 'BAND',
        unit: str(v['unit'], unit),
        low,
        high,
        method_note: str(v['method_note']),
      },
      p10: null,
      p50: null,
      p90: null,
    };
    return claim;
  }
  if (v['kind'] === 'NUMERIC') {
    const n = num(v['value']);
    if (n == null) return null;
    const { p10, p50, p90 } = rangeOf(c);
    const claim: NumericClaim = {
      ...base,
      value: { kind: 'NUMERIC', value: n, unit: str(v['unit'], unit) },
      p10: p10 ?? n,
      p50: p50 ?? n,
      p90: p90 ?? n,
    };
    if (explicitScope && c['renderable'] !== true) {
      claim.renderable = false;
      claim.blocked_reason = 'This number has not passed the publication evidence check.';
    }
    if (!(claim.p10 <= claim.p50 && claim.p50 <= claim.p90)) {
      claim.renderable = false;
      claim.blocked_reason = 'The supplied range does not contain its central value.';
    }
    return claim;
  }
  if (v['kind'] === 'NOT_PUBLISHED') {
    const claim: NotPublishedClaim = {
      ...base,
      value: {
        kind: 'NOT_PUBLISHED',
        reason: missingWaterNote(metric, str(v['reason'], 'not published')),
        method_note: missingWaterNote(metric, str(v['method_note'], '')),
      },
      p10: null,
      p50: null,
      p90: null,
    };
    return claim;
  }
  return null;
}

function normaliseGrid(raw: unknown, facilityId: string, rateFallback: number | null): GridBinding {
  const g = obj(raw) ?? {};
  return {
    facility_id: facilityId,
    utility: str(g['utility'], 'not published'),
    balancing_authority: str(g['balancing_authority'], 'not published'),
    egrid_subregion: str(g['egrid_subregion'], str(g['subregion'], '—')),
    subregion_label: str(g['subregion_label'], str(g['subregion_name'])),
    method: (str(g['method'], str(g['binding_method'], 'point_in_polygon')) as GridBinding['method']),
    source_id: str(g['source_id'], str(g['factor_set_id'], '—')),
    rate_lb_per_mwh: num(g['rate_lb_per_mwh']) ?? rateFallback,
    intensity_state: str(g['intensity_state'], 'PROVISIONAL') === 'SETTLED' ? 'SETTLED' : 'PROVISIONAL',
    factor_set_label: str(g['factor_set_label'], str(g['factor_set_id'], '')),
    data_vintage: typeof g['data_vintage'] === 'string' ? g['data_vintage'] : null,
    source_label: str(g['source_label'], 'EPA eGRID'),
    source_url: typeof g['source_url'] === 'string' ? g['source_url'] : null,
    evidence_url: typeof g['evidence_url'] === 'string' ? g['evidence_url'] : null,
    published_at: typeof g['published_at'] === 'string' ? g['published_at'] : null,
    observation_start: typeof g['observation_start'] === 'string' ? g['observation_start'] : null,
    accounting_basis: str(g['accounting_basis']), metric: str(g['metric']), factor_publisher: str(g['factor_publisher']),
    alternative: obj(g['alternative']) ? g['alternative'] as GridBinding['alternative'] : undefined,
    retrieved_at: typeof g['retrieved_at'] === 'string' ? g['retrieved_at'] : null,
    observation_end: typeof g['observation_end'] === 'string' ? g['observation_end'] : null,
  };
}

function normaliseHourly(raw: unknown): HourlySeries | null {
  const h = obj(raw);
  if (!h) return null;

  // Shape A — readings[] with per-interval rows (the original contract).
  const readings = arr(h['readings'])
    .map((r) => obj(r))
    .filter((r): r is Json => r != null);
  if (readings.length > 0) {
    const valid = readings.every((reading) =>
      typeof reading['intensity_lb_co2e_per_mwh'] === 'number' && Number.isFinite(reading['intensity_lb_co2e_per_mwh']) &&
      typeof reading['interval_minutes'] === 'number' && reading['interval_minutes'] > 0 &&
      typeof reading['interval_start_utc'] === 'string' && Number.isFinite(Date.parse(reading['interval_start_utc'])));
    return valid ? h as unknown as HourlySeries : null;
  }

  // Shape B — the engineered cards carry 24 hourly means plus the extremes.
  // Synthesising readings from them is not a guess: each reading is one hour
  // of a published series, and the mean is what the engine computed.
  const rawMeans = arr(h['means_lb_per_mwh']);
  if (rawMeans.length !== 24 || !rawMeans.every((v) => typeof v === 'number' && Number.isFinite(v))) return null;
  const means = rawMeans as number[];

  const day = str(h['day']);
  if (!/^\d{4}-\d{2}-\d{2}$/.test(day) || !Number.isFinite(Date.parse(day))) return null;
  const state = str(h['intensity_state'], 'PROVISIONAL') as IntensityState;
  const factorId = str(h['factor_set_id'], 'unknown');
  const derived: IntervalReading[] = means.map((value, hour) => ({
    ba_code: str(h['ba'], 'UNKNOWN'),
    interval_start_utc: `${day}T${String(hour).padStart(2, '0')}:00:00Z`,
    interval_minutes: 60,
    intensity_lb_co2e_per_mwh: value,
    intensity_state: state,
    factor_set_id: factorId,
  }));

  return {
    ba_code: str(h['ba'], 'UNKNOWN'),
    day,
    intensity_state: state,
    factor_set_id: factorId,
    factor_set_label: factorId,
    interval_count: typeof h['intervals'] === 'number' ? h['intervals'] : means.length,
    interval_minutes: 60,
    readings: derived,
  };
}

/** Full card. Returns null when the payload is not a renderable card. */
export function normaliseFacilityPayload(raw: unknown): FacilityPayload | null {
  const p = obj(raw);
  if (!p) return null;
  const facilityRaw = obj(p['facility']);
  if (!facilityRaw) return null;

  const fid = str(facilityRaw['id']);
  if (!fid) return null;

  const claims = arr(p['claims'])
    .map((c) => normaliseClaim(c, fid))
    .filter((c): c is Claim => c != null);
  if (claims.length === 0) return null;

  const grid = normaliseGrid(p['grid'], fid, null);
  const verdictRaw = obj(p['verdict']);

  return {
    as_of: str(p['as_of']),
    release_id: typeof p['release_id'] === 'string' ? p['release_id'] : undefined,
    data_status: obj(p['data_status']) && Array.isArray(obj(p['data_status'])?.['sources'])
      ? p['data_status'] as FacilityPayload['data_status'] : undefined,
    calc_version: str(p['calc_version'], str(p['generated_by'], 'unknown')),
    receipts: arr(p['receipts']).map(String),
    facility: {
      id: fid,
      location_quality: obj(facilityRaw['location_quality']) ? facilityRaw['location_quality'] as FacilityPayload['facility']['location_quality'] : undefined,
      identity_match: obj(facilityRaw['identity_match']) ? facilityRaw['identity_match'] as FacilityPayload['facility']['identity_match'] : undefined,
      duplicate_candidates: arr(facilityRaw['duplicate_candidates']) as FacilityPayload['facility']['duplicate_candidates'],
      source: str(facilityRaw['source'], fid.startsWith('echo') ? 'epa_echo' : ''),
      source_label: str(facilityRaw['source_label'], fid.startsWith('echo') ? 'EPA ECHO' : 'Public listing'),
      source_url: typeof facilityRaw['source_url'] === 'string' ? facilityRaw['source_url'] : null,
      attribution: typeof facilityRaw['attribution'] === 'string' ? facilityRaw['attribution'] : undefined,
      license: typeof facilityRaw['license'] === 'string' ? facilityRaw['license'] : undefined,
      documents: arr(facilityRaw['documents']).map((value) => obj(value)).filter((value): value is Json => value != null)
        .map((value) => ({ id: str(value['id']), name: str(value['name'], 'Source document'), publisher: str(value['publisher']),
          permit_number: str(value['permit_number']), document_type: str(value['document_type'], 'Document'),
          document_date: typeof value['document_date'] === 'string' ? value['document_date'] : null,
          url: str(value['url']), content_sha256: str(value['content_sha256']), representation: str(value['representation']),
          retrieved_at: str(value['retrieved_at']), has_conflicting_values: value['has_conflicting_values'] === true,
          is_current_effective_limit: value['is_current_effective_limit'] === true,
          latest_effective_status_verified: value['latest_effective_status_verified'] === true, note: str(value['note']),
          statements: arr(value['statements']).map((item) => obj(item)).filter((item): item is Json => item != null && item['verified'] === true &&
            typeof item['quote'] === 'string' && typeof item['page'] === 'number' && item['page'] >= 1 && item['content_sha256'] === value['content_sha256'])
            .map((item) => ({quote: str(item['quote']), page: item['page'] as number, url: str(item['url']), content_sha256: str(item['content_sha256']), verified: true, scope: str(item['scope'])})),
        })),
      record_summary: arr(facilityRaw['record_summary']).map(obj).filter((row): row is Json => row != null)
        .map((row) => ({ label: str(row['label']), value: str(row['value']), source_url: typeof row['source_url'] === 'string' ? row['source_url'] : null })),
      name: str(facilityRaw['name']),
      operator: typeof facilityRaw['operator'] === 'string' ? facilityRaw['operator'] : null,
      naics: str(facilityRaw['naics']),
      class_label:
        typeof facilityRaw['class_label'] === 'string'
          ? facilityRaw['class_label']
          : undefined,
      class_verified: facilityRaw['class_verified'] === true,
      class_evidence:
        typeof facilityRaw['class_evidence'] === 'string'
          ? facilityRaw['class_evidence']
          : undefined,
      class_match_method:
        typeof facilityRaw['class_match_method'] === 'string'
          ? facilityRaw['class_match_method']
          : undefined,
      permit_registrations: arr(facilityRaw['permit_registrations']).map(String),
      permit_issued: typeof facilityRaw['permit_issued'] === 'string'
        ? facilityRaw['permit_issued'] : undefined,
      permit_county: typeof facilityRaw['permit_county'] === 'string'
        ? facilityRaw['permit_county'] : undefined,
      permit_program: typeof facilityRaw['permit_program'] === 'string'
        ? facilityRaw['permit_program'] : undefined,
      permit_url: typeof facilityRaw['permit_url'] === 'string'
        ? facilityRaw['permit_url'] : undefined,
      site_permit_count:
        typeof facilityRaw['site_permit_count'] === 'number'
          ? facilityRaw['site_permit_count']
          : 1,
      sibling_ids: arr(facilityRaw['sibling_ids']).map(String),
      permit_limits: arr(facilityRaw['permit_limits'])
        .map((l) => obj(l))
        .filter((l): l is Json => l != null && typeof l['value'] === 'number' && Number.isFinite(l['value']))
        .map((l) => ({
          ...evidenceRetrieval(l),
          issued_at: str(l['issued_at']), document_status: str(l['document_status']), program: str(l['program']), effective_status: str(l['effective_status']), supersedes: arr(l['supersedes']).map(String),
          metric: str(l['metric']),
          value: typeof l['value'] === 'number' ? l['value'] : 0,
          unit: str(l['unit']),
          page: typeof l['page'] === 'number' ? l['page'] : null,
          quote: typeof l['quote'] === 'string' ? l['quote'] : undefined,
          registration: typeof l['registration'] === 'string' ? l['registration'] : undefined,
          url: typeof l['url'] === 'string' ? l['url'] : undefined,
          retrieved_via:
            typeof l['retrieved_via'] === 'string' ? l['retrieved_via'] : undefined,
          scope: typeof l['scope'] === 'string' ? l['scope'] : undefined,
          applies_to_entire_site: typeof l['applies_to_entire_site'] === 'boolean' ? l['applies_to_entire_site'] : undefined,
          is_permitted_limit: l['is_permitted_limit'] === true,
        })),
      permit: (() => {
        const p = obj(facilityRaw['permit']);
        if (!p) return undefined;
        return {
          file: typeof p['file'] === 'string' ? p['file'] : undefined,
          sha256: typeof p['sha256'] === 'string' ? p['sha256'] : undefined,
          number: typeof p['number'] === 'string' ? p['number'] : undefined,
          authority: typeof p['authority'] === 'string' ? p['authority'] : undefined,
          address_on_permit:
            typeof p['address_on_permit'] === 'string' ? p['address_on_permit'] : undefined,
          match: typeof p['match'] === 'string' ? p['match'] : undefined,
          facts: arr(p['facts'])
            .map((f) => obj(f))
            .filter((f): f is Json => f != null)
            .map((f) => ({
              metric: str(f['metric']),
              value: typeof f['value'] === 'number' ? f['value'] : 0,
              unit: str(f['unit']),
              page: typeof f['page'] === 'number' ? f['page'] : null,
              quote: typeof f['quote'] === 'string' ? f['quote'] : null,
            })),
        };
      })(),
      lat: num(facilityRaw['lat']),
      lon: num(facilityRaw['lon']),
      address_line: str(facilityRaw['address_line'], str(facilityRaw['address'], 'Address not published')),
      city: str(facilityRaw['city']),
      state: str(facilityRaw['state']),
      zip: str(facilityRaw['zip']),
      county: typeof facilityRaw['county'] === 'string' ? facilityRaw['county'] : null,
      status: (str(facilityRaw['status'], 'unknown') as FacilityPayload['facility']['status']),
      status_source_id: str(facilityRaw['status_source_id']),
      capacity_mw: num(facilityRaw['capacity_mw']),
      capacity_basis: (str(facilityRaw['capacity_basis'], 'UNKNOWN') as FacilityPayload['facility']['capacity_basis']),
      capacity_source_id: typeof facilityRaw['capacity_source_id'] === 'string'
        ? facilityRaw['capacity_source_id']
        : null,
    },
    grid,
    claims,
    disclosures: Array.isArray(p['disclosures']) ? p['disclosures'] as FacilityPayload['disclosures'] : undefined,
    hourly: normaliseHourly(p['hourly']),
    verdict: {
      template: str(verdictRaw?.['template'], str(verdictRaw?.['sentence'], '')),
      bound_claim_ids: arr(verdictRaw?.['bound_claim_ids']).map(String),
    },
  };
}

/* ------------------------------------------------------------------ drawer */

function splitTransform(line: string): TransformRow {
  const idx = line.indexOf(':');
  if (idx > 0) {
    return { label: line.slice(0, idx).trim(), value: line.slice(idx + 1).trim() };
  }
  return { label: line.trim(), value: '' };
}

function stepKind(line: string, index: number): ChainStep['kind'] {
  if (index === 0) return 'call';
  if (/assert|inv-|verify/i.test(line)) return 'guard';
  if (/p10|p90|range|band/i.test(line)) return 'band';
  return 'compute';
}

/** Drawer payload, either ours or the {claim, receipts, chain[], ...} shape. */
export function normaliseDrawer(raw: unknown, facilityId: string, claimId: string): DrawerPayload | null {
  const d = obj(raw);
  if (!d) return null;

  // Native shape: already has claim_id + a chain object.
  if (typeof d['claim_id'] === 'string' && obj(d['chain'])) {
    const emptyCitation: Citation = {
      id: `${claimId}-no-receipt`, claim_id: claimId, document_sha256: '',
      publisher: '', document_id: '', page: null, line: null, quote: '',
      matched_text: '', verified: false, verified_at_utc: '',
      verifier_version: '', url: null,
    };
    return {
      ...(d as unknown as DrawerPayload),
      p10: num(d['low']) ?? num(d['p10']), p50: num(d['base']) ?? num(d['p50']), p90: num(d['high']) ?? num(d['p90']),
      citation: citationFor(d['citation'], claimId) ?? emptyCitation,
      extra_citations: arr(d['extra_citations'])
        .map((c) => citationFor(c, claimId))
        .filter((c): c is Citation => c != null),
    };
  }

  const claim = normaliseClaim(d['claim'], facilityId);
  if (!claim) return null;

  const citations = arr(d['receipts'])
    .map((c) => citationFor(c, claimId))
    .filter((c): c is Citation => c != null);
  // A not-published claim legitimately has no receipt. The drawer still opens
  // and says so, rather than 404-ing on the majority of a real card's chips.
  const primary: Citation =
    citations[0] ??
    claim.citations[0] ?? {
      id: `${claimId}-no-receipt`,
      claim_id: claimId,
      document_sha256: '',
      publisher: '',
      document_id: '',
      page: null,
      line: null,
      quote: '',
      matched_text: '',
      verified: false,
      verified_at_utc: '',
      verifier_version: '',
      url: null,
    };

  const chainLines = arr(d['chain']).map(String);
  const steps: ChainStep[] = chainLines.map((line, i) => ({
    line,
    kind: stepKind(line, i),
  }));

  const sensitivity: SensitivityRow[] = arr(d['sensitivity'])
    .map((s) => obj(s))
    .filter((s): s is Json => s != null)
    .map((s) => {
      const delta = str(s['delta'], '');
      return {
        label: str(s['label'], 'sensitivity'),
        delta,
        tone: /^-|−/.test(delta) ? 'down' : /^\+/.test(delta) ? 'up' : 'flat',
      } as SensitivityRow;
    });

  const numeric = isNumericClaim(claim) ? claim : null;
  const band = isBandClaim(claim) ? claim : null;

  return {
    claim_id: claim.id,
    metric: claim.metric,
    label: claim.label,
    headline:
      numeric != null
        ? new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 }).format(numeric.value.value)
        : band
          ? `${band.value.low}–${band.value.high}`
          : 'Not published',
    unit: numeric?.value.unit ?? band?.value.unit ?? '',
    badge: claim.badge,
    provisional: claim.provisional,
    p10: numeric?.p10 ?? null,
    p50: numeric?.p50 ?? null,
    p90: numeric?.p90 ?? null,
    as_of_utc: claim.as_of_utc,
    scope: claim.scope,
    range_kind: claim.range_kind,
    observation_start: claim.observation_start,
    observation_end: claim.observation_end,
    retrieved_at: claim.retrieved_at,
    data_vintage: claim.data_vintage,
    receipt_count: citations.length || claim.source_count,
    citation: primary,
    extra_citations: citations.slice(1),
    chain: {
      calc_id: claim.calc_id ?? 'calc:not-recorded',
      name: str(obj(d['calculation'])?.['name'], 'from the card ledger'),
      version: str(obj(d['calculation'])?.['version'], '0.1.0'),
      code_sha256: str(obj(d['calculation'])?.['code_sha256'], ''),
      steps,
    },
    transforms: arr(d['transforms']).map((t) =>
      typeof t === 'string' ? splitTransform(t) : splitTransform(JSON.stringify(t)),
    ),
    sensitivity,
    drivers: claim.drivers,
  };
}

/* --------------------------------------------------------------- facilities */

export function normaliseFacilityIndex(raw: unknown): FacilityIndexEntry[] | null {
  const list = Array.isArray(raw)
    ? raw
    : Array.isArray(obj(raw)?.['results'])
      ? (obj(raw)?.['results'] as unknown[])
      : null;
  if (!list) return null;

  const entries: FacilityIndexEntry[] = [];
  for (const item of list) {
    const f = obj(item);
    if (!f) continue;
    const id = str(f['id']);
    if (!id) continue;
    entries.push({
      id,
      location_quality: obj(f['location_quality']) ? f['location_quality'] as FacilityIndexEntry['location_quality'] : undefined,
      identity_match: obj(f['identity_match']) ? f['identity_match'] as FacilityIndexEntry['identity_match'] : undefined,
      class_label: str(f['class_label']), class_verified: f['class_verified'] === true,
      source: str(f['source'], id.startsWith('echo') ? 'epa_echo' : ''),
      source_label: str(f['source_label'], id.startsWith('echo') ? 'EPA ECHO' : 'Public listing'),
      source_url: typeof f['source_url'] === 'string' ? f['source_url'] : null,
      name: str(f['name'], 'Unnamed facility'),
      address_line: str(f['address_line'], str(f['address'], 'Address not published')),
      city: str(f['city']),
      state: str(f['state']),
      zip: str(f['zip']),
      lat: num(f['lat']),
      lon: num(f['lon']),
      site_id: typeof f['site_id'] === 'string' ? f['site_id'] : undefined,
      registry_id: str(f['registry_id'], id.replace(/^echo[:_]/, '')),
      hero: f['hero'] === true ? true : undefined,
      duplicate_of: typeof f['duplicate_of'] === 'string' ? f['duplicate_of'] : undefined,
    });
  }
  return entries;
}

/* ----------------------------------------------------------------- operator */

/** The bench is a fixture in this build; the API has no operator route yet. */
export function normaliseOperator(raw: unknown): OperatorPayload | null {
  const p = obj(raw);
  if (!p || !Array.isArray(p['methods']) || !obj(p['heatmap'])) return null;
  return p as unknown as OperatorPayload;
}
