import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { build } from 'esbuild';
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';

// Exercise the real search page with an index entry, without a network call.
const stubs = {
  name: 'search-page-stubs',
  setup(builder) {
    builder.onResolve({ filter: /^\.\.\/api$/ }, () => ({ path: 'api', namespace: 'stub' }));
    builder.onLoad({ filter: /.*/, namespace: 'stub' }, () => ({
      loader: 'js',
      contents: `export async function getFacilityIndex() { return {source:'api',data:[
        {id:'echo:1',name:'Example facility',address_line:'1 MAIN ST',city:'AUSTIN',state:'TX',zip:'78701',registry_id:'1',site_id:'one'}
      ]}; } export async function resolveAddress() { throw new Error('unexpected geocode'); }`,
    }));
  },
};
const bundle = await build({
  entryPoints: [new URL('../src/pages/SearchPage.tsx', import.meta.url).pathname],
  bundle: true, write: false, platform: 'node', format: 'cjs',
  external: ['react', 'react/jsx-runtime'], plugins: [stubs],
});
const module = { exports: {} };
new Function('module', 'exports', 'require', bundle.outputFiles[0].text)(
  module, module.exports, createRequire(import.meta.url),
);

test('facility results are links inside list items', async () => {
  let root;
  await act(async () => {
    root = TestRenderer.create(React.createElement(module.exports.SearchPage, {initialQuery: ''}));
  });
  const list = root.root.findByProps({role: 'list', 'aria-label': 'Matching facility records'});
  const item = list.findByProps({role: 'listitem'});
  const link = item.findByType('a');
  assert.equal(link.props.href, '#/public/echo%3A1');
  assert.equal(link.props.role, undefined, 'native link semantics must remain available');
  assert.equal(link.findByProps({className:'rrow__name'}).children[0], 'Example facility');
  act(() => root.unmount());
});
