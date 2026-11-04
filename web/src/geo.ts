import type { FacilityIndexEntry, GeocodeMatch, NearestFacility } from './types';

/**
 * Address -> point -> eGRID subregion, in the browser.
 *
 * This is the client-side half of the arbitrary-address fallback. It finds
 * *where* an address is; it never computes a carbon number. The numbers come
 * from public/data/resolve-fallback.json, written by the same estimator that
 * builds the cards.
 *
 * Geocoding is the US Census Bureau's one-line address geocoder: free, no key,
 * public record. It does not send CORS headers ("At this time the geocoder does
 * not support CORS requests"), so it is called the way its docs say to:
 * format=jsonp with a callback. That keeps the lookup off our infrastructure.
 */

const CENSUS_URL = 'https://geocoding.geo.census.gov/geocoder/locations/onelineaddress';
const GEOCODE_TIMEOUT_MS = 9000;

export class GeocodeError extends Error {
  readonly code: 'no_match' | 'unreachable';
  constructor(code: 'no_match' | 'unreachable', message: string) {
    super(message);
    this.code = code;
    this.name = 'GeocodeError';
  }
}

let callbackSeq = 0;

function jsonp(url: string, timeoutMs: number): Promise<unknown> {
  return new Promise((resolve, reject) => {
    const name = `__wdGeocode${Date.now()}_${callbackSeq++}`;
    const script = document.createElement('script');
    const w = window as unknown as Record<string, unknown>;
    const cleanup = () => {
      window.clearTimeout(timer);
      script.remove();
      delete w[name];
    };
    const timer = window.setTimeout(() => {
      cleanup();
      reject(new GeocodeError('unreachable', 'The Census geocoder did not answer in time.'));
    }, timeoutMs);
    w[name] = (data: unknown) => {
      cleanup();
      resolve(data);
    };
    script.onerror = () => {
      cleanup();
      reject(new GeocodeError('unreachable', 'The Census geocoder could not be reached.'));
    };
    script.src = `${url}&callback=${name}`;
    document.head.appendChild(script);
  });
}

interface CensusMatch {
  matchedAddress?: string;
  coordinates?: { x?: number; y?: number };
  addressComponents?: { state?: string };
}

export async function geocodeAddress(address: string): Promise<GeocodeMatch> {
  const url =
    `${CENSUS_URL}?address=${encodeURIComponent(address)}` +
    '&benchmark=Public_AR_Current&format=jsonp';
  const raw = (await jsonp(url, GEOCODE_TIMEOUT_MS)) as {
    result?: { addressMatches?: CensusMatch[] };
  };
  const m = raw?.result?.addressMatches?.[0];
  const lon = m?.coordinates?.x;
  const lat = m?.coordinates?.y;
  if (!m || typeof lat !== 'number' || typeof lon !== 'number') {
    throw new GeocodeError(
      'no_match',
      'The Census geocoder found no street address matching that. It needs a house number, street and city or ZIP.',
    );
  }
  return {
    matched_address: m.matchedAddress ?? address,
    lat,
    lon,
    state: m.addressComponents?.state?.toUpperCase() ?? null,
  };
}

/* ------------------------------------------------------- point in polygon */

type Ring = [number, number][];
interface IndexedRegion {
  sub: string;
  exclusion: boolean;
  minx: number;
  miny: number;
  maxx: number;
  maxy: number;
  polygons: Ring[][];
  tolerance: number;
}

function isRing(value: unknown): value is Ring {
  return Array.isArray(value) && value.length >= 4 && value.every((point) =>
    Array.isArray(point) && point.length >= 2 && Number.isFinite(point[0]) && Number.isFinite(point[1]));
}

/** GeoJSON polygons use the first ring as the exterior and later rings as
 * holes. MultiPolygon members are disjoint polygons, not additive holes. */
