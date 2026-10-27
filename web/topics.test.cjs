// Run with: node --test web/topics.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');

const copy = (value) => JSON.parse(JSON.stringify(value));
const topic = (id = 'papers') => ({ id, config: {
  id, label: `Research ${id}`, lang: 'en', cadence: 'weekly', active: true, window_days: 7,
  arxiv: { categories: ['cs.AI'], include_keywords: ['agents'], max_results: 50 },
  digest: { max_papers: 25, fulltext_per_paper_chars: 8000 },
}, revision: `${id}-v1` });

async function consoleApp(details = [topic()], path = '/topics') {
  const events = {}, windowEvents = {}, requests = [], elements = new Map();
  const records = new Map(details.map((detail) => [detail.id, copy(detail)]));
  const location = new URL(path, 'http://localhost');
  const element = () => ({ dataset: {}, style: { setProperty() {} }, focus() { this.focused = true; }, contains() { return false; }, showModal() { this.open = true; }, close() { this.open = false; }, scrollIntoView() {}, insertAdjacentHTML() {}, querySelector() { return null; } });
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
    allowLeave: false, confirmations: [],
    addEventListener(name, handler) { windowEvents[name] = handler; },
    confirm(message) { this.confirmations.push(message); return this.allowLeave; }, scrollTo() {},
  };
  let failure = null, listFailure = false, hold = null;
  const response = (status, data) => ({ ok: status < 400, status, json: async () => copy(data) });
  vm.runInNewContext(fs.readFileSync(`${__dirname}/app.js`, 'utf8'), {
    document, window: browser, location, history, URL, URLSearchParams, navigator: { language: 'en' },
    localStorage: { getItem() { return null; }, setItem() {} }, clearTimeout() {}, getComputedStyle() { return { backgroundColor: '#000' }; },
    fetch: async (url, options) => {
      requests.push({ url, ...options });
      if (url === '/api/session') return { ok: true, status: 200, json: async () => ({ mode: 'local', user: { id: 'local', username: 'local', role: 'admin', active: true }, csrf_token: 'session-token', llm: { ready: true }, mail: { configured: true } }) };
      if (url === '/api/tasks') return { ok: true, status: 200, json: async () => ({ tasks: [], csrf_token: 'task-token' }) };
      const method = options?.method || 'GET';
      if (url === '/api/overview') return response(200, { pending: [], topics: [], services: {} });
      if (url === '/api/generation') return response(200, { csrf_token: 'generation-token', job: null });
      if (url.startsWith('/api/triage')) return response(404, { error: 'topic not found' });
      if (url === '/api/settings') return response(200, { values: { ISBE_LLM_PROVIDER: 'anthropic', ISBE_FULLTEXT_EXTRACTOR: 'llm' }, configured: {}, sources: {}, csrf_token: 'settings-token' });
      if (url === '/api/topics' && method === 'GET') return listFailure ? response(500, { error: 'Cannot read topics directory' }) : response(200, {
        topics: [...records.values()].map((d) => ({ id: d.id, label: d.config?.label, active: d.config?.active, revision: d.revision, error: d.error })), csrf_token: 'topics-token',
      });
      const id = decodeURIComponent(url.slice('/api/topics/'.length));
      if (method === 'GET') return records.has(id) ? response(200, records.get(id)) : response(404, { error: 'Topic no longer exists' });
      if (hold) await new Promise((resolve) => { hold.release = resolve; });
      if (failure) return response(failure.status, { error: failure.message });
      const body = JSON.parse(options.body);
      if (method === 'DELETE') { records.delete(id); return response(200, { deleted: id }); }
      const saved = { id: body.config.id, config: body.config, revision: `${body.config.id}-v2` };
      records.set(saved.id, copy(saved));
      return response(200, saved);
    },
  });
  const settle = () => new Promise((resolve) => setImmediate(resolve));
  await settle();
  const field = (path, value) => ({ dataset: { topic: path }, type: typeof value === 'boolean' ? 'checkbox' : 'text', value, checked: value });
  return {
    app, requests, elements, location, browser, history, windowEvents, records, settle,
    fail(status, message) { failure = status ? { status, message } : null; },
    failList(value) { listFailure = value; },
    holdWrites() { hold = {}; return () => { const pending = hold; hold = null; pending.release(); }; },
    input(path, value) { events.input({ target: field(path, value) }); },
    feed(index, name, value) { events.input({ target: { dataset: { topicFeed: String(index), feedField: name }, value } }); },
    search(value) { events.input({ target: { dataset: { input: 'topicSearch' }, value } }); },
    async change(path, value) { await events.change({ target: field(path, value) }); await settle(); },
    async action(act, extra = {}) {
      const button = { dataset: { act, ...extra } };
      events.click({ target: { closest(selector) { return selector === '[data-act]' ? button : null; } } });
      await settle();
    },
    async navigate(url) {
      events.click({ button: 0, preventDefault() {}, target: { closest(selector) { return selector === 'a[data-link]' ? { getAttribute() { return url; } } : null; } } });
      await settle();
    },
    save() { return events.submit({ target: { id: 'topic-form' }, preventDefault() {} }); },
  };
}

