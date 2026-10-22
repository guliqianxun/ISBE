const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const paper = (id = 'papers') => ({ id, config: { id, label: `Research ${id}`, arxiv: { categories: ['cs.AI'], include_keywords: ['agents'] } }, revision: '1' });
const job = (state = 'running', topic = 'papers', id = 'job-1') => ({ id, owner: 'local', topic, state, stage: state === 'running' ? 'collect' : 'digest', error: null, result: state === 'succeeded' ? { topic, period: '2026-W42' } : null });

async function consoleApp({ topics = [paper()], runs = [], ready = true, initialJob = null, marker = null, path = '/reports', language = 'en', mode = 'local', confirm = false, user = { id: 'local', username: 'local', role: 'admin', active: true } } = {}) {
  const events = {}, elements = new Map(), requests = [], timers = new Map();
  const saved = new Map(marker ? [[user?.id === 'local' ? 'isbe.generation' : 'isbe.generation.' + user?.id, JSON.stringify(marker)]] : []);
  const location = new URL(path, 'http://localhost');
  let readGate = null, loginUser = user || { id: 'alice', username: 'alice', role: 'user' }, expired = false, currentUser = user, users = [{ id: 'local', username: 'admin', role: 'admin', active: true }], currentJob = initialJob, timerId = 0, statusFailure = false, configFailure = false, postReply = null, release = null;
  const element = (id = '') => ({ id, dataset: {}, style: { setProperty() {} }, open: false,
    focus() { document.activeElement = this; }, contains(el) { return el?.id?.startsWith('guide-'); },
    showModal() { this.open = true; this.shows = (this.shows || 0) + 1; }, close() { this.open = false; },
    querySelector() { return null; }, insertAdjacentHTML() {}, scrollIntoView() {},
  });
  const app = element('app');
  const document = { documentElement: {}, body: element(), activeElement: null,
    querySelector(selector) { if (selector === '#app') return app; if (!elements.has(selector)) elements.set(selector, element(selector.slice(1))); return elements.get(selector); },
    addEventListener(name, fn) { events[name] = fn; },
  };
  const history = { replaceState(state, _, url) { this.state = state; location.href = new URL(url, location).href; }, pushState(state, _, url) { this.replaceState(state, _, url); } };
  const settings = { llm: { ready }, values: { ISBE_LLM_PROVIDER: 'anthropic', ISBE_FULLTEXT_EXTRACTOR: 'llm' }, sources: {}, configured: {}, csrf_token: 'settings-token' };
  const response = (status, data) => ({ ok: status < 400, status, json: async () => JSON.parse(JSON.stringify(data)) });
  vm.runInNewContext(fs.readFileSync(`${__dirname}/app.js`, 'utf8'), {
    crypto: require('node:crypto').webcrypto, document, history, location, URL, URLSearchParams,
    window: { addEventListener() {}, confirm() { return confirm; }, scrollTo() {} }, navigator: { language },
    localStorage: { getItem() { return null; }, setItem() {} },
    sessionStorage: { getItem: (k) => saved.get(k) || null, setItem: (k, v) => saved.set(k, v), removeItem: (k) => saved.delete(k) },
    getComputedStyle: () => ({ backgroundColor: '#000' }),
    setTimeout(fn) { timers.set(++timerId, fn); return timerId; }, clearTimeout(id) { timers.delete(id); },
    fetch: async (url, options) => {
      requests.push({ url, ...options });
      if (url === '/api/session') return configFailure ? response(500, {}) : response(200, { mode, user: currentUser, csrf_token: 'session-token', llm: { ready }, mail: { configured: true } });
      if (url === '/api/login') { if (JSON.parse(options.body).password !== 'correct-password') return response(401, {}); currentUser = loginUser; expired = false; return response(200, { mode, user: currentUser, csrf_token: 'login-token', llm: { ready }, mail: {} }); }
      if (url === '/api/logout') { currentUser = null; return response(200, {}); }
      if (expired) return response(401, {});
      if (url === '/api/users') { if (options?.method === 'POST') { const body = JSON.parse(options.body); const value = { id: 'new-user', username: body.username, role: 'user', active: true }; users.push(value); return response(200, { user: value }); } return response(200, { users, csrf_token: 'users-token' }); }
      if (url.startsWith('/api/users/')) { const value = users.find((u) => u.id === url.split('/').pop()); const body = JSON.parse(options.body); if ('active' in body) value.active = body.active; return response(200, { user: value }); }
      if (url.endsWith('/cancel')) { currentJob = { ...currentJob, state: 'cancelled' }; return response(200, { job: currentJob }); }
      if (url === '/api/tasks' || url.startsWith('/api/tasks/')) {
        if (options?.method === 'POST') {
          if (release) await new Promise((resolve) => { release.resolve = resolve; });
          if (postReply instanceof Error) throw postReply;
          if (postReply) return response(postReply.status, postReply.data);
          currentJob = { ...job('running', JSON.parse(options.body).topic), owner: currentUser.id };
          return response(202, { job: currentJob });
        }
        if (statusFailure) throw new Error('/srv/private/provider-key');
        const snapshot = response(200, url === '/api/tasks' ? { csrf_token: 'generation-token', tasks: currentJob ? [currentJob] : [] } : { csrf_token: 'generation-token', job: currentJob && currentJob.id === url.split('/').pop() ? currentJob : null });
        if (readGate) { const gate = readGate; readGate = null; await new Promise((resolve) => { gate.release = resolve; }); }
        return snapshot;
      }
      if (url === '/api/settings') return configFailure ? response(500, { error: '/srv/private/config' }) : response(200, settings);
      if (url === '/api/topics') return response(200, { topics: topics.map((t) => ({ id: t.id, label: t.config?.label })), csrf_token: 'topic-token' });
      if (url.startsWith('/api/topics/')) return response(200, topics.find((t) => t.id === decodeURIComponent(url.slice('/api/topics/'.length))) || { error: 'missing' });
      if (url === '/api/overview') return response(200, { topics: [], pending: [], services: {} });
      if (url === '/api/reports') return response(200, []);
      if (url === '/api/runs') return response(200, { runs, purposes: [], calls: [] });
      throw new Error(`Unexpected URL ${url}`);
    },
  });
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  await settle();
  const action = async (act, extra = {}) => {
    events.click({ target: { closest: (s) => s === '[data-act]' ? { dataset: { act, ...extra } } : null } });
    await settle();
  };
  return {
    app, elements, requests, timers, saved, location, settings, document, settle, action,
    expire() { expired = true; currentUser = null; },
    loginAs(value) { loginUser = value; },
    holdRead() { const gate = {}; readGate = gate; return () => gate.release(); },
    async submit(id, dataset = {}) { await events.submit({ target: { id, dataset }, preventDefault() {} }); await settle(); },
    get html() { return elements.get('#report-guide')?.innerHTML || ''; },
    get dialog() { return elements.get('#report-guide'); },
    open: (topic = 'papers') => action('guideOpen', { topic }),
    async next() { await action('guideNext'); },
    async readyToStart() { await action('guideNext'); await action('guideNext'); },
    setJob(value) { currentJob = value; }, failStatus(value) { statusFailure = value; }, failConfig(value) { configFailure = value; },
    reply(status, data) { postReply = { status, data }; }, loseReply() { postReply = new Error('secret provider response'); },
    holdPost() { release = {}; return () => { const held = release; release = null; held.resolve(); }; },
    async poll() { const pending = [...timers.values()]; timers.clear(); for (const fn of pending) await fn(); await settle(); },
    async escape() { let prevented = false; events.cancel({ target: { id: 'report-guide' }, preventDefault() { prevented = true; } }); await settle(); return prevented; },
    async navigate(url) { events.click({ button: 0, preventDefault() {}, target: { closest: (s) => s === 'a[data-link]' ? { getAttribute: () => url } : null } }); await settle(); },
    inputSetting(key, value) { events.input({ target: { dataset: { setting: key }, value } }); },
    inputTopic(key, value) { events.input({ target: { dataset: { topic: key }, value } }); },
    async select(value) { events.change({ target: { dataset: { change: 'guideTopic' }, value } }); await settle(); },
  };
}

