import {test} from 'node:test';
import assert from 'node:assert/strict';
import {createRequire} from 'node:module';
import {build} from 'esbuild';
import React from 'react';
import TestRenderer, {act} from 'react-test-renderer';

const bundle=await build({stdin:{contents:"export {ExplainPanel} from './src/components/ExplainPanel'; export {DisclosureExplorer} from './src/components/FacilityDisclosures'; export {RecoveredPermits} from './src/components/RecoveredPermits';",resolveDir:process.cwd(),loader:'tsx'},bundle:true,write:false,platform:'node',format:'cjs',external:['react','react/jsx-runtime'],loader:{'.css':'empty'},plugins:[{name:'explain-stub',setup(b){b.onResolve({filter:/^\.\.\/api$/},()=>({path:'api',namespace:'stub'}));b.onLoad({filter:/.*/,namespace:'stub'},()=>({contents:'export const getExplain = (request,template,options) => globalThis.__askExplain(request,template,options);'}));}}]});
const module={exports:{}};new Function('module','exports','require',bundle.outputFiles[0].text)(module,module.exports,createRequire(import.meta.url));
const {ExplainPanel,DisclosureExplorer,RecoveredPermits}=module.exports;
const request=(id='echo:1',release='r1')=>({release_id:release,subject:{kind:'facility',id}});
const props=(id='echo:1',release='r1')=>({payload:{facility:{id},release_id:release},template:`Source summary for ${id}`,enabled:true});
const reply=(input,text='Checked explanation.')=>({...input,text,source:'model',degraded:false,citations:[{fact_id:'f1',label:'Original permit',url:'https://example.gov/permit.pdf',locator:'page 3',sha256:'a'.repeat(64)}],qualifiers:['Permit condition; not an operating measurement.'],cited_facts:[{id:'f1',text:'The publisher reports a permit condition.',scope:'permit'}],fact_ids:['f1'],validation:{level:'numerals_citations_and_qualifiers',label:'Numbers, citations and scope qualifiers checked'}});
const output=tree=>JSON.stringify(tree.toJSON());

test('explicit click uses the displayed release and keeps citation labels and source wording inspectable',async()=>{
 const calls=[];globalThis.__askExplain=async(input)=>{calls.push(input);return reply(input);};let tree;
 await act(async()=>{tree=TestRenderer.create(React.createElement(ExplainPanel,props()));});
 assert.equal(calls.length,0);assert.match(output(tree),/Calculator changes are not included/);
 await act(async()=>tree.root.findByType('button').props.onClick());
 assert.deepEqual(calls,[request()]);assert.match(output(tree),/Original permit/);assert.match(output(tree),/page 3/);assert.match(output(tree),/The publisher reports a permit condition/);assert.match(output(tree),/NUMERALS & SOURCES CHECKED/);assert.doesNotMatch(output(tree),/fully verified|EVIDENCE SELECTED/);
 await act(async()=>tree.unmount());
});

for(const change of ['subject','release'])test(`in-flight ${change} changes discard the previous answer and preserve the new target`,async()=>{
 const pending=[];globalThis.__askExplain=(input,template,options)=>new Promise(resolve=>pending.push({input,options,resolve}));let tree;
 await act(async()=>{tree=TestRenderer.create(React.createElement(ExplainPanel,props()));});
 let oldPromise;act(()=>{oldPromise=tree.root.findByType('button').props.onClick();});
 const next=change==='subject'?props('echo:2'):props('echo:1','r2');
 await act(async()=>tree.update(React.createElement(ExplainPanel,next)));
 assert.equal(pending[0].options.signal.aborted,true);assert.doesNotMatch(output(tree),/Preparing explanation/);
 await act(async()=>{pending[0].resolve(reply(pending[0].input,'Old subject response'));await oldPromise;});
 assert.doesNotMatch(output(tree),/Old subject response/);
 let nextPromise;act(()=>{nextPromise=tree.root.findByType('button').props.onClick();});
 await act(async()=>{pending[1].resolve(reply(pending[1].input,'New correct response'));await nextPromise;});
 assert.match(output(tree),/New correct response/);await act(async()=>tree.unmount());
});

