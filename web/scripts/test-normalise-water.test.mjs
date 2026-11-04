import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import ts from 'typescript';

const source = readFileSync(new URL('../src/normalise.ts', import.meta.url), 'utf8');
const compiled = ts.transpileModule(source, {
  compilerOptions: { module: ts.ModuleKind.ESNext, target: ts.ScriptTarget.ES2020 },
}).outputText.replace("from './types'", "from 'data:text/javascript,export const isBandClaim=()=>false;export const isNumericClaim=()=>false'");
const { normaliseClaim } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);

const oldNote = 'no US state requires facility-level operational water reporting';
const raw = { id: 'water', metric: 'water_gal', value: 'NOT_PUBLISHED', note: oldNote,
  citations: [{ publisher: 'Example', quote: 'Evidence' }] };

test('legacy water wording is corrected without dropping source citations', () => {
  const claim = normaliseClaim(raw, 'facility');
  assert.match(claim.value.reason, /public sources used for this card/);
  assert.equal(claim.citations[0].quote, 'Evidence');
});

test('other water notes and other metrics retain their original wording', () => {
  assert.equal(normaliseClaim({ ...raw, note: 'A specific site note' }, 'facility').value.reason, 'A specific site note');
  assert.equal(normaliseClaim({ ...raw, metric: 'diesel_mw' }, 'facility').value.reason, oldNote);
});

test('nested missing value shapes also correct the legacy wording', () => {
  const state = normaliseClaim({ ...raw, value: { state: 'NOT_PUBLISHED', reason: oldNote } }, 'facility');
  const kind = normaliseClaim({ ...raw, value: { kind: 'NOT_PUBLISHED', reason: oldNote, method_note: oldNote } }, 'facility');
  assert.match(state.value.reason, /public sources used for this card/);
  assert.match(kind.value.method_note, /public sources used for this card/);
});