export function indexSubregions(geojson: unknown): IndexedRegion[] {
  type Feature = { properties?: { subregion?: string; geometry_valid?: boolean }; geometry?: { type?: string; coordinates?: unknown } };
  const source = geojson as {
    metadata?: { tolerance_degrees?: number };
    tolerance_degrees?: number;
    boundary_buffer_degrees?: number;
    features?: Feature[];
    overlap_geojson?: { features?: Feature[] };
  };
  const simplification = source?.tolerance_degrees ?? source?.metadata?.tolerance_degrees ?? 0;
  const requestedBuffer = source?.boundary_buffer_degrees ?? simplification * 2;
  const tolerance = Number.isFinite(requestedBuffer) ? Math.max(0, requestedBuffer) : 0;
  const out: IndexedRegion[] = [];
  const overlap = source?.overlap_geojson?.features ?? [];
  if (overlap.some((feature) => feature.properties?.geometry_valid === false)) return [];
  const all = [...(source?.features ?? []).map((feature) => ({ feature, exclusion: false })),
    ...overlap.map((feature) => ({ feature, exclusion: true }))];
  for (const { feature, exclusion } of all) {
    if (feature.properties?.geometry_valid === false) continue;
    const sub = feature.properties?.subregion || (exclusion ? 'overlap' : null);
    const geometry = feature.geometry;
    if (!sub || !geometry || !Array.isArray(geometry.coordinates)) continue;
    const rawPolygons: unknown[] = geometry.type === 'MultiPolygon' ? geometry.coordinates
      : geometry.type === 'Polygon' ? [geometry.coordinates] : [];
    const polygons = rawPolygons.filter((polygon): polygon is Ring[] =>
      Array.isArray(polygon) && polygon.length > 0 && polygon.every(isRing));
    if (polygons.length === 0) continue;
    let minx = Infinity, miny = Infinity, maxx = -Infinity, maxy = -Infinity;
    for (const polygon of polygons) for (const ring of polygon) for (const [x, y] of ring) {
      minx = Math.min(minx, x); miny = Math.min(miny, y);
      maxx = Math.max(maxx, x); maxy = Math.max(maxy, y);
    }
    out.push({ sub, exclusion, minx, miny, maxx, maxy, polygons, tolerance });
  }
  return out;
}

function pointInRing(x: number, y: number, ring: Ring): boolean {
  let inside = false;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [xi, yi] = ring[i]!;
    const [xj, yj] = ring[j]!;
    if (yi > y !== yj > y && x < ((xj - xi) * (y - yi)) / (yj - yi) + xi) inside = !inside;
  }
  return inside;
}

function nearRing(x: number, y: number, ring: Ring, tolerance: number): boolean {
  const limit = Math.max(tolerance, 1e-12) ** 2;
  for (let i = 0, j = ring.length - 1; i < ring.length; j = i++) {
    const [ax, ay] = ring[j]!, [bx, by] = ring[i]!;
    const dx = bx - ax, dy = by - ay;
    const length = dx * dx + dy * dy;
    const ratio = length ? Math.max(0, Math.min(1, ((x - ax) * dx + (y - ay) * dy) / length)) : 0;
    if ((x - ax - ratio * dx) ** 2 + (y - ay - ratio * dy) ** 2 <= limit) return true;
  }
  return false;
}

export function subregionForPoint(index: IndexedRegion[], lat: number, lon: number): string | null {
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || Math.abs(lat) > 90 || Math.abs(lon) > 180) return null;
  const matches = new Set<string>();
  for (const region of index) {
    const tolerance = region.tolerance;
    if (lon < region.minx - tolerance || lon > region.maxx + tolerance || lat < region.miny - tolerance || lat > region.maxy + tolerance) continue;
    for (const polygon of region.polygons) {
      if (polygon.some((ring) => nearRing(lon, lat, ring, tolerance))) return null;
      if (pointInRing(lon, lat, polygon[0]!) && !polygon.slice(1).some((ring) => pointInRing(lon, lat, ring))) {
        if (region.exclusion) return null;
        matches.add(region.sub);
      }
    }
  }
  return matches.size === 1 ? [...matches][0]! : null;
}

/* --------------------------------------------------------- nearest ECHO */

