import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { build } from 'esbuild';
import React from 'react';
import { renderToStaticMarkup } from 'react-dom/server';
import TestRenderer, { act } from 'react-test-renderer';

// Render the real application shell and shared navigation. Page bodies are
// stubs so this checks routing/navigation without requiring a browser or map.
const pageStub = {
  name: 'page-stubs',
  setup(builder) {
    builder.onResolve({ filter: /^\.\.?\/auth$/ }, () => ({ path: 'auth', namespace: 'auth-stub' }));
    builder.onLoad({ filter: /.*/, namespace: 'auth-stub' }, () => ({ contents: 'export const authConfigured = false; export const socialProviders = {Facebook: false, Google: false}; export const initializeAuth = async () => null; export const signIn = async () => {}; export const signOut = async () => {};', loader: 'js' }));
    builder.onResolve({ filter: /^\.\/pages\/[^.]+$/ }, ({ path }) => ({ path, namespace: 'stub' }));
    builder.onLoad({ filter: /.*/, namespace: 'stub' }, ({ path }) => {
      const name = path.split('/').at(-1);
      const contents = name === 'AtlasPage'
        ? `import React from 'react'; export function AtlasPage({onSelectFacility,initialQuery,selectedId}) {
            return React.createElement('div', {id:'page-stub','data-page':'AtlasPage','data-query':initialQuery,'data-selected':selectedId},
              React.createElement('button', {id:'select-nonhero', onClick:()=>onSelectFacility('echo:110072102042')}, 'Select'));
          }`
        : `import React from 'react'; export function ${name}({facilityId,query,initialQuery,family,locationId,topic}) {
            return React.createElement('div', {id:'page-stub','data-page':'${name}','data-id':facilityId || '',
              'data-query':query ?? initialQuery,'data-family':family,'data-location':locationId,'data-topic':topic});
          }`;
      return { contents, loader: 'js' };
    });
    builder.onResolve({ filter: /\.css$/ }, ({ path }) => ({ path, namespace: 'css-stub' }));
    builder.onLoad({ filter: /.*/, namespace: 'css-stub' }, () => ({ contents: '', loader: 'js' }));
  },
};
const bundle = await build({
  entryPoints: [new URL('../src/App.tsx', import.meta.url).pathname],
  bundle: true, write: false, platform: 'node', format: 'cjs',
  external: ['react', 'react/jsx-runtime'], plugins: [pageStub],
});
const module = { exports: {} };
new Function('module', 'exports', 'require', bundle.outputFiles[0].text)(
  module, module.exports, createRequire(import.meta.url),
);
const App = module.exports.default;

function navigation(hash) {
  globalThis.window = { location: { hash } };
  const html = renderToStaticMarkup(React.createElement(App));
  const found = html.match(/<nav\b[^>]*aria-label="Main navigation"[^>]*>([\s\S]*?)<\/nav>/);
  assert.ok(found, 'shared main navigation exists');
  return found[1];
}
function currentLabels(markup) {
  return [...markup.matchAll(/<a\b[^>]*aria-current="page"[^>]*>([\s\S]*?)<\/a>/g)]
    .map((match) => match[1].replace(/<[^>]*>/g, '').trim());
}

