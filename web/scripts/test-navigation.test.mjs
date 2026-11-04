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
    builder.onResolve({ filter: /^\.\/pages\// }, ({ path }) => ({ path, namespace: 'stub' }));
    builder.onLoad({ filter: /.*/, namespace: 'stub' }, ({ path }) => {
      const name = path.split('/').at(-1);
      const contents = name === 'AtlasPage'
        ? `import React from 'react'; export function AtlasPage({onSelectFacility}) {
            return React.createElement('button', {id:'select-nonhero', onClick:()=>onSelectFacility('echo:110072102042')}, 'Select');
          }`
        : `import React from 'react'; export function ${name}({facilityId}) {
            return React.createElement('div', {id:'page-stub', 'data-id':facilityId || ''});
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

test('map, address, grid, and data share navigation and mark the current page', () => {
  for (const [hash, label] of [['#/', 'Map'], ['#/address', 'Address'], ['#/grid', 'Grid'], ['#/data', 'Data']]) {
    const nav = navigation(hash);
    assert.deepEqual(currentLabels(nav), [label]);
    for (const text of ['Map', 'Address', 'Public card', 'Operator', 'Grid', 'Data']) {
      assert.match(nav, new RegExp(text));
    }
    assert.equal((nav.match(/aria-disabled="true"/g) ?? []).length, 2);
  }
});

test('facility deep links retain their ID in both navigation destinations', () => {
  for (const [hash, label] of [
    ['#/public/echo%3A110072102042', 'Public card'],
    ['#/operator/echo%3A110072102042', 'Operator'],
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
  assert.match(nav, /Map/);
  assert.match(nav, /Address/);
});

test('malformed facility links show the not-found view instead of crashing', () => {
  for (const hash of ['#/public/%ZZ', '#/operator/%E0%A4%A']) {
    const nav = navigation(hash);
    assert.deepEqual(currentLabels(nav), []);
    assert.match(nav, /Map/);
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
  return {root, go, nav, link, active};
}

test('selected non-hero facility survives map, card, address, operator, and back navigation', () => {
  const app = mountApp('#/');
  act(() => app.root.root.findByProps({id:'select-nonhero'}).props.onClick());
  assert.equal(app.link('Public card').props.href, '#/public/echo%3A110072102042');
  app.go(app.link('Public card').props.href);
  assert.deepEqual(app.active(), ['Public card']);
  app.go(app.link('Address').props.href);
  assert.deepEqual(app.active(), ['Address']);
  assert.equal(app.link('Operator').props.href, '#/operator/echo%3A110072102042');
  app.go(app.link('Operator').props.href);
  assert.deepEqual(app.active(), ['Operator']);
  app.go('#/address'); // equivalent to browser history returning to address
  app.go('#/');
  assert.deepEqual(app.active(), ['Map']);
  assert.equal(app.link('Public card').props.href, '#/public/echo%3A110072102042');
  act(() => app.root.unmount());
});

test('direct record route preserves its navigation on other routes and on a simulated refresh', () => {
  const direct = '#/public/echo_110072102042';
  const app = mountApp(direct);
  assert.deepEqual(app.active(), ['Public card']);
  assert.equal(app.link('Operator').props.href, '#/operator/echo%3A110072102042');
  app.go('#/address');
  assert.equal(app.link('Public card').props.href, '#/public/echo%3A110072102042');
  act(() => app.root.unmount());
  const refreshed = mountApp(direct);
  assert.deepEqual(refreshed.active(), ['Public card']);
  assert.equal(refreshed.link('Operator').props.href, '#/operator/echo%3A110072102042');
  act(() => refreshed.root.unmount());
});