const EARTH_KM = 6371.0088;

function hasCoordinates(entry: FacilityIndexEntry): entry is FacilityIndexEntry & { lat: number; lon: number } {
  return entry.location_quality?.map_eligible !== false && typeof entry.lat === 'number' && typeof entry.lon === 'number' && Number.isFinite(entry.lat) && Number.isFinite(entry.lon) &&
    Math.abs(entry.lat) <= 90 && Math.abs(entry.lon) <= 180 &&
    !(entry.lat === 0 && entry.lon === 0);
}

export function haversineKm(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const rad = Math.PI / 180;
  const dLat = (lat2 - lat1) * rad;
  const dLon = (lon2 - lon1) * rad;
  const a =
    Math.sin(dLat / 2) ** 2 + Math.cos(lat1 * rad) * Math.cos(lat2 * rad) * Math.sin(dLon / 2) ** 2;
  return 2 * EARTH_KM * Math.asin(Math.min(1, Math.sqrt(a)));
}

/**
 * The closest index records, one per EPA site: a campus with five permits is
 * one place on the map, not five neighbours.
 */
export function nearestFacilities(
  entries: FacilityIndexEntry[],
  lat: number,
  lon: number,
  k = 3,
): NearestFacility[] {
  const bySite = new Map<string, NearestFacility>();
  for (const e of entries) {
    if (!hasCoordinates(e)) continue;
    const km = haversineKm(lat, lon, e.lat, e.lon);
    const key = e.site_id || e.registry_id || e.id;
    const prev = bySite.get(key);
    if (!prev || km < prev.km) bySite.set(key, { entry: e, km });
  }
  return [...bySite.values()].sort((a, b) => a.km - b.km).slice(0, k);
}

/**
 * The index record at the geocoded address, if there is one. A geocoded point
 * (street interpolation) and an ECHO point (site centroid) for the same
 * building can sit hundreds of metres apart, and a Loudoun campus packs five
 * sites into that distance, so nearness alone is not identity: it needs the
 * same street address and ZIP, within a kilometre. A house number and ZIP
 * alone are not identity: two nearby streets can both have a 100 Main/Oak.
 */
function normaliseStreetAddress(address: string): string {
  const aliases: Record<string, string> = {
    STREET: 'ST', ROAD: 'RD', AVENUE: 'AVE', BOULEVARD: 'BLVD', DRIVE: 'DR',
    LANE: 'LN', COURT: 'CT', PLACE: 'PL', PARKWAY: 'PKWY', HIGHWAY: 'HWY',
    NORTH: 'N', SOUTH: 'S', EAST: 'E', WEST: 'W',
    NORTHEAST: 'NE', NORTHWEST: 'NW', SOUTHEAST: 'SE', SOUTHWEST: 'SW',
  };
  return (address.toUpperCase().match(/[A-Z0-9]+/g) ?? [])
    .map((word) => aliases[word] ?? word).join(' ');
}

export function facilityAtAddress(
  entries: FacilityIndexEntry[],
  match: GeocodeMatch,
): NearestFacility | null {
  const house = match.matched_address.match(/^\s*(\d+)\s/)?.[1];
  const zip = match.matched_address.match(/(\d{5})\s*$/)?.[1];
  const street = normaliseStreetAddress(match.matched_address.split(',')[0] ?? '');
  if (!house || !zip) return null;
  let best: NearestFacility | null = null;
  for (const e of entries) {
    if (!hasCoordinates(e) || !e.address_line.trim().startsWith(`${house} `) ||
        !e.zip.startsWith(zip) || normaliseStreetAddress(e.address_line) !== street) continue;
    const km = haversineKm(match.lat, match.lon, e.lat, e.lon);
    if (km <= 1 && (!best || km < best.km)) best = { entry: e, km };
  }
  return best;
}

/** A street address starts with a house number and has a word after it. */
export function looksLikeStreetAddress(q: string): boolean {
  return /^\s*\d+[A-Za-z]?\s+\S+/.test(q) && /[A-Za-z]{2,}/.test(q);
}
