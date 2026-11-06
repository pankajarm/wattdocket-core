import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { readFile } from 'node:fs/promises';
import { build } from 'esbuild';

const bundle = await build({
  entryPoints: [fileURLToPath(new URL('../src/learning.ts', import.meta.url))],
  bundle: true, write: false, platform: 'node', format: 'cjs',
});
const module = { exports: {} };
new Function('module', 'exports', 'require', bundle.outputFiles[0].text)(module, module.exports, createRequire(import.meta.url));
const { selectReportedExample, waterShares } = module.exports;
const evidence = JSON.parse(await readFile(new URL('../../docs/verification/awareness-inventory-2026-11-03.json', import.meta.url), 'utf8'));
const examples = evidence.examples.map(location => ({
  ...location,
  record_links: [],
  observations: location.observations.map(row => ({
    ...row,
    disclosure_location_id: location.disclosure_location_id,
    reporting_scope: location.reporting_scope,
    source_id: location.source_id,
    period_start: `${row.data_year}-01-01T00:00:00Z`,
    period_end: `${row.data_year + 1}-01-01T00:00:00Z`,
  })),
}));
const data = { locations: examples };
const waterMetrics = ['water_withdrawal', 'water_discharge', 'water_consumption'];
const carbonMetrics = ['emissions_scope2_location', 'emissions_scope2_market'];
const mutateLocation = (id, mutate) => ({ locations: examples.map(location => location.disclosure_location_id === id ? mutate(structuredClone(location)) : location) });

test('the approved water example uses the captured reported values and native unit', () => {
  const example = selectReportedExample(data, 'google:douglas-county-ga', waterMetrics, true);
  assert.equal(example.year, 2025);
  assert.deepEqual(example.rows.map(row => row.value), [440.6, 73.5, 367.1]);
  assert.ok(example.rows.every(row => row.unit === 'million gallons'));
  const shares = waterShares(example);
  assert.ok(Math.abs(shares.discharged + shares.consumed - 100) < 1e-9);
  assert.ok(shares.consumed > shares.discharged);
});

test('zero market-based emissions is retained alongside the same-period location-based value', () => {
  const example = selectReportedExample(data, 'meta:altoona-ia', carbonMetrics, true);
  assert.equal(example.year, 2024);
  assert.deepEqual(example.rows.map(row => row.value), [666434, 0]);
  assert.equal(example.location.reporting_scope, 'campus');
});

test('a partial new reporting year never borrows the other method from an older year', () => {
  const changed = mutateLocation('meta:altoona-ia', location => {
    const market = location.observations.find(row => row.metric === 'emissions_scope2_market');
    location.observations.push({ ...market, observation_id: 'newer-market', data_year: 2025, period_start: '2025-01-01T00:00:00Z', period_end: '2026-01-01T00:00:00Z' });
    return location;
  });
  assert.equal(selectReportedExample(changed, 'meta:altoona-ia', carbonMetrics, true), null);
});

test('different annual boundaries and duplicate metric rows withhold the comparison', () => {
  const period = mutateLocation('meta:altoona-ia', location => {
    location.observations.find(row => row.metric === 'emissions_scope2_market').period_end = '2024-12-01T00:00:00Z';
    return location;
  });
  assert.equal(selectReportedExample(period, 'meta:altoona-ia', carbonMetrics, true), null);
  const duplicate = mutateLocation('meta:altoona-ia', location => {
    location.observations.push({ ...location.observations.find(row => row.metric === 'emissions_scope2_market'), observation_id: 'ambiguous-duplicate' });
    return location;
  });
  assert.equal(selectReportedExample(duplicate, 'meta:altoona-ia', carbonMetrics, true), null);
});

test('a different reporting scope or unit cannot enter the water diagram', () => {
  for (const patch of [{ reporting_scope: 'building' }, { unit: 'million liters' }]) {
    const changed = mutateLocation('google:douglas-county-ga', location => {
      Object.assign(location.observations.find(row => row.metric === 'water_consumption'), patch);
      return location;
    });
    assert.equal(selectReportedExample(changed, 'google:douglas-county-ga', waterMetrics, true), null);
  }
});

test('unreconciled water quantities remain numbers without a misleading proportional diagram', () => {
  const changed = mutateLocation('google:douglas-county-ga', location => {
    location.observations.find(row => row.metric === 'water_consumption').value = 100;
    return location;
  });
  const example = selectReportedExample(changed, 'google:douglas-county-ga', waterMetrics, true);
  assert.equal(example.rows[2].value, 100);
  assert.equal(waterShares(example), null);
});

test('missing data and a missing metric remain unavailable instead of receiving a default', () => {
  assert.equal(selectReportedExample(null, 'meta:altoona-ia', carbonMetrics, true), null);
  assert.equal(selectReportedExample(data, 'missing-location', carbonMetrics, true), null);
  const missing = mutateLocation('google:douglas-county-ga', location => ({ ...location, observations: location.observations.filter(row => row.metric !== 'water_discharge') }));
  assert.equal(selectReportedExample(missing, 'google:douglas-county-ga', waterMetrics, true), null);
});