test('run ledger exposes per-paper failures and fallback warnings as escaped text', async () => {
  const run = { job: 'enrich', topic_id: 'wind-ensemble', started_at: new Date().toISOString(), finished_at: new Date().toISOString() };
  for (const language of ['en', 'zh-CN']) {
    const c = await consoleApp({ path: '/runs', language, runs: [
      { ...run, status: 'failed', error: '2 of 2 papers failed', stats: JSON.stringify({ failed: { 'arxiv:1': 'download paper PDF: timeout', 'arxiv:2': '<img src=x onerror=alert(1)>' } }) },
      { ...run, status: 'ok', stats: JSON.stringify({ figure_failures: { 'arxiv:3': 'image unavailable' }, abstract_fallback: { 'arxiv:4': 'HTML unavailable' } }) },
      { ...run, status: 'ok', stats: 'broken json' },
      { ...run, status: 'ok', stats: 'null' },
    ] });
    assert.match(c.app.innerHTML, /2 of 2 papers failed/);
    assert.match(c.app.innerHTML, /download paper PDF: timeout/);
    assert.match(c.app.innerHTML, /image unavailable/);
    assert.match(c.app.innerHTML, /HTML unavailable/);
    assert.match(c.app.innerHTML, /&lt;img src=x onerror=alert\(1\)&gt;/);
    assert.doesNotMatch(c.app.innerHTML, /<img src=x/);
    assert.match(c.app.innerHTML, language === 'en' ? /View paper details/ : /查看逐篇详情/);
  }
});

