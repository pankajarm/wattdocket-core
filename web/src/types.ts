/**
 * Wire types — these mirror SPEC.md §2 exactly, field for field, in snake_case.
 * The fixtures in public/data/ have this shape, and so does the real API, so
 * nothing has to be remapped between the two.
 */

export type Metric =
  | 'power_mw'
  | 'carbon_t'
  | 'water_gal'
  | 'diesel_mw'
  | 'diesel_hours'
  | 'land_acres';

/** Closed set of four. PROVISIONAL is an orthogonal flag, not a fifth badge. */
export type Badge = 'MEASURED' | 'REPORTED' | 'MODELED' | 'ESTIMATED';

export type CapacityBasis = 'IT_MW' | 'GRID_MW' | 'UNKNOWN';

export type FacilityStatus =
  | 'operating'
  | 'under_construction'
  | 'expanding'
  | 'proposed'
  | 'permitted'
  | 'unknown';

export type IntensityState = 'PROVISIONAL' | 'SETTLED';

export type GridMethod = 'point_in_polygon' | 'operator_filing' | 'manual';

export interface SourceStatus {
  id: string;
  label: string;
  url: string;
  retrieved_at: string | null;
  observation_start: string | null;
  observation_end: string | null;
  published_at: string | null;
  data_vintage?: string | null;
  status: 'current' | 'stale' | 'failed' | 'not_configured';
  stale_after_seconds: number | null;
  record_count: number;
  coverage?: string | Record<string, unknown> | null;
  note: string | null;
  last_attempt_at?: string | null;
  last_success_at?: string | null;
  error?: string | null;
  partial?: boolean;
  documents_failed?: number;
}

export interface PipelineStatus {
  status: string;
  started_at?: string;
  completed_at?: string;
  promoted?: boolean;
  release_id?: string;
  last_published_release?: string | null;
  sources?: SourceStatus[];
  error?: string;
}

export interface DataStatus {
  release_id: string;
  built_at: string;
  sources: SourceStatus[];
  coverage?: Record<string, unknown>;
  pipeline?: Record<string, unknown>;
  quality?: Record<string, unknown>;
  permit_recovery?: {documents: RecoveredPermitDocument[]};
}

export interface LocationQuality {
  status: 'physical' | 'suspect' | 'unknown';
  address_type: 'physical' | 'mailing' | 'unknown';
  reasons: string[];
  map_eligible: boolean;
}

export interface IdentityMatch { status: string; method: string | null; note?: string; matched_source_ids?: string[]; }
export interface ScenarioBounds { low: number; base: number; high: number; }
export interface ScenarioInputs {
  load_mw: number;
  capacity_basis: 'GRID_MW';
  utilization: ScenarioBounds;
  hours_per_year: number;
  factor_year?: string | number | null;
  input_source?: 'illustrative_preset' | 'user_input';
  facility_measurement?: false;
}

export interface DataManifest {
  schema_version: '2.0' | '2.1';
  release_id: string;
  built_at: string;
  base_path: string;
  cards_path?: string;
  operators_path?: string;
  drawers_path?: string;
  objects?: Record<string, unknown>;
}

export interface OperatorWaterObservation {
  location_label: string;
  country?: string;
  operator: string;
  data_year: number;
  withdrawal_million_gallons: number | null;
  discharge_million_gallons: number | null;
  consumption_million_gallons: number | null;
  unit: string;
  measurement_basis: string;
  facility_binding_verified: false;
  source_url: string;
  source_page: number;
  reporting_scope?: string;
}

export interface OperatorWaterData {
  release_id: string;
  built_at: string;
  retrieved_at: string;
  observations: OperatorWaterObservation[];
  aggregates: OperatorWaterObservation[];
  source: { publisher: string; report: string; pdf_url: string; source_page: number; data_year: number; published_at: string | null; publication_month: string; methodology_note: string; geography_note: string; pdf_sha256: string; };
}

export interface GridRegionSummary {
  code: string;
  name: string;
  latest_utc: string | null;
  source_id: string;
  readings_count: number;
  unit: string;
  latest_demand_mw?: number | null;
}

export interface GridRegionSeries {
  code: string;
  name: string;
  source_id: string;
  observation_start: string | null;
  observation_end: string | null;
  retrieved_at: string | null;
  unit: string;
  five_minute?: GridRegionSeries;
  readings: {
    interval_start_utc: string;
    interval_minutes: number;
    demand_mw: number | null;
    generation_mw?: number | null;
    fuel_mix?: Record<string, number>;
    storage_mw?: number | null;
  }[];
}

