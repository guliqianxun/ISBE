// Run with: node --test web/settings.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const defaults = {
  anthropic: { ISBE_LLM_BASE_URL: 'https://api.anthropic.com', ISBE_LLM_MODEL_FAST: 'claude-haiku-5-5', ISBE_LLM_MODEL_SMART: 'claude-sonnet-5-5' },
  deepseek: { ISBE_LLM_BASE_URL: 'https://api.deepseek.com', ISBE_LLM_MODEL_FAST: 'deepseek-chat', ISBE_LLM_MODEL_SMART: 'deepseek-chat' },
  openai: { ISBE_LLM_BASE_URL: 'https://api.openai.com/v1', ISBE_LLM_MODEL_FAST: 'gpt-5-mini', ISBE_LLM_MODEL_SMART: 'gpt-5' },
};

async function consoleApp(values = {}, sources = {}, mailReply = async () => ({ state: 'accepted', request_id: 'test-id' })) {
  const events = {}, windowEvents = {}, requests = [], elements = new Map();
  const location = new URL('http://localhost/settings');
  const snapshot = {
    values: { EMAIL_TRANSPORT: 'zeptomail_api', ISBE_LLM_PROVIDER: 'anthropic', ...defaults.anthropic, ISBE_LLM_NO_AUTH: 'false', ISBE_LLM_EXTRA_BODY: '{}', ISBE_FULLTEXT_EXTRACTOR: values.METRAIL_API_URL ? 'metrail' : 'llm', METRAIL_API_URL: '', ...values },
    configured: { ANTHROPIC_API_KEY: true, OPENAI_API_KEY: true }, provider_defaults: defaults, llm: { ready: true, error: null, connection_verified: false },
    sources: { ANTHROPIC_API_KEY: 'environment', OPENAI_API_KEY: 'file', ISBE_FULLTEXT_EXTRACTOR: 'default', ...sources }, csrf_token: 'test-csrf', mail: { configured: true, sender: 'sender@example.com' },
  };
  const element = () => ({ dataset: {}, style: { setProperty() {} }, focus() {}, scrollIntoView() {}, insertAdjacentHTML() {}, querySelector() { return null; } });
  const app = element();
  const document = {
    documentElement: {}, body: element(), title: '',
    querySelector(selector) { if (selector === '#app') return app; if (!elements.has(selector)) elements.set(selector, element()); return elements.get(selector); },
    addEventListener(name, handler) { events[name] = handler; },
  };
  const history = {
    state: null, movements: [],
    replaceState(state, _, url) { this.state = state; location.href = new URL(url, location).href; },
    pushState(state, _, url) { this.replaceState(state, _, url); },
    go(delta) { this.movements.push(delta); },
  };
  const browser = {
    allowLeave: false, confirmations: 0,
    addEventListener(name, handler) { windowEvents[name] = handler; },
    confirm(message) { this.confirmations++; this.lastConfirmation = message; return this.allowLeave; }, scrollTo() {},
  };
  let failSave = false;
  vm.runInNewContext(fs.readFileSync(`${__dirname}/app.js`, 'utf8'), {
    clearTimeout() {}, document, window: browser, location, history, URL, URLSearchParams, navigator: { language: 'en' },
    localStorage: { getItem() { return null; }, setItem() {} }, getComputedStyle() { return { backgroundColor: '#000' }; },
    fetch: async (url, options) => {
      requests.push({ url, ...options });
      if (url === '/api/session') return { ok: true, status: 200, json: async () => ({ mode: 'local', user: { id: 'local', username: 'local', role: 'admin', active: true }, csrf_token: 'session-token', llm: { ready: true }, mail: { configured: true } }) };
      if (url === '/api/tasks') return { ok: true, status: 200, json: async () => ({ tasks: [], csrf_token: 'task-token' }) };
      if (url === '/api/mail') return { ok: true, json: async () => ({ mail: snapshot.mail, recipients: snapshot.values.ISBE_EMAIL_DEFAULT_RECIPIENTS || '', csrf_token: 'test-csrf' }) };
      if (url.startsWith('/api/mail/')) return { ok: true, json: mailReply };
      if (url !== '/api/settings') throw new Error('Invalid topic configuration');
      if (options?.method === 'PUT') {
        if (failSave) return { ok: false, status: 400, json: async () => ({ error: 'Invalid service URL' }) };
        for (const [key, value] of Object.entries(JSON.parse(options.body))) {
          if (key.endsWith('_KEY') || key === 'GITHUB_TOKEN' || key === 'ZEPTOMAIL_TOKEN' || key === 'SMTP_PASSWORD') snapshot.configured[key] = !!value;
          else snapshot.values[key] = value ?? '';
          snapshot.sources[key] = value ? 'file' : 'default';
        }
        for (const [key, value] of Object.entries(defaults[snapshot.values.ISBE_LLM_PROVIDER])) if (!snapshot.sources[key] || snapshot.sources[key] === 'default') snapshot.values[key] = value;
        if (snapshot.sources.ISBE_FULLTEXT_EXTRACTOR === 'default') snapshot.values.ISBE_FULLTEXT_EXTRACTOR = snapshot.values.METRAIL_API_URL ? 'metrail' : 'llm';
      }
      return { ok: true, status: 200, json: async () => JSON.parse(JSON.stringify(snapshot)) };
    },
  });
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  await settle();
  return {
    app, requests, location, browser, history, windowEvents, snapshot,
    failSave(value) { failSave = value; },
    input(key, value) { events.input({ target: { dataset: { setting: key }, value } }); },
    recipients(value) { events.input({ target: { id: 'mail-recipients', dataset: {}, value } }); },
    sendMail() { return events.submit({ target: { id: 'mail-form' }, preventDefault() {} }); },
    async change(key, value) {
      await events.change({ target: { dataset: { setting: key }, value } });
      await settle();
    },
    async action(act, extra = {}) {
      const button = { dataset: { act, ...extra } };
      events.click({ target: { closest(selector) { return selector === '[data-act]' ? button : null; } } });
      await settle();
    },
    async navigate(url) {
      events.click({ button: 0, preventDefault() {}, target: { closest(selector) { return selector === 'a[data-link]' ? { getAttribute() { return url; } } : null; } } });
      await settle();
    },
    async save() { await events.submit({ target: { id: 'settings-form' }, preventDefault() {} }); },
  };
}

