import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { build } from 'esbuild';

const bundle = await build({entryPoints:[new URL('../src/normalise.ts',import.meta.url).pathname],bundle:true,write:false,platform:'node',format:'cjs'});
const module={exports:{}};
new Function('module','exports','require',bundle.outputFiles[0].text)(module,module.exports,createRequire(import.meta.url));
const {normaliseClaim,normaliseDrawer,normaliseFacilityPayload,normaliseFacilityIndex}=module.exports;

test('missing-value sentinels describe the collected dataset and never become zero',()=>{
  for (const value of ['NOT_PUBLISHED','NOT_REPORTED',{state:'NOT_PUBLISHED'},{state:'NOT_REPORTED'},{kind:'NOT_PUBLISHED'},{kind:'NOT_PUBLISHED',reason:'Not published.'}]) {
    const claim=normaliseClaim({id:'power',metric:'power_mw',value},'echo:1');
    assert.equal(claim.value.kind,'NOT_PUBLISHED');
    assert.equal(claim.value.reason,'Not available in this dataset');
    assert.deepEqual([claim.p10,claim.p50,claim.p90],[null,null,null]);
  }
  const zero=normaliseClaim({id:'power',metric:'power_mw',value:0,unit:'MW',scope:'facility',renderable:true},'echo:1');
  assert.equal(zero.value.kind,'NUMERIC');
  assert.equal(zero.value.value,0);
});

test('normalization preserves explicit source qualifications rather than rewriting their meaning',()=>{
  const reason='The 2024 report states that this quantity was not published for this reporting location.';
  const claim=normaliseClaim({metric:'power_mw',value:{state:'NOT_PUBLISHED',reason}},'echo:1');
  assert.equal(claim.value.reason,reason);
  const threshold=normaliseClaim({metric:'water_gal',value:{state:'BELOW_THRESHOLD'}},'echo:1');
  assert.equal(threshold.value.reason,'below the reporting threshold');
});

test('card and index fallback fields do not infer an unpublished address or utility',()=>{
  const payload=normaliseFacilityPayload({facility:{id:'echo:1'},claims:[{metric:'water_gal',value:'NOT_PUBLISHED'}],grid:{}});
  assert.equal(payload.facility.address_line,'Address not available in this dataset');
  assert.equal(payload.grid.utility,'Not available in this dataset');
  assert.equal(payload.grid.balancing_authority,'Not available in this dataset');
  assert.equal(normaliseFacilityIndex([{id:'echo:1'}])[0].address_line,'Address not available in this dataset');
});

test('both drawer formats replace only generic missing-value headlines',()=>{
  const apiDrawer=normaliseDrawer({claim:{id:'power',metric:'power_mw',value:'NOT_PUBLISHED'}},'echo:1','power');
  assert.equal(apiDrawer.headline,'Not available in this dataset');
  const nativeDrawer=normaliseDrawer({claim_id:'power',headline:'Not published',chain:{}},'echo:1','power');
  assert.equal(nativeDrawer.headline,'Not available in this dataset');
  const numericDrawer=normaliseDrawer({claim_id:'power',headline:'0',chain:{}},'echo:1','power');
  assert.equal(numericDrawer.headline,'0');
});