test('rapid duplicate clicks cannot cause duplicate paid requests',async()=>{
 let resolve;let count=0;globalThis.__askExplain=input=>{count++;return new Promise(done=>{resolve=()=>done(reply(input));});};let tree;
 await act(async()=>{tree=TestRenderer.create(React.createElement(ExplainPanel,props()));});
 const click=tree.root.findByType('button').props.onClick;let first;
 act(()=>{first=click();void click();void click();});assert.equal(count,1);
 await act(async()=>{resolve();await first;});await act(async()=>tree.unmount());
});

test('unmount aborts the request and a mismatched response never becomes visible',async()=>{
 let signal;let resolve;globalThis.__askExplain=(input,template,options)=>{signal=options.signal;return new Promise(done=>{resolve=()=>done(reply({...input,subject:{kind:'power_site',id:'wrong'}},'Wrong report'));});};let tree;
 await act(async()=>{tree=TestRenderer.create(React.createElement(ExplainPanel,props()));});let promise;
 act(()=>{promise=tree.root.findByType('button').props.onClick();});await act(async()=>{resolve();await promise;});
 assert.doesNotMatch(output(tree),/Wrong report/);assert.match(output(tree),/did not match the displayed evidence/);
 act(()=>{promise=tree.root.findByType('button').props.onClick();});await act(async()=>tree.unmount());assert.equal(signal.aborted,true);resolve();await promise;
});

test('annual location action is explicit, release-pinned and independent of table filters',async()=>{
 const calls=[];globalThis.__askExplain=async input=>{calls.push(input);return reply(input);};
 const row=(metric)=>({observation_id:metric,metric,label:metric,value:10,unit:metric==='electricity_consumption'?'MWh':'million liters',data_year:2024,reporting_scope:'campus',reporting_basis:'operator_reported',publisher:'Meta',source_url:'https://example.gov/report.pdf',source_sha256:'a'.repeat(64),period_start:'2024-01-01T00:00:00Z',period_end:'2025-01-01T00:00:00Z',methodology_note:'Annual campus report.'});
 const location={disclosure_location_id:'meta:henrico-va',location_label:'Henrico',operator:'Meta',reporting_scope:'campus',observations:[row('electricity_consumption'),row('water_withdrawal')],record_links:[]};let tree;
 await act(async()=>{tree=TestRenderer.create(React.createElement(DisclosureExplorer,{data:{release_id:'r-location',locations:[location],observation_count:2},initialLocationId:location.disclosure_location_id}));});
 assert.equal(calls.length,0);assert.match(output(tree),/filters do not change the explanation/);
 await act(async()=>tree.root.findByType('select').props.onChange({target:{value:'water'}}));
 const button=tree.root.findAllByType('button').find(b=>b.children.join('')==='Explain this reporting location');
 await act(async()=>button.props.onClick());assert.deepEqual(calls,[{release_id:'r-location',subject:{kind:'disclosure_location',id:'meta:henrico-va'}}]);
 await act(async()=>tree.unmount());
});

test('missing release disables the action and changing displayed template never calls the model',async()=>{
 let calls=0;globalThis.__askExplain=async()=>{calls++;throw Error('unexpected');};let tree;
 await act(async()=>{tree=TestRenderer.create(React.createElement(ExplainPanel,{payload:{facility:{id:'echo:1'}},template:'Source summary',enabled:true}));});
 assert.equal(tree.root.findByType('button').props.disabled,true);await act(async()=>tree.root.findByType('button').props.onClick());assert.equal(calls,0);
 await act(async()=>tree.update(React.createElement(ExplainPanel,{...props(),template:'Updated local calculator template'})));assert.equal(calls,0);
 await act(async()=>tree.unmount());
});