test('topic management skips overview and preserves untouched advanced settings and literal RSS templates', async () => {
  const original = topic();
  Object.assign(original.config, {
    schedule_timezone: 'Asia/Shanghai', schedules: { collect: '0 8 * * *' },
    rss: { feeds: [{ name: 'Library', url: '${RSS_BASE}/feed', custom: 'keep' }], lookback_days: 30, exclude_keywords: ['sponsor'] },
    metrail: { enabled: false, base_url: 'http://parser:9000', poll_interval_s: 3 },
    retrieval: { intent: 'Find agents', version: 7, facets: [{ id: 'hidden', queries: ['keep'] }], quality_bar: 'Measured results' },
  });
  const c = await consoleApp([original]);
  assert.deepEqual(c.requests.map((r) => r.url), ['/api/session', '/api/topics']);
  assert.match(c.app.innerHTML, /href="\/settings"/);
  await c.action('topicSelect', { id: 'papers' });
  assert.match(c.app.innerHTML, /id="topic-id"[^>]*disabled[^>]*value="papers"/);
  assert.match(c.app.innerHTML, /id="topic-discard"[^>]*disabled/);
  assert.match(c.app.innerHTML, /value="\$\{RSS_BASE\}\/feed"/);
  c.input('id', 'cannot-rename');
  c.input('label', '<Revised research>');
  assert.equal(c.elements.get('#topic-discard').disabled, false);
  c.input('label', original.config.label);
  assert.equal(c.elements.get('#topic-discard').disabled, true);
  c.input('label', '<Revised research>');
  await c.action('panel');
  await c.action('lang', { v: 'zh' });
  await c.action('theme', { v: 'coral' });
  assert.match(c.app.innerHTML, /value="&lt;Revised research&gt;"/);
  await c.save();
  const put = c.requests.find((r) => r.method === 'PUT');
  assert.equal(put.headers['X-ISBE-CSRF'], 'topics-token');
  assert.deepEqual(JSON.parse(put.body), { config: { ...original.config, label: '<Revised research>' }, revision: 'papers-v1' });
  assert.match(c.app.innerHTML, /主题已保存/);
  assert.equal(c.requests.some((r) => r.url === '/api/overview'), false);
});

