import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';

const bundle = await build({
  stdin: { contents: `export * from './src/api'; export * from './src/normalise'; export * from './src/dataPresentation';`, resolveDir: fileURLToPath(new URL('../', import.meta.url)), loader: 'ts' },
  bundle: true, write: false, platform: 'node', format: 'cjs', define: { 'import.meta.env': 'globalThis.__testEnv' },
});
function api(fetcher, env = { VITE_EXPLAIN_URL: 'http://127.0.0.1:8787/explain' }) {
  globalThis.window = { setTimeout, clearTimeout };
  globalThis.__testEnv = env;
  globalThis.fetch = fetcher;
  const module = { exports: {} };
  new Function('module', 'exports', 'require', bundle.outputFiles[0].text)(module, module.exports, createRequire(import.meta.url));
  return module.exports;
}
const response = (body, status = 200) => new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } });
const manifest = { schema_version: '2.0', release_id: 'release-a', built_at: '2026-11-03T12:00:00Z', base_path: '/data/releases/release-a' };
const listing = { id: 'osm:123', name: 'Source-listed facility', source: 'osm', source_label: 'OpenStreetMap', lat: null, lon: null };
const card = {
  release_id: 'release-a', as_of: '2026-11-03T12:00:00Z', facility: listing,
  claims: [
    { id: 'power-mw', metric: 'power_mw', value: 'NOT_PUBLISHED', scope: 'facility', renderable: true },
    { id: 'scenario-carbon', metric: 'carbon_t', value: 100, scope: 'scenario', range_kind: 'scenario', label: 'Scenario carbon', renderable: true, calc_id: 'scenario-1', range: { p10: 70, p50: 100, p90: 140 }, observation_end: '2023-12-31', retrieved_at: '2026-11-03T10:00:00Z' },
  ],
};

test('one page pins the manifest and reads all records from that release', async () => {
  const calls = [];
  const client = api(async (url) => {
    calls.push(url);
    if (url === '/data/current.json') return response(manifest);
    if (url === '/data/releases/release-a/facilities.json') return response({ results: [listing], release_id: 'release-a', as_of: manifest.built_at });
    if (url === '/data/releases/release-a/card/osm_123.json') return response(card);
    throw new Error(`Unexpected ${url}`);
  });
  await client.getFacilityIndex();
  await client.getFacilityPayload('osm:123');
  await client.getFacilityIndex();
  assert.equal(calls.filter((url) => url === '/data/current.json').length, 1);
  assert.ok(calls.every((url) => !url.startsWith('/api/')));
});

test('bundled cards are fetched once and preserve source identities and dates', async () => {
  let bundleReads = 0;
  const client = api(async (url) => {
    if (url === '/data/current.json') return response({ ...manifest, cards_path: `${manifest.base_path}/cards.json` });
    if (url === `${manifest.base_path}/cards.json`) { bundleReads++; return response({ osm_123: card }); }
    throw new Error(`Unexpected ${url}`);
  });
  const first = await client.getFacilityPayload('osm:123');
  await client.getFacilityPayload('osm:123');
  assert.equal(bundleReads, 1);
  assert.equal(first.data.facility.source_label, 'OpenStreetMap');
  assert.equal(first.data.facility.lat, null);
  assert.equal(first.data.claims[1].observation_end, '2023-12-31');
  assert.equal(first.data.claims[1].retrieved_at, '2026-11-03T10:00:00Z');
  assert.equal(first.data.claims[1].scope, 'scenario');
  assert.equal(first.data.claims[1].range_kind, 'scenario');
});

test('a missing production record cannot fall back to legacy data', async () => {
  const calls = [];
  const client = api(async (url) => { calls.push(url); return url === '/data/current.json' ? response(manifest) : response({ error: 'missing' }, 404); });
  await assert.rejects(client.getFacilityPayload('osm:123'), /unavailable in data release/);
  assert.deepEqual(calls, ['/data/current.json', `${manifest.base_path}/card/osm_123.json`]);
});

test('a manifest server failure is not treated as a legacy deployment', async () => {
  const calls = [];
  const client = api(async (url) => { calls.push(url); return response({ error: 'unavailable' }, 503); });
  await assert.rejects(client.getFacilityIndex(), /release lookup failed/);
  assert.deepEqual(calls, ['/data/current.json']);
});

test('an absent manifest permits the existing API but never invents its date', async () => {
  const client = api(async (url) => url === '/data/current.json' ? response({}, 404) : response([listing]));
  const result = await client.getFacilityIndex();
  assert.equal(result.indexAsOf, null);
  assert.equal(result.data[0].lat, null);
});