test('mail defaults can be saved and cleared; per-send recipients never overwrite them', async () => {
  const c = await consoleApp();
  c.input('ISBE_EMAIL_DEFAULT_RECIPIENTS', 'saved@example.com');
  await c.save();
  await c.action('mailTest');
  assert.match(c.app.innerHTML, /id="mail-recipients"[^>]*value="saved@example.com"/);
  c.recipients('temporary@example.com; TEMPORARY@example.com');
  c.browser.allowLeave = true;
  await c.sendMail();
  const post = c.requests.find((r) => r.method === 'POST');
  assert.equal(post.url, '/api/mail/test');
  assert.deepEqual(JSON.parse(post.body), { recipients: 'TEMPORARY@example.com' });
  assert.equal(post.headers['X-ISBE-CSRF'], 'test-csrf');
  assert.match(c.app.innerHTML, /inbox delivery is not confirmed/);
  await c.sendMail();
  assert.equal(c.requests.filter((r) => r.method === 'POST').length, 1);
  await c.action('mailClose');
  await c.action('mailTest');
  assert.match(c.app.innerHTML, /id="mail-recipients"[^>]*value="saved@example.com"/);
  c.input('ISBE_EMAIL_DEFAULT_RECIPIENTS', '');
  await c.save();
  await c.action('mailClose');
  await c.action('mailTest');
  assert.match(c.app.innerHTML, /id="mail-recipients"[^>]*value=""/);
  c.recipients('manual@example.com');
  await c.sendMail();
  assert.equal(c.requests.filter((r) => r.method === 'PUT').length, 2);
});

