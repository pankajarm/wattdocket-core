import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
import { build } from 'esbuild';
import React from 'react';
import TestRenderer, { act } from 'react-test-renderer';

const bundle = await build({
  stdin: { contents: "export { ExplainPanel } from './src/components/ExplainPanel';", resolveDir: process.cwd(), loader: 'tsx' },
  bundle: true, write: false, platform: 'node', format: 'cjs',
  external: ['react', 'react/jsx-runtime'],
  plugins: [{ name: 'explain-stub', setup(builder) {
    builder.onResolve({ filter: /^\.\.\/api$/ }, () => ({ path: 'api', namespace: 'stub' }));
    builder.onLoad({ filter: /.*/, namespace: 'stub' }, () => ({
      contents: 'export async function getExplain() { return globalThis.__explainFixture; }', loader: 'js',
    }));
  } }],
});
const module = { exports: {} };
new Function('module', 'exports', 'require', bundle.outputFiles[0].text)(module, module.exports, createRequire(import.meta.url));
const { ExplainPanel } = module.exports;

for (const [reason, message] of [
  ['DAILY_QUOTA_EXHAUSTED', /Today’s explanation limit has been reached/],
  ['QUOTA_UNAVAILABLE', /explanation service is temporarily unavailable/],
]) {
  test(`${reason} displays the trusted template and a readable reason`, async () => {
    globalThis.__explainFixture = { source: 'template', degraded: true, reason, text: 'Trusted fallback.' };
    let renderer;
    await act(async () => {
      renderer = TestRenderer.create(React.createElement(ExplainPanel, {
        payload: { facility: { id: 'test-site' } }, template: 'Initial template.', enabled: true,
      }));
    });
    await act(async () => { await renderer.root.findByType('button').props.onClick(); });
    const output = JSON.stringify(renderer.toJSON());
    assert.match(output, /Trusted fallback/);
    assert.match(output, /TEMPLATE FALLBACK/);
    assert.match(output, message);
    assert.doesNotMatch(output, new RegExp(reason));
    act(() => renderer.unmount());
  });
}