test('a production-required deployment never falls back when its manifest disappears', async () => {
  for (const result of [response({},404),new Response('<html>SPA</html>',{headers:{'Content-Type':'text/html'}})]) {
    const calls=[];
    const client=api(async(url)=>{calls.push(url);return result;},{VITE_REQUIRE_PRODUCTION_DATA:'true'});
    await assert.rejects(client.getFacilityIndex(),/production data manifest/);
    assert.deepEqual(calls,['/data/current.json']);
  }
});

test('mismatched releases and external bundle paths are rejected', async () => {
  let client = api(async (url) => url === '/data/current.json' ? response(manifest) : response({ results: [], release_id: 'release-b' }));
  await assert.rejects(client.getFacilityIndex(), /another data release/);
  client = api(async () => response({ ...manifest, cards_path: 'https://outside.example/cards.json' }));
  await assert.rejects(client.getFacilityIndex(), /invalid bundle path/);
});

test('production explanation sends only the displayed release and canonical subject identity', async () => {
  const calls = [];
  const reply = {text:'A guarded model sentence.',source:'model',degraded:false,release_id:'release-a',subject:{kind:'facility',id:'osm:123'},citations:[],qualifiers:[],fact_ids:['source-record'],cited_facts:[{id:'source-record',text:'A source record exists.',scope:'source_record'}],validation:{level:'numerals_citations_and_qualifiers',label:'Numbers, citations and scope qualifiers checked'}};
  const client = api(async (url, init) => {
    calls.push({ url, body: init?.body });
    return response(reply);
  });
  const result = await client.getExplain(client.normaliseFacilityPayload(card), 'Evidence summary');
  assert.equal(calls.length, 1);
  assert.equal(calls[0].url, 'http://127.0.0.1:8787/explain');
  assert.deepEqual(JSON.parse(calls[0].body), {release_id:'release-a',subject:{kind:'facility',id:'osm:123'}});
  assert.deepEqual(result, reply);
});

test('a public clone makes no model request until an endpoint is explicitly configured', async () => {
  let requests = 0;
  const client = api(async () => { requests++; throw new Error('Unexpected network call'); }, {});
  assert.equal(client.explainAvailable(), false);
  const result = await client.getExplain(client.normaliseFacilityPayload(card), 'Evidence summary');
  assert.equal(requests, 0);
  assert.equal(result.source, 'template');
  assert.equal(result.text, 'Evidence summary');
});

test('a failed explanation call falls back to the deterministic template', async () => {
  const client = api(async () => { throw new Error('network down'); });
  const result = await client.getExplain(client.normaliseFacilityPayload(card), 'Evidence summary');
  assert.equal(result.text,'Evidence summary');assert.equal(result.source,'template');assert.equal(result.reason,'unreachable');
  assert.equal(result.release_id,'release-a');assert.deepEqual(result.subject,{kind:'facility',id:'osm:123'});
});

test('annual, power and permit requests preserve canonical subjects and never transmit client facts or filters',async()=>{
  for(const subject of [{kind:'disclosure_location',id:'meta:henrico-va'},{kind:'power_site',id:'nlr:esif-hpc-golden-co'},{kind:'permit_document',id:'73643-5'}]){
    const calls=[];const client=api(async(url,init)=>{calls.push(JSON.parse(init.body));return response({text:'Source summary.',source:'template',degraded:true,release_id:'release-a',subject,citations:[],qualifiers:[],fact_ids:[]});});
    const result=await client.getExplain({release_id:'release-a',subject,facts:{power:99999},chartWeek:'2025-08-29'},'Local template with client numbers 99999');
    assert.deepEqual(calls,[{release_id:'release-a',subject}]);assert.equal(result.text,'Source summary.');
  }
});

test('wrong-release or wrong-subject explanation responses are withheld',async()=>{
  for(const mismatch of [{release_id:'release-b'},{subject:{kind:'power_site',id:'other'}}]){
    const client=api(async()=>response({text:'Wrong site model prose.',source:'model',degraded:false,release_id:'release-a',subject:{kind:'facility',id:'osm:123'},...mismatch}));
    const result=await client.getExplain(client.normaliseFacilityPayload(card),'Safe displayed-record summary');
    assert.equal(result.source,'template');assert.equal(result.reason,'EVIDENCE_MISMATCH');assert.doesNotMatch(result.text,/Wrong site/);
  }
});

test('missing displayed release makes no paid request and there is no automatic retry',async()=>{
  let count=0;const client=api(async()=>{count++;throw Error('offline');});
  const result=await client.getExplain({facility:{id:'echo:123'}},'Local source summary');
  assert.equal(count,0);assert.equal(result.reason,'RELEASE_UNAVAILABLE');
  await client.getExplain(client.normaliseFacilityPayload(card),'Local source summary');assert.equal(count,1);
});