test('new topics start with an invalid empty arxiv query and save typed values after correction', async () => {
  const c = await consoleApp([]);
  assert.match(c.app.innerHTML, /No topics yet/);
  await c.action('topicNew');
  await c.save();
  assert.equal(c.requests.filter((r) => r.method === 'POST').length, 0);
  c.input('id', 'robotics'); c.input('label', 'Robotics');
  await c.save();
  assert.match(c.app.innerHTML, /Add at least one arXiv category/);
  c.input('arxiv.categories', 'cs.RO\n\n cs.AI'); c.input('arxiv.include_keywords', 'robot learning\nplanning');
  c.input('window_days', '14'); c.input('digest.max_papers', '12'); c.input('arxiv.max_results', '80');
  await c.change('active', false); await c.change('lang', 'zh'); await c.change('cadence', 'daily');
  c.input('github.repos', 'org/repo\n owner/another');
  await c.save();
  const post = c.requests.find((r) => r.method === 'POST');
  assert.deepEqual(JSON.parse(post.body), { config: {
    id: 'robotics', label: 'Robotics', lang: 'zh', cadence: 'daily', active: false, window_days: 14,
    arxiv: { categories: ['cs.RO', 'cs.AI'], include_keywords: ['robot learning', 'planning'], max_results: 80 },
    digest: { max_papers: 12 }, github: { repos: ['org/repo', 'owner/another'] },
  } });
  assert.match(c.app.innerHTML, /Topic saved/);
  assert.match(c.app.innerHTML, /class="card first-report-guide"/);
  // Saving no longer opens a dialog: the card links to the guide on Help.
  assert.match(c.app.innerHTML, /href="\/help\?topic=robotics#report-guide"/);
  assert.doesNotMatch(c.app.innerHTML, /isbe --data|uv run|\/srv\/research/);
  assert.equal(c.requests.some((r) => r.url === '/api/generation' && r.method === 'POST'), false);
  assert.match(c.app.innerHTML, /id="topic-id"[^>]*disabled[^>]*value="robotics"/);
});

test('arxiv guidance marks required fields and separate failures focus the field without losing the draft', async () => {
  const c = await consoleApp([]); await c.action('topicNew');
  assert.match(c.app.innerHTML, /arXiv categories \(required\)/);
  assert.match(c.app.innerHTML, /id="topic-arxiv-categories"[^>]*required aria-required="true"/);
  assert.match(c.app.innerHTML, /cs.LG \(machine learning\)/);
  assert.match(c.app.innerHTML, /https:\/\/arxiv.org\/category_taxonomy/);
  assert.match(c.app.innerHTML, /two groups with AND/);
  c.input('id', 'draft'); c.input('label', 'My saved draft');
  await c.save();
  assert.equal(c.elements.get('#topic-arxiv-categories').focused, true);
  assert.match(c.app.innerHTML, /Add at least one arXiv category/);
  c.input('arxiv.categories', 'cs.RO'); await c.save();
  assert.equal(c.elements.get('#topic-arxiv-include_keywords').focused, true);
  assert.match(c.app.innerHTML, /Add at least one paper keyword/);
  assert.match(c.app.innerHTML, /value="My saved draft"/);
  assert.match(c.app.innerHTML, />cs.RO<\/textarea>/);
  assert.equal(c.requests.some((r) => r.method === 'POST'), false);
  c.input('arxiv.include_keywords', 'robot learning'); await c.save();
  assert.equal(c.requests.filter((r) => r.method === 'POST').length, 1);
});

test('RSS rows preserve advanced fields and templates while arxiv drafts survive toggling', async () => {
  const original = topic();
  original.config.rss = { feeds: [{ name: 'Original', url: '${RSS_BASE}/original', retain: true }], lookback_days: 30 };
  const c = await consoleApp([original]);
  await c.action('topicSelect', { id: 'papers' });
  c.input('arxiv.include_keywords', 'unsaved query');
  await c.change('arxivEnabled', false);
  assert.doesNotMatch(c.app.innerHTML, /id="topic-arxiv-include_keywords"/);
  await c.change('arxivEnabled', true);
  assert.match(c.app.innerHTML, />unsaved query<\/textarea>/);
  c.feed(0, 'name', 'Renamed');
  await c.action('topicAddFeed');
  await c.save();
  assert.match(c.app.innerHTML, /Each RSS feed needs a name and URL/);
  assert.equal(c.requests.filter((r) => r.method === 'PUT').length, 0);
  c.feed(1, 'name', 'Second'); c.feed(1, 'url', '${SECOND_BASE}/news');
  await c.action('topicAddFeed'); await c.action('topicRemoveFeed', { index: '2' });
  await c.save();
  const saved = JSON.parse(c.requests.find((r) => r.method === 'PUT').body).config;
  assert.deepEqual(saved.rss, { feeds: [
    { name: 'Renamed', url: '${RSS_BASE}/original', retain: true }, { name: 'Second', url: '${SECOND_BASE}/news' },
  ], lookback_days: 30 });
  assert.deepEqual(saved.arxiv.include_keywords, ['unsaved query']);
  await c.change('arxivEnabled', false); await c.save();
  assert.equal(JSON.parse(c.requests.at(-1).body).config.arxiv, undefined);
  assert.deepEqual(JSON.parse(c.requests.at(-1).body).config.rss, saved.rss);
});

