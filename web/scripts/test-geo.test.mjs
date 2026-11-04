import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

// Exercise the same TypeScript module used by the app, without a second test runner.
const source = readFileSync(new URL('../src/geo.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText;
const { facilityAtAddress, nearestFacilities, indexSubregions, subregionForPoint } = await import(
  `data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`
);

const entry = (address, overrides = {}) => ({
  id: 'echo:123', registry_id: '123', site_id: 'echo:123', name: 'Example',
  address_line: address, city: 'Example', state: 'VA', zip: '20147',
  lat: 39, lon: -77.48, ...overrides,
});
const query = { matched_address: '100 MAIN STREET, EXAMPLE, VA, 20147', lat: 39, lon: -77.48, state: 'VA' };

test('a nearby site on a different street is not identified as the address', () => {
  assert.equal(facilityAtAddress([entry('100 OAK ST')], query), null);
});

test('equivalent street abbreviations resolve to the matching site', () => {
  assert.equal(facilityAtAddress([entry('100 MAIN ST')], query)?.entry.id, 'echo:123');
});

test('matching street and ZIP must also be close', () => {
  assert.equal(facilityAtAddress([entry('100 MAIN ST', { lat: 40 })], query), null);
});

test('missing coordinates are omitted from nearest facilities', () => {
  assert.deepEqual(nearestFacilities([entry('100 MAIN ST', { lat: 0, lon: 0 })], 0, 0), []);
  assert.deepEqual(nearestFacilities([entry('100 MAIN ST', { lat: null, lon: null })], 0, 0), []);
});

const square = (x, y, size) => [[x,y],[x+size,y],[x+size,y+size],[x,y+size],[x,y]];
const feature = (subregion, coordinates, type = 'Polygon') => ({ properties: { subregion }, geometry: { type, coordinates } });

test('polygon holes are excluded and disjoint MultiPolygon islands are included', () => {
  const index = indexSubregions({ features: [feature('A', [square(0,0,10),square(3,3,2)]), feature('B', [[square(20,0,2)],[square(25,0,2)]], 'MultiPolygon')] });
  assert.equal(subregionForPoint(index, 1, 1), 'A');
  assert.equal(subregionForPoint(index, 4, 4), null);
  assert.equal(subregionForPoint(index, 1, 21), 'B');
  assert.equal(subregionForPoint(index, 1, 26), 'B');
});

test('overlapping regions and simplification boundary bands are withheld', () => {
  const index = indexSubregions({ metadata: { tolerance_degrees: 0.01 }, features: [feature('A', [square(0,0,10)]), feature('B', [square(5,5,10)])] });
  assert.equal(subregionForPoint(index, 6, 6), null);
  assert.equal(subregionForPoint(index, 1, 0.015), null);
  assert.equal(subregionForPoint(index, 1, 0.03), 'A');
  assert.equal(subregionForPoint(index, 0, 1), null);
});

test('explicit release buffer and overlap exclusions take precedence', () => {
  const index = indexSubregions({ tolerance_degrees: 0.0001, boundary_buffer_degrees: 0.0021,
    features: [feature('A', [square(0,0,10)])],
    overlap_geojson: { features: [feature(null, [square(3,3,2)])] } });
  assert.equal(subregionForPoint(index, 1, 0.002), null);
  assert.equal(subregionForPoint(index, 1, 0.003), 'A');
  assert.equal(subregionForPoint(index, 4, 4), null);
});

test('invalid region geometry is rejected and invalid overlap masks disable binding', () => {
  const invalid = feature('A', [square(0,0,10)]);
  invalid.properties.geometry_valid = false;
  assert.equal(subregionForPoint(indexSubregions({features:[invalid]}), 1, 1), null);
  const index = indexSubregions({ features: [feature('A', [square(0,0,10)])], overlap_geojson: {features:[invalid]} });
  assert.equal(subregionForPoint(index, 1, 1), null);
});