test('original-page DNS setting preserves saved selection and uses the existing dirty and save flow', async () => {
  const c = await consoleApp({ISBE_ARTICLE_DNS:'system'}, {ISBE_ARTICLE_DNS:'default'});
  assert.match(c.app.innerHTML,/data-setting="ISBE_ARTICLE_DNS"[^>]*>[\s\S]*?<option value="system" selected>System default/);
  assert.match(c.app.innerHTML,/Cloudflare receives public domain names/);
  await c.change('ISBE_ARTICLE_DNS','cloudflare'); await c.action('panel');
  assert.match(c.app.innerHTML,/<option value="cloudflare" selected>Cloudflare encrypted DNS/);
  await c.save();
  const put = c.requests.find((request) => request.method === 'PUT');
  assert.deepEqual(JSON.parse(put.body),{ISBE_ARTICLE_DNS:'cloudflare'});
  assert.equal(put.headers['X-ISBE-CSRF'],'test-csrf');
  assert.equal(c.snapshot.values.ISBE_ARTICLE_DNS,'cloudflare');
  assert.match(c.app.innerHTML,/id="settings-save" disabled/);
  await c.change('ISBE_ARTICLE_DNS','system'); await c.action('discardSettings');
  assert.match(c.app.innerHTML,/<option value="cloudflare" selected>Cloudflare encrypted DNS/);
});

test('mail confirmation identifies exact report and cancellation does not send', async () => {
  const c = await consoleApp();
  await c.action('mailOpen', { topic: 'history-topic', period: '2026-W40' });
  c.recipients('reader@example.com');
  await c.sendMail();
  assert.equal(c.requests.filter((r) => r.method === 'POST').length, 0);
  assert.match(c.browser.lastConfirmation, /Topic: history-topic\nPeriod: 2026-W40\nSubject: ISBE · history-topic · 2026-W40\nRecipients for this email: reader@example.com/);
  c.browser.allowLeave = true;
  await c.sendMail();
  const post = c.requests.find((r) => r.method === 'POST');
  assert.deepEqual(JSON.parse(post.body), { topic: 'history-topic', period: '2026-W40', recipients: 'reader@example.com' });
  assert.equal(post.url, '/api/mail/report');
});

test('pending email blocks duplicate submits and departure; unknown results do not retry', async () => {
  let finish;
  const c = await consoleApp({}, {}, () => new Promise((resolve) => { finish = resolve; }));
  await c.action('mailTest');
  c.recipients('reader@example.com');
  c.browser.allowLeave = true;
  const pending = c.sendMail();
  await new Promise(setImmediate);
  await c.sendMail();
  await c.action('mailTest');
  await c.navigate('/topics');
  assert.equal(c.location.pathname, '/settings');
  assert.equal(c.requests.filter((r) => r.method === 'POST').length, 1);
  assert.match(c.app.innerHTML, /id="mail-form"[^]*?<fieldset disabled/);
  finish({ state: 'unknown' });
  await pending;
  assert.match(c.app.innerHTML, /Result unknown/);
  await c.sendMail();
  assert.equal(c.requests.filter((r) => r.method === 'POST').length, 1);
});

test('mail network failures become unknown while explicit provider rejection stays failed', async () => {
  for (const [response, text] of [[async () => { throw new Error('connection lost'); }, /Result unknown/], [async () => ({ state: 'failed', message: 'Provider rejected authentication' }), /Sending failed.*Provider rejected authentication/]]) {
    const c = await consoleApp({}, {}, response);
    await c.action('mailTest'); c.recipients('reader@example.com'); c.browser.allowLeave = true;
    await c.sendMail();
    assert.match(c.app.innerHTML, text);
    assert.equal(c.requests.filter((r) => r.method === 'POST').length, 1);
  }
});