test('optional filtering rules require intent and keep advanced fields when edited', async () => {
  const original = topic();
  original.config.retrieval = { intent: 'Old intent', facets: [{ id: 'a', terms: ['unchanged'] }], version: 3 };
  const c = await consoleApp([original]);
  await c.action('topicSelect', { id: 'papers' });
  c.input('retrieval.intent', ''); c.input('retrieval.in_scope', 'methods\n datasets');
  await c.save();
  assert.match(c.app.innerHTML, /Add a research intent/);
  c.input('retrieval.intent', 'New intent');
  c.input('retrieval.out_of_scope', 'ads'); c.input('retrieval.out_of_scope_keywords', 'sponsored');
  c.input('retrieval.require_any', 'robot\nagent'); c.input('retrieval.quality_bar', 'Reproducible experiments');
  await c.save();
  assert.deepEqual(JSON.parse(c.requests.at(-1).body).config.retrieval, {
    intent: 'New intent', facets: [{ id: 'a', terms: ['unchanged'] }], version: 3,
    in_scope: ['methods', 'datasets'], out_of_scope: ['ads'], out_of_scope_keywords: ['sponsored'],
    require_any: ['robot', 'agent'], quality_bar: 'Reproducible experiments',
  });
});

test('a malformed RSS row produces a useful validation error and remains removable', async () => {
  const original = topic(); original.config.rss = { feeds: [null], lookback_days: 14 };
  const c = await consoleApp([original]); await c.action('topicSelect', { id: 'papers' });
  c.input('label', 'Repair feeds');
  await c.save();
  assert.match(c.app.innerHTML, /Each RSS feed needs a name and URL/);
  assert.equal(c.requests.some((r) => r.method === 'PUT'), false);
  await c.action('topicRemoveFeed', { index: '0' }); await c.save();
  assert.deepEqual(JSON.parse(c.requests.at(-1).body).config.rss, { feeds: [], lookback_days: 14 });
});

test('revision conflicts retain drafts and require confirmation before reloading new contents', async () => {
  const c = await consoleApp();
  await c.action('topicSelect', { id: 'papers' });
  c.input('label', 'My unsaved work'); c.fail(409, 'revision mismatch');
  await c.save();
  assert.match(c.app.innerHTML, /Your draft is preserved/);
  assert.match(c.app.innerHTML, /value="My unsaved work"/);
  c.input('label', 'Continued draft');
  assert.match(c.elements.get('#topic-status').textContent, /Your draft is preserved/);
  await c.action('panel');
  c.records.get('papers').config.label = 'External edit'; c.records.get('papers').revision = 'external-v2';
  await c.action('topicReload');
  assert.match(c.app.innerHTML, /value="Continued draft"/);
  c.browser.allowLeave = true; await c.action('topicReload');
  assert.match(c.app.innerHTML, /value="External edit"/);
  assert.doesNotMatch(c.app.innerHTML, /Your draft is preserved/);
  c.fail(null); c.input('label', 'After reload'); await c.save();
  assert.equal(JSON.parse(c.requests.at(-1).body).revision, 'external-v2');
});

test('duplicate IDs during creation keep the editable draft and show the server error', async () => {
  const c = await consoleApp(); await c.action('topicNew');
  c.input('id', 'papers'); c.input('label', 'New research');
  c.input('arxiv.categories', 'cs.AI'); c.input('arxiv.include_keywords', 'agents');
  c.fail(409, 'topic already exists'); await c.save();
  assert.match(c.app.innerHTML, /topic already exists/);
  assert.match(c.app.innerHTML, /value="New research"/);
  assert.doesNotMatch(c.app.innerHTML, /reload the latest version|data-act="topicReload"/);
  c.input('id', 'different'); c.fail(null); await c.save();
  assert.equal(JSON.parse(c.requests.filter((r) => r.method === 'POST').at(-1).body).config.id, 'different');
  assert.match(c.app.innerHTML, /Topic saved/);
});

