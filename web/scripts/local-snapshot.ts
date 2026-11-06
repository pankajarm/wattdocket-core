import { readFileSync } from 'node:fs';
import { gunzipSync } from 'node:zlib';
import { createHash } from 'node:crypto';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import type { Plugin } from 'vite';

const root = fileURLToPath(new URL('../../site/data/', import.meta.url));

/** Keep development and the portable build on the same verified local release. */
export function localSnapshot(): Plugin {
  const pointer = readFileSync(path.join(root, 'current.json'));
  const manifest = JSON.parse(pointer.toString('utf8'));
  if (!/^[a-zA-Z0-9][a-zA-Z0-9_-]{0,95}$/.test(manifest.release_id) ||
      manifest.base_path !== `/data/releases/${manifest.release_id}` || !manifest.objects) {
    throw new Error('Invalid bundled data manifest. Run python3 scripts/sync_public_data.py --verify-only.');
  }
  const immutable = readFileSync(path.join(root, 'releases', manifest.release_id, 'manifest.json'));
  if (JSON.stringify(JSON.parse(immutable.toString('utf8'))) !== JSON.stringify(manifest)) {
    throw new Error('Snapshot pointer differs from immutable manifest');
  }
  // Verify and cache before serving. A refresh cannot mix an old object map
  // with a new pointer; restart the server to opt into the refreshed release.
  const files = new Map<string, { bytes: Buffer; gzip: boolean }>([
    ['/data/current.json', { bytes: pointer, gzip: false }],
    [`${manifest.base_path}/manifest.json`, { bytes: immutable, gzip: false }],
  ]);
  for (const name of Object.keys(manifest.objects)) {
    if (!/^[a-zA-Z0-9_./-]+\.json$/.test(name) || name.includes('..') || name.startsWith('/')) {
      throw new Error('Invalid snapshot object path');
    }
    const info = manifest.objects[name];
    if (!Number.isSafeInteger(info.bytes) || info.bytes < 1 || info.bytes > 256 * 1024 * 1024 ||
        !/^[a-f0-9]{64}$/.test(info.sha256)) throw new Error(`Invalid object metadata: ${name}`);
    const bytes = readFileSync(path.join(root, 'releases', manifest.release_id, `${name}.gz`));
    const raw = gunzipSync(bytes, { maxOutputLength: info.bytes + 1 });
    if (raw.length !== info.bytes || createHash('sha256').update(raw).digest('hex') !== info.sha256) {
      throw new Error(`Snapshot checksum mismatch: ${name}`);
    }
    files.set(`${manifest.base_path}/${name}`, { bytes, gzip: true });
  }
  const middleware: import('connect').NextHandleFunction = (req, res, next) => {
    const url = new URL(req.url ?? '/', 'http://localhost');
    if (!url.pathname.startsWith('/data/')) return next();
    // Legacy address-lookup support has its own generated static asset.
    if (url.pathname === '/data/egrid-subregions.json') return next();
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.writeHead(405, { Allow: 'GET, HEAD' }); res.end(); return;
    }
    const entry = files.get(url.pathname);
    if (!entry) {
      res.writeHead(404, { 'Content-Type': 'application/json' });
      res.end(JSON.stringify({ error: 'Not available in the bundled snapshot' })); return;
    }
    const bytes = entry.bytes;
    res.statusCode = 200;
    res.setHeader('Content-Type', 'application/json; charset=utf-8');
    res.setHeader('Cache-Control', 'no-store');
    res.setHeader('Content-Length', bytes.length);
    if (entry.gzip) res.setHeader('Content-Encoding', 'gzip');
    res.end(req.method === 'HEAD' ? undefined : bytes);
  };
  return {
    name: 'wattdocket-local-snapshot',
    configureServer(server) { server.middlewares.use(middleware); },
    configurePreviewServer(server) { server.middlewares.use(middleware); },
    generateBundle() {
      for (const [url, entry] of files) {
        const bytes = entry.bytes;
        const raw = entry.gzip ? gunzipSync(bytes) : bytes;
        if (entry.gzip) {
          const info = manifest.objects[url.slice(manifest.base_path.length + 1)];
          if (raw.length !== info.bytes || createHash('sha256').update(raw).digest('hex') !== info.sha256) {
            throw new Error(`Snapshot checksum mismatch: ${url}`);
          }
        }
        this.emitFile({ type: 'asset', fileName: url.slice(1), source: raw });
      }
    },
  };
}