test('settings loads without overview; drafts survive appearance and language changes', async () => {
  const c = await consoleApp();
  assert.deepEqual(c.requests.map((r) => r.url), ['/api/session', '/api/settings']);
  assert.match(c.app.innerHTML, /id="setting-ANTHROPIC_API_KEY"[^>]*disabled[^>]*value=""/);
  await c.change('ISBE_LLM_PROVIDER', 'openai');
  c.input('ISBE_LLM_MODEL_SMART', '<draft-model>');
  c.input('OPENAI_API_KEY', 'replacement-secret');
  await c.action('panel');
  await c.action('lang', { v: 'zh' });
  assert.match(c.app.innerHTML, /value="&lt;draft-model&gt;"/);
  assert.match(c.app.innerHTML, /value="replacement-secret"/);
  assert.match(c.app.innerHTML, /保存设置/);
  await c.save();
  const put = c.requests.find((r) => r.method === 'PUT');
  assert.deepEqual(JSON.parse(put.body), { ISBE_LLM_PROVIDER: 'openai', ISBE_LLM_MODEL_SMART: '<draft-model>', OPENAI_API_KEY: 'replacement-secret' });
  assert.equal(put.headers['X-ISBE-CSRF'], 'test-csrf');
  assert.doesNotMatch(c.app.innerHTML, /replacement-secret/);
  assert.match(c.app.innerHTML, /设置已保存/);
});

test('provider edits do not persist previous defaults or untouched secrets', async () => {
  const c = await consoleApp();
  c.input('ANTHROPIC_API_KEY', 'environment-must-win');
  await c.change('ISBE_LLM_PROVIDER', 'openai');
  c.input('OPENAI_API_KEY', '');
  await c.save();
  assert.deepEqual(JSON.parse(c.requests.find((r) => r.method === 'PUT').body), { ISBE_LLM_PROVIDER: 'openai' });
});

test('default provider fields follow switches before saving and after reload', async () => {
  const c = await consoleApp();
  const hasDefaults = (provider) => {
    for (const [key, value] of Object.entries(defaults[provider])) assert.match(c.app.innerHTML, new RegExp(`id="setting-${key}"[^>]*value="${value.replaceAll('.', '\\.') }"`));
  };
  hasDefaults('anthropic');
  await c.change('ISBE_LLM_PROVIDER', 'openai'); hasDefaults('openai');
  await c.change('ISBE_LLM_PROVIDER', 'deepseek'); hasDefaults('deepseek');
  await c.change('ISBE_LLM_PROVIDER', 'anthropic'); hasDefaults('anthropic');
  await c.change('ISBE_LLM_PROVIDER', 'openai'); await c.save(); hasDefaults('openai');
  assert.deepEqual(JSON.parse(c.requests.find((r) => r.method === 'PUT').body), { ISBE_LLM_PROVIDER: 'openai' });
  await c.navigate('/topics'); await c.navigate('/settings'); hasDefaults('openai');
  await c.change('ISBE_LLM_PROVIDER', 'anthropic'); hasDefaults('anthropic');
});

test('provider switches keep file, environment and explicit edits, including values equal to a default', async () => {
  const c = await consoleApp({ ISBE_LLM_BASE_URL: 'http://localhost:9000/v1', ISBE_LLM_MODEL_FAST: 'env-model' }, { ISBE_LLM_BASE_URL: 'file', ISBE_LLM_MODEL_FAST: 'environment' });
  c.input('ISBE_LLM_MODEL_SMART', defaults.anthropic.ISBE_LLM_MODEL_SMART);
  await c.change('ISBE_LLM_PROVIDER', 'openai');
  assert.match(c.app.innerHTML, /value="http:\/\/localhost:9000\/v1"/);
  assert.match(c.app.innerHTML, /id="setting-ISBE_LLM_MODEL_FAST"[^>]*disabled[^>]*value="env-model"/);
  assert.match(c.app.innerHTML, /value="claude-sonnet-5-5"/);
  await c.save();
  assert.deepEqual(JSON.parse(c.requests.find((r) => r.method === 'PUT').body), { ISBE_LLM_PROVIDER: 'openai', ISBE_LLM_MODEL_SMART: 'claude-sonnet-5-5' });
  await c.navigate('/topics'); await c.navigate('/settings');
  await c.change('ISBE_LLM_PROVIDER', 'deepseek');
  assert.match(c.app.innerHTML, /value="http:\/\/localhost:9000\/v1"/);
  assert.match(c.app.innerHTML, /value="claude-sonnet-5-5"/);
  c.input('ISBE_LLM_BASE_URL', ''); c.input('ISBE_LLM_MODEL_SMART', '');
  await c.save();
  assert.match(c.app.innerHTML, /value="https:\/\/api.deepseek.com"/);
  assert.match(c.app.innerHTML, /value="deepseek-chat"/);
  await c.change('ISBE_LLM_PROVIDER', 'openai');
  assert.match(c.app.innerHTML, /value="https:\/\/api.openai.com\/v1"/);
  assert.match(c.app.innerHTML, /value="gpt-5"/);
});