test('model answers need auditable fact IDs and safe original-source links',async()=>{
  const base={text:'Untrusted model answer',source:'model',degraded:false,release_id:'release-a',subject:{kind:'facility',id:'osm:123'},citations:[],qualifiers:[],fact_ids:['f1'],cited_facts:[{id:'f1',text:'Source evidence',scope:'permit'}],validation:{level:'numerals_citations_and_qualifiers'}};
  for(const mutation of [{cited_facts:[]},{fact_ids:[]},{validation:{level:'all_claims_verified'}},{citations:[{fact_id:'f1',label:'Source',url:'javascript:alert(1)',locator:'page 1',sha256:'a'.repeat(64)}]},{citations:[{fact_id:'other',label:'Source',url:'https://example.gov/source.pdf',locator:'page 1',sha256:'a'.repeat(64)}]}]){
    const client=api(async()=>response({...base,...mutation}));const result=await client.getExplain(client.normaliseFacilityPayload(card),'Safe source summary');
    assert.equal(result.reason,'INVALID_RESPONSE');assert.equal(result.text,'Safe source summary');
  }
});

test('client timeout allows the bounded server call to finish before offering a retry',async()=>{
  let delay;const client=api(async()=>response({reason:'REGISTRY_UNAVAILABLE'},503));
  window.setTimeout=(callback,ms)=>{delay=ms;return setTimeout(callback,ms);};
  const result=await client.getExplain(client.normaliseFacilityPayload(card),'Source summary');
  assert.equal(delay,40000);assert.equal(result.reason,'REGISTRY_UNAVAILABLE');
});

test('freshness ages without reclassifying an old reporting year as current observations', () => {
  const client = api(async () => response({}));
  const source = { status: 'current', retrieved_at: '2026-11-01T12:00:00Z', stale_after_seconds: 86400, data_vintage: '2023', observation_start: null, observation_end: null };
  assert.equal(client.sourceState(source, Date.parse('2026-11-03T12:00:00Z')), 'stale');
  assert.equal(client.observationLabel(source), '2023');
  assert.equal(client.normaliseFacilityIndex({ results: [] }).length, 0);
  assert.equal(client.normaliseClaim({ id: 'x', metric: 'carbon_t', value: NaN }, 'x'), null);
});

test('current pipeline health can report a failure without changing the pinned fact release', async () => {
  const calls = [];
  const client = api(async (url) => {
    calls.push(url);
    if (url === '/data/current.json') return response(manifest);
    if (url === '/data/pipeline-status.json') return response({status:'failed',started_at:'2026-11-04T12:00:00Z',error:'Publication gate failed'});
    return response(card);
  });
  const first = await client.getFacilityPayload('osm:123');
  const health = await client.getPipelineStatus();
  const second = await client.getFacilityPayload('osm:123');
  assert.equal(health.status, 'failed');
  assert.equal(first.releaseId, second.releaseId);
  assert.equal(calls.filter((url) => url === '/data/current.json').length,1);
});

test('schema2.1 release and low/base/high bounds are accepted before the data switchover', async () => {
  const client=api(async(url)=>url==='/data/current.json' ? response({...manifest,schema_version:'2.1'}) : response({...card,claims:[{...card.claims[1],range:{low:70,base:100,high:140}}]}));
  const result=await client.getFacilityPayload('osm:123');
  assert.equal(result.data.claims[0].p10,70);assert.equal(result.data.claims[0].p90,140);assert.equal(result.data.claims[0].scope,'scenario');
});

test('optional operator water is read only when advertised and stays release-pinned',async()=>{
  const calls=[];
  let client=api(async(url)=>{calls.push(url);return response(manifest)});
  assert.equal(await client.getOperatorWater(),null);assert.deepEqual(calls,['/data/current.json']);
  calls.length=0;
  client=api(async(url)=>{calls.push(url);return response(url==='/data/current.json'?{...manifest,objects:{'operator-water.json':{}}}:{release_id:'release-a',observations:[],aggregates:[],source:{publisher:'Google'}})});
  assert.equal((await client.getOperatorWater()).source.publisher,'Google');assert.deepEqual(calls,['/data/current.json','/data/releases/release-a/operator-water.json']);
});


test('schema2.2 disclosure fetch stays pinned and remains optional on an older release',async()=>{
  const calls=[];
  const client=api(async(url)=>{calls.push(url);return response(url==='/data/current.json'?{...manifest,schema_version:'2.2',objects:{'facility-observations.json':{}}}:{release_id:'release-a',locations:[],observation_count:0})});
  assert.equal((await client.getFacilityObservations()).release_id,'release-a');
  assert.deepEqual(calls,['/data/current.json','/data/releases/release-a/facility-observations.json']);
  const older=api(async()=>response({...manifest,schema_version:'2.1'}));
  assert.equal(await older.getFacilityObservations(),null);
});