test('invalid configuration is visible and can be deleted with its revision, retaining historical data', async () => {
  const c = await consoleApp([{ id: 'broken', config: null, revision: 'raw-file-hash', error: '<invalid yaml>' }]);
  assert.match(c.app.innerHTML, /&lt;invalid yaml&gt;/);
  await c.action('topicSelect', { id: 'broken' });
  assert.match(c.app.innerHTML, /cannot be shown as a form/);
  assert.doesNotMatch(c.app.innerHTML, /id="topic-form"/);
  await c.action('topicDelete');
  assert.equal(c.requests.some((r) => r.method === 'DELETE'), false);
  c.browser.allowLeave = true; await c.action('topicDelete');
  const deletion = c.requests.find((r) => r.method === 'DELETE');
  assert.deepEqual(JSON.parse(deletion.body), { revision: 'raw-file-hash' });
  assert.equal(deletion.headers['X-ISBE-CSRF'], 'topics-token');
  assert.match(c.browser.confirmations.at(-1), /Reports, items, and memory stay available; reusing this ID continues its history/);
  assert.match(c.app.innerHTML, /Historical data is retained/);
  assert.match(c.app.innerHTML, /No topics yet/);
});

test('search and appearance keep drafts, and topic switching, page navigation, and back are guarded', async () => {
  const c = await consoleApp([topic(), topic('vision')]);
  await c.action('topicSelect', { id: 'papers' }); c.input('label', 'Draft label');
  c.search('vision');
  assert.match(c.elements.get('#topic-list').innerHTML, /data-id="vision"/);
  assert.doesNotMatch(c.elements.get('#topic-list').innerHTML, /data-id="papers"/);
  await c.action('mode', { v: 'light' });
  assert.match(c.app.innerHTML, /value="Draft label"/);
  assert.match(c.app.innerHTML, /id="topic-search"[^>]*value="vision"/);
  await c.action('topicSelect', { id: 'vision' }); await c.action('topicNew');
  assert.match(c.app.innerHTML, /value="Draft label"/);
  await c.navigate('/reports'); assert.equal(c.location.pathname, '/topics');
  let prevented = false; c.windowEvents.beforeunload({ preventDefault() { prevented = true; } }); assert.equal(prevented, true);
  c.location.pathname = '/reports'; c.windowEvents.popstate({ state: { isbeIndex: -1 } });
  assert.deepEqual(c.history.movements, [1]);
  c.location.pathname = '/topics'; c.windowEvents.popstate({ state: { isbeIndex: 0 } });
  c.browser.allowLeave = true; await c.action('topicSelect', { id: 'vision' });
  assert.match(c.app.innerHTML, /value="Research vision"/);
  prevented = false; c.windowEvents.beforeunload({ preventDefault() { prevented = true; } }); assert.equal(prevented, false);
  await c.navigate('/reports'); assert.equal(c.location.pathname, '/reports');
});

test('pending writes disable the form and prevent double submission or page departure', async () => {
  const c = await consoleApp(); await c.action('topicSelect', { id: 'papers' }); c.input('label', 'Pending');
  const release = c.holdWrites(); const pending = c.save(); await c.settle();
  assert.match(c.app.innerHTML, /<fieldset disabled>/);
  await c.save(); await c.navigate('/reports');
  assert.equal(c.location.pathname, '/topics');
  assert.equal(c.requests.filter((r) => r.method === 'PUT').length, 1);
  release(); await pending;
  assert.match(c.app.innerHTML, /Topic saved/);
});

test('missing triage topics are shown explicitly instead of silently selecting another topic', async () => {
  const c = await consoleApp([], '/triage?topic=deleted');
  assert.match(c.app.innerHTML, /The selected topic no longer exists/);
  assert.match(c.app.innerHTML, /href="\/topics"/);
});
