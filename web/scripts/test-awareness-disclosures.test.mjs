import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import TestRenderer, { act } from 'react-test-renderer';

const bundle = await build({
  stdin: {contents: `export * from './src/disclosureData'; export {DisclosureExplorer} from './src/components/FacilityDisclosures';`, resolveDir: fileURLToPath(new URL('../', import.meta.url)), loader:'tsx'},
  bundle:true, write:false, platform:'node', format:'cjs', external:['react','react/jsx-runtime'],
});
const module = {exports:{}};
new Function('module','exports','require',bundle.outputFiles[0].text)(module,module.exports,createRequire(import.meta.url));
const {filterDisclosureLocations,disclosureSummary,reportedLocationHref,reportingBasisLabel,DisclosureExplorer} = module.exports;

const row = (metric,year,value,extra={}) => ({
  observation_id:`test:${metric}:${year}`,disclosure_location_id:'test:campus',metric,label:metric.replaceAll('_',' '),value,unit:'MWh',data_year:year,
  reporting_scope:'campus',reporting_basis:'operator_reported',publisher:'Operator',source_id:'test',source_url:'https://example.com/report.pdf',
  source_page:6,source_sha256:'a'.repeat(64),period_start:`${year}-01-01T00:00:00Z`,period_end:`${year+1}-01-01T00:00:00Z`,
  methodology_note:'Annual campus total, with estimates permitted.',inventory_allocation:false,...extra,
});
const campus = {
  disclosure_location_id:'test:campus',location_label:'Test campus',operator:'Operator',reporting_scope:'campus',source_id:'test',record_links:[],
  observations:[row('electricity_consumption',2025,150),row('emissions_scope2_location',2024,80,{unit:'metric tonnes CO2e'}),row('emissions_scope2_market',2024,0,{unit:'metric tonnes CO2e'})],
};
const missing = {disclosure_location_id:'ma-lber:missing',location_label:'Unreported building',operator:null,reporting_scope:'building',source_id:'massachusetts_energy',record_links:[],observations:[]};
const data = {release_id:'release-test',built_at:'2026-11-03T22:00:00Z',observation_count:3,locations:[campus,missing],coverage:{},note:''};

test('municipal addresses and empty source entries remain searchable without inventing values',()=>{
  const city={...missing,disclosure_location_id:'nyc-ll84:1',source_id:'nyc_benchmarking',
    address:{street:'60 Hudson Street',city:'New York',state:'NY'},missing_metrics:['electricity_consumption','water_use_reported']};
  assert.equal(filterDisclosureLocations([city],{query:'60 hudson'}).length,1);
  assert.equal(filterDisclosureLocations([city],{publisher:'New York City Department of Buildings'}).length,1);
  assert.equal(filterDisclosureLocations([city],{query:'60 hudson',family:'water'}).length,0);
  const markup=renderToStaticMarkup(React.createElement(DisclosureExplorer,{data:{...data,locations:[city]},initialLocationId:city.disclosure_location_id}));
  assert.match(markup,/Publisher address:.*60 Hudson Street/);
  assert.match(markup,/Missing or withheld:.*electricity, water/);
});

test('quantity filters must be satisfied by the same observation, not unrelated years on one campus',()=>{
  assert.equal(filterDisclosureLocations(data.locations,{family:'electricity',year:'2024'}).length,0);
  const result=filterDisclosureLocations(data.locations,{family:'electricity',year:'2025'});
  assert.equal(result.length,1);
  assert.deepEqual(result[0].observations.map(value=>value.metric),['electricity_consumption']);
});

test('explicit zero survives emission filtering and separate accounting methods are never added',()=>{
  const result=filterDisclosureLocations(data.locations,{family:'emissions',year:'2024'});
  assert.deepEqual(result[0].observations.map(value=>value.value),[80,0]);
  assert.deepEqual(campus.observations.map(value=>value.value),[150,80,0]);
});

test('unreported entries remain discoverable without pretending they match a quantity or year',()=>{
  assert.equal(filterDisclosureLocations(data.locations).length,2);
  assert.equal(filterDisclosureLocations(data.locations,{scope:'building'})[0].location.disclosure_location_id,missing.disclosure_location_id);
  assert.equal(filterDisclosureLocations(data.locations,{publisher:'Massachusetts Department of Energy Resources'}).length,1);
  assert.equal(filterDisclosureLocations(data.locations,{scope:'building',year:'2025'}).length,0);
  assert.equal(filterDisclosureLocations(data.locations,{family:'water'}).length,0);
});

test('coverage counts populated entries separately and ignores duplicate canonical references',()=>{
  const summary=disclosureSummary({...data,locations:[campus,missing,campus]});
  assert.equal(summary.locations,2);
  assert.equal(summary.populated,1);
  assert.equal(summary.missing,1);
  assert.equal(summary.observations,3);
  assert.equal(summary.electricity,1);
  assert.equal(summary.emissions,1);
  assert.equal(summary.water,0);
  assert.deepEqual(summary.years,[2025,2024]);
});

test('location links encode identifiers and a missing linked location does not fall back to an unrelated one',()=>{
  assert.equal(reportedLocationHref('test:campus & building'),'#/reported?location=test%3Acampus%20%26%20building');
  assert.equal(filterDisclosureLocations(data.locations,{locationId:'missing:unknown'}).length,0);
  assert.equal(filterDisclosureLocations(data.locations,{locationId:campus.disclosure_location_id}).length,1);
});

test('publisher calculation basis remains adjacent to a Massachusetts emissions value',()=>{
  const value=row('reported_ghg_total',2025,14,{reporting_basis:'publisher_calculated_from_reported_energy',unit:'metric tons CO2e'});
  assert.equal(reportingBasisLabel(value),'Publisher-calculated from reported energy');
  const html=renderToStaticMarkup(React.createElement(DisclosureExplorer,{advanced:true,data:{...data,locations:[{...campus,observations:[value]}]}}));
  assert.match(html,/2025 · Publisher-calculated from reported energy/);
  assert.doesNotMatch(html,/verified measurement|metered emissions/i);
});

test('dedicated explorer filters and reset restore genuine missing-data entries',async()=>{
  let root;
  await act(async()=>{root=TestRenderer.create(React.createElement(DisclosureExplorer,{advanced:true,data,initialFamily:'electricity'}));});
  let text=JSON.stringify(root.toJSON());
  assert.match(text,/electricity consumption/);
  assert.doesNotMatch(text,/Unreported building/);
  const select=root.root.findAllByType('select');
  assert.equal(select.length,4);
  await act(async()=>{select[1].props.onChange({target:{value:'2024'}});});
  text=JSON.stringify(root.toJSON());
  assert.match(text,/No location matches these filters/);
  await act(async()=>{root.root.findAllByType('button').find(button=>button.children.includes('Clear all filters')).props.onClick();});
  text=JSON.stringify(root.toJSON());
  assert.match(text,/Unreported building/);
  assert.match(text,/No numeric data/);
  await act(async()=>{root.unmount();});
});

test('changing a routed location opens the requested entry and resets stale filters',async()=>{
  let root;
  await act(async()=>{root=TestRenderer.create(React.createElement(DisclosureExplorer,{advanced:true,data,initialFamily:'water'}));});
  await act(async()=>{root.update(React.createElement(DisclosureExplorer,{advanced:true,data,initialLocationId:campus.disclosure_location_id}));});
  const result=root.root.findAllByProps({className:'disclosure-result'});
  assert.equal(result.length,1);
  assert.equal(result[0].props.open,true);
  assert.match(JSON.stringify(root.toJSON()),/electricity consumption/);
  await act(async()=>{root.unmount();});
});