test('saving incomplete settings is explicit and local no-auth does not claim a connection test', async () => {
  const c = await consoleApp();
  await c.change('ISBE_LLM_PROVIDER', 'openai');
  c.snapshot.llm = { ready: false, error: 'OPENAI_API_KEY is not set', connection_verified: false };
  await c.save();
  assert.match(c.app.innerHTML, /Settings saved/);
  assert.match(c.app.innerHTML, /Model configuration incomplete/);
  assert.match(c.app.innerHTML, /OPENAI_API_KEY is not set/);
  await c.change('ISBE_LLM_NO_AUTH', 'true');
  c.input('ISBE_LLM_BASE_URL', 'http://localhost:11434/v1');
  c.snapshot.llm = { ready: true, error: null, connection_verified: false };
  await c.save();
  assert.match(c.app.innerHTML, /Model configuration complete; connection not verified/);
  assert.match(c.app.innerHTML, /Authorization is omitted even if an old key exists/);
  assert.deepEqual(JSON.parse(c.requests.filter((r) => r.method === 'PUT').at(-1).body), { ISBE_LLM_NO_AUTH: 'true', ISBE_LLM_BASE_URL: 'http://localhost:11434/v1' });
});

test('explicit key removal survives save errors; invalid JSON never submits', async () => {
  const c = await consoleApp();
  c.input('ISBE_LLM_EXTRA_BODY', '[]');
  await c.save();
  assert.equal(c.requests.filter((r) => r.method === 'PUT').length, 0);
  assert.match(c.app.innerHTML, /Extra request parameters must be a JSON object/);
  c.input('ISBE_LLM_EXTRA_BODY', '{}');
  await c.change('ISBE_LLM_PROVIDER', 'openai');
  await c.action('removeSecret', { key: 'OPENAI_API_KEY' });
  c.failSave(true);
  await c.save();
  assert.match(c.app.innerHTML, /Invalid service URL/);
  assert.match(c.app.innerHTML, /Key will be removed on save/);
  c.failSave(false);
  await c.save();
  assert.deepEqual(JSON.parse(c.requests.at(-1).body), { ISBE_LLM_PROVIDER: 'openai', OPENAI_API_KEY: null });
  assert.match(c.app.innerHTML, /Settings saved/);
});

test('provider selection shows one matching key with its own source, lock, and draft', async () => {
  const c = await consoleApp();
  const input = (key) => c.app.innerHTML.match(new RegExp(`<input[^>]*id="setting-${key}"[^>]*>`))?.[0];
  const visibleKeys = () => ['ANTHROPIC_API_KEY', 'DEEPSEEK_API_KEY', 'OPENAI_API_KEY'].filter((key) => input(key));
  assert.deepEqual(visibleKeys(), ['ANTHROPIC_API_KEY']);
  assert.match(input('ANTHROPIC_API_KEY'), /disabled[^>]*value=""/);
  assert.match(c.app.innerHTML, /Environment · configured/);

  await c.change('ISBE_LLM_PROVIDER', 'deepseek');
  assert.deepEqual(visibleKeys(), ['DEEPSEEK_API_KEY']);
  assert.doesNotMatch(input('DEEPSEEK_API_KEY'), /disabled/);
  assert.match(c.app.innerHTML, /Default · not set/);
  c.input('DEEPSEEK_API_KEY', 'deepseek-draft');

  await c.change('ISBE_LLM_PROVIDER', 'openai');
  assert.deepEqual(visibleKeys(), ['OPENAI_API_KEY']);
  assert.doesNotMatch(input('OPENAI_API_KEY'), /disabled/);
  assert.match(input('OPENAI_API_KEY'), /value=""/);
  assert.match(c.app.innerHTML, /Local settings · configured/);
  c.input('OPENAI_API_KEY', 'openai-draft');

  await c.change('ISBE_LLM_PROVIDER', 'deepseek');
  assert.deepEqual(visibleKeys(), ['DEEPSEEK_API_KEY']);
  assert.match(input('DEEPSEEK_API_KEY'), /value="deepseek-draft"/);
  assert.doesNotMatch(c.app.innerHTML, /openai-draft/);
  await c.change('ISBE_LLM_PROVIDER', 'anthropic');
  assert.deepEqual(visibleKeys(), ['ANTHROPIC_API_KEY']);
  assert.match(input('ANTHROPIC_API_KEY'), /disabled[^>]*value=""/);
  await c.change('ISBE_LLM_PROVIDER', 'openai');
  assert.match(input('OPENAI_API_KEY'), /value="openai-draft"/);
  await c.save();
  assert.deepEqual(JSON.parse(c.requests.find((r) => r.method === 'PUT').body), {
    ISBE_LLM_PROVIDER: 'openai', DEEPSEEK_API_KEY: 'deepseek-draft', OPENAI_API_KEY: 'openai-draft',
  });
  assert.deepEqual(visibleKeys(), ['OPENAI_API_KEY']);
  assert.match(input('OPENAI_API_KEY'), /value=""/);
  await c.change('ISBE_LLM_PROVIDER', 'deepseek');
  assert.deepEqual(visibleKeys(), ['DEEPSEEK_API_KEY']);
  assert.match(input('DEEPSEEK_API_KEY'), /value=""/);
  assert.match(c.app.innerHTML, /Local settings · configured/);
  assert.doesNotMatch(c.app.innerHTML, /deepseek-draft|openai-draft/);
});

