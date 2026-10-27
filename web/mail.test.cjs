// Sending a report by email from the classic Reports page. The test email
// on Settings moved to the React console with that page (#79).
// Run with: node --test web/mail.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const REPORT = { topic: 'history-topic', topic_id: 'history-topic', period: '2026-W40', kind: 'weekly', label: 'History', issues: [{ period: '2026-W40' }], html: '<p>Report</p>' };

async function consoleApp(mailReply = async () => ({ state: 'accepted', request_id: 'test-id' }), defaults = '') {
  const events = {}, windowEvents = {}, requests = [], elements = new Map();
  const location = new URL('http://localhost/reports?topic=history-topic&period=2026-W40');
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
  const ok = (body) => ({ ok: true, status: 200, json: async () => body });
  vm.runInNewContext(fs.readFileSync(`${__dirname}/app.js`, 'utf8'), {
    clearTimeout() {}, document, window: browser, location, history, URL, URLSearchParams, navigator: { language: 'en' },
    localStorage: { getItem() { return null; }, setItem() {} }, getComputedStyle() { return { backgroundColor: '#000' }; },
    DOMParser: class { parseFromString(html) { return { body: { innerHTML: html }, querySelectorAll() { return []; } }; } },
    fetch: async (url, options) => {
      requests.push({ url, ...options });
      if (url === '/api/session') return ok({ mode: 'local', user: { id: 'local', username: 'local', role: 'admin', active: true }, csrf_token: 'session-token', llm: { ready: true }, mail: { configured: true } });
      if (url.startsWith('/api/overview')) return ok({ topics: [], pending: [], services: {}, reports: [], runs: [], llm: {}, now: '2026-10-19T08:00:00Z' });
      if (url.startsWith('/api/report?')) return ok(REPORT);
      if (url === '/api/mail') return ok({ mail: { configured: true, sender: 'sender@example.com' }, recipients: defaults, csrf_token: 'test-csrf' });
      if (url.startsWith('/api/mail/')) return { ok: true, json: mailReply };
      throw new Error(`unexpected ${url}`);
    },
  });
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  await settle();
  return {
    app, requests, location, browser,
    recipients(value) { events.input({ target: { id: 'mail-recipients', dataset: {}, value } }); },
    sendMail() { return events.submit({ target: { id: 'mail-form' }, preventDefault() {} }); },
    async action(act, extra = {}) {
      const button = { dataset: { act, ...extra } };
      events.click({ target: { closest(selector) { return selector === '[data-act]' ? button : null; } } });
      await settle();
    },
    async navigate(url) {
      events.click({ button: 0, preventDefault() {}, target: { closest(selector) { return selector === 'a[data-link]' ? { getAttribute() { return url; } } : null; } } });
      await settle();
    },
  };
}

test('the report email starts from the default recipients and sends one deduplicated list', async () => {
  const c = await consoleApp(undefined, 'saved@example.com');
  await c.action('mailOpen', { topic: 'history-topic', period: '2026-W40' });
  assert.match(c.app.innerHTML, /id="mail-form"/);
  assert.match(c.app.innerHTML, /id="mail-recipients"[^>]*value="saved@example.com"/);
  assert.doesNotMatch(c.app.innerHTML, /Send test email/);
  c.recipients('temporary@example.com; TEMPORARY@example.com');
  c.browser.allowLeave = true;
  await c.sendMail();
  const post = c.requests.find((r) => r.method === 'POST');
  assert.equal(post.url, '/api/mail/report');
  assert.deepEqual(JSON.parse(post.body), { topic: 'history-topic', period: '2026-W40', recipients: 'TEMPORARY@example.com' });
  assert.equal(post.headers['X-ISBE-CSRF'], 'test-csrf');
  assert.match(c.app.innerHTML, /inbox delivery is not confirmed/);
  await c.sendMail();
  assert.equal(c.requests.filter((r) => r.method === 'POST').length, 1);
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
  const c = await consoleApp(() => new Promise((resolve) => { finish = resolve; }));
  await c.action('mailOpen', { topic: 'history-topic', period: '2026-W40' });
  c.recipients('reader@example.com');
  c.browser.allowLeave = true;
  const pending = c.sendMail();
  await new Promise(setImmediate);
  await c.sendMail();
  await c.action('mailOpen', { topic: 'other-topic', period: '2026-W41' });
  await c.navigate('/topics');
  assert.equal(c.location.pathname, '/reports');
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
    const c = await consoleApp(response);
    await c.action('mailOpen', { topic: 'history-topic', period: '2026-W40' }); c.recipients('reader@example.com'); c.browser.allowLeave = true;
    await c.sendMail();
    assert.match(c.app.innerHTML, text);
    assert.equal(c.requests.filter((r) => r.method === 'POST').length, 1);
  }
});
