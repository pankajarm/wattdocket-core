import type { DataManifest } from './types';

const explicitApi = import.meta.env.VITE_API_BASE as string | undefined;
const manifestUrl = (import.meta.env.VITE_DATA_MANIFEST_URL as string | undefined) ?? '/data/current.json';
const requireProductionData = import.meta.env.VITE_REQUIRE_PRODUCTION_DATA === 'true';
let pinnedRelease: Promise<DataManifest | null> | undefined;

/** Pin one immutable release for the entire page session. Failed reads never
 * fall through to an older API after a production manifest has been selected. */
export function getRelease(): Promise<DataManifest | null> {
  if (explicitApi) return Promise.resolve(null);
  pinnedRelease ??= readManifest();
  return pinnedRelease;
}

async function readManifest(): Promise<DataManifest | null> {
  const controller = new AbortController();
  const timeout = window.setTimeout(() => controller.abort(), 10000);
  try {
    const response = await fetch(manifestUrl, {
      cache: 'no-store', headers: { accept: 'application/json' }, signal: controller.signal,
    });
    // Old deployments have no manifest. Their SPA fallback may answer HTML.
    if (response.status === 404 || response.status === 410) {
      if (requireProductionData) throw new Error('The production data manifest is unavailable. Reload after publication is restored.');
      return null;
    }
    if (!response.ok) throw new Error(`Data release lookup failed (${response.status}). Reload to retry.`);
    if ((response.headers.get('content-type') ?? '').includes('text/html')) {
      if (requireProductionData) throw new Error('The production data manifest did not return a valid response.');
      return null;
    }
    const raw = await response.json() as Partial<DataManifest>;
    if (!['2.0', '2.1', '2.2'].includes(raw.schema_version ?? '') || typeof raw.release_id !== 'string' ||
        !/^[a-zA-Z0-9._-]+$/.test(raw.release_id) ||
        raw.base_path !== `/data/releases/${raw.release_id}` || typeof raw.built_at !== 'string') {
      throw new Error('The data release manifest is invalid. Reload after the next successful publication.');
    }
    for (const path of [raw.cards_path, raw.operators_path, raw.drawers_path]) {
      if (path != null && (typeof path !== 'string' || !path.startsWith(`${raw.base_path}/`) ||
          !/^[a-zA-Z0-9_./-]+\.json$/.test(path) || path.includes('..'))) {
        throw new Error('The data release contains an invalid bundle path.');
      }
    }
    return raw as DataManifest;
  } finally {
    window.clearTimeout(timeout);
  }
}

export async function readReleaseJson<T>(release: DataManifest, path: string): Promise<T> {
  if (!/^[a-zA-Z0-9_./-]+\.json$/.test(path) || path.includes('..') || path.startsWith('/')) {
    throw new Error('Invalid data path.');
  }
  const controller = new AbortController();
  // Detailed national boundaries are fetched only for address resolution.
  // A slow mobile download needs a longer window than an individual record.
  const timeout = window.setTimeout(() => controller.abort(), path === 'egrid-subregions.json' ? 60000 : 15000);
  try {
    const response = await fetch(`${release.base_path}/${path}`, {
      headers: { accept: 'application/json' }, signal: controller.signal,
    });
    if (!response.ok || !(response.headers.get('content-type') ?? '').includes('json')) {
      throw new Error(`This record is unavailable in data release ${release.release_id}. Reload to try the latest release.`);
    }
    const value = await response.json() as T;
    if (value && typeof value === 'object' && 'release_id' in value &&
        (value as { release_id?: string }).release_id !== release.release_id) {
      throw new Error('The record belongs to another data release. Reload to retry.');
    }
    return value;
  } finally {
    window.clearTimeout(timeout);
  }
}

export function releaseSlug(value: string): string {
  return value.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
}
