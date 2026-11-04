import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { renderToStaticMarkup } from 'react-dom/server';

const bundle = await build({
  stdin: { contents: `export {PublicCardPage} from './src/pages/PublicCardPage'; export {DemandChart} from './src/pages/GridPage'; export {PermitLimitsPanel} from './src/components/PermitLimitsPanel'; export {SourceDocuments} from './src/components/SourceDocuments'; export {EvidenceFreshness} from './src/components/EvidenceFreshness'; export {MethodBars} from './src/components/MethodBars'; export {ComplianceStrip} from './src/components/ComplianceStrip'; export {operatorCsv,operatorJson} from './src/operatorExport'; export {normaliseFacilityPayload} from './src/normalise';`, resolveDir: fileURLToPath(new URL('../', import.meta.url)), loader: 'tsx' },
  bundle: true, write: false, platform: 'node', format: 'cjs', define: { 'import.meta.env': '{}' }, external: ['react', 'react/jsx-runtime'],
  plugins: [{ name: 'page-data', setup(builder) {
    builder.onResolve({ filter: /^\.\.\/api$/ }, () => ({ path:'api', namespace:'stub' }));
    builder.onResolve({ filter: /\.css$/ }, () => ({ path:'css', namespace:'stub' }));
    builder.onLoad({ filter: /.*/, namespace:'stub' }, ({path}) => ({ loader:'js', contents: path === 'css' ? '' : `
      export async function getFacilityPayload(){return {data:globalThis.__productionCard,source:'api',releaseId:'release-a'}};
      export async function getDrawer(){throw new Error('No drawer request expected')};
      export async function getExplain(){throw new Error('No model request expected')};
      export function explainAvailable(){return true};
      export async function getGridIndex(){return {regions:[]}};
      export async function getGridRegion(){throw new Error('No region request expected')};
    ` }));
  }}],
});
const module = {exports:{}};
new Function('module','exports','require',bundle.outputFiles[0].text)(module,module.exports,createRequire(import.meta.url));
const {PublicCardPage, DemandChart, PermitLimitsPanel, SourceDocuments, EvidenceFreshness, MethodBars, ComplianceStrip, operatorCsv, operatorJson, normaliseFacilityPayload} = module.exports;

test('a facility card separates unavailable operating data from the chosen load scenario', async () => {
  const missing = (id, metric, label) => ({id,metric,label,value:'NOT_PUBLISHED',scope:'facility',renderable:true});
  globalThis.__productionCard = normaliseFacilityPayload({
    grid:{subregion:'NWPP',subregion_name:'Northwest',rate_lb_per_mwh:635.2668276,data_vintage:'2023'},release_id:'release-a',as_of:'2026-11-03T12:00:00Z',facility:{id:'echo:123',name:'Facility One',source_label:'EPA ECHO',record_summary:[{label:'Registry status',value:'Active'}]},
    claims:[missing('power-mw','power_mw','Facility power'),missing('facility-carbon','carbon_t','Facility carbon'),missing('water-gal','water_gal','Facility water'),missing('diesel-mw','diesel_mw','Reported generators'),
      {id:'scenario-power',metric:'power_mw',label:'Reference load',value:10,unit:'MW',scope:'scenario',range_kind:'scenario',renderable:true,calc_id:'ref-1'},
      {id:'scenario-carbon',metric:'carbon_t',label:'Scenario carbon',value:19000,unit:'t CO2e/year',range:{p10:13000,p50:19000,p90:23000},scope:'scenario',range_kind:'scenario',renderable:true,calc_id:'ref-1'}],
    verdict:{sentence:'Public records are available for this site.'},
  });
  globalThis.window = {addEventListener(){},removeEventListener(){}};
  let root;
  await act(async () => {root=TestRenderer.create(React.createElement(PublicCardPage,{facilityId:'echo:123'}));});
  const evidence = root.root.findByProps({'aria-labelledby':'facility-evidence-title'});
  const evidenceText = JSON.stringify(evidence.toJSON?.() ?? evidence.findAllByType('button').map((node)=>node.props['aria-label']));
  assert.doesNotMatch(evidenceText,/Reference load|Scenario carbon/);
  assert.equal(evidence.findAllByType('button').filter((node)=>node.props['data-metric']).length,4);
  const scenario = root.root.findByProps({className:'evidence-scenario'});
  assert.equal(scenario.props.open,undefined,'the scenario is collapsed initially');
  assert.equal(scenario.findAllByType('input').length,4);
  assert.equal(scenario.findAllByType('button').filter((node)=>node.props['data-metric']).length,0,'preset claim chips are not shown as facility results');
  const text = JSON.stringify(root.toJSON());
  assert.match(text,/Registry status/);
  assert.match(text,/illustrative presets/);
  assert.match(text,/2023 annual regional emissions rate/);
  assert.doesNotMatch(text,/90% range|p10–p90/);
  assert.match(text,/Explain this card/);
  act(()=>root.unmount());
});