export interface EvidenceRetrieval {
  freshness?: 'current' | 'cached' | 'stale' | 'archived';
  retrieved_at?: string | null;
  archived_by_at?: string | null;
  latest_fetch_status?: string | null;
  latest_fetch_error?: string | null;
  latest_fetch_at?: string | null;
  representation?: string;
  page_derivation?: string;
}

export interface PermitLimit extends EvidenceRetrieval {
  metric: string;
  value: number;
  unit: string;
  page?: number | null;
  quote?: string;
  registration?: string;
  url?: string;
  retrieved_via?: string;
  is_permitted_limit?: boolean;
  scope?: string;
  applies_to_entire_site?: boolean;
  issued_at?: string | null;
  document_status?: string;
  program?: string;
  effective_status?: string;
  supersedes?: string[];
  recovery_review?: PermitRecoveryReview;
  reviewed_condition_scope?: string;
  reviewed_operating_purpose?: string;
  reviewed_period?: string;
}

export interface PermitPageEvidence {
  value?: string;
  pdf_page?: number | null;
  quote?: string;
}

export interface PermitReviewEvidence {
  url: string;
  sha256?: string;
  locator?: string;
  quote?: string;
  pdf_page?: number | null;
  receipt?: {retrieved_at?: string | null};
}

export interface PermitHistoryReview {
  review_version: string;
  status: 'verified' | 'review_required' | 'unavailable';
  reviewed_at?: string;
  checked_at?: string;
  registry_listing?: {
    status: 'latest_listed_for_registration' | 'listed_with_later_document' | 'not_listed' | 'unknown';
    registry_as_of?: string;
    checked_at?: string;
    current_rows?: {registration: string; url: string; issued_at?: string; program?: string}[];
  };
  legal_effectiveness?: {
    status: 'source_confirmed_current' | 'superseded' | 'unknown';
    scope?: 'entire_document' | 'specified_conditions' | 'not_established';
    condition_refs?: string[];
    note?: string;
    evidence?: PermitReviewEvidence[];
  };
  events?: {type: string; scope: string; evidence?: PermitReviewEvidence[]}[];
  note?: string;
}

export interface PermitSiteIdentityReview {
  review_version: string;
  status: 'regulator_site_verified' | 'candidate' | 'unresolved' | 'review_required';
  regulator_site_id?: string;
  registration_root?: string;
  source_freshness?: string;
  retrieved_at?: string;
  checked_at?: string;
  physical_address?: {street?: string; city?: string; state?: string; postal_code?: string};
  site_status?: {status_group?: string; air_operating_status?: string; note?: string};
  evidence?: PermitReviewEvidence[];
  inventory_links?: {record_id: string; relationship: 'same_regulator_record' | 'within_permit_site' | 'candidate'; verification_status?: string;
    combined_address_conflict?: {consolidated_frs_address?: string; air_program_address?: string; deq_physical_address?: string; resolution?: string} | null}[];
  note?: string;
}

export interface PermitRecoveryReview {
  review_version: string;
  reviewed_at?: string;
  registration?: string;
  publisher_url?: string;
  document_sha256?: string;
  document_extent: 'full_permit' | 'partial_amendment' | 'correspondence' | 'permit_letter' | 'unknown';
  status?: string;
  identity_review?: {registration?: PermitPageEvidence; county?: PermitPageEvidence; physical_address?: PermitPageEvidence};
  supersession_review?: {
    status?: string;
    current_legal_effectiveness: 'unknown';
    statements: {pdf_page?: number | null; quote: string; replaced_dates: string[]; scope?: string}[];
  };
}

export interface PermitDocumentReview extends EvidenceRetrieval {
  registration: string;
  county_fips?: string;
  issued_at?: string | null;
  url: string;
  content_sha256?: string;
  parse_status?: string;
  recovery_review: PermitRecoveryReview;
  permit_history_review?: PermitHistoryReview;
  site_identity_review?: PermitSiteIdentityReview;
}

export interface RecoveredPermitDocument extends PermitDocumentReview {
  site: string;
  county?: string;
  inventory_record_ids?: string[];
  conditions?: PermitLimit[];
  equipment_claims?: unknown[];
}