test('dirty navigation asks before discarding; reload is guarded', async () => {
  const c = await consoleApp();
  c.input('ISBE_LLM_MODEL_SMART', 'new-model');
  let prevented = false;
  c.windowEvents.beforeunload({ preventDefault() { prevented = true; } });
  assert.equal(prevented, true);
  await c.navigate('/runs');
  assert.equal(c.location.pathname, '/settings');
  assert.equal(c.browser.confirmations, 1);
  c.browser.allowLeave = true;
  await c.navigate('/runs');
  assert.equal(c.location.pathname, '/runs');
  prevented = false;
  c.windowEvents.beforeunload({ preventDefault() { prevented = true; } });
  assert.equal(prevented, false);
});

test('full-text defaults to LLM with one existing provider key and optional paper services', async () => {
  const c = await consoleApp();
  assert.match(c.app.innerHTML, /<option value="llm" selected>LLM extraction \(arXiv HTML\)<\/option>/);
  assert.doesNotMatch(c.app.innerHTML, /id="setting-METRAIL_API_URL"/);
  assert.match(c.app.innerHTML, /provider, fast model, and key above/);
  assert.match(c.app.innerHTML, /verbatim quote checks/);
  assert.match(c.app.innerHTML, /Papers without HTML use abstracts/);
  assert.match(c.app.innerHTML, /does not run PDF OCR or recognize image content/);
  assert.match(c.app.innerHTML, /Save captioned source figures locally for reports/);
  assert.match(c.app.innerHTML, /Keep panels together without extra vision-model calls/);
  assert.match(c.app.innerHTML, /Missing or failed images leave a text report/);
  assert.match(c.app.innerHTML, /cached HTML papers can gain figures/);
  assert.match(c.app.innerHTML, /public arXiv API by default/);
  assert.match(c.app.innerHTML, /each topic uses metrail when enabled with a URL, otherwise LLM/);
  assert.equal((c.app.innerHTML.match(/id="setting-(?:ANTHROPIC|OPENAI|DEEPSEEK)_API_KEY"/g) || []).length, 1);
  assert.match(c.app.innerHTML, /<details id="settings-sources-advanced"><summary>Advanced source options<\/summary><div class="settings-grid">[\s\S]*?id="setting-ARXIV_API_URL"[\s\S]*?id="setting-GITHUB_API_URL"/);
  await c.action('lang', { v: 'zh' });
  assert.match(c.app.innerHTML, /<option value="llm" selected>LLM 提取（arXiv HTML）<\/option>/);
  assert.match(c.app.innerHTML, /HTML 不可用时使用摘要/);
  assert.match(c.app.innerHTML, /组图保留多个面板，不增加视觉模型调用/);
});

