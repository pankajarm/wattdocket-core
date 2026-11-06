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


test('combined generator-hours and reviewed exceptions retain their specific scope', () => {
  const payload=normaliseFacilityPayload({facility:{id:'echo:81609',name:'Record',permit_limits:[{registration:'81609-5',metric:'generator_group_hours_limit',value:2680,unit:'hours/year',reviewed_condition_scope:'Combined operating hours of all twelve generators; not each generator',reviewed_period:'annual',representation:'reviewed_pdf_transcription'},{registration:'81609-5',metric:'mcrt_hours_limit',value:100,unit:'hours/year',reviewed_operating_purpose:'Maintenance and readiness testing; additional documented hours may be allowed by the quoted exception'}]},claims:[{id:'power-mw',metric:'power_mw',value:'NOT_PUBLISHED'}]});
  const html=renderToStaticMarkup(React.createElement(PermitLimitsPanel,{payload}));
  assert.match(html,/Combined generator-hours condition/);
  assert.match(html,/Combined operating hours of all twelve generators; not each generator/);
  assert.match(html,/additional documented hours may be allowed/);
  assert.match(html,/quoted baseline <b>100<\/b>/);
  assert.doesNotMatch(html,/no more than <b>100<\/b>/);
  assert.match(html,/transcribed and checked against the original PDF image/);
});

test('changed permit bytes visibly withdraw earlier numeric review approval', () => {
  const document=reviewedPermit({recovery_review:{review_version:'va-reviewed-pdf-1',status:'review_required'}});
  const payload=normaliseFacilityPayload({facility:{id:'echo:1',name:'Record',permit_reviews:[document]},claims:[{id:'power-mw',metric:'power_mw',value:'NOT_PUBLISHED'}]});
  const html=renderToStaticMarkup(React.createElement(PermitLimitsPanel,{payload}));
  assert.match(html,/document has changed/);
  assert.match(html,/numeric review approval is withdrawn/);
  assert.doesNotMatch(html,/no more than/);
});



function reviewedPermit(overrides = {}) {
  return {registration:'73977-2',county_fips:'51059',issued_at:'2013-08-19',url:'https://www.deq.virginia.gov/home/showpublisheddocument/25793/638628697085200000',content_sha256:'a'.repeat(64),representation:'publisher_pdf',parse_status:'parsed',retrieved_at:'2026-11-05T02:27:00Z',
    recovery_review:{review_version:'va-reviewed-pdf-1',reviewed_at:'2026-11-05T02:30:00Z',document_extent:'partial_amendment',
      identity_review:{registration:{value:'73977',pdf_page:1},physical_address:{value:'14725 Lee Road, Chantilly, VA 20151',pdf_page:3},mailing_address:{value:'Do not map this mailing address',pdf_page:1}},
      supersession_review:{status:'selected_pages_replaced',current_legal_effectiveness:'unknown',statements:[{pdf_page:3,quote:'This permit page and pages 6 and 10 supersede the corresponding pages of your permit dated April 9, 2012.',replaced_dates:['2012-04-09'],scope:'selected permit pages'}]}},
    ...overrides};
}