export interface Facility {
  id: string;
  source?: string;
  source_label?: string;
  source_url?: string | null;
  attribution?: string;
  license?: string;
  documents?: FacilityDocument[];
  record_summary?: { label: string; value: string; source_url?: string | null }[];
  location_quality?: LocationQuality;
  identity_match?: IdentityMatch;
  duplicate_candidates?: {id?: string; group_id?: string; basis: string; record_ids: string[]; merge_verified: boolean; note?: string}[];
  name: string;
  operator: string | null;
  naics: string;
  /** What the source says about the facility type — not an assertion that it *is* a data center. */
  class_label?: string;
  /** false when the type rests on a NAICS code alone. */
  class_verified?: boolean;
  class_evidence?: string;
  /** Set only when a state regulator's own registry names this site. */
  class_match_method?: string;
  permit_registrations?: string[];
  permit_issued?: string;
  permit_county?: string;
  permit_program?: string;
  permit_url?: string;
  /** Conditions quoted from documents; current legal effectiveness may be unknown. */
  permit_limits?: PermitLimit[];
  permit_reviews?: PermitDocumentReview[];
  /** How many permitted air sources EPA lists at this same site. */
  site_permit_count?: number;
  sibling_ids?: string[];
  /** Facts read out of a state air permit, each with a derived page number. */
  permit?: {
    file?: string;
    sha256?: string;
    number?: string;
    authority?: string;
    address_on_permit?: string;
    facts?: { metric: string; value: number; unit: string; page?: number | null; quote?: string | null }[];
    match?: string;
  };
  lat: number | null;
  lon: number | null;
  address_line: string;
  city: string;
  state: string;
  zip: string;
  county: string | null;
  status: FacilityStatus;
  status_source_id: string;
  capacity_mw: number | null;
  capacity_basis: CapacityBasis;
  capacity_source_id: string | null;
}

export interface FacilityDocument {
  id: string;
  name: string;
  publisher: string;
  permit_number?: string;
  document_type: string;
  document_date?: string | null;
  url: string;
  content_sha256: string;
  representation: string;
  retrieved_at: string;
  statements: { quote: string; page: number; url: string; content_sha256: string; verified: boolean; scope: string }[];
  has_conflicting_values: boolean;
  is_current_effective_limit: boolean;
  latest_effective_status_verified: boolean;
  note: string;
}

export interface GridBinding {
  facility_id: string;
  utility: string;
  balancing_authority: string;
  egrid_subregion: string;
  subregion_label: string;
  method: GridMethod;
  source_id: string;
  /** The published annual subregion rate the claim actually used, lb CO2e/MWh. */
  rate_lb_per_mwh: number | null;
  intensity_state: IntensityState;
  factor_set_label: string;
  data_vintage?: string | null;
  source_label?: string;
  source_url?: string | null;
  retrieved_at?: string | null;
  observation_end?: string | null;
  observation_start?: string | null;
  published_at?: string | null;
  evidence_url?: string | null;
  accounting_basis?: string;
  metric?: string;
  factor_publisher?: string;
  alternative?: { data_year: string | number; rate_lb_per_mwh: number; source_label: string; source_url?: string; evidence_url?: string; methodology?: string; [key: string]: unknown };
}

/** Grid-side interval, never facility-side. Minutes are explicit per INV-3. */
export interface IntervalReading {
  ba_code: string;
  interval_start_utc: string;
  interval_minutes: number;
  intensity_lb_co2e_per_mwh: number;
  intensity_state: IntensityState;
  factor_set_id: string;
}

export interface Citation extends EvidenceRetrieval {
  id: string;
  claim_id: string;
  document_sha256: string;
  publisher: string;
  document_id: string;
  page: number | null;
  line: string | null;
  /** Verbatim text from the document. Never paraphrased. */
  quote: string;
  /** The exact substring inside `quote` that the parser extracted. */
  matched_text: string;
  verified: boolean;
  verified_at_utc: string;
  verifier_version: string;
  url: string | null;
}

/**
 * The value slot of a claim — a discriminated union on purpose.
 *
 * A NOT_PUBLISHED or BAND value has no numeric field at all, so `claim.value.value`
 * does not typecheck until `kind` is narrowed. TypeScript therefore refuses to
 * render a missing number as a number. See `isRenderableNumber` below.
 */
