import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import {readFile} from 'node:fs/promises';
import React from 'react';
import TestRenderer,{act} from 'react-test-renderer';
import {renderToStaticMarkup} from 'react-dom/server';
const bundle = await build({stdin:{contents:`export * from './src/scenario'; export {OperatorWater} from './src/components/OperatorWater'; export {mapLocationCounts} from './src/dataPresentation'; export {ReferenceCalculator} from './src/components/ReferenceCalculator'; export {GridStrip} from './src/components/GridStrip'; export {RecordQuality} from './src/components/RecordQuality'; export {TopBar} from './src/components/TopBar'; export {operatorCsv,operatorJson} from './src/operatorExport'; export {normaliseFacilityPayload,normaliseClaim,normaliseDrawer} from './src/normalise'; export {nearestFacilities,facilityAtAddress} from './src/geo';`,resolveDir:process.cwd(),loader:'tsx'},bundle:true,write:false,platform:'node',format:'cjs',external:['react','react/jsx-runtime']});
const module={exports:{}};
new Function('module','exports','require',bundle.outputFiles[0].text)(module,module.exports,createRequire(import.meta.url));
const {OperatorWater,mapLocationCounts,calculateScenario,defaultScenario,ReferenceCalculator,GridStrip,RecordQuality,TopBar,operatorCsv,operatorJson,normaliseClaim,normaliseDrawer,nearestFacilities,facilityAtAddress}=module.exports;

test('historical scenario uses grid-side inputs with no PUE and scales user inputs',()=>{
  const inputs=defaultScenario('2023'); const result=calculateScenario(inputs,635.2668276347086);
  assert.equal(result.energy_mwh.base,56940);assert.ok(Math.abs(result.carbon_t.base-16407.385)<.001);
  assert.equal(calculateScenario({...inputs,load_mw:20},635.2668276347086).energy_mwh.base,113880);
  assert.equal(calculateScenario(inputs,0).carbon_t.base,0,'a reported zero factor is valid');
});
test('invalid scenario inputs fail without a numeric answer',()=>{
  for(const inputs of [{...defaultScenario(),load_mw:NaN},{...defaultScenario(),load_mw:-1},{...defaultScenario(),utilization:{low:.8,base:.5,high:.9}},{...defaultScenario(),utilization:{low:0,base:.6,high:1.1}}]) assert.ok(calculateScenario(inputs,635).error);
  assert.ok(calculateScenario(defaultScenario(),null).error);
});
test('calculator exposes editable inputs, updates the result and reports invalid values',()=>{
  let root;act(()=>{root=TestRenderer.create(React.createElement(ReferenceCalculator,{rate:635.2668276347086,year:'2023',region:'NWPP'}));});
  assert.equal(root.root.findAllByType('input')[0].props.value,10);
  assert.match(JSON.stringify(root.toJSON()),/16,407.4/);
  act(()=>root.root.findAllByType('input')[0].props.onChange({currentTarget:{value:'20'}}));
  assert.match(JSON.stringify(root.toJSON()),/32,814.8/);
  act(()=>root.root.findAllByType('input')[0].props.onChange({currentTarget:{value:''}}));
  assert.match(JSON.stringify(root.toJSON()),/greater than zero/);
  assert.equal(root.root.findAllByType('table').length,0);act(()=>root.unmount());
});
test('new bounds preserve scenario meaning while cached percentile-shaped releases remain readable',()=>{
  const base={id:'scenario-carbon',metric:'carbon_t',scope:'scenario',range_kind:'scenario',value:20,renderable:true,calc_id:'x'};
  for(const range of [{low:10,base:20,high:30},{p10:10,p50:20,p90:30}]) { const c=normaliseClaim({...base,range},'echo:1');assert.equal(c.p10,10);assert.equal(c.p50,20);assert.equal(c.p90,30);assert.equal(c.range_kind,'scenario'); }
  const drawer=normaliseDrawer({claim_id:'x',chain:{steps:[]},low:10,base:20,high:30},'echo:1','x');assert.equal(drawer.p50,20);
});
test('suspect record coordinates are excluded from nearest and address matching',()=>{
  const e={id:'echo:1',name:'Contact address',lat:38,lon:-77,address_line:'100 MAIN ST',zip:'20001',location_quality:{status:'suspect',address_type:'mailing',map_eligible:false,reasons:['mailing address']}};
  assert.deepEqual(nearestFacilities([e],38,-77),[]);assert.equal(facilityAtAddress([e],{matched_address:'100 MAIN ST, WASHINGTON, DC, 20001',lat:38,lon:-77}),null);
  assert.match(renderToStaticMarkup(React.createElement(RecordQuality,{facility:e})),/Map pin and geographic calculations withheld/);
});
test('historical factor year and evidence link stay beside the rate',()=>{
  const html=renderToStaticMarkup(React.createElement(GridStrip,{grid:{egrid_subregion:'NWPP',data_vintage:'2023',rate_lb_per_mwh:635,source_url:'https://example.gov/workbook.xlsx',evidence_url:'/data/releases/a/evidence/egrid/NWPP.json'},hourly:null}));
  assert.match(html,/2023 annual regional emissions rate/);assert.match(html,/href="\/data\/releases\/a\/evidence\/egrid\/NWPP.json"/);assert.doesNotMatch(html,/Click any number/);
});
test('exports retain chosen inputs, low/base/high results, source periods and release identity',()=>{
  const p={facility_id:'echo:1',facility_label:'Example',as_of:'2026-11-03',calc_version:'2.1',release_id:'release-a',scope:'scenario',scenario_inputs:{...defaultScenario('2023'),load_mw:20},synthetic_meter_data:false,methods:[],compliance:[],peak:{key:'peak',label:'Grid-side load',value:'20',unit:'MW',sub:'User input'},levers:[],method:{rate_lb_per_mwh:635,chain:[],data_vintage:'2023',observation_start:'2023-01-01',observation_end:'2024-01-01',retrieved_at:'2026-11-03'},data_status:{built_at:'2026-11-03T20:00:00Z',sources:[]}};
  const csv=operatorCsv(p,{dataSource:'api',exportedAt:'2026-11-03T21:00:00Z'});
  assert.match(csv,/scenario_input,load_mw,20,MW/);assert.match(csv,/scenario_result,base_annual_energy,113880/);assert.match(csv,/2023-01-01,2024-01-01,release-a,2026-11-03T20:00:00Z/);assert.doesNotMatch(csv,/p10|p50|p90/);
  assert.equal(JSON.parse(operatorJson(p,{dataSource:'api',exportedAt:''})).payload.scenario_inputs.load_mw,20);
});

