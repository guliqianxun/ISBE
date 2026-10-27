// Run with: node --test web/*.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

async function reportsPage(path, reports, topics = [], reader = null) {
  const location = new URL(path, 'http://localhost');
  const element = { dataset: {}, style: { setProperty() {} }, insertAdjacentHTML() {}, querySelector() { return null; } };
  const app = { ...element };
  vm.runInNewContext(fs.readFileSync(`${__dirname}/app.js`, 'utf8'), {
    clearTimeout() {}, document: { documentElement: {}, body: element, querySelector(s) { return s === '#app' ? app : element; }, addEventListener() {} },
    window: { addEventListener() {} }, location, history: { replaceState() {} }, URL, URLSearchParams,
    navigator: { language: 'en' }, localStorage: { getItem() { return null; } },
    getComputedStyle() { return { backgroundColor: '#000' }; },
    DOMParser: class { parseFromString(html) { return { body: { innerHTML: html }, querySelectorAll() { return []; } }; } },
    fetch: async (url) => ({ ok: true, json: async () => url === '/api/session' ? { mode: 'local', user: { id: 'local', role: 'admin' }, csrf_token: 'token', llm: { ready: false }, mail: {} } : url.startsWith('/api/overview')
      ? { topics, pending: [], services: {}, reports, runs:[], llm:{}, now:'2026-10-19T08:00:00Z' }
      : url.startsWith('/api/reports') ? reports : reader }),
  });
  await new Promise((resolve) => setImmediate(resolve));
  return app.innerHTML;
}

test('empty selected topic opens a guide for that topic even when other topics have reports', async () => {
  const html = await reportsPage('/reports?topic=new', [{ topic_id: 'old' }], [{ id: 'new', label: 'New research', flavor: 'papers' }, { id: 'other', flavor: 'papers' }]);
  assert.match(html, /first-report-guide/);
  // The guide is on Help, opened on the topic.
  assert.match(html, /<a class="btn primary" href="\/help\?topic=new#report-guide">/);
  assert.doesNotMatch(html, /isbe --data|uv run|Docker|data directory/);
  assert.match(html, /Model configuration incomplete/);
  assert.doesNotMatch(html, /<dialog id="report-guide"/);
});

test('an empty library offers the guide without inventing a topic or running a command', async () => {
  const html = await reportsPage('/reports', []);
  assert.match(html, /href="\/help#report-guide"/);
  assert.doesNotMatch(html, /isbe --data|uv run|Docker|data directory/);
});

test('historical titles appear on both shelf and reader and an included period is not repeated on the cover', async () => {
  for (const [label, showPeriod] of [['旧项目观察 · 2026-W41', false], ['Old research', true]]) {
    const report = { topic_id: 'old', period: '2026-W41', kind: 'weekly', label, show_period: showPeriod, tldr: [], papers: 0 };
    const shelf = await reportsPage('/reports?topic=old', [report]);
    const cover = shelf.match(/<a class="cover"[\s\S]*?<\/a>/)[0];
    assert.equal(cover.includes('class="cv-period"'), showPeriod);
    assert.match(cover, new RegExp(label));
    const reader = await reportsPage('/reports?topic=old&period=2026-W41', [], [], { ...report, topic: 'old', issues: [{ period: '2026-W41' }], html: `<h1>${label}</h1><p>Original body</p>` });
    assert.match(reader, new RegExp(`<span>${label}<\/span>`));
    assert.match(reader, new RegExp(`href="/reports\\?topic=old"[^>]*>${label}<\/a>`));
    assert.match(reader, new RegExp(`<h1>${label}<\/h1><p>Original body<\/p>`));
  }
});


test('other-owner report navigation and downloads keep owner and are read-only', async () => {
  const r = { topic: 'papers', topic_id: 'papers', period: '2026-W42', kind: 'weekly', label: 'Private research', issues: [{ period: '2026-W42' }, { period: '2026-W41' }], html: '<p>Report</p>', path: '/private/workspaces/alice/report.md' };
  const html = await reportsPage('/reports?topic=papers&period=2026-W42&owner=alice', [], [], r);
  assert.match(html, /href="\/api\/report.md\?topic=papers&period=2026-W42&owner=alice"/);
  assert.match(html, /href="\/reports\?topic=papers&period=2026-W41&owner=alice"/);
  assert.match(html, /href="\/reports\?topic=papers&owner=alice"/);
  assert.doesNotMatch(html, /data-act="mailOpen"|#report-guide|\/private\/workspaces/);
});

test('media weekly shelf and issue navigation count source material instead of zero papers', async () => {
  const report = { topic_id:'shanghai-esg', topic:'shanghai-esg', period:'2026-W42-media', kind:'weekly', media:true, label:'Shanghai ESG', tldr:[], materials:146, papers:0 };
  const shelf = await reportsPage('/reports?topic=shanghai-esg',[report]);
  assert.match(shelf,/<span class="cv-foot"><span>146 sources<\/span>/);
  assert.doesNotMatch(shelf,/0 papers/);
  const reader = await reportsPage('/reports?topic=shanghai-esg&period=2026-W42-media',[],[],{...report,issues:[report],html:'<h1>Weekly media</h1>'});
  assert.match(reader,/>146 sources<\/span><\/a>/);
  assert.match(reader,/ISBE · weekly/);
  const overview = await reportsPage('/',[report]);
  assert.match(overview,/<span class="right">146 sources<\/span>/);
  assert.doesNotMatch(overview,/0 papers/);
});