export type NumericValue = {
  kind: 'NUMERIC';
  value: number;
  unit: string;
};

/** INV-9: water is never a scalar. A band is a range, not a point. */
export type BandValue = {
  kind: 'BAND';
  unit: string;
  low: number;
  high: number;
  method_note: string;
};

export type NotPublishedValue = {
  kind: 'NOT_PUBLISHED';
  reason: string;
  method_note: string;
};

export type ClaimValue = NumericValue | BandValue | NotPublishedValue;

interface ClaimBase extends EvidenceRetrieval {
  id: string;
  facility_id: string;
  metric: Metric;
  label: string;
  badge: Badge;
  provisional: boolean;
  as_of_utc: string;
  source_count: number;
  citations: Citation[];
  /** Ordered list of the assumptions that produce most of the band width. */
  drivers: string[];
  calc_id: string | null;
  /** INV-7: false when no verified citation and no resolvable calc. */
  renderable: boolean;
  blocked_reason?: string;
  unit_note?: string;
  scope?: 'facility' | 'grid' | 'scenario';
  range_kind?: 'scenario' | 'confidence' | 'reported' | null;
  observation_start?: string | null;
  observation_end?: string | null;
  retrieved_at?: string | null;
  data_vintage?: string | null;
  inputs?: ScenarioInputs;
}

export interface NumericClaim extends ClaimBase {
  value: NumericValue;
  p10: number;
  p50: number;
  p90: number;
}

export interface BandClaim extends ClaimBase {
  value: BandValue;
  p10: null;
  p50: null;
  p90: null;
}

export interface NotPublishedClaim extends ClaimBase {
  value: NotPublishedValue;
  p10: null;
  p50: null;
  p90: null;
}

export type Claim = NumericClaim | BandClaim | NotPublishedClaim;

/** Narrowing guard: only a numeric, verified claim may render a number. */
export function isRenderableNumber(
  claim: Claim,
): claim is NumericClaim & { renderable: true } {
  return claim.renderable === true && claim.value.kind === 'NUMERIC';
}

/**
 * Nested discriminants narrow `claim.value`, not `claim` itself, so the parent
 * object needs an explicit guard before its p10/p50/p90 can be read as numbers.
 */
export function isNumericClaim(claim: Claim): claim is NumericClaim {
  return claim.value.kind === 'NUMERIC';
}

export function isBandClaim(claim: Claim): claim is BandClaim {
  return claim.value.kind === 'BAND';
}

export function isNotPublishedClaim(claim: Claim): claim is NotPublishedClaim {
  return claim.value.kind === 'NOT_PUBLISHED';
}

export interface HourlySeries {
  ba_code: string;
  day: string;
  intensity_state: IntensityState;
  factor_set_id: string;
  factor_set_label: string;
  interval_count: number;
  interval_minutes: number;
  readings: IntervalReading[];
}

export interface Verdict {
  /** Plain sentence; `**double asterisks**` mark the numbers that trace to slots. */
  template: string;
  bound_claim_ids: string[];
}

export interface FacilityPayload {
  disclosures?: LinkedDisclosure[];
  as_of: string;
  calc_version: string;
  receipts: string[];
  facility: Facility;
  grid: GridBinding;
  claims: Claim[];
  /** null when the facility has no hourly series (the API returns null). */
  hourly: HourlySeries | null;
  verdict: Verdict;
  release_id?: string;
  data_status?: DataStatus;
}

/* ------------------------------------------------------------------ drawer */

export interface ChainStep {
  /** One line per step, rendered in mono. */
  line: string;
  kind: 'call' | 'compute' | 'band' | 'guard';
}

export interface TransformRow {
  label: string;
  value: string;
  note?: string;
}

export interface SensitivityRow {
  label: string;
  delta: string;
  tone: 'up' | 'down' | 'flat';
}

export interface DrawerPayload {
  claim_id: string;
  metric: Metric;
  label: string;
  headline: string;
  unit: string;
  badge: Badge;
  provisional: boolean;
  p10: number | null;
  p50: number | null;
  p90: number | null;
  as_of_utc: string;
  receipt_count: number;
  citation: Citation;
  extra_citations: Citation[];
  chain: {
    calc_id: string;
    name: string;
    version: string;
    code_sha256: string;
    steps: ChainStep[];
  };
  transforms: TransformRow[];
  sensitivity: SensitivityRow[];
  drivers: string[];
  scope?: Claim['scope'];
  range_kind?: Claim['range_kind'];
  observation_start?: string | null;
  observation_end?: string | null;
  retrieved_at?: string | null;
  data_vintage?: string | null;
}

