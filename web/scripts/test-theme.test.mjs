import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { runInNewContext } from 'node:vm';
import { build } from 'esbuild';

const bundle = await build({entryPoints:[new URL('../src/theme.ts',import.meta.url).pathname],bundle:true,write:false,format:'cjs',platform:'node'});
function environment(saved, dark, blocked=false) {
  const meta = {content:'', setAttribute(_,value) {this.content=value;}};
  const document = {documentElement:{dataset:{},style:{}},querySelector:()=>meta};
  const localStorage = {getItem() {if(blocked) throw Error('unavailable'); return saved;},setItem(_,value) {if(blocked) throw Error('unavailable');saved=value;}};
  const matchMedia = ()=>({matches:dark});
  const context = {module:{exports:{}},document,localStorage,matchMedia,window:{localStorage,matchMedia}};
  runInNewContext(bundle.outputFiles[0].text,context);
  return {context,theme:context.module.exports,document,meta};
}

test('explicit appearance survives a conflicting operating-system preference',()=>{
  for (const preference of ['light','dark']) {
    const e=environment(preference,preference==='light');
    e.theme.applyTheme(e.theme.readThemePreference());
    assert.equal(e.document.documentElement.dataset.theme,preference);
    assert.equal(e.document.documentElement.style.colorScheme,preference);
  }
});
test('Auto and invalid saved values follow the operating-system appearance',()=>{
  for(const saved of [null,'system','invalid']) for(const dark of [true,false]) {
    const e=environment(saved,dark);
    e.theme.applyTheme(e.theme.readThemePreference());
    assert.equal(e.document.documentElement.dataset.theme,dark?'dark':'light');
  }
});
test('theme remains usable when browser storage is unavailable',()=>{
  const e=environment(null,false,true);
  assert.equal(e.theme.readThemePreference(),'system');
  assert.doesNotThrow(()=>e.theme.saveThemePreference('dark'));
  assert.equal(e.document.documentElement.dataset.theme,'dark');
});
test('pre-paint theme agrees with runtime on direct page loads',()=>{
  const html=readFileSync(new URL('../index.html',import.meta.url),'utf8');
  const script=html.match(/<script>([\s\S]*?)<\/script>/)[1];
  for(const saved of ['light','dark','system',null,'invalid']) for(const dark of [true,false]) {
    const e=environment(saved,dark);
    runInNewContext(script,e.context);
    const initial=e.document.documentElement.dataset.theme;
    e.theme.applyTheme(e.theme.readThemePreference());
    assert.equal(initial,e.document.documentElement.dataset.theme);
    assert.equal(e.meta.content,initial==='dark'?'#0d1720':'#f4f8fa');
  }
});
