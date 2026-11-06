import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import { setTimeout as delay } from 'node:timers/promises';
import { chromium } from 'playwright';

const channel = process.env.PLAYWRIGHT_CHANNEL;
const manifest = JSON.parse(readFileSync(new URL('../../site/data/current.json', import.meta.url)));
const results = [];

for (const [mode, port] of [['dev', 5187], ['preview', 4187]]) {
  const base = `http://127.0.0.1:${port}`;
  const args = ['node_modules/vite/bin/vite.js', ...(mode === 'preview' ? ['preview'] : []), '--host', '127.0.0.1', '--port', String(port), '--strictPort'];
  const server = spawn(process.execPath, args, { cwd: new URL('../', import.meta.url), env: { ...process.env, MODEL_OFF: '1' }, stdio: 'pipe' });
  let output = '';
  server.stdout.on('data', value => { output += value; });
  server.stderr.on('data', value => { output += value; });
  let browser;
  try {
    let ready = false;
    for (let attempt = 0; attempt < 80; attempt++) {
      try { if ((await fetch(base)).ok) { ready = true; break; } } catch {}
      if (server.exitCode !== null) break;
      await delay(250);
    }
    assert(ready, output);
    const local = await (await fetch(base + '/data/current.json')).json();
    assert.equal(local.release_id, manifest.release_id);
    const missing = await fetch(base + '/data/does-not-exist.json');
    assert.equal(missing.status, 404);
    assert.match(missing.headers.get('content-type'), /json/);
    browser = await chromium.launch({ headless: true, ...(channel ? { channel } : {}) });
    for (const viewport of [{ width: 1440, height: 1000 }, { width: 390, height: 844 }]) {
      const context = await browser.newContext({ viewport });
      const external = [];
      await context.route('**/*', route => {
        const url = new URL(route.request().url());
        if (url.origin === base) return route.continue();
        external.push(url.href);
        return route.abort();
      });
      const page = await context.newPage();
      const errors = [];
      page.on('pageerror', error => errors.push(error.message));
      await page.goto(base);
      await page.getByRole('heading', { name: 'See the evidence in action.' }).waitFor();
      assert.equal(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth), true);
      await page.getByRole('button', { name: 'Play the 2 minute 52 second WattDocket demo' }).click();
      await page.waitForFunction(() => { const v = document.querySelector('video'); return v.readyState >= 2 && !v.paused && v.currentTime > 0; });
      assert.equal(await page.locator('video').evaluate(v => v.duration), 172);
      await page.locator('video').evaluate(v => { v.pause(); v.textTracks[0].mode = 'showing'; });
      await page.waitForFunction(() => document.querySelector('video').textTracks[0]?.cues?.length === 42);

      await page.goto(base + '/#/public/echo%3A110072155814');
      await page.getByRole('heading', { name: 'VANTAGE DATA CENTER TX2', exact: true }).waitFor();
      await page.getByText('Open the historical grid calculator', { exact: true }).click();
      await page.getByRole('cell', { name: '56,940', exact: true }).waitFor();
      await page.getByLabel('Grid-side load (MW)', { exact: true }).fill('20');
      await page.getByRole('cell', { name: '113,880', exact: true }).waitFor();
      // The production model is never configured by default.
      assert.equal(await page.locator('.explain__text').count(), 0);

      await page.goto(base + '/#/public/echo%3AVA0000005110774287');
      await page.getByRole('heading', { name: 'VANTAGE DATA CENTERS VA16 LLC', exact: true }).waitFor();
      await page.goto(base + '/#/reported');
      await page.locator('.reported-page__provenance').waitFor();
      await page.goto(base + '/#/reported?view=power');
      await page.getByRole('heading', { name: 'Facility power & hourly readings', exact: true }).waitFor();
      await page.waitForFunction(() => document.body.innerText.includes('INL'));
      await page.goto(base + '/#/grid');
      await page.locator('.grid-current').waitFor();
      await page.goto(base + '/#/data');
      await page.locator('.source-row').first().waitFor();
      assert.equal(await page.locator('.source-row').count() > 0, true);
      assert.equal(await page.locator('body').innerText().then(t => t.includes(manifest.release_id)), true);
      assert.deepEqual(errors, []);
      assert.equal(external.some(url => /wattdocket\.com|lambda-url|api\.meta\.ai/.test(url)), false,
        'Local app attempted to use a production service');
      results.push({ mode, viewport, release_id: manifest.release_id, external_requests_blocked: true,
        video: true, captions: true, public_records: true, calculator: true, disclosures: true, power: true, grid: true, sources: true });
      await context.close();
    }
  } finally {
    await browser?.close();
    server.kill('SIGTERM');
  }
}
console.log(JSON.stringify({ status: 'passed', results }, null, 2));
