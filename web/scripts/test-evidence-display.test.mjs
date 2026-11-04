import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { createRequire } from 'node:module';
import { build } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import TestRenderer, { act } from 'react-test-renderer';

// Exercise the real components with committed snapshots; only network loading
// is replaced, so the tests cover the copy readers actually see.
const bundle = await build({
  stdin: { contents: `export { GridStrip } from './src/components/GridStrip';
export { HonestyBar } from './src/components/HonestyBar';
export { SourceDrawer } from './src/components/SourceDrawer';
export { normaliseFacilityPayload, normaliseDrawer } from './src/normalise';`, resolveDir: process.cwd(), loader: 'tsx' },
  bundle: true, write: false, platform: 'node', format: 'cjs',
  external: ['react', 'react/jsx-runtime'],
  plugins: [{ name:'api-stub', setup(builder) {
    builder.onResolve({ filter:/^\.\.\/api$/ }, () => ({ path:'api', namespace:'stub' }));
    builder.onLoad({ filter:/.*/, namespace:'stub' }, () => ({ contents:`export async function getDrawer(){return {data:globalThis.__drawerFixture,source:'api'}}`, loader:'js' }));
  } }],
});
const module = { exports:{} };
new Function('module','exports','require',bundle.outputFiles[0].text)(module,module.exports,createRequire(import.meta.url));
const { GridStrip, HonestyBar, SourceDrawer, normaliseFacilityPayload, normaliseDrawer } = module.exports;
const fixture = (name) => readFile(new URL(`../../site/api/${name}`, import.meta.url), 'utf8').then(JSON.parse);
const payload = normaliseFacilityPayload(await fixture('card/echo_110072155814'));
assert.ok(payload);
const claim = (metric) => payload.claims.find((item) => item.metric === metric);

async function drawerText(metric) {
  globalThis.__drawerFixture = normaliseDrawer(await fixture(`drawer/echo_110072155814/${metric}`), 'echo:110072155814', metric);
  globalThis.window = { addEventListener(){}, removeEventListener(){} };
  let root;
  await act(async () => { root = TestRenderer.create(React.createElement(SourceDrawer, {facilityId:'echo:110072155814',claim:claim(metric),onClose(){},onOperator(){}})); });
  const text = JSON.stringify(root.toJSON());
  act(() => root.unmount());
  return text;
}

test('grid displays supplied average values and does not synthesize marginal factors', () => {
  const html = renderToStaticMarkup(React.createElement(GridStrip, {grid:payload.grid,hourly:payload.hourly}));
  assert.match(html, /737/);
  assert.match(html, /neither an hourly nor a marginal emissions factor/);
  assert.doesNotMatch(html, /1017|1,017|aria-pressed/);
});

test('a point value has a single marker and no invented uncertainty range', () => {
  const html = renderToStaticMarkup(React.createElement(HonestyBar,{p10:10,p50:10,p90:10,state:'value'}));
  assert.match(html, /Single value 10, no uncertainty range/);
  assert.doesNotMatch(html, /90% range|honesty__band|honesty__edge/);
  assert.match(html, /honesty__tick/);
});

test('reference power does not claim it has no number or a 90% band', async () => {
  const text = await drawerText('power_mw');
  assert.match(text, /No citation was supplied for this figure/);
  assert.match(text, /no range/);
  assert.doesNotMatch(text, /no number to show|not-published value|p10–p90 10–10|nothing was estimated/);
});

test('carbon drawer omits unsupported marginal sensitivity and does not deny its estimate', async () => {
  const text = await drawerText('carbon_t');
  assert.match(text, /utilization 0.45/);
  assert.doesNotMatch(text, /marginal instead of average|\+38%|nothing was estimated/);
  assert.match(text, /No additional uncertainty drivers were supplied/);
});

test('missing water remains a dataset gap rather than a claim about publication', async () => {
  const text = await drawerText('water_gal');
  assert.match(text, /No citation is available for this metric and record in this dataset/);
  assert.match(text, /no value available in this dataset/);
  assert.doesNotMatch(text, /not publish this metric|do not publish this metric|no published value/);
});
