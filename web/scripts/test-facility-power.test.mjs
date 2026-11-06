import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';
const built = await build({stdin:{contents:"export * from './src/facilityPowerData'; export {parseRoute} from './src/router'; export {FacilityPower} from './src/components/FacilityPower';",resolveDir:process.cwd(),loader:'tsx'},bundle:true,write:false,platform:'node',format:'cjs',external:['react','react/jsx-runtime'],plugins:[{name:'capture-api',setup(b){b.onResolve({filter:/^\.\.\/api$/},()=>({path:'api',namespace:'mock'}));b.onLoad({filter:/.*/,namespace:'mock'},()=>({contents:'export const getExplain = async (request) => { globalThis.__powerExplainRequests.push(request); return {text: "Published power summary",source:"template",degraded:true,release_id:request.release_id,subject:request.subject}; }; export const getFacilityPower = async () => globalThis.__powerFixture; export const getPowerReadings = async () => { if(globalThis.__failMeter) throw Error("offline"); return globalThis.__meterFixture; };'}));b.onLoad({filter:/\.css$/},()=>({contents:'',loader:'text'}));}}]});
const module={exports:{}};
new Function('module','exports','require',built.outputFiles[0].text)(module,module.exports,createRequire(import.meta.url));
const {powerWindow,powerPaths,FacilityPower}=module.exports;
const readings=[['2025-01-01T00:00:00Z',100,1,'local','MST'],['2025-01-01T01:00:00Z',null,2,'local','MST'],['2025-01-01T02:00:00Z',200,3,'local','MST']];
test('missing hours break chart lines and remain null in the selected window',()=>{
 assert.equal(powerPaths(readings,200,100).length,2);
 assert.equal(powerWindow(readings,'2025-01-01')[1][1],null);
 assert.equal(powerPaths([readings[1],readings[1]],200,100).length,0);
});
test('window uses UTC calendar boundaries and keeps zero readings',()=>{
 const rows=[['2025-01-01T23:00:00Z',0,1,'',''],['2025-01-02T00:00:00Z',10,2,'','']];
 assert.deepEqual(powerWindow(rows,'2025-01-01'),[rows[0]]);
 assert.deepEqual(powerWindow(rows,''),[]);
 assert.deepEqual(powerWindow(readings,'2025-01-08'),[]);
});
test('real power view preserves scope, period, missing values and retry recovery',async()=>{
 const series={series_id:'test',site_id:'test-site',label:'Historical HPC meter',unit:'kW',measurement_boundary:'Meter boundary not specified',methodology_note:'Historical readings; no whole-building claim.',time_basis:'Explicit MST offsets',readings_path:'power/test.json',observed_start:'2025-01-01T00:00:00Z',observed_end:'2025-01-01T02:00:00Z',measured_count:2,missing_count:1,source_url:'https://example.gov/data.xlsx',source_locator:'Sheet1',retrieved_at:'2026-11-04T00:00:00Z',checked_at:'2026-11-04T00:00:00Z',collection_status:'current',license:'Public fixture'};
 globalThis.__powerFixture={release_id:'test',built_at:'2026-11-04T00:00:00Z',sites:[{site_id:'test-site',name:'Fixture site',address:{street:'1 Public Road'},reporting_boundary:'HPC meter',identity_evidence:[]}],series:[series],demands:[{site_id:'test-site',demand_id:'d1',value:12.6,unit:'kW',reporting_year:2024,whole_facility:false,note:'One meter; not whole-building peak.',source_url:'https://example.gov/report',source_locator:'native field'}],coverage:{power_reporting_sites:1}};
 globalThis.__meterFixture={...series,readings};globalThis.__failMeter=true;globalThis.__powerExplainRequests=[];
 let tree;await act(async()=>{tree=TestRenderer.create(React.createElement(FacilityPower));});
 let text=JSON.stringify(tree.toJSON());assert.match(text,/2024/);assert.match(text,/Largest individual meter entry/);assert.match(text,/"1"," missing"/);assert.match(text,/do not provide live readings/);
 const button=()=>tree.root.findByProps({className:'power-chart'}).findAllByType('button')[0];await act(async()=>button().props.onClick());assert.match(JSON.stringify(tree.toJSON()),/could not be loaded/);
 globalThis.__failMeter=false;await act(async()=>button().props.onClick());text=JSON.stringify(tree.toJSON());assert.match(text,/Missing/);assert.match(text,/100–200 kW/);assert.match(text,/2025-01-01/);assert.match(text,/Open all published readings/);
 assert.equal(globalThis.__powerExplainRequests.length,0,'reading a chart causes no paid explanation');
 const explain=tree.root.findAllByType('button').find(b=>b.children.join('')==='Explain this power dataset');
 await act(async()=>explain.props.onClick());assert.deepEqual(globalThis.__powerExplainRequests,[{release_id:'test',subject:{kind:'power_site',id:'test-site'}}]);
 await act(async()=>tree.unmount());
});