test('guide uses three explicit steps, native focus, and no task starts on opening', async () => {
  const c = await consoleApp();
  assert.equal(c.requests.some((r) => r.url === '/api/tasks'), false);
  await c.open();
  assert.equal(c.dialog.open, true);
  assert.equal(c.document.activeElement.id, 'guide-title');
  assert.match(c.html, /Step 1 of 3/);
  assert.match(c.html, /connection not verified/);
  await c.next();
  assert.match(c.html, /Step 2 of 3/);
  assert.match(c.html, /arXiv categories and keywords are configured/);
  await c.next();
  assert.match(c.html, /Step 3 of 3/);
  assert.match(c.html, /using saved model settings/);
  assert.equal(c.requests.some((r) => r.method === 'POST'), false);
  assert.doesNotMatch(c.html, /isbe --data|uv run|data directory|Docker|\d+%/);
  assert.equal(await c.escape(), true);
  assert.equal(c.dialog.open, false);
});

test('missing model, article-only sources, and invalid paper sources cannot start', async () => {
  const model = await consoleApp({ ready: false }); await model.open(); await model.readyToStart(); await model.action('guideStart');
  assert.match(model.html, /Model configuration incomplete/);
  assert.match(model.html, /id="guide-next"[^>]*disabled/);
  assert.equal(model.requests.some((r) => r.method === 'POST'), false);
  for (const config of [{ rss: { feeds: [{ name: 'News', url: 'https://example.com/feed' }] } }, { arxiv: { categories: [], include_keywords: [] } }]) {
    const c = await consoleApp({ topics: [{ id: 'papers', config }] }); await c.open(); await c.next(); await c.action('guideStart');
    assert.match(c.html, /article sources only|Complete and save/);
    assert.match(c.html, /id="guide-next"[^>]*disabled/);
    assert.equal(c.requests.some((r) => r.method === 'POST'), false);
  }
});

test('explicit start carries CSRF and topic, prevents duplicate pending submissions, and polls stages', async () => {
  const c = await consoleApp(); await c.open(); await c.readyToStart();
  const release = c.holdPost(); await c.action('guideStart'); await c.action('guideStart');
  const posts = c.requests.filter((r) => r.method === 'POST');
  assert.equal(posts.length, 1); assert.equal(posts[0].headers['X-ISBE-CSRF'], 'session-token');
  assert.equal(JSON.parse(posts[0].body).topic, 'papers'); assert.match(JSON.parse(posts[0].body).request_id, /^[a-f0-9]{32}$/);
  assert.match(c.html, /Starting generation/);
  release(); await c.settle();
  assert.match(c.html, /Generating a report for/); assert.equal(c.timers.size, 1);
  c.setJob({ ...job(), stage: 'enrich' }); await c.poll();
  assert.match(c.html, /aria-current="step"><span>Retrieve full text/);
  assert.match(c.html, /role="status" aria-live="polite" aria-atomic="true"/);
  c.setJob(job('succeeded')); await c.poll();
  assert.match(c.html, /Your report is ready/); assert.match(c.html, /href="\/reports\?topic=papers&period=2026-W42"/);
  assert.equal(c.timers.size, 0); assert.equal(c.saved.has('isbe.generation'), false);
});

