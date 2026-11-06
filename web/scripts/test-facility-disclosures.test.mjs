import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { readFile } from 'node:fs/promises';
import { build } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import TestRenderer, { act } from 'react-test-renderer';

// Render/export the production components. The MA quantity comes from captured
// publisher rows, and deliberately uses a unit other than the operator PDFs.
const bundle = await build({
  stdin: { contents: `export {LinkedFacilityDisclosures,DisclosureExplorer} from './src/components/FacilityDisclosures';
export {operatorRows,operatorCsv,operatorJson} from './src/operatorExport';`,
    resolveDir: fileURLToPath(new URL('../', import.meta.url)), loader:'tsx' },
  bundle:true, write:false, platform:'node', format:'cjs', external:['react','react/jsx-runtime'],
  define:{'import.meta.env':'{}'},loader:{'.css':'empty'},
});
const module = {exports:{}};
new Function('module','exports','require',bundle.outputFiles[0].text)(module,module.exports,createRequire(import.meta.url));
const {LinkedFacilityDisclosures,DisclosureExplorer,operatorRows,operatorCsv,operatorJson} = module.exports;
const captured = JSON.parse(await readFile(new URL('../../core/tests/fixtures/facility_reporting/massachusetts_energy.json',import.meta.url),'utf8'));
const [maUrl,maCapture] = Object.entries(captured)[0];
const maRow = maCapture.payload.features.map(x=>x.attributes).find(x=>x.UBID === '87JCHQ2M+7VV-32-27-31-23');

const observation = (metric,value,unit,extra={}) => ({
  observation_id:`meta:test:2024:${metric}`, disclosure_location_id:'meta:test', metric,
  label:metric.replaceAll('_',' '), value, unit, data_year:2024,
  period_start:'2024-01-01T00:00:00Z', period_end:'2025-01-01T00:00:00Z',
  reporting_scope:'campus', reporting_basis:'operator_reported', methodology_note:'Annual campus total; may contain multiple buildings.',
  source_url:'https://sustainability.atmeta.com/report.pdf', source_page:6,
  source_sha256:'a'.repeat(64), publisher:'Meta', retrieved_at:'2026-11-03T23:40:07Z',
  facility_binding_verified:false, source_status:'fresh', ...extra,
});
const location = (observations,extra={}) => ({
  disclosure_location_id:'meta:test',operator:'Meta',location_label:'Test campus',reporting_scope:'campus',
  scope_note:'Campus reporting boundary; no allocation to an individual building.',observations,record_links:[],...extra,
});
const relatedLink = {record_id:'echo:1',disclosure_location_id:'meta:test',relationship:'within_disclosure_location',
  verification_status:'verified',inventory_allocation:false,reason:'Official campus evidence links the record to a multi-building campus.',
  evidence:[{url:'https://example.gov/identity',publisher:'Public regulator',excerpt:'Source record lies within this campus.'}]};
const groupsHtml = groups => renderToStaticMarkup(React.createElement(LinkedFacilityDisclosures,{groups}));
const maId = 'ma-lber:87jchq2m-7vv-32-27-31-23';
const maObservation = observation('electricity_consumption',maRow.Total_Electricity_Use_Purchased_From_Grid_kBtu,'kBtu',{
  observation_id:`${maId}:2025:electricity_consumption`,disclosure_location_id:maId,label:'Reported annual electricity',
  data_year:2025,period_start:'2025-01-01T00:00:00Z',period_end:'2026-01-01T00:00:00Z',reporting_scope:'building',
  reporting_basis:'reported_energy',methodology_note:'Annual purchased-grid electricity in kBtu; not MW demand.',
  source_url:maUrl,source_page:`UBID ${maRow.UBID}; field Total_Electricity_Use_Purchased_From_Grid_kBtu`,
  source_field:'Total_Electricity_Use_Purchased_From_Grid_kBtu',source_sha256:maCapture.receipt.sha256,
  publisher:'Massachusetts Department of Energy Resources',
});
const maLocation = location([maObservation],{disclosure_location_id:maId,location_label:'41 Alexander Rd · Billerica, MA',
  operator:null,reporting_scope:'building',scope_note:'Selected building; publisher-reported energy, not instantaneous demand.'});