test('the demand chart leaves a gap where a grid observation is missing', () => {
  const readings = [100,null,140].map((demand_mw,index)=>({interval_start_utc:`2026-11-03T0${index}:00:00Z`,interval_minutes:60,demand_mw}));
  const html = renderToStaticMarkup(React.createElement(DemandChart,{readings}));
  const path = html.match(/class="demand-chart__line" d="([^"]*)"/)[1];
  assert.equal((path.match(/M/g)||[]).length,2);
  assert.equal((path.match(/L/g)||[]).length,0);
  assert.match(html,/Missing intervals are gaps/);
  assert.doesNotMatch(html,/CO2|carbon/);
});

test('permit conditions preserve the quoted scope without inventing a per-generator or whole-site limit', () => {
  const html=renderToStaticMarkup(React.createElement(PermitLimitsPanel,{payload:{facility:{permit_limits:[{metric:'generator_hours_limit',value:100,unit:'hours per consecutive 12 months',scope:'quoted_condition',applies_to_entire_site:false,quote:'The affected units are limited to 100 hours.',page:3,url:'https://example.gov/permit.pdf'}]}}}));
  assert.match(html,/hours per consecutive 12 months/);
  assert.match(html,/whole-site limit is not established/);
  assert.match(html,/The affected units are limited to 100 hours/);
  assert.doesNotMatch(html,/per generator|A site capped/);
});

test('source documents keep drafts and conflicting statements out of numeric facility claims', () => {
  const html=renderToStaticMarkup(React.createElement(SourceDocuments,{documents:[{id:'draft',name:'Technical review',publisher:'Regulator',document_type:'draft',document_date:'2025-12-02',url:'https://example.gov/draft.pdf',content_sha256:'a'.repeat(64),retrieved_at:'2026-11-03T12:00:00Z',note:'Current effectiveness is not verified. Conflicting values appear in this document.',statements:[{quote:'The applicant proposes generators.',page:2}],has_conflicting_values:true}]}));
  assert.match(html,/draft/);
  assert.match(html,/Current effectiveness is not verified/);
  assert.match(html,/not presented as a current operating limit/);
  assert.doesNotMatch(html,/MW nameplate|measured capacity/);
});


test('archival and failed-refresh dates never become a fresh document retrieval', () => {
  const html = renderToStaticMarkup(React.createElement(EvidenceFreshness,{evidence:{freshness:'archived',retrieved_at:null,archived_by_at:'2026-10-29T12:00:00Z',latest_fetch_status:'failed',latest_fetch_at:'2026-11-03T12:00:00Z'}}));
  assert.match(html,/Archived evidence/);
  assert.match(html,/Original retrieval date unavailable/);
  assert.match(html,/Present in the archive by/);
  assert.match(html,/Latest document refresh failed/);
  assert.doesNotMatch(html,/Original retrieval: Nov 3/);
});


test('missing operator values remain null while a computed zero stays visible and exportable', () => {
  const methods = [{key:'location',label:'Computed zero',tonnes:0,note:'Computed with reported factor',tone:'teal'}, {key:'market',label:'Market-based',tonnes:null,note:'Withheld: records unavailable',tone:'amber'}];
  const html = renderToStaticMarkup(React.createElement(MethodBars,{methods,note:'Evidence only'}));
  assert.match(html,/Computed zero: 0 tonnes CO2e/);
  assert.match(html,/Market-based: withheld, not estimated/);
  const missing = {key:'water',label:'Actual water',value:'—',unit:'gal/day',sub:'Not available',pct:null,mark_pct:null,tone:'amber'};
  const gauge = renderToStaticMarkup(React.createElement(ComplianceStrip,{items:[missing],peak:{...missing,key:'peak'}}));
  assert.doesNotMatch(gauge,/gauge__fill|0%/);
  const payload = {facility_id:'echo:1',facility_label:'Facility',as_of:'2026-11-03',calc_version:'2',synthetic_meter_data:false,methods,matched_pct:null,compliance:[missing],peak:missing,levers:[],method:{factor_set_id:'grid',chain:[]},sources:[]};
  const meta={dataSource:'api',exportedAt:'2026-11-03T12:00:00Z'};
  const csv=operatorCsv(payload,meta);
  assert.match(csv,/scenario,Computed zero,0,t CO2e\/yr,illustrative_calculation/);
  assert.match(csv,/facility_accounting,Market-based,,t CO2e\/yr,withheld/);
  const json=JSON.parse(operatorJson(payload,meta));
  assert.equal(json.payload.methods[0].tonnes,0);
  assert.equal(json.payload.methods[1].tonnes,null);
  assert.equal(json.payload.matched_pct,null);
});
