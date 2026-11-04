import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { build } from 'esbuild';
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';

// Exercise the real atlas with deterministic records and a map stub.
const stubs = {
  name: 'atlas-stubs',
  setup(builder) {
    builder.onResolve({ filter: /^\.\.\/api$/ }, () => ({ path: 'api', namespace: 'stub' }));
    builder.onResolve({ filter: /^\.\.\/components\/AtlasMap$/ }, () => ({ path: 'map', namespace: 'stub' }));
    builder.onLoad({ filter: /.*/, namespace: 'stub' }, ({ path }) => ({
      loader: 'js', contents: path === 'api' ? `
        export async function getFacilityIndex() { return {source:'api', data:[
          {id:'echo:1',name:'Alpha',address_line:'1 MAIN ST',city:'AUSTIN',state:'TX',zip:'78701',registry_id:'1',site_id:'one'},
          {id:'echo:3',name:'Charlie',address_line:'1 MAIN ST',city:'AUSTIN',state:'TX',zip:'78701',registry_id:'3',site_id:'one'},
          {id:'echo:2',name:'Bravo',address_line:'2 MAIN ST',city:'DENVER',state:'CO',zip:'80202',registry_id:'2',site_id:'two'}
        ]}; }
        export async function getFacilityPayload() { throw new Error('unavailable'); }
      ` : `import React from 'react'; export function AtlasMap({selectedId}) {
        return React.createElement('div', {'data-map-selected': selectedId || ''});
      }`,
    }));
  },
};
const bundle = await build({
  entryPoints: [new URL('../src/pages/AtlasPage.tsx', import.meta.url).pathname],
  bundle: true, write: false, platform: 'node', format: 'cjs',
  external: ['react', 'react/jsx-runtime'], plugins: [stubs],
});

test('a sibling record remains visible when its site matches the search', async () => {
  let selected = 'echo:1';
  let root;
  const props = () => ({initialQuery: '', selectedId: selected, onSelectFacility: (id) => { selected = id; }});
  await act(async () => { root = TestRenderer.create(React.createElement(AtlasPage, props())); });
  act(() => root.root.findByProps({type:'search'}).props.onChange({target:{value:'Alpha'}}));
  const sibling = root.root.findAllByType('button').find((node) => node.children.join('').includes('Charlie'));
  assert.ok(sibling, 'sibling record is available in selected-site details');
  await act(async () => { sibling.props.onClick(); root.update(React.createElement(AtlasPage, props())); });
  assert.equal(selected, 'echo:3');
  assert.ok(root.root.findAllByType('h2').some((node) => node.children.join('') === 'Charlie'));
  assert.equal(root.root.findAllByType('h2').some((node) => node.children.join('') === 'Selected record is outside these filters.'), false);
  act(() => root.unmount());
});
const module = {exports:{}};
new Function('module', 'exports', 'require', bundle.outputFiles[0].text)(module, module.exports, createRequire(import.meta.url));
const { AtlasPage } = module.exports;

test('filtering a selection hides its map detail without clearing navigation selection', async () => {
  const selections = [];
  let root;
  await act(async () => {
    root = TestRenderer.create(React.createElement(AtlasPage, {
      initialQuery: '', selectedId: 'echo:1', onSelectFacility: (id) => selections.push(id),
    }));
  });
  const mapSelected = () => root.root.findAllByType('div').find((node) => Object.hasOwn(node.props, 'data-map-selected')).props['data-map-selected'];
  assert.equal(mapSelected(), 'echo:1');
  const search = root.root.findByProps({type: 'search'});
  act(() => search.props.onChange({target:{value:'Bravo'}}));
  assert.equal(mapSelected(), '');
  assert.deepEqual(selections, []);
  assert.equal(root.root.findAllByType('h2').some((node) => node.children.join('') === 'Selected record is outside these filters.'), true);
  act(() => search.props.onChange({target:{value:''}}));
  assert.equal(mapSelected(), 'echo:1');
  assert.deepEqual(selections, []);
  act(() => root.unmount());
});