test('close, reopen, and refresh recover running or completed work without another POST', async () => {
  const c = await consoleApp({ initialJob: job() }); await c.open();
  await c.action('guideClose'); assert.equal(c.timers.size, 0);
  c.setJob({ ...job(), stage: 'triage' }); await c.open();
  assert.match(c.html, /aria-current="step"><span>Filter papers/);
  const marker = JSON.parse(c.saved.get('isbe.generation'));
  const refreshed = await consoleApp({ initialJob: job('succeeded'), marker });
  assert.equal(refreshed.dialog.open, true); assert.match(refreshed.html, /Your report is ready/);
  assert.equal(refreshed.requests.some((r) => r.method === 'POST'), false);
  const lost = await consoleApp({ marker });
  assert.match(lost.html, /was interrupted or could not be found/);
  assert.equal(lost.timers.size, 0); assert.equal(lost.requests.some((r) => r.method === 'POST'), false);
});

test('a running task for another topic does not prevent a new topic submission', async () => {
  const c = await consoleApp({ topics: [paper(), paper('other')], initialJob: job('running', 'other') });
  await c.open('papers'); assert.match(c.html, /Step 1 of 3/); await c.readyToStart(); await c.action('guideStart');
  assert.match(c.html, /Generating a report for “papers”/);
});

test('busy response adopts the server task and status failure stops polling without exposing internals', async () => {
  const c = await consoleApp({ topics: [paper(), paper('other')] }); await c.open(); await c.readyToStart();
  c.reply(409, { code: 'busy', error: 'private internals', job: job('running', 'other') }); await c.action('guideStart');
  assert.match(c.html, /Generating a report for “other”/); assert.doesNotMatch(c.html, /private internals/);
  c.failStatus(true); await c.poll(); assert.match(c.html, /Task status is temporarily unavailable/);
  assert.equal(c.timers.size, 0); assert.doesNotMatch(c.html, /id="guide-start"/);
  assert.doesNotMatch(c.html, /\/srv\/private|provider-key/);
  c.failStatus(false); c.setJob(job('succeeded', 'other')); await c.action('guideCheck');
  assert.match(c.html, /Your report is ready/);
});

test('an ambiguous start is checked with GET and is never resubmitted automatically', async () => {
  const c = await consoleApp(); await c.open(); await c.readyToStart();
  c.loseReply(); c.setJob(job()); await c.action('guideStart');
  assert.match(c.html, /Submission is unconfirmed/); assert.doesNotMatch(c.html, /secret provider response/);
  assert.equal(c.requests.filter((r) => r.method === 'POST').length, 1);
});

test('another report requires a new explicit start and an old completed job cannot confirm that start', async () => {
  const c = await consoleApp({ initialJob: job('succeeded') }); await c.open();
  assert.match(c.html, /Generate another report/); await c.action('guideAgain');
  assert.match(c.html, /Step 1 of 3/); assert.equal(c.requests.some((r) => r.method === 'POST'), false);
  await c.readyToStart(); c.loseReply(); await c.action('guideStart');
  assert.match(c.html, /Submission is unconfirmed/); assert.doesNotMatch(c.html, /Your report is ready/);
  assert.equal(c.requests.filter((r) => r.method === 'POST').length, 1);
});