/* ---------------------------------------------------------------- operator */

export interface MethodRow {
  inputs?: ScenarioInputs;
  scope?: string;
  key: 'location' | 'market' | 'hourly';
  label: string;
  tonnes: number | null;
  note: string;
  tone: 'red' | 'teal' | 'amber';
}

export interface ComplianceItem {
  key: string;
  label: string;
  value: string;
  unit: string;
  sub: string;
  pct: number | null;
  mark_pct: number | null;
  tone: 'red' | 'amber' | 'blue' | 'teal';
}

export interface Lever {
  rank: number;
  title: string;
  detail: string;
  tonnes: number;
  cost: string;
  tone: 'teal' | 'amber';
}

export interface HeatmapCell {
  month: number;
  hour: number;
  /** matched fraction 0–1; null = unmatched hour (rendered red). */
  matched: number | null;
}

export interface OperatorPayload {
  disclosures?: LinkedDisclosure[];
  as_of: string;
  calc_version: string;
  facility_id: string;
  facility_label: string;
  synthetic_meter_data: boolean;
  methods: MethodRow[];
  matching_note: string;
  matched_pct: number | null;
  compliance: ComplianceItem[];
  peak: ComplianceItem;
  levers: Lever[];
  heatmap: {
    months: string[];
    hours: number;
    cells: HeatmapCell[];
    note: string;
  };
  export: { label: string; detail: string }[];
  footer: string;
  /** Present only where a permit yielded both installed capacity and a cap. */
  permit_envelope?: PermitEnvelope | null;
  /** Factor set and arithmetic. Absent from payloads built before the export. */
  method?: OperatorMethod;
  /** One row per cited passage. Absent from payloads built before the export. */
  sources?: OperatorSource[];
  release_id?: string;
  data_status?: DataStatus;
  built_at?: string;
  scope?: string;
  observation_start?: string | null;
  observation_end?: string | null;
  scenario_inputs?: ScenarioInputs;
  permit_limits?: PermitLimit[];
}

export interface OperatorMethod {
  factor_set_id: string | null;
  factor_set_sha256: string | null;
  subregion: string | null;
  subregion_name: string | null;
  rate_lb_per_mwh: number | null;
  binding_method: string | null;
  calc_id: string | null;
  chain: string[];
  transforms: string[];
  data_vintage?: string | null;
  source_url?: string | null;
  evidence_url?: string | null;
  published_at?: string | null;
  retrieved_at?: string | null;
  observation_start?: string | null;
  observation_end?: string | null;
  scope?: string;
  inputs?: ScenarioInputs;
}

export interface OperatorSource extends EvidenceRetrieval {
  /** The claim or permit metric this passage supports. */
  supports: string | null;
  publisher: string | null;
  document: string | null;
  /** "p.8" or "field SRCO2EQA/SRNGENAN". */
  locator: string | null;
  quote: string | null;
  doc_sha256: string | null;
  url: string | null;
  verified: boolean;
}

export interface PermitEnvelope {
  installed_mw: number;
  hours_cap: number;
  permitted_mwh: number;
  rows: { label: string; value: string; sub: string }[];
  binding: 'fuel' | 'hours' | 'neither' | null;
  note: string;
  assumption?: string;
  implied_gal_per_mwh?: number;
  effective_hours?: number;
  /** True when the two caps cannot describe the same fleet; no claim is made. */
  inconsistent?: boolean;
}

/* ------------------------------------------------------------------ search */

export interface FacilityIndexEntry {
  id: string;
  source?: string;
  source_label?: string;
  source_url?: string | null;
  name: string;
  address_line: string;
  city: string;
  state: string;
  zip: string;
  lat: number | null;
  lon: number | null;
  registry_id: string;
  /** EPA RegistryID — the facility, which may hold several permitted sources. */
  site_id?: string;
  /** Present when ECHO lists the same premises under more than one name. */
  duplicate_of?: string;
  location_quality?: LocationQuality;
  identity_match?: IdentityMatch;
  class_label?: string;
  class_verified?: boolean;
  hero?: boolean;
}