test('mobile navigation opens explicitly and closes on route selection',()=>{
  globalThis.window={addEventListener(){},removeEventListener(){},matchMedia:()=>({matches:false,addEventListener(){},removeEventListener(){}}),localStorage:{getItem(){return null},setItem(){}}};
  globalThis.document={querySelector(){return null},documentElement:{dataset:{},style:{setProperty(){}}}};
  let root;act(()=>{root=TestRenderer.create(React.createElement(TopBar,{active:'map',facilityId:'echo:1',signedIn:false,onSignOut(){}}));});
  const toggle=root.root.findByProps({className:'nav-toggle'});assert.equal(toggle.props['aria-expanded'],false);
  act(()=>toggle.props.onClick());assert.equal(toggle.props['aria-expanded'],true);
  act(()=>root.root.findByProps({id:'main-navigation'}).props.onClick());assert.equal(toggle.props['aria-expanded'],false);act(()=>root.unmount());
});

test('an unmatched record with no match method renders without inventing a verified identity',()=>{
  const html=renderToStaticMarkup(React.createElement(RecordQuality,{facility:{id:'echo:1',class_label:'Type unverified',identity_match:{status:'unmatched',method:null,note:'No supported permit identity match.'},location_quality:{status:'unknown',address_type:'unknown',map_eligible:true,reasons:[]}}}));
  assert.match(html,/unmatched/);assert.match(html,/No supported permit identity match/);assert.doesNotMatch(html,/Physical location supported/);
});

test('operator-reported location water retains scope, exact report units and separate aggregates',()=>{
  const row={location_label:'Ashburn, VA',operator:'Google',data_year:2025,withdrawal_million_gallons:76.5,discharge_million_gallons:23.3,consumption_million_gallons:53.2,unit:'million gallons',measurement_basis:'operator reported; may include estimates',facility_binding_verified:false};
  const html=renderToStaticMarkup(React.createElement(OperatorWater,{data:{observations:[row],aggregates:[{...row,location_label:'Data centers total',consumption_million_gallons:10523}],retrieved_at:'2026-11-03T22:03:36Z',source:{publisher:'Google',report:'2026 Environmental Report',data_year:2025,publication_month:'2026-06',published_at:null,source_page:97,pdf_url:'https://sustainability.google/example.pdf',methodology_note:'Operator-reported; may include estimates.',geography_note:'No facility join is verified.'}}}));
  assert.match(html,/2025 Google location totals may include estimates/);assert.match(html,/global locations, not individual EPA-record measurements/);assert.match(html,/June 2026 \(exact day not supplied\)/);assert.match(html,/million gallons/);assert.doesNotMatch(html,/million US gallons|June 1,/);assert.match(html,/must not be added/);assert.match(html,/publisher aggregates/);assert.equal((html.match(/<table /g)||[]).length,2);assert.doesNotMatch(html,/<details open/);
});
test('filtered map counts expose withheld coordinates while preserving the record total',()=>{
  const good={id:'a',lat:38,lon:-77,location_quality:{map_eligible:true}};
  const suspect={...good,id:'b',location_quality:{map_eligible:false}};
  assert.deepEqual(mapLocationCounts([good,suspect]),{shown:1,review:1});assert.deepEqual(mapLocationCounts([suspect]),{shown:0,review:1});assert.deepEqual(mapLocationCounts([{...good,lat:null}]),{shown:0,review:1});
});
test('anchor offsets track the compact mobile header instead of a stale fixed height',async()=>{
  const css=await readFile(new URL('../src/pages/data-pages.css',import.meta.url),'utf8');
  assert.match(css,/\.page \[id\]\s*\{\s*scroll-margin-top:calc\(var\(--header-height, 68px\) \+ 24px\)/);
  const theme=await readFile(new URL('../src/unified-theme.css',import.meta.url),'utf8');
  assert.equal([...theme.matchAll(/--header-height:\s*(\d+)px/g)].at(-1)[1],'64');
});