test('failed jobs show useful no-data feedback while unknown errors and configuration failures stay safe', async () => {
  const noData = "No papers remain in this topic's current window. Review its sources, keywords and filters in Topics, then try again.";
  for (const language of ['en', 'zh-CN']) {
    const c = await consoleApp({ language, initialJob: { ...job('failed'), error: noData } }); await c.open();
    assert.match(c.html, language === 'en' ? /No usable papers remain/ : /没有可用于报告的论文/);
    assert.match(c.html, /href="\/triage\?topic=papers"/);
    assert.doesNotMatch(c.html, /Your report is ready|报告已生成/);
  }
  const otherOwner = await consoleApp({ initialJob: { ...job('failed'), owner: 'other', error: noData } }); await otherOwner.open();
  assert.doesNotMatch(otherOwner.html, /href="\/triage/);
  const failed = await consoleApp({ initialJob: { ...job('failed'), error: '/srv/secret token=abc' } }); await failed.open();
  assert.match(failed.html, /A report could not be created/); assert.doesNotMatch(failed.html, /\/srv\/secret|token=abc/);
  const broken = await consoleApp(); broken.failConfig(true); await broken.open();
  assert.match(broken.html, /Saved configuration could not be checked/); assert.doesNotMatch(broken.html, /\/srv\/private/);
});

test('settings and topic drafts survive guide opening, checking, closing, and cancelled navigation', async () => {
  const settings = await consoleApp({ path: '/settings' }); settings.inputSetting('ISBE_LLM_MODEL_FAST', 'draft-model');
  await settings.open(); assert.match(settings.html, /Save or discard the draft/);
  await settings.navigate('/topics'); assert.equal(settings.location.pathname, '/settings'); assert.equal(settings.dialog.open, true);
  await settings.action('guideCheck'); await settings.action('guideClose'); await settings.action('panel');
  assert.match(settings.app.innerHTML, /value="draft-model"/);
  const topics = await consoleApp({ path: '/topics' }); await topics.action('topicSelect', { id: 'papers' }); topics.inputTopic('label', 'Unsaved research');
  await topics.open(); await topics.action('guideCheck'); await topics.action('guideStart');
  assert.match(topics.html, /Save or discard the draft/); assert.equal(topics.requests.some((r) => r.method === 'POST'), false);
  await topics.action('guideClose'); await topics.action('panel'); assert.match(topics.app.innerHTML, /value="Unsaved research"/);
});

const sharedUser = { id: 'alice', username: 'alice', role: 'user', active: true };

test('shared login gates data, uses CSRF, clears passwords, and logs out', async () => {
  const c = await consoleApp({ mode: 'shared', user: null });
  assert.deepEqual(c.requests.map((r) => r.url), ['/api/session']);
  assert.match(c.app.innerHTML, /autocomplete="username"/);
  assert.match(c.app.innerHTML, /autocomplete="current-password"/);
  c.document.querySelector('#login-username').value = 'alice';
  c.document.querySelector('#login-password').value = 'wrong';
  await c.submit('login-form');
  assert.match(c.app.innerHTML, /Sign-in failed/);
  c.document.querySelector('#login-password').value = 'correct-password';
  await c.submit('login-form');
  assert.match(c.app.innerHTML, /Sign out/);
  const login = c.requests.find((r) => r.url === '/api/login');
  assert.equal(login.headers['X-ISBE-CSRF'], 'session-token');
  assert.doesNotMatch(c.app.innerHTML, /correct-password/);
  await c.action('logout');
  assert.match(c.app.innerHTML, /id="login-form"/);
  assert.equal(c.requests.find((r) => r.url === '/api/logout').headers['X-ISBE-CSRF'], 'login-token');
  assert.equal(c.requests.find((r) => r.url === '/api/logout').headers['Content-Type'], 'application/json');
  assert.equal(c.requests.find((r) => r.url === '/api/logout').body, '{}');
});

test('ordinary users have no privileged links or model settings requests', async () => {
  const c = await consoleApp({ mode: 'shared', user: sharedUser, path: '/settings' });
  assert.equal(c.location.pathname, '/');
  assert.doesNotMatch(c.app.innerHTML, /href="\/(settings|runs|triage|users)/);
  await c.navigate('/topics'); await c.action('topicSelect', { id: 'papers' });
  assert.doesNotMatch(c.app.innerHTML, /topicAddFeed|topic-github|RSS|metrail|commands?/);
  await c.open(); assert.match(c.html, /administrator manages the model/);
  await c.readyToStart(); await c.action('guideStart');
  assert.equal(c.requests.some((r) => /^\/api\/(settings|runs|triage|overview|users)/.test(r.url)), false);
  assert.ok(c.saved.has('isbe.generation.alice'));
  assert.equal(c.saved.has('isbe.generation'), false);
});

test('task center polls queue and stages, retains history and allows queued cancellation only', async () => {
  const queued = { ...job('queued'), owner: 'alice', stage: 'queued', position: 3 };
  const c = await consoleApp({ mode: 'shared', user: sharedUser, path: '/tasks', initialJob: queued, confirm: true });
  assert.match(c.app.innerHTML, /Queue position: 3/);
  assert.match(c.app.innerHTML, /data-act="taskCancel"/);
  assert.equal(c.timers.size, 1);
  const initialPage = c.app.innerHTML;
  c.setJob({ ...queued, state: 'running', stage: 'enrich', position: null }); await c.poll();
  assert.equal(c.app.innerHTML, initialPage);
  assert.match(c.elements.get('#task-list').innerHTML, /Retrieve full text/);
  assert.doesNotMatch(c.elements.get('#task-list').innerHTML, /data-act="taskCancel"/);
  c.setJob({ ...job('succeeded'), owner: 'alice' }); await c.poll();
  assert.equal(c.timers.size, 0);
  assert.match(c.elements.get('#task-list').innerHTML, /Task history/);
  assert.match(c.elements.get('#task-list').innerHTML, /href="\/reports\?topic=papers&period=2026-W42"/);
  c.setJob(queued); await c.action('tasksRefresh'); await c.action('taskCancel', { id: queued.id });
  const cancel = c.requests.find((r) => r.url.endsWith('/cancel'));
  assert.equal(cancel.method, 'POST'); assert.equal(cancel.headers['X-ISBE-CSRF'], 'generation-token');
  assert.match(c.elements.get('#task-list').innerHTML, /Cancelled/);
  assert.equal(c.timers.size, 0);
});

test('refresh recovers the named task rather than an unrelated latest task', async () => {
  const c = await consoleApp({ mode: 'shared', user: sharedUser, initialJob: { ...job('queued'), owner: 'alice', stage: 'queued', position: 2 }, marker: { id: 'job-1', topic: 'papers' } });
  assert.match(c.html, /Queue position: 2/);
  assert.ok(c.requests.some((r) => r.url === '/api/tasks/job-1'));
  assert.equal(c.requests.some((r) => r.method === 'POST'), false);
});

test('ambiguous retries reuse the same request ID across refresh and never claim an old result', async () => {
  const c = await consoleApp(); await c.action('guideOpen', { fresh: 'true' }); await c.readyToStart();
  c.setJob(job('succeeded')); c.loseReply(); await c.action('guideStart');
  assert.match(c.html, /Submission is unconfirmed/); assert.doesNotMatch(c.html, /Your report is ready/);
  const first = JSON.parse(c.requests.find((r) => r.method === 'POST').body);
  const marker = JSON.parse(c.saved.get('isbe.generation'));
  const refreshed = await consoleApp({ marker, initialJob: job('succeeded') });
  assert.match(refreshed.html, /Submission is unconfirmed/);
  assert.equal(refreshed.requests.some((r) => r.method === 'POST'), false);
  await refreshed.action('guideStart');
  const retry = JSON.parse(refreshed.requests.find((r) => r.method === 'POST').body);
  assert.equal(retry.request_id, first.request_id);
});

test('expiry removes private task content, stops polling, and offers login', async () => {
  const c = await consoleApp({ mode: 'shared', user: sharedUser, path: '/tasks', initialJob: { ...job(), owner: 'alice', topic: 'private-topic' } });
  assert.match(c.app.innerHTML, /private-topic/);
  c.expire(); await c.poll();
  assert.match(c.app.innerHTML, /Your session expired/);
  assert.doesNotMatch(c.app.innerHTML, /private-topic/);
  assert.equal(c.timers.size, 0);
});

test('administrators create, deactivate, and reset accounts with explicit forms and CSRF', async () => {
  const c = await consoleApp({ mode: 'shared', path: '/users', confirm: true });
  assert.match(c.app.innerHTML, /autocomplete="new-password"/);
  c.document.querySelector('#new-username').value = 'researcher';
  c.document.querySelector('#new-password').value = 'new-secure-password';
  await c.submit('user-create');
  const create = c.requests.find((r) => r.url === '/api/users' && r.method === 'POST');
  assert.equal(create.headers['X-ISBE-CSRF'], 'users-token');
  assert.deepEqual(JSON.parse(create.body), { username: 'researcher', password: 'new-secure-password' });
  assert.match(c.app.innerHTML, /Account created/); assert.doesNotMatch(c.app.innerHTML, /new-secure-password/);
  await c.action('userActive', { id: 'new-user', active: 'false' });
  assert.match(c.app.innerHTML, /Inactive/);
  c.document.querySelector('#password-new-user').value = 'reset-secure-password';
  await c.submit('', { userPassword: 'new-user' });
  assert.deepEqual(JSON.parse(c.requests.filter((r) => r.method === 'PUT').at(-1).body), { password: 'reset-secure-password' });
  assert.doesNotMatch(c.app.innerHTML, /reset-secure-password/);
});

test('administrator task results carry the owner workspace', async () => {
  const c = await consoleApp({ mode: 'shared', path: '/tasks', initialJob: { ...job('succeeded'), owner: 'alice' } });
  assert.match(c.app.innerHTML, /href="\/reports\?topic=papers&period=2026-W42&owner=alice"/);
  assert.doesNotMatch(c.app.innerHTML, /data-act="taskOpen"/);
});


test('a task response from the previous session cannot populate the next account', async () => {
  const c = await consoleApp({ mode: 'shared', user: sharedUser, path: '/tasks', initialJob: { ...job(), owner: 'alice', topic: 'private-alice' } });
  const release = c.holdRead(); await c.action('tasksRefresh');
  await c.action('logout');
  c.loginAs({ id: 'bob', username: 'bob', role: 'user', active: true });
  c.setJob({ ...job('queued'), owner: 'bob', topic: 'private-bob', stage: 'queued', position: 1 });
  c.document.querySelector('#login-username').value = 'bob';
  c.document.querySelector('#login-password').value = 'correct-password';
  await c.submit('login-form');
  release(); await c.settle();
  assert.match(c.app.innerHTML, /private-bob/);
  assert.doesNotMatch(c.app.innerHTML, /private-alice/);
  assert.doesNotMatch(c.elements.get('#task-list').innerHTML, /private-alice/);
});


test('local empty task center keeps active and history sections plus an explicit guide entry', async () => {
  for (const [language, active, history] of [['en', 'Active tasks', 'Task history'], ['zh-CN', '活动任务', '历史任务']]) {
    const c = await consoleApp({ path: '/tasks', language });
    const groups = c.app.innerHTML.match(/<section class="task-group">[\s\S]*?<\/section>/g) || [];
    assert.equal(groups.length, 2, 'An empty queue still needs both task sections');
    assert.ok(groups[0].includes('<h2>' + active + '</h2>'), language + ' active section');
    assert.ok(groups[1].includes('<h2>' + history + '</h2>'), language + ' history section');
    for (const group of groups) assert.match(group, /<p class="empty">[^<]+<\/p>/);
    assert.match(c.app.innerHTML, /data-act="guideOpen" data-fresh="true"/);
    assert.doesNotMatch(c.app.innerHTML, /id="login-form"|class="card error"/);
    assert.equal(c.timers.size, 0);
    assert.equal(c.requests.some((r) => r.method === 'POST'), false);
    await c.action('guideOpen', { fresh: 'true' });
    assert.equal(c.dialog.open, true);
    assert.equal(c.requests.some((r) => r.method === 'POST'), false);
  }
});

test('task loading errors are distinct from a successfully loaded empty queue', async () => {
  const c = await consoleApp({ path: '/tasks' });
  c.failStatus(true); await c.action('tasksRefresh');
  const failed = c.elements.get('#task-list').innerHTML;
  assert.match(failed, /Task status is temporarily unavailable/);
  assert.doesNotMatch(failed, /<p class="empty">|\/srv\/private|provider-key/);
  assert.equal(c.timers.size, 0);
  c.failStatus(false); await c.action('tasksRefresh');
  const recovered = c.elements.get('#task-list').innerHTML;
  assert.match(recovered, /<h2>Active tasks<\/h2>/);
  assert.match(recovered, /<h2>Task history<\/h2>/);
  assert.equal((recovered.match(/<p class="empty">/g) || []).length, 2);
  assert.doesNotMatch(recovered, /Task status is temporarily unavailable/);
});