test('overview, learning, reported data and investigative tools share navigation and mark their current route', () => {
  for (const [hash, label] of [
    ['#/', 'Overview'], ['#/learn', 'Understand'], ['#/learn/water', 'Understand'],
    ['#/reported', 'Reported data'], ['#/map', 'Records map'], ['#/address', 'Address lookup'],
    ['#/grid', 'Regional grid'], ['#/data', 'Sources'],
  ]) {
    const nav = navigation(hash);
    assert.deepEqual(currentLabels(nav), [label]);
    for (const text of ['Overview', 'Understand', 'Reported data', 'Records map', 'Regional grid', 'Sources', 'Address lookup']) {
      assert.match(nav, new RegExp(text));
    }
    assert.match(nav, /Select a map record to open its operator workspace/);
    assert.doesNotMatch(nav, /href="#\/(public|operator)\//);
  }
});

test('facility deep links retain their ID in both navigation destinations', () => {
  for (const [hash, label] of [
    ['#/public/echo%3A110072102042', 'Selected record'],
    ['#/operator/echo%3A110072102042', 'Operator workspace'],
  ]) {
    const nav = navigation(hash);
    assert.deepEqual(currentLabels(nav), [label]);
    assert.match(nav, /href="#\/public\/echo%3A110072102042"/);
    assert.match(nav, /href="#\/operator\/echo%3A110072102042"/);
    assert.doesNotMatch(nav, /aria-disabled="true"/);
  }
});

test('snapshot underscore IDs are normalized consistently for navigation', () => {
  const nav = navigation('#/operator/echo_110072102042');
  assert.match(nav, /href="#\/public\/echo%3A110072102042"/);
  assert.match(nav, /href="#\/operator\/echo%3A110072102042"/);
});

test('unknown routes retain the same navigation without claiming a current page', () => {
  const nav = navigation('#/not-a-route');
  assert.deepEqual(currentLabels(nav), []);
  assert.match(nav, /Records map/);
  assert.match(nav, /Address lookup/);
});

test('malformed facility links show the not-found view instead of crashing', () => {
  for (const hash of ['#/public/%ZZ', '#/operator/%E0%A4%A']) {
    const nav = navigation(hash);
    assert.deepEqual(currentLabels(nav), []);
    assert.match(nav, /Records map/);
  }
});

// These interaction tests run React and the real route hook with page bodies
// stubbed. They cover shell state across hash changes, not browser layout or
// external API loading/error behavior.
function mountApp(hash) {
  const listeners = new Set();
  globalThis.window = {
    location: { hash },
    addEventListener(name, fn) { if (name === 'hashchange') listeners.add(fn); },
    removeEventListener(name, fn) { if (name === 'hashchange') listeners.delete(fn); },
    scrollTo() {},
  };
  let root;
  act(() => { root = TestRenderer.create(React.createElement(App)); });
  function go(next) {
    act(() => {
      window.location.hash = next;
      for (const listener of [...listeners]) listener();
    });
  }
  function nav() { return root.root.findByProps({'aria-label': 'Main navigation'}); }
  function link(label) { return nav().findAllByType('a').find((a) => a.children.join('') === label); }
  function active() { return nav().findAllByType('a').filter((a) => a.props['aria-current'] === 'page').map((a) => a.children.join('')); }
  function page() { return root.root.findByProps({id:'page-stub'}); }
  return {root, go, nav, link, active, page};
}

test('selected record survives overview, learn, reported data, map, card, address and operator navigation', () => {
  const app = mountApp('#/map');
  act(() => app.root.root.findByProps({id:'select-nonhero'}).props.onClick());
  assert.equal(app.link('Selected record').props.href, '#/public/echo%3A110072102042');
  app.go(app.link('Selected record').props.href);
  assert.deepEqual(app.active(), ['Selected record']);
  for (const [hash,label] of [['#/','Overview'],['#/learn/water','Understand'],['#/reported?family=water','Reported data'],['#/address','Address lookup'],['#/grid','Regional grid']]) {
    app.go(hash);
    assert.deepEqual(app.active(), [label]);
    assert.equal(app.link('Selected record').props.href, '#/public/echo%3A110072102042');
    assert.equal(app.link('Operator workspace').props.href, '#/operator/echo%3A110072102042');
  }
  app.go(app.link('Operator workspace').props.href);
  assert.deepEqual(app.active(), ['Operator workspace']);
  app.go('#/address'); // equivalent to browser history returning to address
  app.go('#/map');
  assert.deepEqual(app.active(), ['Records map']);
  assert.equal(app.page().props['data-selected'], 'echo:110072102042');
  assert.equal(app.link('Selected record').props.href, '#/public/echo%3A110072102042');
  act(() => app.root.unmount());
});

test('direct record route preserves its navigation on other routes and on a simulated refresh', () => {
  const direct = '#/public/echo_110072102042';
  const app = mountApp(direct);
  assert.deepEqual(app.active(), ['Selected record']);
  assert.equal(app.link('Operator workspace').props.href, '#/operator/echo%3A110072102042');
  app.go('#/address');
  assert.equal(app.link('Selected record').props.href, '#/public/echo%3A110072102042');
  act(() => app.root.unmount());
  const refreshed = mountApp(direct);
  assert.deepEqual(refreshed.active(), ['Selected record']);
  assert.equal(refreshed.link('Operator workspace').props.href, '#/operator/echo%3A110072102042');
  act(() => refreshed.root.unmount());
});

test('root opens the overview while legacy root searches still open the map with their query', () => {
  const app=mountApp('#/');
  assert.equal(app.page().props['data-page'],'AwarenessHomePage');
  assert.deepEqual(app.active(),['Overview']);
  for (const hash of ['#/?q=Henrico%20County','#/map?q=Henrico%20County']) {
    app.go(hash);
    assert.equal(app.page().props['data-page'],'AtlasPage');
    assert.equal(app.page().props['data-query'],'Henrico County');
    assert.deepEqual(app.active(),['Records map']);
  }
  app.go('#/?q=');
  assert.equal(app.page().props['data-page'],'AtlasPage');
  assert.equal(app.page().props['data-query'],'');
  act(()=>app.root.unmount());
});

test('learning and reported links pass their topic, measure, search and location to the correct page', () => {
  const app=mountApp('#/learn/emissions');
  assert.equal(app.page().props['data-page'],'LearnPage');
  assert.equal(app.page().props['data-topic'],'emissions');
  app.go('#/reported?family=water&q=Douglas%20County&location=google%3Adouglas-county-ga');
  assert.equal(app.page().props['data-page'],'ReportedDataPage');
  assert.equal(app.page().props['data-family'],'water');
  assert.equal(app.page().props['data-query'],'Douglas County');
  assert.equal(app.page().props['data-location'],'google:douglas-county-ga');
  app.go('#/reported?measure=electricity');
  assert.equal(app.page().props['data-family'],'electricity');
  app.go('#/reported');
  assert.equal(app.page().props['data-family'],'all');
  assert.equal(app.page().props['data-query'],'');
  assert.equal(app.page().props['data-location'],undefined);
  act(()=>app.root.unmount());
});