/** Every fetch result says whether it came from the API or a fixture. */
export interface Sourced<T> {
  data: T;
  source: 'api' | 'fixture';
  truncated?: boolean;
  indexAsOf?: string | null;
  releaseId?: string;
  sourceStatus?: SourceStatus[];
}

/* ------------------------------------------------------------- resolve */

export interface Band3 {
  p10: number;
  p50: number;
  p90: number;
}

/** One row of public/data/resolve-fallback.json, written by the estimator. */
export interface SubregionReference {
  name: string;
  data_vintage?: string;
  rate_lb_per_mwh: number;
  badge: Badge;
  energy_mwh: ScenarioBounds | Band3;
  carbon_t: ScenarioBounds | Band3;
  drivers: string[];
  chain: string[];
  citation: {
    publisher: string;
    dataset: string;
    field: string;
    quote: string;
    doc_sha256: string;
    page: number;
    line: number;
    verified: boolean;
    url?: string;
  };
  source_url?: string;
  evidence_url?: string;
  retrieved_at?: string;
  published_at?: string | null;
}

/** Published historical factor table; interactive scenarios use explicit user inputs. */
export interface ResolveFallbackTable {
  release_id?: string;
  built_at?: string;
  calc_version: string;
  reference_mw: number;
  capacity_basis: string;
  utilization_prior: [number, number, number];
  factor_set: { id: string; sha256: string; url: string };
  geometry: string;
  state_default: Record<string, string>;
  subregions: Record<string, SubregionReference>;
}

/** What the US Census geocoder said about the typed address. */
export interface GeocodeMatch {
  matched_address: string;
  lat: number;
  lon: number;
  state: string | null;
}

export interface NearestFacility {
  entry: FacilityIndexEntry;
  km: number;
}

/**
 * The address fallback, the client-side stand-in for SPEC §6 `GET /v1/resolve`.
 * `binding` says how the subregion was found: inside an EPA polygon, or by the
 * coarse state default when the point falls outside every polygon (a coastline
 * simplified to 110 m can miss a pier). The second is labelled on screen.
 */
export interface ResolveResult {
  query: string;
  geocode: GeocodeMatch;
  subregion: string;
  binding: 'point_in_polygon' | 'state_default';
  reference: SubregionReference;
  table: ResolveFallbackTable;
  nearest: NearestFacility[];
  /** An index record with the same house number and ZIP, within 1 km. */
  at_address: NearestFacility | null;
}

/** Annual public disclosures retain their reporting entity; campus totals are not allocated. */
export interface ReportedObservation {
  observation_id: string;
  disclosure_location_id: string;
  metric: string;
  label: string;
  value: number;
  unit: string;
  data_year: number;
  period_start: string;
  period_end: string;
  reporting_scope: 'campus' | 'building' | 'regulatory_facility' | 'operator_location';
  reporting_basis: string;
  methodology_note: string;
  source_id: string;
  source_url: string;
  source_page?: number | string;
  source_field?: string;
  source_quote?: string;
  source_sha256: string;
  publisher: string;
  retrieved_at?: string | null;
  source_status?: string;
  inventory_allocation: false;
}
export interface DisclosureRecordLink {
  record_id: string;
  disclosure_location_id: string;
  relationship: 'same_physical_scope' | 'within_disclosure_location' | 'candidate';
  verification_status: string;
  inventory_allocation: boolean;
  reason?: string;
  note?: string;
  expected_record?: {name?: string; address?: string; city?: string; state?: string};
  evidence?: {url?: string; source_url?: string; excerpt?: string; excerpts?: string[]; quote?: string; page?: number; title?: string; publisher?: string}[];
}
export interface DisclosureLocation {
  disclosure_location_id: string;
  operator?: string | null;
  location_label: string;
  reporting_scope: ReportedObservation['reporting_scope'];
  scope_note?: string;
  address?: {street?: string; city?: string; state?: string; postal_code?: string; country?: string};
  missing_metrics?: string[];
  source_id: string;
  source_status?: string;
  retrieved_at?: string | null;
  observations: ReportedObservation[];
  record_links: DisclosureRecordLink[];
}
export interface LinkedDisclosure { location: DisclosureLocation; link: DisclosureRecordLink; }
export interface FacilityObservations {
  release_id: string;
  built_at: string;
  locations: DisclosureLocation[];
  observation_count: number;
  coverage: Record<string, number>;
  note: string;
}
