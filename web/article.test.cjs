const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

function harness(fetch, owner = '', role = 'admin') {
  const source = fs.readFileSync(`${__dirname}/app.js`, 'utf8');
  const startup = '  render();\n})();';
  assert.ok(source.includes(startup));
  const window = { addEventListener() {} };
  vm.runInNewContext(source.replace(startup, `
    session = { user: { id: 'local', role: '${role}' }, csrf_token: 'token' };
    window.article = { attachReportArticles, toggleReportArticle };
  })();`), {
    window, fetch, document: { addEventListener() {} }, URL, URLSearchParams,
    location: new URL(`/reports${owner ? '?owner=' + owner : ''}`, 'http://localhost'),
    history: { replaceState() {} }, navigator: { language: 'zh' },
    localStorage: { getItem() { return null; } },
  });
  return window.article;
}

function details() {
  const summary = {}, body = { setAttribute(name, value) { this[name] = value; } };
  return { open: true, dataset: { topic: 't', period: 'w', material: 'm' }, summary, body,
    querySelector(selector) { return selector === 'summary' ? summary : body; } };
}

test('expansion binds only frozen report links and stays disabled for other owners and members', () => {
  for (const [owner, role, expected] of [['', 'admin', true], ['alice', 'admin', false], ['', 'member', false]]) {
    let inserted = '';
    const paragraph = { querySelectorAll() { return []; }, tagName: 'P', insertAdjacentHTML(where, html) { assert.equal(where, 'afterend'); inserted += html; } };
    const heading = (url) => ({ nextElementSibling: paragraph, querySelector() { return { getAttribute() { return url; } }; } });
    const doc = { querySelectorAll() { return [heading('https://example.com/a'), heading('https://example.com/unknown')]; } };
    harness(() => { throw new Error('must not fetch on render'); }, owner, role).attachReportArticles(doc, {
      topic: 't', period: 'w', articles: [{ id: 'm"<', url: 'https://example.com/a' }],
    });
    assert.equal(inserted.includes('展开原文'), expected);
    if (expected) {
      assert.equal((inserted.match(/<details/g) || []).length, 1);
      assert.match(inserted, /data-material="m&quot;&lt;"/);
    }
  }
});

test('original paragraphs are escaped, loaded once and can collapse and reopen', async () => {
  let calls = 0;
  const api = harness(async (url, options) => {
    calls++;
    assert.equal(url, '/api/sources/article');
    assert.equal(options.headers['X-ISBE-CSRF'], 'token');
    assert.deepEqual(JSON.parse(options.body), { topic: 't', period: 'w', id: 'm' });
    return { ok: true, json: async () => ({ paragraphs: ['前文之后的完整句子。', '<img src=x onerror=bad()> & 内容'], fetched_at: '2026-10-19T08:00:00Z' }) };
  });
  const d = details();
  await api.toggleReportArticle(d);
  assert.match(d.body.innerHTML, /<p>前文之后的完整句子。<\/p>/);
  assert.match(d.body.innerHTML, /&lt;img src=x onerror=bad\(\)&gt; &amp; 内容/);
  assert.doesNotMatch(d.body.innerHTML, /<img/);
  assert.equal(d.body['aria-busy'], 'false');
  d.open = false; await api.toggleReportArticle(d);
  assert.equal(d.summary.textContent, '展开原文');
  d.open = true; await api.toggleReportArticle(d);
  assert.equal(d.summary.textContent, '收起原文');
  assert.equal(calls, 1);
});

test('pending reads are not repeated when toggled, and failures allow retry', async () => {
  let release, calls = 0;
  const api = harness(async () => { calls++; return new Promise((resolve) => { release = resolve; }); });
  const d = details();
  const request = api.toggleReportArticle(d);
  assert.equal(d.body.textContent, '正在读取原文…');
  d.open = false; await api.toggleReportArticle(d);
  d.open = true; await api.toggleReportArticle(d);
  assert.equal(calls, 1);
  release({ ok: false, json: async () => ({ code: 'source_unavailable' }) }); await request;
  assert.match(d.body.textContent, /点击报道标题查看/);
  assert.equal(d.dataset.loaded, undefined);
  assert.equal(d.dataset.loading, undefined);
  const retry = api.toggleReportArticle(d);
  assert.equal(calls, 2);
  release({ ok: true, json: async () => ({ paragraphs: [] }) }); await retry;
  assert.equal(d.dataset.loaded, undefined);
  assert.match(d.body.textContent, /重试/);
});

test('unsupported extraction explains the limitation and saved originals show their date', async () => {
  const d = details();
  await harness(async () => ({ ok: false, json: async () => ({ code: 'unsupported_body' }) })).toggleReportArticle(d);
  assert.match(d.body.textContent, /正文暂未适配/);
  assert.doesNotMatch(d.body.textContent, /重试/);
  await harness(async () => ({ ok: true, json: async () => ({ cached: true, paragraphs: ['已保存的原文。'], fetched_at: '2026-10-19T08:00:00Z' }) })).toggleReportArticle(d);
  assert.match(d.body.innerHTML, /已保存原文/);
  assert.equal(d.dataset.loaded, '1');
});

test('expansion follows all paragraphs, data and sources of the item', () => {
  let inserted = '';
  const next = { tagName: 'H3' };
  const sources = { querySelectorAll() { return []; }, tagName: 'UL', nextElementSibling: next, insertAdjacentHTML(where) { inserted = where; } };
  const table = { querySelectorAll() { return []; }, tagName: 'TABLE', nextElementSibling: sources };
  const paragraph = { querySelectorAll() { return []; }, tagName: 'P', nextElementSibling: table };
  const heading = { nextElementSibling: paragraph, querySelector() { return { getAttribute() { return 'https://example.com/a'; } }; } };
  harness(() => { throw new Error('must not fetch'); }).attachReportArticles({ querySelectorAll() { return [heading]; } }, { topic: 't', period: 'w', articles: [{ id: 'm', url: 'https://example.com/a' }] });
  assert.equal(inserted, 'afterend');
});

test('a grouped item exposes each frozen source once with its own label', async () => {
  let inserted = '';
  const link = (url, textContent) => ({ textContent, getAttribute() { return url; } });
  const sources = { tagName: 'P', querySelectorAll() { return [link('https://example.com/a', '来源甲｜标题甲'), link('https://example.com/b', '来源乙｜标题乙')]; }, insertAdjacentHTML(where, html) { inserted = html; } };
  const heading = { nextElementSibling: sources, querySelector() { return link('https://example.com/a', '议题标题'); } };
  harness(() => {}).attachReportArticles({ querySelectorAll() { return [heading]; } }, { topic: 't', period: 'w', articles: [{ id: 'a', url: 'https://example.com/a' }, { id: 'b', url: 'https://example.com/b' }] });
  assert.equal((inserted.match(/<details/g) || []).length, 2);
  assert.match(inserted, /展开原文 · 来源甲｜标题甲/);
  assert.match(inserted, /data-material="b"/);
  const d = details(); d.dataset.label = '来源乙｜标题乙';
  await harness(async () => ({ ok: false, json: async () => ({ code: 'article_unavailable' }) })).toggleReportArticle(d);
  assert.match(d.summary.textContent, /收起原文 · 来源乙｜标题乙/);
  assert.match(d.body.textContent, /已保存的原文暂时无法读取/);
  assert.doesNotMatch(d.body.textContent, /原网站/);
});
