import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { fileURLToPath } from 'node:url';
import { build } from 'esbuild';
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';
import { renderToStaticMarkup } from 'react-dom/server';

const bundle = await build({
  stdin: { contents: `export {AtlasPage,FacilityDetails} from './src/pages/AtlasPage'; export {PublicCardPage} from './src/pages/PublicCardPage'; export {normaliseFacilityPayload} from './src/normalise';`, resolveDir: fileURLToPath(new URL('../', import.meta.url)), loader: 'tsx' },
  bundle: true, write: false, platform: 'node', format: 'cjs', define: { 'import.meta.env': '{}' }, external: ['react', 'react/jsx-runtime'],
  plugins: [{name: 'awareness-evidence-data', setup(builder) {
    builder.onResolve({filter: /^\.\.\/api$/}, () => ({path:'api',namespace:'stub'}));
    builder.onResolve({filter: /^\.\.\/components\/AtlasMap$/}, () => ({path:'map',namespace:'stub'}));
    builder.onResolve({filter: /\.css$/}, () => ({path:'css',namespace:'stub'}));
    builder.onLoad({filter: /.*/,namespace:'stub'}, ({path}) => ({loader:'js',contents:path === 'css' ? '' : path === 'map' ? `import React from 'react'; export function AtlasMap(){return React.createElement('div')}` : `
      export async function getFacilityIndex(){return {data:[{id:'echo:1',name:'First source record',address_line:'1 Main St',city:'Austin',state:'TX',zip:'78701',registry_id:'1'}],source:'api'}};
      export async function getFacilityPayload(){globalThis.__awarenessPayloadRequests++;return {data:globalThis.__awarenessCard,source:'api',releaseId:'release-a'}};
      export async function getDrawer(){throw new Error('No drawer request expected')};
      export async function getExplain(){throw new Error('No model request expected')};
      export function explainAvailable(){return false};
    `}));
  }}],
});
const module = {exports:{}};
new Function('module','exports','require',bundle.outputFiles[0].text)(module,module.exports,createRequire(import.meta.url));
const {AtlasPage, FacilityDetails, PublicCardPage, normaliseFacilityPayload} = module.exports;

function fixture() {
  const payload = normaliseFacilityPayload({
    release_id:'release-a', as_of:'2026-11-03T12:00:00Z', facility:{id:'echo:1',name:'Source record one',source_label:'EPA ECHO'},
    grid:{subregion:'ERCT',subregion_name:'Texas',rate_lb_per_mwh:null,data_vintage:'2023'},
    claims:[
      {id:'power-mw',metric:'power_mw',label:'Facility electricity demand',value:'NOT_PUBLISHED',scope:'facility',renderable:true,note:'No observation in the collected sources.'},
      {id:'diesel-mw',metric:'diesel_mw',label:'Reported generators',value:0,unit:'MW',scope:'facility',renderable:true,citations:[{url:'https://example.gov/document',quote:'No generating capacity reported.'}]},
    ], verdict:{sentence:'A public source record.'},
  });
  payload.disclosures = [{
    location:{disclosure_location_id:'campus:one',location_label:'Related campus',reporting_scope:'campus',operator:'Example operator',record_links:[],observations:[{
      observation_id:'energy-2024',metric:'electricity_consumption',label:'Electricity use',reporting_basis:'reported_energy',reporting_scope:'campus',data_year:2024,value:987654,unit:'MWh',publisher:'Example operator',source_url:'https://example.com/report',source_sha256:'a'.repeat(64),methodology_note:'Annual total for the campus.',period_start:'2024-01-01',period_end:'2025-01-01',retrieved_at:'2026-11-03T12:00:00Z',
    }]},
    link:{record_id:'echo:1',relationship:'campus_member',inventory_allocation:false,verification_status:'verified',reason:'Reviewed campus association.'},
  }];
  return payload;
}

test('map starts with the collection explanation rather than treating the first source row as a representative site', async () => {
  const selections=[];
  globalThis.__awarenessPayloadRequests=0;
  let root;
  await act(async () => {root=TestRenderer.create(React.createElement(AtlasPage,{initialQuery:'',selectedId:undefined,onSelectFacility:id=>selections.push(id)}));});
  assert.deepEqual(selections,[null]);
  assert.equal(globalThis.__awarenessPayloadRequests,0);
  assert.match(JSON.stringify(root.toJSON()),/What can public records tell us about this place/);
  act(()=>root.unmount());
});

test('map previews related annual evidence without converting or allocating campus energy to record demand', () => {
  const html=renderToStaticMarkup(React.createElement(FacilityDetails,{payload:fixture()}));
  assert.match(html,/href="#\/reported\?location=campus%3Aone"/);
  assert.match(html,/2024/);
  assert.match(html,/Related totals; no allocation to this record/);
  assert.match(html,/Facility electricity demand \(MW\)/);
  assert.match(html,/Not available in our sources/);
  assert.doesNotMatch(html,/987,?654/,'campus energy is not a record-level demand value');
});

test('public record promotes linked reports while retaining numeric zero and compact, closed collection gaps', async () => {
  globalThis.__awarenessCard=fixture();
  globalThis.window={addEventListener(){},removeEventListener(){}};
  let root;
  await act(async () => {root=TestRenderer.create(React.createElement(PublicCardPage,{facilityId:'echo:1'}));});
  const sections=root.root.findAllByType('section');
  const linked=sections.findIndex(node=>node.props['aria-labelledby']==='facility-disclosures-title');
  const evidence=sections.findIndex(node=>node.props['aria-labelledby']==='facility-evidence-title');
  assert.ok(linked>=0 && linked<evidence,'related annual data precedes record evidence');
  const gaps=root.root.findByProps({className:'record-gaps'});
  assert.equal(gaps.props.open,undefined);
  assert.equal(gaps.findAllByType('button').filter(node=>node.props['data-metric']==='power_mw').length,1,'missing evidence keeps its source drawer entry');
  const chips=root.root.findAllByType('button').filter(node=>(node.props.className||'').split(' ').includes('chip'));
  assert.equal(chips.length,1,'missing claims no longer occupy headline tiles');
  assert.equal(chips[0].props['data-metric'],'diesel_mw');
  assert.equal(chips[0].findByProps({className:'chip__num'}).children.join(''),'0','a cited numeric zero remains visible');
  assert.match(JSON.stringify(root.toJSON()),/Related location totals/);
  act(()=>root.unmount());
});
