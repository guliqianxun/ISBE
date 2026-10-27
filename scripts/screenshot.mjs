#!/usr/bin/env node
// Screenshot a console page for evidence (docs/process.md): headless Chrome
// over the DevTools protocol loads the page, waits a fixed time, prints console
// errors and the page's main text, and saves a PNG. Unlike `chrome --screenshot`,
// pages that update in requestAnimationFrame (the run page) render before the shot.
//   scripts/screenshot.mjs <url> <out.png> [width=1440] [height=900] [wait-ms=15000]
// Exits 1 without a PNG when the page does not load or answers other than 2xx.
// Needs google-chrome (CHROME overrides); swiftshader gives it WebGL.
import { spawn } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const [url, out, w = '1440', h = '900', wait = '15000'] = process.argv.slice(2);
if (!url || !out) {
  console.error('usage: scripts/screenshot.mjs <url> <out.png> [width] [height] [wait-ms]');
  process.exit(2);
}
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const port = 9300 + Math.floor(Math.random() * 500);
// Chrome's singleton socket path must stay short: keep the profile under /tmp.
const profile = mkdtempSync(join(tmpdir().length < 40 ? tmpdir() : '/tmp', 'shot-'));
let chrome;
let ws;

async function shoot() {
  const failed = new Promise((_, reject) => {
    chrome = spawn(
      process.env.CHROME || 'google-chrome',
      [
        '--headless=new',
        '--use-angle=swiftshader',
        '--enable-unsafe-swiftshader',
        '--hide-scrollbars',
        `--remote-debugging-port=${port}`,
        `--user-data-dir=${profile}`,
        `--window-size=${w},${h}`,
        'about:blank',
      ],
      { stdio: 'ignore' },
    );
    chrome.once('error', (e) => reject(new Error(`cannot start Chrome: ${e.message}`)));
    chrome.once('exit', (code, signal) => reject(new Error(`Chrome exited (${signal ?? code})`)));
  });
  failed.catch(() => {});
  const race = (p) => Promise.race([p, failed]);

  let target;
  for (let i = 0; i < 150 && !target; i++) {
    await race(sleep(200));
    try {
      const list = await (await fetch(`http://127.0.0.1:${port}/json`)).json();
      target = list.find((t) => t.type === 'page');
    } catch {
      /* not up yet */
    }
  }
  if (!target) throw new Error('Chrome did not start');
  ws = new WebSocket(target.webSocketDebuggerUrl);
  await race(new Promise((r) => (ws.onopen = r)));

  let id = 0;
  const waiting = new Map();
  const logs = [];
  ws.onmessage = (m) => {
    const msg = JSON.parse(m.data);
    if (msg.id && waiting.has(msg.id)) {
      waiting.get(msg.id)(msg);
      waiting.delete(msg.id);
    }
    if (msg.method === 'Runtime.exceptionThrown')
      logs.push('exception: ' + msg.params.exceptionDetails.exception?.description);
    if (msg.method === 'Runtime.consoleAPICalled' && ['error', 'warning'].includes(msg.params.type))
      logs.push(msg.params.type + ': ' + msg.params.args.map((a) => a.value ?? a.description).join(' '));
  };
  const send = (method, params = {}) =>
    race(
      new Promise((r) => {
        const n = ++id;
        waiting.set(n, r);
        ws.send(JSON.stringify({ id: n, method, params }));
      }),
    );
  const evaluate = async (expression) =>
    (await send('Runtime.evaluate', { expression, returnByValue: true })).result?.result?.value;

  await send('Runtime.enable');
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', {
    width: +w,
    height: +h,
    deviceScaleFactor: 1,
    mobile: +w < 600,
  });
  const t0 = Date.now();
  const nav = await send('Page.navigate', { url });
  if (nav.error) throw new Error(`cannot load ${url}: ${nav.error.message}`);
  if (nav.result.errorText) throw new Error(`cannot load ${url}: ${nav.result.errorText}`);
  await race(sleep(+wait));
  const status = await evaluate(`performance.getEntriesByType('navigation')[0]?.responseStatus ?? 0`);
  if (!(status >= 200 && status < 300)) throw new Error(`${url} answered HTTP ${status}`);
  const text = await evaluate(`(document.querySelector('main')||document.body).innerText.slice(0,400)`);
  const shot = await send('Page.captureScreenshot', { format: 'png' });
  writeFileSync(out, Buffer.from(shot.result.data, 'base64'));
  console.log(`url: ${url}\nstatus: ${status}\nwaited: ${Date.now() - t0} ms`);
  console.log(`console errors: ${logs.length ? '\n  ' + logs.join('\n  ') : 'none'}`);
  console.log('main text:\n  ' + String(text).replace(/\n/g, ' | '));
}

let code = 0;
try {
  await shoot();
} catch (e) {
  console.error(e.message);
  code = 1;
} finally {
  ws?.close();
  if (chrome && chrome.exitCode === null && chrome.pid) {
    const exited = new Promise((r) => chrome.once('exit', r));
    chrome.kill();
    await Promise.race([exited, sleep(5000)]);
  }
  rmSync(profile, { recursive: true, force: true, maxRetries: 5 });
}
process.exit(code);
