import { readFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';
import { defineConfig, loadEnv, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

// The address fallback needs the eGRID subregion polygons. They live once, in
// data/, where core/wattdocket/geo.py reads them for the cards; copying them
// into public/ would put a second 4 MB file in git that could drift from the
// first. This serves them in dev and emits them into the build instead, as
// .json so the deploy gives them the short-cache JSON headers and CloudFront
// compresses them.
const EGRID_SRC = fileURLToPath(new URL('../data/egrid_subregions.geojson', import.meta.url));
const EGRID_OUT = 'data/egrid-subregions.json';

function egridPolygons(): Plugin {
  const read = () => JSON.stringify(JSON.parse(readFileSync(EGRID_SRC, 'utf8')));
  return {
    name: 'wattdocket-egrid-polygons',
    configureServer(server) {
      server.middlewares.use(`/${EGRID_OUT}`, (_req, res) => {
        res.setHeader('content-type', 'application/json');
        res.end(read());
      });
    },
    generateBundle() {
      this.emitFile({ type: 'asset', fileName: EGRID_OUT, source: read() });
    },
  };
}

// In local dev and preview, serve the checked-in static API using the same
// path mapping as CloudFront. A configured API base opts out of this plugin.
function staticApi(): Plugin {
  const apiRoot = fileURLToPath(new URL('../site/api/', import.meta.url));
  const slug = (value: string) => value.toLowerCase().replace(/[^a-z0-9]+/g, '_').replace(/^_|_$/g, '');
  const middleware: import('connect').NextHandleFunction = (req, res, next) => {
    const url = new URL(req.url ?? '/', 'http://localhost');
    if (!url.pathname.startsWith('/api/v1/')) return next();
    if (req.method !== 'GET' && req.method !== 'HEAD') {
      res.statusCode = 405;
      res.setHeader('Allow', 'GET, HEAD');
      res.end('Method not allowed');
      return;
    }
    let key: string | null = null;
    try {
      const drawer = url.pathname.match(/^\/api\/v1\/facility\/([^/]+)\/drawer\/([^/]+)\/?$/);
      const facility = url.pathname.match(/^\/api\/v1\/facility\/([^/]+)\/?$/);
      if (/^\/api\/v1\/facilities\/?$/.test(url.pathname)) key = 'facilities';
      else if (drawer) key = `drawer/${slug(decodeURIComponent(drawer[1]))}/${slug(decodeURIComponent(drawer[2]))}`;
      else if (/^\/api\/v1\/operator\/scenario\/?$/.test(url.pathname)) key = `operator/${slug(url.searchParams.get('facility_id') ?? '')}`;
      else if (facility) key = `card/${slug(decodeURIComponent(facility[1]))}`;
    } catch {
      res.statusCode = 400;
      res.end('Invalid request path');
      return;
    }
    if (!key || key.endsWith('/')) {
      res.statusCode = 404;
      res.setHeader('Content-Type', 'application/json; charset=utf-8');
      res.end(JSON.stringify({ error: 'Snapshot endpoint not found' }));
      return;
    }
    void readFile(path.join(apiRoot, key)).then((data) => {
      res.statusCode = 200;
      res.setHeader('Content-Type', 'application/json; charset=utf-8');
      res.setHeader('Cache-Control', 'no-store');
      res.end(req.method === 'HEAD' ? undefined : data);
    }).catch(() => {
      res.statusCode = 404;
      res.setHeader('Content-Type', 'application/json; charset=utf-8');
      res.end(JSON.stringify({ error: 'Snapshot record not found' }));
    });
  };
  return {
    name: 'wattdocket-local-static-api',
    configureServer(server) { server.middlewares.use(middleware); },
    configurePreviewServer(server) { server.middlewares.use(middleware); },
  };
}

export default defineConfig(({ mode }) => ({
  plugins: [react(), egridPolygons(), ...(!loadEnv(mode, process.cwd(), 'VITE_').VITE_API_BASE ? [staticApi()] : [])],
  // Relative base keeps hash routes working from any path prefix (S3 origin,
  // CloudFront behaviour, a preview URL under /web/).
  base: './',
  build: {
    outDir: 'dist',
    sourcemap: false,
    target: 'es2020',
  },
  server: {
    port: 5173,
  },
}));