test('metrail appears only when selected and its URL draft survives switching and saving', async () => {
  const c = await consoleApp();
  await c.change('ISBE_FULLTEXT_EXTRACTOR', 'metrail');
  assert.match(c.app.innerHTML, /<option value="metrail" selected>/);
  assert.match(c.app.innerHTML, /id="setting-METRAIL_API_URL"[^>]*value=""/);
  assert.match(c.app.innerHTML, /This mode needs a service URL/);
  c.input('METRAIL_API_URL', 'http://localhost:9000');
  await c.change('ISBE_FULLTEXT_EXTRACTOR', 'off');
  assert.match(c.app.innerHTML, /<option value="off" selected>Disable full-text fetching/);
  assert.match(c.app.innerHTML, /Do not fetch new full text or create or refresh fact cards/);
  assert.match(c.app.innerHTML, /Keep existing text and card caches/);
  assert.doesNotMatch(c.app.innerHTML, /id="setting-METRAIL_API_URL"/);
  await c.action('panel');
  await c.change('ISBE_FULLTEXT_EXTRACTOR', 'metrail');
  assert.match(c.app.innerHTML, /id="setting-METRAIL_API_URL"[^>]*value="http:\/\/localhost:9000"/);
  await c.save();
  assert.deepEqual(JSON.parse(c.requests.at(-1).body), { ISBE_FULLTEXT_EXTRACTOR: 'metrail', METRAIL_API_URL: 'http://localhost:9000' });
  await c.change('ISBE_FULLTEXT_EXTRACTOR', 'llm');
  await c.save();
  assert.deepEqual(JSON.parse(c.requests.at(-1).body), { ISBE_FULLTEXT_EXTRACTOR: 'llm' });
  await c.change('ISBE_FULLTEXT_EXTRACTOR', 'metrail');
  assert.match(c.app.innerHTML, /id="setting-METRAIL_API_URL"[^>]*value="http:\/\/localhost:9000"/);
  c.input('METRAIL_API_URL', 'http://localhost:9001');
  await c.change('ISBE_FULLTEXT_EXTRACTOR', 'llm');
  await c.save();
  assert.deepEqual(JSON.parse(c.requests.at(-1).body), { METRAIL_API_URL: 'http://localhost:9001' });
  assert.doesNotMatch(c.app.innerHTML, /id="setting-METRAIL_API_URL"/);
  await c.change('ISBE_FULLTEXT_EXTRACTOR', 'metrail');
  assert.match(c.app.innerHTML, /id="setting-METRAIL_API_URL"[^>]*value="http:\/\/localhost:9001"/);
});

test('effective metrail defaults and environment locks come from the settings snapshot', async () => {
  const c = await consoleApp({ METRAIL_API_URL: 'http://existing-parser:9000' }, { METRAIL_API_URL: 'file' });
  assert.match(c.app.innerHTML, /<option value="metrail" selected>/);
  assert.match(c.app.innerHTML, /id="setting-METRAIL_API_URL"[^>]*value="http:\/\/existing-parser:9000"/);
  await c.change('ISBE_FULLTEXT_EXTRACTOR', 'off');
  await c.save();
  assert.deepEqual(JSON.parse(c.requests.at(-1).body), { ISBE_FULLTEXT_EXTRACTOR: 'off' });
  await c.change('ISBE_FULLTEXT_EXTRACTOR', 'metrail');
  assert.match(c.app.innerHTML, /id="setting-METRAIL_API_URL"[^>]*value="http:\/\/existing-parser:9000"/);

  const locked = await consoleApp({ ISBE_FULLTEXT_EXTRACTOR: 'off' }, { ISBE_FULLTEXT_EXTRACTOR: 'environment' });
  assert.match(locked.app.innerHTML, /<select[^>]*id="setting-ISBE_FULLTEXT_EXTRACTOR"[^>]*disabled>/);
  await locked.change('ISBE_FULLTEXT_EXTRACTOR', 'metrail');
  assert.match(locked.app.innerHTML, /<option value="off" selected>/);
  assert.doesNotMatch(locked.app.innerHTML, /id="setting-METRAIL_API_URL"/);
  await locked.save();
  assert.equal(locked.requests.filter((r) => r.method === 'PUT').length, 0);
});