test('all recovered permits are independent document actions and only exact inventory links appear',async()=>{
 const calls=[];globalThis.__askExplain=async input=>{calls.push(input);return reply(input);};
 const registrations=['11541-3','11790-1','72374-4','73170-1','73200-3','73363-2','73370-12','73567-1','73643-5','73977-2','74060-2','74094-1','81609-5'];
 const documents=registrations.map((registration,index)=>({registration,site:'Permit site '+registration,county:'Virginia county',url:'https://example.gov/'+registration+'.pdf',representation:'publisher_pdf',content_sha256:'a'.repeat(64),issued_at:'2013-08-19',retrieved_at:'2026-11-05T02:00:00Z',inventory_record_ids:index===1?['echo:11790']:index===11?['echo:74094']:[],conditions:[],equipment_claims:[],recovery_review:{review_version:'va-reviewed-pdf-1',document_extent:registration==='73643-5'?'correspondence':'full_permit',supersession_review:{current_legal_effectiveness:'unknown',statements:[]}}}));
 let tree;await act(async()=>{tree=TestRenderer.create(React.createElement(RecoveredPermits,{documents,releaseId:'permit-release',focusedRegistration:'73643-5'}));});
 assert.equal(calls.length,0);
 assert.equal(tree.root.findAllByType('button').length,13);
 const links=tree.root.findAllByType('a').filter(link=>link.props.href.startsWith('#/public/'));
 assert.equal(links.length,2);
 assert.match(output(tree),/No verified inventory match/);
 assert.match(output(tree),/independent document records/);
 const target=tree.root.findByProps({id:'permit-73643-5'});
 assert.equal(target.props.open,true);
 assert.equal(tree.root.findByProps({id:'permit-73567-1'}).props.open,false);
 const action=target.findAllByType('button').find(button=>button.children.join('')==='Explain this permit document');
 await act(async()=>action.props.onClick());
 assert.deepEqual(calls,[{release_id:'permit-release',subject:{kind:'permit_document',id:'73643-5'}}]);
 assert.match(output(tree),/do not by themselves establish current legal limits/);
 await act(async()=>tree.unmount());
});

test('unavailable or older permit collections create no fabricated document or model action',async()=>{
 let calls=0;globalThis.__askExplain=async()=>{calls++;};
 let tree;await act(async()=>{tree=TestRenderer.create(React.createElement(RecoveredPermits,{documents:[],releaseId:'older-release',focusedRegistration:'73643-5'}));});
 assert.equal(tree.toJSON(),null);assert.equal(calls,0);
 await act(async()=>tree.unmount());
});

test('permit query navigation scrolls to the expanded row after the shell reset without calling the model',async()=>{
 const oldWindow=globalThis.window;
 const frames=new Map();const scrolls=[];let frameId=0;let calls=0;
 globalThis.window={requestAnimationFrame(fn){frames.set(++frameId,fn);return frameId;},cancelAnimationFrame(id){frames.delete(id);},scrollTo(){scrolls.push('page-top');}};
 globalThis.__askExplain=async()=>{calls++;throw Error('navigation must not explain');};
 const documents=['11790-1','73643-5','81609-5'].map(registration=>({registration,site:'Permit '+registration,url:'https://example.gov/'+registration+'.pdf',inventory_record_ids:[],conditions:[],equipment_claims:[],recovery_review:{review_version:'va-reviewed-pdf-1',document_extent:'full_permit',supersession_review:{current_legal_effectiveness:'unknown',statements:[]}}}));
 function Shell({permit}) {
  React.useEffect(()=>window.scrollTo({top:0}),[permit]);
  return React.createElement(RecoveredPermits,{documents,releaseId:'same-release',focusedRegistration:permit});
 }
 const flush=()=>{const pending=[...frames.values()];frames.clear();for(const callback of pending)callback();};
 let tree;
 try {
  await act(async()=>{tree=TestRenderer.create(React.createElement(Shell,{permit:'11790-1'}),{createNodeMock(node){return node.type==='details'&&node.props.id?.startsWith('permit-')?{scrollIntoView(){scrolls.push(node.props.id);}}:null;}});});
  assert.deepEqual(scrolls,['page-top']);flush();assert.equal(scrolls.at(-1),'permit-11790-1');
  await act(async()=>tree.update(React.createElement(Shell,{permit:'73643-5'})));
  await act(async()=>tree.update(React.createElement(Shell,{permit:'81609-5'})));
  assert.equal(tree.root.findByProps({id:'permit-81609-5'}).props.open,true);
  assert.equal(tree.root.findByProps({id:'permit-11790-1'}).props.open,false);
  assert.equal(tree.root.findByProps({id:'permit-73643-5'}).props.open,false);
  assert.equal(scrolls.at(-1),'page-top');flush();
  assert.equal(scrolls.at(-1),'permit-81609-5');assert.ok(!scrolls.includes('permit-73643-5'));
  assert.equal(calls,0);
  await act(async()=>tree.update(React.createElement(Shell,{permit:'73643-5'})));
  await act(async()=>tree.unmount());tree=null;assert.equal(frames.size,0);
 } finally {if(tree)await act(async()=>tree.unmount());globalThis.window=oldWindow;}
});