test('Massachusetts renders the reported year and native energy unit without converting to demand',()=>{
  const html=renderToStaticMarkup(React.createElement(DisclosureExplorer,{data:{locations:[maLocation],observation_count:1}}));
  assert.match(html,/23,932,780/);
  assert.match(html,/>kBtu</);
  assert.match(html,/2025 · reported energy/);
  assert.match(html,/Building/);
  assert.match(html,/Operator not specified/);
  assert.match(html,/not MW demand/);
  assert.doesNotMatch(html,/2026 · reported energy|23,932,780 MW|23,932,780 MWh/);
});

test('API row locators remain text and never become invalid PDF page fragments',()=>{
  const html=renderToStaticMarkup(React.createElement(DisclosureExplorer,{data:{locations:[maLocation],observation_count:1}}));
  assert.doesNotMatch(html,/#page=UBID|p\. UBID/);
  assert.match(html,/Total_Electricity_Use_Purchased_From_Grid_kBtu/);
  assert.match(html,new RegExp(maCapture.receipt.sha256));
});

test('related campus disclosures explain that totals are not individual-record measurements',()=>{
  const rows=[observation('electricity_consumption',12000,'MWh'),observation('emissions_scope2_market',0,'metric tons CO2e')];
  const html=groupsHtml([{location:location(rows),link:relatedLink}]);
  assert.match(html,/Related location totals — no allocation to this individual record/);
  assert.match(html,/totals are not this record’s individual measurements/);
  assert.match(html,/Campus/);
  assert.match(html,/12,000/);
  assert.match(html,/>0<span>metric tons CO2e/);
  assert.doesNotMatch(html,/Matched reporting boundary|<td>0<span>MWh/);
});

test('reviewed same-boundary building data still says annual reported values',()=>{
  const html=groupsHtml([{location:maLocation,link:{...relatedLink,disclosure_location_id:maId,
    relationship:'same_physical_scope',inventory_allocation:true}}]);
  assert.match(html,/Matched reporting boundary/);
  assert.match(html,/remain reported annual values/);
  assert.match(html,/Building/);
  assert.doesNotMatch(html,/Related location totals — no allocation/);
});

test('a known building with missing annual quantities renders no fabricated zero',()=>{
  const empty=location([],{disclosure_location_id:'ma-lber:missing',location_label:'MGHPCC',operator:null,reporting_scope:'building'});
  const html=renderToStaticMarkup(React.createElement(DisclosureExplorer,{data:{locations:[empty],observation_count:0}}));
  assert.match(html,/No numeric data/);
  assert.match(html,/A missing value is not zero/);
  assert.match(html,/<tbody><\/tbody>/);
  assert.doesNotMatch(html,/<td>0|0<span>kBtu|0<span>MWh/);
});

test('historical regulatory combustion remains 2023 with its source-defined boundary',()=>{
  const row=observation('reported_ghg_total',41464.144,'metric tons CO2e',{
    observation_id:'ghgrp:1014631:2023:reported_ghg_total',disclosure_location_id:'ghgrp:1014631',
    label:'Reported emissions · source-defined boundary',data_year:2023,
    period_start:'2023-01-01T00:00:00Z',period_end:'2024-01-01T00:00:00Z',reporting_scope:'regulatory_facility',
    reporting_basis:'sum_of_publisher_reported_co2e_components',source_page:'GHGRP facility 1014631; year 2023; Subpart C',
    source_url:'https://data.epa.gov/efservice/PUB_FACTS_SUBP_GHG_EMISSION/FACILITY_ID/1014631/JSON',
    publisher:'U.S. EPA GHGRP',methodology_note:'Direct stationary fuel combustion, Subpart C; excludes electricity Scope 2. Not a continuous measurement.'});
  const loc=location([row],{disclosure_location_id:'ghgrp:1014631',location_label:'Crosby Chase CTB',operator:'Crusoe',reporting_scope:'regulatory_facility'});
  const html=renderToStaticMarkup(React.createElement(DisclosureExplorer,{data:{locations:[loc],observation_count:1}}));
  assert.match(html,/Regulatory reporting facility/);
  assert.match(html,/41,464\.144/);
  assert.match(html,/2023 · sum of publisher reported co2e components/);
  assert.match(html,/excludes electricity Scope 2/);
  assert.doesNotMatch(html,/2026 · sum of publisher/);
});

test('measure filtering retains distinct accounting scopes instead of combining emissions',async()=>{
  const campus=location([observation('emissions_scope2_location',100,'metric tons CO2e'),
    observation('emissions_scope2_market',0,'metric tons CO2e')]);
  let root;
  await act(async()=>{root=TestRenderer.create(React.createElement(DisclosureExplorer,{data:{locations:[maLocation,campus],observation_count:3}}));});
  await act(async()=>{root.root.findByType('select').props.onChange({target:{value:'emissions'}});});
  let text=JSON.stringify(root.toJSON());
  assert.match(text,/emissions scope2 location/);
  assert.match(text,/emissions scope2 market/);
  assert.doesNotMatch(text,/41 Alexander Rd/);
  await act(async()=>{root.root.findByType('select').props.onChange({target:{value:'water'}});});
  text=JSON.stringify(root.toJSON());
  assert.match(text,/No location matches these filters/);
  assert.doesNotMatch(text,/reported value.*0/i);
  act(()=>root.unmount());
});

test('CSV and JSON preserve metrics, annual units and non-allocation without netting or filling',()=>{
  const observations=[observation('electricity_consumption',12000,'MWh'),
    observation('water_withdrawal',70,'million liters'),observation('water_consumption',35,'million liters'),
    observation('emissions_scope2_location',100,'metric tons CO2e'),observation('emissions_scope2_market',0,'metric tons CO2e'),
    observation('emissions_scope1_and2_market',7,'metric tons CO2e')];
  const missing={key:'water',label:'Facility water',value:'—',unit:'gal/day',sub:'Withheld: no individual-building water observation',pct:null,mark_pct:null};
  const payload={facility_id:'echo:1',facility_label:'Record',as_of:'2026-11-03',calc_version:'2',synthetic_meter_data:false,
    methods:[{key:'market',label:'Unobserved building emissions',tonnes:null,note:'Withheld: no individual-building accounting'}],
    compliance:[missing],peak:{...missing,key:'peak'},levers:[],method:{factor_set_id:'none',chain:[]},sources:[],
    disclosures:[{location:location(observations),link:relatedLink}]};
  const meta={dataSource:'api',exportedAt:'2026-11-03T23:40:07Z'};
  const rows=operatorRows(payload,meta).filter(x=>x.section==='reported_disclosure');
  assert.equal(rows.length,6);
  assert.deepEqual(rows.map(x=>x.value),[12000,70,35,100,0,7]);
  assert.equal(rows[0].unit,'MWh');
  assert.ok(rows.every(x=>x.scope==='campus' && x.observation_start==='2024-01-01T00:00:00Z'));
  assert.ok(rows.every(x=>x.basis.includes('inventory allocation: false') && x.basis.includes('source SHA-256')));
  assert.ok(rows.every((x,i)=>x.basis.includes(observations[i].observation_id)));
  const csv=operatorCsv(payload,meta);
  assert.match(csv,/water withdrawal,70,million liters/);
  assert.match(csv,/water consumption,35,million liters/);
  assert.match(csv,/emissions scope2 market,0,metric tons CO2e/);
  assert.match(csv,/facility_accounting,Unobserved building emissions,,t CO2e\/yr,withheld/);
  const json=JSON.parse(operatorJson(payload,meta));
  assert.deepEqual(json.payload.disclosures[0].location.observations.map(x=>x.metric),observations.map(x=>x.metric));
  assert.equal(json.payload.methods[0].tonnes,null);
  assert.equal(json.payload.disclosures[0].link.inventory_allocation,false);
});