test('explicit default LLM selection stays fixed when a hidden metrail URL is saved', async () => {
  const c = await consoleApp();
  await c.change('ISBE_FULLTEXT_EXTRACTOR', 'metrail');
  c.input('METRAIL_API_URL', 'http://later-parser:9000');
  await c.change('ISBE_FULLTEXT_EXTRACTOR', 'llm');
  assert.doesNotMatch(c.app.innerHTML, /No global method is fixed yet/);
  assert.doesNotMatch(c.app.innerHTML, /id="setting-METRAIL_API_URL"/);
  await c.save();
  assert.deepEqual(JSON.parse(c.requests.at(-1).body), {
    ISBE_FULLTEXT_EXTRACTOR: 'llm', METRAIL_API_URL: 'http://later-parser:9000',
  });
  assert.match(c.app.innerHTML, /<option value="llm" selected>/);
  assert.doesNotMatch(c.app.innerHTML, /id="setting-METRAIL_API_URL"/);
  assert.doesNotMatch(c.app.innerHTML, /No global method is fixed yet/);
  await c.change('ISBE_FULLTEXT_EXTRACTOR', 'metrail');
  assert.match(c.app.innerHTML, /id="setting-METRAIL_API_URL"[^>]*value="http:\/\/later-parser:9000"/);
});


test('mail sender settings save without returning token and respect environment locks', async () => {
  const c = await consoleApp({}, { ZEPTOMAIL_API_URL: 'environment' });
  assert.match(c.app.innerHTML, /id="setting-ZEPTOMAIL_API_URL"[^>]*disabled/);
  assert.match(c.app.innerHTML, /id="setting-ZEPTOMAIL_TOKEN"[^>]*type="password"/);
  c.input('ZEPTOMAIL_TOKEN', 'private-mail-token');
  c.input('SMTP_FROM', 'sender@example.com');
  await c.save();
  const changes = JSON.parse(c.requests.find(r => r.method === 'PUT').body);
  assert.equal(changes.ZEPTOMAIL_TOKEN, 'private-mail-token');
  assert.equal(changes.SMTP_FROM, 'sender@example.com');
  assert.doesNotMatch(c.app.innerHTML, /private-mail-token/);
});


test('SMTP method shows its own fields and preserves drafts across method switches', async () => {
  const c = await consoleApp({ EMAIL_TRANSPORT: 'smtp' });
  assert.match(c.app.innerHTML, /id="setting-SMTP_HOST"/);
  assert.match(c.app.innerHTML, /id="setting-SMTP_PASSWORD"[^>]*type="password"/);
  assert.doesNotMatch(c.app.innerHTML, /id="setting-ZEPTOMAIL_API_URL"/);
  c.input('SMTP_HOST', 'smtp.example.com');
  c.input('SMTP_PASSWORD', 'smtp-private-password');
  c.input('SMTP_USER', 'sender@example.com');
  await c.change('EMAIL_TRANSPORT', 'zeptomail_api');
  assert.match(c.app.innerHTML, /id="setting-ZEPTOMAIL_API_URL"/);
  assert.doesNotMatch(c.app.innerHTML, /id="setting-SMTP_HOST"/);
  await c.change('EMAIL_TRANSPORT', 'smtp');
  assert.match(c.app.innerHTML, /id="setting-SMTP_HOST"[^>]*value="smtp.example.com"/);
  await c.save();
  const changes = JSON.parse(c.requests.find(r => r.method === 'PUT').body);
  assert.equal(changes.SMTP_HOST, 'smtp.example.com');
  assert.equal(changes.SMTP_PASSWORD, 'smtp-private-password');
  assert.doesNotMatch(c.app.innerHTML, /smtp-private-password/);
});

test('administrators get a help link that leaves the single-page app for /help', async () => {
  const c = await consoleApp();
  assert.match(c.app.innerHTML, /<a href="\/help" class="setting-hint">Help<\/a>/);
});