test('a recovered partial amendment remains visible without a numeric condition', () => {
  const input={facility:{id:'echo:73977',name:'Aerospace record',permit_reviews:[reviewedPermit()],permit_limits:[]},claims:[{id:'power-mw',metric:'power_mw',value:'NOT_PUBLISHED'}]};
  const payload=normaliseFacilityPayload(input);
  assert.equal(payload.facility.permit_reviews.length,1);
  const html=renderToStaticMarkup(React.createElement(PermitLimitsPanel,{payload}));
  assert.match(html,/PERMIT DOCUMENTS/);
  assert.match(html,/Partial amendment/);
  assert.match(html,/Original publisher PDF/);
  assert.match(html,/Current legal effectiveness is unknown/);
  assert.match(html,/Earlier documents are needed/);
  assert.match(html,/No qualifying numeric condition is shown/);
  assert.match(html,/Physical location stated in document: 14725 Lee Road/);
  assert.match(html,/#page=3/);
  assert.match(html,/This permit page and pages 6 and 10 supersede/);
  assert.doesNotMatch(html,/Do not map this mailing address|no more than|whole-site condition/);
});

test('permit correspondence is not rendered as a complete current permit', () => {
  const document=reviewedPermit({registration:'73643-5',recovery_review:{review_version:'va-reviewed-pdf-1',document_extent:'correspondence'}});
  const payload=normaliseFacilityPayload({facility:{id:'echo:73643',name:'Visa record',permit_reviews:[document]},claims:[{id:'power-mw',metric:'power_mw',value:'NOT_PUBLISHED'}]});
  const html=renderToStaticMarkup(React.createElement(PermitLimitsPanel,{payload}));
  assert.match(html,/Permit correspondence/);
  assert.match(html,/does not provide a complete set of permit conditions/);
  assert.match(html,/Current legal effectiveness is unknown/);
  assert.doesNotMatch(html,/Full permit document|current legal cap/);
});

test('review normalization retains condition review without inventing identity or trusting an unsafe document link', () => {
  const doc=reviewedPermit({url:'javascript:alert(1)',recovery_review:{review_version:'va-reviewed-pdf-1',document_extent:'unsupported',identity_review:{physical_address:{value:42,pdf_page:-1}},supersession_review:{status:'unknown',current_legal_effectiveness:'verified',statements:[null,{quote:'Earlier pages are referenced.',pdf_page:2,replaced_dates:[null,'2012-04-09']}]}}});
  const payload=normaliseFacilityPayload({facility:{id:'echo:1',name:'Record',permit_reviews:[doc],permit_limits:[{registration:'73977-2',metric:'generator_hours_limit',value:500,unit:'hours/year',recovery_review:doc.recovery_review}]},claims:[{id:'power-mw',metric:'power_mw',value:'NOT_PUBLISHED'}]});
  assert.equal(payload.facility.permit_limits[0].recovery_review.document_extent,'unknown');
  assert.equal(payload.facility.permit_reviews[0].recovery_review.supersession_review.current_legal_effectiveness,'unknown');
  const html=renderToStaticMarkup(React.createElement(PermitLimitsPanel,{payload}));
  assert.doesNotMatch(html,/javascript:|Physical location stated in document|No qualifying numeric condition/);
  assert.match(html,/500/);
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

test('a verified regulator site preserves shutdown status without making its permit current', () => {
  const document=reviewedPermit({registration:'11541-3',
    site_identity_review:{review_version:'va-site-1',status:'regulator_site_verified',physical_address:{street:'A regulator physical address',city:'Lebanon',state:'VA'},site_status:{air_operating_status:'Temporarily Shutdown'},note:'The EPA program identity is verified; this does not merge other records.',evidence:[{url:'https://gisdata.deq.virginia.gov/arcgis/rest/services/public/EDMA/MapServer/294',locator:'DEQ air-site registration',receipt:{retrieved_at:'2026-11-05T04:00:00Z'}}]},
    permit_history_review:{review_version:'va-history-1',status:'verified',registry_listing:{status:'latest_listed_for_registration',registry_as_of:'2026-11-02'},legal_effectiveness:{status:'unknown',note:'No explicit current-condition confirmation is in the checked records.'}}});
  const payload=normaliseFacilityPayload({facility:{id:'echo:1',name:'Record',permit_reviews:[document]},claims:[{id:'power-mw',metric:'power_mw',value:'NOT_PUBLISHED'}]});
  const html=renderToStaticMarkup(React.createElement(PermitLimitsPanel,{payload}));
  assert.match(html,/Site verified in the regulator/);
  assert.match(html,/Temporarily Shutdown/);
  assert.match(html,/Physical address in the regulator record: A regulator physical address/);
  assert.match(html,/Latest action listed for this registration/);
  assert.match(html,/Current legal effectiveness is unknown/);
  assert.match(html,/not confirmation that every permit condition remains effective/);
  assert.doesNotMatch(html,/confirms this document as current/);
});

test('later campus documents do not silently supersede a different building condition', () => {
  const document=reviewedPermit({registration:'73370-12',permit_history_review:{review_version:'va-history-1',status:'verified',registry_listing:{status:'listed_with_later_document',current_rows:[{registration:'73370-12',url:'https://example.gov/acc7.pdf',issued_at:'2013-01-01'},{registration:'73370-18',url:'https://example.gov/acc10.pdf',issued_at:'2017-01-01'}]},legal_effectiveness:{status:'unknown'},events:[{type:'amends',scope:'Later documents describe other buildings; replacement of the ACC7 condition is not established.',evidence:[{url:'https://example.gov/acc10.pdf',pdf_page:2,quote:'ACC10 equipment only.'}]}]}});
  const payload=normaliseFacilityPayload({facility:{id:'echo:1',name:'Record',permit_reviews:[document]},claims:[{id:'power-mw',metric:'power_mw',value:'NOT_PUBLISHED'}]});
  const html=renderToStaticMarkup(React.createElement(PermitLimitsPanel,{payload}));
  assert.match(html,/Other, later documents share this registration/);
  assert.match(html,/replacement of the ACC7 condition is not established/);
  assert.match(html,/https:\/\/example.gov\/acc10.pdf#page=2/);
  assert.doesNotMatch(html,/explicitly replaces this document/);
});

test('changed history cannot retain an earlier current-effectiveness assertion', () => {
  const document=reviewedPermit({permit_history_review:{review_version:'va-history-1',status:'review_required',legal_effectiveness:{status:'source_confirmed_current',scope:'entire_document',evidence:[{url:'https://example.gov/status'}]}}});
  const payload=normaliseFacilityPayload({facility:{id:'echo:1',name:'Record',permit_reviews:[document]},claims:[{id:'power-mw',metric:'power_mw',value:'NOT_PUBLISHED'}]});
  assert.equal(payload.facility.permit_reviews[0].permit_history_review.legal_effectiveness.status,'unknown');
  const html=renderToStaticMarkup(React.createElement(PermitLimitsPanel,{payload}));
  assert.match(html,/source record changed/);
  assert.doesNotMatch(html,/confirms this document as current/);
});

test('conflicting consolidated and air-program addresses are both visible without silently moving the site', () => {
  const document=reviewedPermit({site_identity_review:{review_version:'va-site-1',status:'regulator_site_verified',physical_address:{street:'1175 North Main Street',city:'Harrisonburg',state:'VA'},inventory_links:[{record_id:'echo:110040513209',relationship:'same_regulator_record',verification_status:'verified',combined_address_conflict:{consolidated_frs_address:'139 TYCO STREET',air_program_address:'1175 NORTH MAIN STREET',deq_physical_address:'1175 North Main Street'}}]}});
  const payload=normaliseFacilityPayload({facility:{id:'echo:110040513209',name:'Record',permit_reviews:[document]},claims:[{id:'power-mw',metric:'power_mw',value:'NOT_PUBLISHED'}]});
  const html=renderToStaticMarkup(React.createElement(PermitLimitsPanel,{payload}));
  assert.match(html,/139 TYCO STREET/);assert.match(html,/1175 NORTH MAIN STREET/);
  assert.match(html,/Address conflict in the source records/);
  assert.match(html,/Map placement and regional grid assignment are withheld/);
});
