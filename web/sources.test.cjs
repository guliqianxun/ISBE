// Run with: node --test web/sources.test.cjs
const { test } = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const vm = require('node:vm');
const copy = (value) => JSON.parse(JSON.stringify(value));
const source = () => ({ config: { id:'city', name:'City news', topic:'Shanghai ESG', kind:'rss', url:'https://example.com/feed', enabled:false, link_pattern:'', max_items:100 }, revision:'r1', origin:'manual', discovered_from:null, last_checked:null, last_status:null, last_error:null, item_count:1 });
const material = () => ({ id:'m1', source_id:'city', source_name:'City news', title:'Policy notice', url:'https://example.com/news/1', summary:'A source summary', published_at:null, updated_at:null, first_seen:'2026-10-18T08:00:00Z', last_seen:'2026-10-18T08:00:00Z', version_count:1, kind:'rss' });

async function consoleApp({ records = [source()], items = [material()], jobs = [], role = 'admin', failures = {}, plans = [], providers = { searxng:{ configured:false } }, initialUrl = 'http://localhost/sources', settings = {}, reports = [], now } = {}) {
  const events = {}, windowEvents = {}, requests = [], elements = new Map(), timers = new Map(), readHolds = new Map();
  const location = new URL(initialUrl); let timerId = 0, hold = null;
  const element = () => ({ dataset:{}, style:{ setProperty() {} }, focus() { this.focused = true; }, contains() { return false; }, showModal() {}, close() {}, scrollIntoView() {}, insertAdjacentHTML() {}, querySelector() { return null; } });
  const app = element();
  const document = { documentElement:{}, body:element(), title:'', querySelector(selector) { if (selector === '#app') return app; if (!elements.has(selector)) elements.set(selector, element()); return elements.get(selector); }, addEventListener(name, handler) { events[name] = handler; } };
  const browser = { allowLeave:false, confirmations:[], addEventListener(name, handler) { windowEvents[name] = handler; }, confirm(message) { this.confirmations.push(message); return this.allowLeave; }, scrollTo() {} };
  const history = { state:null, movements:[], replaceState(state, _, url) { this.state = state; location.href = new URL(url,location).href; }, pushState(state, _, url) { this.replaceState(state,_,url); }, go(delta) { this.movements.push(delta); } };
  const response = (status,data) => ({ ok:status < 400, status, json:async () => copy(data) });
  const readResponse = async (url,data) => { const snapshot = copy(data); if (readHolds.has(url)) await new Promise((resolve) => { readHolds.get(url).release = resolve; }); return response(200,snapshot); };
  vm.runInNewContext(fs.readFileSync(`${__dirname}/app.js`,'utf8'), {
    document, window:browser, location, history, URL, URLSearchParams, navigator:{ language:'en' }, Uint8Array, crypto:{ getRandomValues(array) { array.fill(7); return array; } },
    Date:now ? class extends Date { constructor(...args) { super(...(args.length ? args : [now])); } static now() { return new Date(now).getTime(); } } : Date,
    localStorage:{ getItem() { return null; }, setItem() {} }, getComputedStyle() { return { backgroundColor:'#000' }; },
    setTimeout(fn,ms) { const id = ++timerId; timers.set(id,{ fn,ms }); return id; }, clearTimeout(id) { timers.delete(id); },
    fetch:async (url,options) => {
      const method = options?.method || 'GET'; requests.push({ url, ...options });
      if (url === '/api/session') return response(200,{ mode:role === 'admin' ? 'local' : 'shared', user:{ id:'user', username:'User', role, active:true }, csrf_token:'session-token' });
      const path = new URL(url,'http://localhost').pathname;
      if (failures[url] || failures[path]) return response(failures[url] || failures[path],{ error:'Read or write failed' });
      if (url === '/api/topics') return response(200,{ topics:[], csrf_token:'topic-token' });
      if (url === '/api/reports' && method === 'GET') return readResponse(url,reports);
      if (url === '/api/settings') return response(200,{ values:{}, configured:{}, sources:{}, csrf_token:'settings-token', ...settings });
      if (url === '/api/sources/search' && method === 'GET') return readResponse(url,{ plans, providers, csrf_token:'search-token' });
      if (path === '/api/sources' && method === 'GET') {
        const params = new URL(url,'http://localhost').searchParams, offset = Number(params.get('offset') || 0);
        const all = records.map((item) => ({ ...item, site:new URL(item.config.url).host }));
        const matchesStatus = (item,status) => !status || (status === 'enabled' ? item.config.enabled : status === 'disabled' ? !item.config.enabled : status === 'never' ? !item.last_checked : item.last_status === status);
        const filtered = all.filter((item) => (!params.get('q') || [item.config.name,item.config.url,item.config.id].some((v) => v.toLowerCase().includes(params.get('q').toLowerCase()))) && (!params.get('site') || item.site === params.get('site')) && (!params.get('topic') || item.config.topic === params.get('topic')) && matchesStatus(item,params.get('status')));
        const facets = (key) => [...new Set(all.map((item) => key === 'site' ? item.site : item.config.topic))].map((value) => ({ value,count:all.filter((item) => (key === 'site' ? item.site : item.config.topic) === value).length }));
        return readResponse(url,{ sources:filtered.slice(offset,offset+50),total:filtered.length,offset,limit:50,summary:{ total:all.length,...Object.fromEntries(['enabled','disabled','failed','empty','never'].map((status) => [status,all.filter((item) => matchesStatus(item,status)).length])) },facets:{ sites:facets('site'),topics:facets('topic') },csrf_token:'source-token' });
      }
      if (url.startsWith('/api/sources/config/')) { const detail = records.find((item) => item.config.id === decodeURIComponent(url.slice('/api/sources/config/'.length))); return detail ? readResponse(url,detail) : response(404,{ error:'Not found' }); }
      if (url === '/api/sources/jobs') return response(200,{ jobs });
      if (url.startsWith('/api/sources/materials/') && url.endsWith('/timeline')) { const item = items.find((entry) => entry.id === decodeURIComponent(url.split('/')[4])); return item?.timeline ? response(200,item.timeline) : response(404,{ error:'Material not found' }); }
      if (url.startsWith('/api/sources/materials?')) { const params = new URL(url,'http://localhost').searchParams; const offset = Number(params.get('offset')); const filtered = items.filter((item) => (!params.get('source') || item.source_id === params.get('source')) && (!params.get('date_status') || (item.date_status || 'unknown') === params.get('date_status'))); return response(200,{ items:filtered.slice(offset,offset+50), total:filtered.length, offset, limit:50, counts:{ hits:filtered.reduce((n,item) => n+(item.hit_count || 0),0), materials:filtered.length, versions:filtered.reduce((n,item) => n+(item.version_count || 1),0), articles:new Set(filtered.map((item) => item.identity?.key ?? item.id)).size } }); }
      if (method === 'POST' || method === 'PUT') {
        if (hold) await new Promise((resolve) => { hold.release = resolve; });
        const body = JSON.parse(options.body);
        if (url === '/api/sources/search') { const detail = { config:body.config, revision:'search-r2' }; const index = plans.findIndex((item) => item.config.id === body.config.id); if (index < 0) plans.push(detail); else plans[index] = detail; return response(200,detail); }
        if (url === '/api/sources/search/run') { const job = { id:'search-job', state:'running', operation:'search', created_at:'2026-10-18T08:00:00Z', stats:{ plan_id:body.config.id }, error:null }; jobs.splice(0,jobs.length,job); return response(202,{ job }); }
        if (url.startsWith('/api/sources/search/') && url.endsWith('/recheck')) { const job = { id:'recheck-job', state:'running', operation:'recheck', created_at:'2026-10-19T08:00:00Z', stats:{ plan_id:decodeURIComponent(url.split('/')[4]) }, error:null }; jobs.splice(0,jobs.length,job); return response(202,{ job }); }
        if (url.startsWith('/api/sources/search/') && url.endsWith('/weekly')) return response(200,{ topic:body.topic_id, period:'2026-W42-media', url:'/reports?topic='+encodeURIComponent(body.topic_id)+'&period=2026-W42-media', weekly:11, background:29, newer:1, unknown:62, conflict:17, outside:26, materials:146 });
        if (url.endsWith('/discover') || url.endsWith('/collect')) { const job = { id:'job1', state:'running', operation:url.endsWith('/discover') ? 'discover' : 'collect', created_at:'2026-10-18T08:00:00Z', stats:{}, error:null }; jobs.splice(0,jobs.length,job); return response(202,{ job }); }
        if (url.endsWith('/import')) return response(200,{ imported:body.sources.length });
        if (url.endsWith('/batch')) {
          if (body.sources.some((selected) => !records.some((item) => item.config.id === selected.id && item.revision === selected.revision))) return response(409,{ error:'Conflict' });
          for (const selected of body.sources) { const item = records.find((item) => item.config.id === selected.id); item.config.enabled = body.enabled; item.revision += '-updated'; }
          return response(200,{ updated:body.sources.length });
        }
        const detail = { ...source(), config:body.config, revision:'r2' }; const index = records.findIndex((item) => item.config.id === body.config.id); if (index < 0) records.push(detail); else records[index] = detail; return response(200,detail);
      }
      return response(404,{ error:'Not found' });
    },
  });
  const settle = () => new Promise((resolve) => setImmediate(resolve)); await settle();
  const field = (dataset,value) => ({ dataset, type:typeof value === 'boolean' ? 'checkbox' : 'text', value, checked:value });
  return {
    app, elements, requests, location, browser, history, windowEvents, records, items, jobs, failures, timers, settle, plans, providers,
    input(key,value) { events.input({ target:field({ source:key },value) }); },
    discover(key,value) { events.input({ target:field({ discovery:key },value) }); },
    importText(value) { events.input({ target:field({ input:'sourceImport' },value) }); },
    query(key,value) { events.input({ target:field({ sourceQuery:key },value) }); },
    search(key,value) { events.input({ target:field({ search:key },value) }); },
    weekly(key,value) { events.change({ target:field({ weekly:key },value) }); },
    async dateFilter(value) { events.change({ target:field({ change:'sourceDateFilter' },value) }); await settle(); },
    async check(id,value) { events.change({ target:field(id === '*' ? { change:'sourceSelectPage' } : { sourceCheck:id },value) }); await settle(); },
    submit(id) { return events.submit({ target:{ id }, preventDefault() {} }); },
    async action(act,extra = {}) { const button = { dataset:{ act,...extra } }; events.click({ target:{ closest(selector) { return selector === '[data-act]' ? button : null; } } }); await settle(); },
    async navigate(url) { events.click({ button:0, preventDefault() {}, target:{ closest(selector) { return selector === 'a[data-link]' ? { getAttribute() { return url; } } : null; } } }); await settle(); },
    async tick() { const [id,timer] = timers.entries().next().value || []; if (timer) { timers.delete(id); await timer.fn(); await settle(); } },
    holdWrites() { hold = {}; return () => { const pending = hold; hold = null; pending.release(); }; },
    holdRead(url) { const pending = {}; readHolds.set(url,pending); return () => { readHolds.delete(url); pending.release(); }; },
  };
}

test('source edits retain revisions and drafts across refresh, locale changes, conflicts, and navigation', async () => {
  const c = await consoleApp();
  assert.equal(c.requests.some((r) => r.url === '/api/overview'),false);
  // Sources sits under System in the shared sidebar.
  assert.match(c.app.innerHTML,/<a class="nav" href="\/system" aria-current="page">/);
  // React pages are whole-page loads; classic ones stay in this console.
  assert.match(c.app.innerHTML,/<a class="nav" href="\/">/);
  assert.match(c.app.innerHTML,/<a class="nav" data-link href="\/reports">/);
  await c.action('sourceSelect',{ id:'city' });
  c.input('name','Revised <name>'); c.input('id','cannot-rename');
  await c.action('lang',{ v:'zh' }); await c.action('sourcesRefresh');
  c.records[0].last_checked = '2026-10-18T10:00:00Z'; c.records[0].last_status = 'ok'; c.records[0].revision = 'r5';
  await c.action('sourcesRefresh');
  assert.match(c.elements.get('#source-details-status').innerHTML,/2026.*ok/);
  await c.action('panel');
  assert.match(c.app.innerHTML,/Revised &lt;name&gt;/);
  await c.navigate('/topics'); assert.equal(c.location.pathname,'/sources');
  await c.submit('source-form');
  const request = c.requests.find((r) => r.method === 'PUT');
  assert.equal(request.headers['X-ISBE-CSRF'],'source-token');
  assert.equal(JSON.parse(request.body).revision,'r1');
  assert.equal(JSON.parse(request.body).config.id,'city');
  c.input('name','Keep this draft'); c.failures['/api/sources/city'] = 409;
  await c.submit('source-form'); assert.match(c.app.innerHTML,/草稿已保留/); assert.match(c.app.innerHTML,/Keep this draft/);
  c.browser.allowLeave = true; await c.navigate('/topics'); assert.equal(c.location.pathname,'/topics');
});

test('discovery defaults to auto-enable and polls without repeating submissions; a failed status read stops polling', async () => {
  const c = await consoleApp();
  assert.match(c.app.innerHTML,/data-discovery="auto_enable" checked/);
  c.discover('url','https://example.com'); c.discover('topic','上海 ESG');
  await c.submit('source-discovery-form');
  const post = c.requests.find((r) => r.method === 'POST');
  assert.equal(JSON.parse(post.body).auto_enable,true);
  assert.equal(JSON.parse(post.body).topic,'上海 ESG');
  assert.match(JSON.parse(post.body).request_id,/^[0-9a-f]{32}$/);
  assert.equal([...c.timers.values()][0].ms,2000);
  c.failures['/api/sources/jobs'] = 503; await c.tick();
  assert.equal(c.timers.size,0);
  assert.match(c.elements.get('#source-jobs').innerHTML,/Automatic refresh has stopped/);
  assert.equal(c.requests.filter((r) => r.method === 'POST').length,1);
  delete c.failures['/api/sources/jobs']; await c.action('sourcesRefresh'); assert.equal(c.timers.size,1);
  await c.navigate('/topics'); assert.equal(c.timers.size,0);
});

test('manual trial works for a disabled source; completing the task refreshes material and configuration', async () => {
  const c = await consoleApp(); await c.action('sourceSelect',{ id:'city' });
  await c.action('sourceCollect');
  assert.equal(c.requests.filter((r) => r.url === '/api/sources/city/collect').length,1);
  await c.action('sourceView',{ view:'materials' });
  c.jobs[0].state = 'succeeded'; c.jobs[0].stats = { new:1,updated:0,unchanged:0 };
  c.items.push({ ...material(), id:'m2', title:'New notice' });
  await c.tick();
  assert.equal(c.timers.size,0);
  assert.match(c.elements.get('#source-materials').innerHTML,/New notice/);
  assert.equal(c.requests.filter((r) => r.method === 'POST').length,1);
});

test('terminal polling hides the collection tab running indicator without rerendering the page', async () => {
  const c = await consoleApp({jobs:[{id:'running-search',operation:'search',state:'running',created_at:'2026-10-18T08:00:00Z',stats:{}}]});
  await c.action('sourceView',{view:'jobs'});
  assert.equal(c.elements.get('#source-running-indicator').hidden,false);
  const page = c.app.innerHTML;
  c.jobs[0].state = 'partial'; await c.tick();
  assert.equal(c.elements.get('#source-running-indicator').hidden,true);
  assert.match(c.elements.get('#source-jobs').innerHTML,/Partly completed/);
  assert.equal(c.app.innerHTML,page);
  assert.equal(c.timers.size,0);
});

test('material inbox escapes source content, rejects non-HTTP links, separates unknown dates, and paginates', async () => {
  const items = Array.from({ length:51 },(_,i) => ({ ...material(), id:`m${i}`, title:i ? `News ${i}` : '<script>bad</script>', source_name:'<Publisher>', summary:'<img src=x onerror=bad()>', url:i ? 'https://example.com/news' : 'javascript:alert(1)' }));
  const c = await consoleApp({ items });
  assert.equal(c.requests.some((r) => r.url.startsWith('/api/sources/materials')),false);
  await c.action('sourceView',{ view:'materials' });
  assert.match(c.elements.get('#source-materials').innerHTML,/&lt;script&gt;bad&lt;\/script&gt;/);
  assert.doesNotMatch(c.elements.get('#source-materials').innerHTML,/<script>|href="javascript:|<img src=x/);
  assert.match(c.elements.get('#source-materials').innerHTML,/Publication date unknown · First collected/);
  assert.match(c.app.innerHTML,/Excerpts and original links/);
  await c.action('sourcePage',{ offset:'50' });
  assert.match(c.elements.get('#source-materials').innerHTML,/News 50/);
  assert.match(c.elements.get('#source-materials').innerHTML,/51–51 \/ 51/);
  await c.action('sourceMaterialsFor',{ id:'missing',name:'Missing source' }); assert.match(c.elements.get('#source-materials').innerHTML,/No collected material/);
});

test('failed initial reads show errors rather than successful empty states and can recover explicitly', async () => {
  const c = await consoleApp({ failures:{ '/api/sources':500, '/api/sources/jobs':500, '/api/sources/materials?source=&offset=0':500 } });
  assert.doesNotMatch(c.app.innerHTML,/No configured sources yet|No collection tasks yet|No collected material/);
  assert.match(c.app.innerHTML,/Could not load this section/);
  for (const key of Object.keys(c.failures)) delete c.failures[key];
  await c.action('sourcesRefresh');
  assert.match(c.elements.get('#source-list').innerHTML,/City news/);
});

test('JSON import is explicit and atomic conflicts retain text; new source validates before sending', async () => {
  const c = await consoleApp(); await c.action('sourceNew'); await c.submit('source-form');
  assert.equal(c.requests.some((r) => r.method === 'POST'),false);
  c.input('name','A feed'); c.input('topic','Green finance'); c.input('url','https://example.com/feed');
  for (const id of ['UPPER','a'.repeat(65),'con','com1']) {
    c.input('id',id); await c.submit('source-form');
    assert.equal(c.requests.some((r) => r.method === 'POST'),false);
  }
  c.input('id','new-feed'); c.input('name','A feed'); c.input('topic','Green finance'); c.input('url','https://example.com/feed');
  await c.submit('source-form');
  const created = JSON.parse(c.requests.find((r) => r.method === 'POST').body).config;
  assert.equal(created.max_items,100); assert.equal(created.enabled,true);
  c.importText('{broken'); await c.submit('source-import-form');
  assert.match(c.elements.get('#source-status').textContent,/JSON object/);
  const text = JSON.stringify({ sources:[source().config] }); c.importText(text); c.failures['/api/sources/import'] = 409;
  await c.submit('source-import-form'); assert.match(c.app.innerHTML,/&quot;sources&quot;/);
  delete c.failures['/api/sources/import']; await c.submit('source-import-form');
  assert.match(c.app.innerHTML,/1 sources imported/); assert.match(c.app.innerHTML,/href="\/api\/sources\/export" download/);
});

test('ordinary shared users cannot navigate to or request sources', async () => {
  const c = await consoleApp({ role:'user' });
  assert.equal(c.location.pathname,'/overview');
  assert.equal(c.requests.some((r) => r.url.startsWith('/api/sources')),false);
  assert.doesNotMatch(c.app.innerHTML,/href="\/sources"/);
  await c.action('sourceCollect'); await c.submit('source-discovery-form');
  assert.equal(c.requests.some((r) => r.url.startsWith('/api/sources')),false);
});

test('pending writes block duplicate submits and departure; unsuccessful discovery does not restart', async () => {
  const c = await consoleApp(); await c.action('sourceSelect',{ id:'city' }); c.input('name','Pending draft');
  const release = c.holdWrites(); const saving = c.submit('source-form'); await c.settle();
  await c.submit('source-form'); await c.navigate('/topics');
  assert.equal(c.location.pathname,'/sources'); assert.equal(c.requests.filter((r) => r.method === 'PUT').length,1);
  assert.match(c.app.innerHTML,/id="source-json"[^>]*disabled/);
  release(); await saving;
  c.discover('url','https://example.com'); c.discover('topic','ESG'); c.failures['/api/sources/discover'] = 503;
  await c.submit('source-discovery-form');
  assert.match(c.app.innerHTML,/Submission is unconfirmed/); assert.equal(c.timers.size,0);
  await c.action('sourcesRefresh');
  assert.equal(c.requests.filter((r) => r.url === '/api/sources/discover').length,1);
});

test('session expiry clears source drafts and prevents stale responses from another session', async () => {
  const c = await consoleApp(); await c.action('sourceSelect',{ id:'city' }); c.input('name','Private draft');
  const release = c.holdWrites(); const save = c.submit('source-form'); await c.settle();
  c.failures['/api/sources/jobs'] = 401;
  await c.action('sourceMaterialsRefresh'); // independent reads do not expose private drafts elsewhere
  await c.action('sourcesRefresh'); // busy guard prevents conflicting reads
  c.failures['/api/sources/materials?source=&offset=0'] = 401;
  await c.action('sourceMaterialsRefresh');
  release(); await save;
  assert.match(c.app.innerHTML,/Sign in/);
  assert.doesNotMatch(c.app.innerHTML,/Private draft/);
});

const catalog = (count = 1000) => Array.from({ length:count },(_,i) => ({ ...source(),config:{ ...source().config,id:`source-${i}`,name:`News ${i}`,url:`https://site-${i}.example/feed`,topic:`Topic ${i}`,enabled:i % 2 === 0 } }));
const queryUrl = (q = '', site = '', topic = '', status = '', offset = 0) => '/api/sources?'+new URLSearchParams({q,site,topic,status,offset:String(offset)});

test('large catalogs render one server page, bounded facet suggestions and separate views', async () => {
  const c = await consoleApp({ records:catalog() });
  assert.equal(c.requests.filter((r) => r.url === queryUrl()).length,1);
  assert.equal((c.app.innerHTML.match(/data-source-check=/g) || []).length,50);
  assert.match(c.app.innerHTML,/1,000 matching sources|1000 matching sources/);
  assert.doesNotMatch(c.app.innerHTML,/id="source-form"|id="source-materials"|id="source-jobs"|id="source-filter"/);
  assert.match(c.app.innerHTML,/id="source-tools" class="card source-tools">/);
  for (const key of ['site','topic']) assert.equal((c.app.innerHTML.match(new RegExp(`<datalist id="source-facet-${key}">([\\s\\S]*?)<\/datalist>`))[1].match(/<option /g) || []).length,20);
  c.query('site','site-999.example');
  assert.match(c.elements.get('#source-facet-site').innerHTML,/site-999.example/);
  assert.equal(c.requests.filter((r) => r.url.startsWith('/api/sources?')).length,1);
  await c.submit('source-search-form');
  assert.match(c.elements.get('#source-list').innerHTML,/News 999/);
  assert.match(c.elements.get('#source-list').innerHTML,/1 matching sources/);
  assert.match(c.elements.get('#source-overview').innerHTML,/1,000|1000/);
  await c.action('sourceStatusFilter',{status:'enabled'});
  assert.equal(c.requests.at(-1).url,queryUrl('','','','enabled'));
  assert.match(c.app.innerHTML,/500 matching sources/);
  await c.action('sourceResetFilters'); await c.action('sourceCatalogPage',{ offset:'50' });
  assert.equal(c.requests.at(-1).url,queryUrl('','','','',50));
  assert.ok(c.elements.get('#source-list').innerHTML.includes('51–100 / 1000 items'));
  assert.doesNotMatch(c.elements.get('#source-list').innerHTML,/data-source-check="source-0"/);
  await c.action('sourceView',{ view:'jobs' }); assert.match(c.app.innerHTML,/Latest 20 collection records/); assert.doesNotMatch(c.app.innerHTML,/id="source-list"/);
});

test('batch updates only selected current-page revisions and preserve conflicts until reselection', async () => {
  const c = await consoleApp({ records:catalog(51) });
  await c.check('*',true);
  assert.equal(c.elements.get('#source-select-page').focused,true);
  await c.action('sourceCatalogPage',{ offset:'50' });
  assert.match(c.elements.get('#source-list').innerHTML,/0 selected on this page/);
  await c.check('source-50',true);
  assert.equal(c.elements.get('[data-source-check="source-50"]').focused,true);
  c.records[50].revision = 'r9';
  await c.action('sourceBatch',{ enabled:'false' });
  let post = c.requests.filter((r) => r.url === '/api/sources/batch').at(-1);
  assert.deepEqual(JSON.parse(post.body),{ sources:[{id:'source-50',revision:'r1'}],enabled:false });
  assert.equal(post.headers['X-ISBE-CSRF'],'source-token');
  assert.match(c.app.innerHTML,/Nothing was changed/); assert.equal(c.records[50].config.enabled,true);
  await c.action('sourcesRefresh');
  assert.match(c.elements.get('#source-list').innerHTML,/1 selected on this page/);
  await c.action('sourceClearSelection'); await c.check('source-50',true); await c.action('sourceBatch',{ enabled:'false' });
  post = c.requests.filter((r) => r.url === '/api/sources/batch').at(-1);
  assert.equal(JSON.parse(post.body).sources[0].revision,'r9');
  assert.equal(c.records[50].config.enabled,false); assert.equal(c.records[0].config.enabled,true);
});

test('disabling the last filtered page clamps the page and refreshes a clean editor outside results', async () => {
  const records = catalog(51); records.forEach((item) => { item.config.enabled = true; });
  const c = await consoleApp({ records });
  await c.action('sourceStatusFilter',{ status:'enabled' }); await c.action('sourceCatalogPage',{ offset:'50' });
  await c.action('sourceSelect',{ id:'source-50' }); await c.check('*',true); await c.action('sourceBatch',{ enabled:'false' });
  assert.ok(c.app.innerHTML.includes('1–50 / 50 items'));
  assert.doesNotMatch(c.app.innerHTML,/data-source="enabled" checked/);
  c.input('name','Changed after disabling'); await c.submit('source-form');
  const put = c.requests.find((r) => r.method === 'PUT'); assert.equal(JSON.parse(put.body).revision,'r1-updated');
  assert.equal(JSON.parse(put.body).config.enabled,false);
  const single = await consoleApp({records:[{...source(),config:{...source().config,enabled:true}}]});
  await single.action('sourceStatusFilter',{status:'enabled'}); await single.check('*',true); await single.action('sourceBatch',{enabled:'false'});
  assert.ok(single.app.innerHTML.includes('0–0 / 0 items')); assert.match(single.app.innerHTML,/No sources match/);
});

test('selecting a scrolled row retains its viewport while a new page starts at the top', async () => {
  const c = await consoleApp({ records:catalog(60) });
  c.elements.set('.source-table-scroll',{ dataset:{page:'0'},scrollTop:1800,scrollLeft:40 });
  let markup = '';
  Object.defineProperty(c.elements.get('#source-list'),'innerHTML',{
    get() { return markup; },
    set(value) { markup = value; c.elements.set('.source-table-scroll',{ dataset:{page:value.match(/data-page="(\d+)"/)[1]},scrollTop:0,scrollLeft:0 }); },
  });
  await c.check('source-25',true);
  assert.equal(c.elements.get('.source-table-scroll').scrollTop,1800);
  assert.equal(c.elements.get('.source-table-scroll').scrollLeft,40);
  await c.action('sourceCatalogPage',{ offset:'50' });
  assert.equal(c.elements.get('.source-table-scroll').scrollTop,0);
  assert.equal(c.elements.get('.source-table-scroll').scrollLeft,0);
});

test('late catalog query results cannot replace a newer filter or selection', async () => {
  const c = await consoleApp({records:catalog(5)});
  const release = c.holdRead(queryUrl('News 1'));
  c.query('q','News 1'); const old = c.submit('source-search-form'); await c.settle();
  c.query('q','News 2'); await c.submit('source-search-form'); await c.check('source-2',true);
  release(); await old;
  assert.match(c.elements.get('#source-list').innerHTML,/News 2/); assert.doesNotMatch(c.elements.get('#source-list').innerHTML,/News 1/);
  assert.match(c.elements.get('#source-list').innerHTML,/1 selected on this page/);
});

test('off-page and reserved IDs load direct details; late details cannot reopen a closed editor', async () => {
  const records = catalog(60); records[59].config.id = 'jobs';
  const c = await consoleApp({records});
  await c.action('sourceSelect',{id:'jobs'});
  assert.equal(c.requests.at(-1).url,'/api/sources/config/jobs');
  assert.match(c.app.innerHTML,/id="source-name"[^>]+value="News 59"/);
  await c.action('sourceClose');
  const release = c.holdRead('/api/sources/config/jobs'); await c.action('sourceSelect',{id:'jobs'});
  await c.action('sourceView',{view:'jobs'}); release(); await c.settle();
  assert.match(c.app.innerHTML,/Latest 20 collection records/); assert.doesNotMatch(c.app.innerHTML,/id="source-form"/);
});

test('catalog filtering and inbox source context stay independent without enumerating every source', async () => {
  const c = await consoleApp({records:catalog(70),items:[{...material(),source_id:'source-62',source_name:'News 62'},{...material(),id:'other',source_id:'source-1'}]});
  c.query('q','News 62'); await c.submit('source-search-form');
  await c.action('sourceMaterialsFor',{id:'source-62',name:'News 62'});
  assert.match(c.app.innerHTML,/Current source: News 62/);
  assert.equal(c.requests.at(-1).url,'/api/sources/materials?source=source-62&offset=0');
  assert.doesNotMatch(c.app.innerHTML,/id="source-filter"|id="source-list"/);
  await c.action('sourceAllMaterials'); assert.equal(c.requests.at(-1).url,'/api/sources/materials?source=&offset=0');
  await c.action('sourceView',{view:'catalog'}); assert.match(c.app.innerHTML,/value="News 62"/); assert.match(c.app.innerHTML,/1 matching sources/);
});

test('catalog escapes facet and source text and treats search punctuation literally', async () => {
  const item = source(); item.config.name = '<News>_100%'; item.config.topic = '"><img src=x onerror=bad()>';
  const c = await consoleApp({records:[item]});
  assert.match(c.app.innerHTML,/&lt;News&gt;_100%/); assert.doesNotMatch(c.app.innerHTML,/<img src=x|<News>/);
  c.query('q','_100%'); await c.submit('source-search-form');
  assert.equal(c.requests.at(-1).url,queryUrl('_100%'));
  assert.match(c.elements.get('#source-list').innerHTML,/1 matching sources/);
});

test('$ sequences in a name show as written in translated labels', async () => {
  const item = source(); item.config.name = 'Q3 $& $` $$ review';
  const c = await consoleApp({records:[item]});
  assert.match(c.app.innerHTML,/aria-label="Select Q3 \$&amp; \$` \$\$ review"/);
});

test('a failed read is not cached: the next render of the same page asks again', async () => {
  const c = await consoleApp({ initialUrl:'http://localhost/reports', failures:{ '/api/reports':500 } });
  assert.match(c.app.innerHTML,/\/api\/reports: Read or write failed/);
  delete c.failures['/api/reports'];
  await c.action('lang',{ v:'en' });
  assert.doesNotMatch(c.app.innerHTML,/Read or write failed/);
  assert.equal(c.requests.filter((r) => r.url === '/api/reports').length,2);
});

const searchPlan = () => ({ config:{ id:'shanghai-news', name:'Shanghai reporting', topic:'Shanghai ESG', provider:'searxng', queries:['上海 ESG','Shanghai sustainable finance'], start_date:'2026-10-01', end_date:'2026-10-18', language:'zh-CN', max_results:10 }, revision:'search-r1' });
const searchProviders = () => ({ searxng:{ configured:true } });

test('web search opens directly, uses local recent dates, and permits saving before a service is configured', async () => {
  const c = await consoleApp({initialUrl:'http://localhost/sources?view=search'});
  assert.match(c.app.innerHTML,/data-view="search" aria-pressed="true"/);
  assert.match(c.app.innerHTML,/id="search-run"[^>]*disabled/);
  assert.match(c.app.innerHTML,/This search service is not configured/);
  assert.match(c.app.innerHTML,/href="\/settings#sources-settings-h"/);
  await c.action('searchRun'); assert.equal(c.requests.filter((r) => r.method === 'POST').length,0);
  await c.submit('web-search-form');
  const save = c.requests.find((r) => r.method === 'POST'); const config = JSON.parse(save.body).config;
  const today = new Date(), start = new Date(); start.setDate(today.getDate()-6);
  const localDate = (date) => `${date.getFullYear()}-${String(date.getMonth()+1).padStart(2,'0')}-${String(date.getDate()).padStart(2,'0')}`;
  assert.equal(save.url,'/api/sources/search'); assert.equal(save.headers['X-ISBE-CSRF'],'search-token');
  assert.equal(config.start_date,localDate(start)); assert.equal(config.end_date,localDate(today));
  assert.equal(config.queries.length,4); assert.equal(c.plans.length,1);
  assert.equal(c.requests.some((r) => r.url === '/api/sources/search/run'),false);
});

test('web search saves an edited revision before running and polls without repeating requests', async () => {
  const c = await consoleApp({plans:[searchPlan()],providers:searchProviders()});
  await c.action('sourceView',{view:'search'}); assert.equal(c.location.search,'?view=search');
  await c.action('searchSelect',{id:'shanghai-news'}); c.search('queries','  上海 ESG 媒体报道  \n\n上海 绿色金融');
  const release = c.holdWrites(); await c.action('searchRun'); await c.action('searchRun');
  assert.equal(c.requests.filter((r) => r.method === 'POST').length,1);
  release(); await c.settle(); await c.settle();
  const posts = c.requests.filter((r) => r.method === 'POST');
  assert.deepEqual(posts.map((r) => r.url),['/api/sources/search','/api/sources/search/run']);
  assert.equal(JSON.parse(posts[0].body).revision,'search-r1');
  assert.deepEqual(JSON.parse(posts[1].body).config.queries,['上海 ESG 媒体报道','上海 绿色金融']);
  assert.match(JSON.parse(posts[1].body).request_id,/^[0-9a-f]{32}$/);
  assert.match(c.app.innerHTML,/Latest 20 collection records/); assert.match(c.app.innerHTML,/Web discovery/);
  assert.equal(c.timers.size,1); await c.tick();
  assert.equal(c.requests.filter((r) => r.method === 'POST').length,2);
});

test('search save conflicts keep the draft and never start a run', async () => {
  const c = await consoleApp({plans:[searchPlan()],providers:searchProviders()});
  await c.action('sourceView',{view:'search'}); await c.action('searchSelect',{id:'shanghai-news'});
  c.search('name','My revised news plan'); c.failures['/api/sources/search'] = 409;
  await c.action('searchRun');
  assert.match(c.app.innerHTML,/My revised news plan/); assert.match(c.app.innerHTML,/Your draft is kept/);
  assert.equal(c.requests.some((r) => r.url === '/api/sources/search/run'),false);
  await c.navigate('/topics'); assert.equal(c.location.pathname,'/sources');
});

test('unknown search submissions are never automatically retried after refresh', async () => {
  const c = await consoleApp({plans:[searchPlan()],providers:searchProviders()});
  await c.action('sourceView',{view:'search'}); await c.action('searchSelect',{id:'shanghai-news'});
  c.failures['/api/sources/search/run'] = 503; await c.action('searchRun');
  assert.match(c.app.innerHTML,/Submission is unconfirmed/);
  await c.action('sourcesRefresh'); await c.tick();
  assert.equal(c.requests.filter((r) => r.url === '/api/sources/search/run').length,1);
  assert.equal(c.requests.filter((r) => r.url === '/api/sources/search' && r.method === 'POST').length,0);
});

test('invalid search dates and excessive query counts cannot be submitted', async () => {
  const c = await consoleApp({providers:searchProviders()}); await c.action('sourceView',{view:'search'});
  c.search('start_date','2026-02-30'); await c.action('searchRun');
  assert.equal(c.requests.filter((r) => r.method === 'POST').length,0); assert.match(c.elements.get('#source-status').textContent,/valid date range/);
  c.search('start_date','2026-10-18'); c.search('end_date','2026-10-01'); await c.submit('web-search-form');
  assert.equal(c.requests.filter((r) => r.method === 'POST').length,0);
  c.search('end_date','2026-10-18'); c.search('queries',Array.from({length:9},(_,i) => 'query '+i).join('\n')); await c.submit('web-search-form');
  assert.equal(c.requests.filter((r) => r.method === 'POST').length,0);
});

test('search materials distinguish publisher, original publication and unverified search date hints', async () => {
  const base = { ...material(),kind:'web_search',source_name:'My saved plan',provider:'searxng',search_queries:['Shanghai ESG'],search_date_hint:'2026-10-17',date_status:'unknown',publisher:'Shanghai Daily',start_date:'2026-10-01',end_date:'2026-10-18',read_status:'not_checked' };
  const c = await consoleApp({items:[{ ...base,title:'Unknown date' },{ ...base,id:'m2',title:'Known date',published_at:'2026-10-15',date_status:'in_range' },{ ...base,id:'m3',publisher:null,title:'Conflict',date_status:'conflict' }]});
  await c.action('sourceView',{view:'materials'});
  const html = c.elements.get('#source-materials').innerHTML;
  assert.match(html,/Shanghai Daily · Unknown date/); assert.doesNotMatch(html,/My saved plan ·/);
  assert.match(html,/example\.com · Conflict/); assert.match(html,/Original publication：2026-10-15/);
  assert.match(html,/Search date hint \(not original publication\)：2026-10-17/);
  assert.match(html,/Search date window：2026-10-01 – 2026-10-18/);
  assert.match(html,/Original date not checked yet/);
  assert.doesNotMatch(html,/Original publication：2026-10-17/);
  assert.match(html,/Date unverified/); assert.match(html,/Conflicting dates/);
  await c.dateFilter('unknown'); assert.match(c.requests.at(-1).url,/date_status=unknown/);
  assert.match(c.elements.get('#source-materials').innerHTML,/Unknown date/); assert.doesNotMatch(c.elements.get('#source-materials').innerHTML,/Known date| · Conflict/);
  await c.dateFilter('out_of_range'); assert.match(c.elements.get('#source-materials').innerHTML,/No material matches the current date filter/);
  assert.doesNotMatch(c.elements.get('#source-materials').innerHTML,/No collected material for this selection yet/);
  c.items.splice(0); await c.dateFilter(''); assert.match(c.elements.get('#source-materials').innerHTML,/No collected material for this selection yet/);
});

test('search history shows partial query failures and first-batch limits without claiming complete coverage', async () => {
  const c = await consoleApp({jobs:[{id:'search-partial',operation:'search',state:'partial',created_at:'2026-10-18T08:00:00Z',stats:{new:3,limited:true,queries:[{query:'上海 ESG',state:'failed',error:'Provider <temporarily> unavailable'},{query:'Shanghai finance',state:'partial',unresponsive_engines:2},{query:'上海 银行',state:'ok',found:3}]}}]});
  await c.action('sourceView',{view:'jobs'});
  assert.match(c.app.innerHTML,/上海 ESG/); assert.match(c.app.innerHTML,/Provider &lt;temporarily&gt; unavailable/);
  assert.match(c.app.innerHTML,/2 search engines did not respond/);
  assert.match(c.app.innerHTML,/Only the first batch of results was collected/);
  assert.doesNotMatch(c.app.innerHTML,/<temporarily>/);
});

test('original search publication dates retain their source calendar date across time zones', async () => {
  const c = await consoleApp({items:[{...material(),kind:'web_search',title:'Late evening publication',published_at:'2026-10-16T23:30:00-07:00',date_status:'in_range',start_date:'2026-10-16',end_date:'2026-10-16'}]});
  await c.action('sourceView',{view:'materials'});
  assert.match(c.elements.get('#source-materials').innerHTML,/Original publication：2026-10-16/);
  assert.doesNotMatch(c.elements.get('#source-materials').innerHTML,/Original publication：2026-10-17/);
});

test('SearXNG date guidance does not promise exact upstream filtering and offers no other provider', async () => {
  const c = await consoleApp({providers:searchProviders()}); await c.action('sourceView',{view:'search'});
  assert.match(c.app.innerHTML,/month\/year hint that upstream engines may ignore/);
  assert.doesNotMatch(c.app.innerHTML,/id="search-language"[^>]*disabled/);
  assert.match(c.app.innerHTML,/<select id="search-provider" data-search="provider"><option value="searxng" selected>SearXNG<\/option><\/select>/);
});

test('baidu removed: a plan saved for it still lists but must switch to SearXNG before it runs', async () => {
  const old = searchPlan(); old.config.id = 'old-baidu'; old.config.provider = 'baidu';
  const c = await consoleApp({plans:[old],providers:searchProviders()}); await c.action('sourceView',{view:'search'});
  assert.match(c.app.innerHTML,/<small>Baidu \(removed\) · 2026-10-01/);
  assert.match(c.app.innerHTML,/<option value="baidu" selected disabled>Baidu \(removed\)<\/option>/);
  assert.match(c.app.innerHTML,/class="error">Baidu search has been removed\. Switch this plan to SearXNG/);
  assert.match(c.app.innerHTML,/id="search-run"[^>]*disabled/);
  await c.action('searchRun'); assert.equal(c.requests.filter((r) => r.method === 'POST').length,0);
  c.search('provider','searxng');
  assert.match(c.elements.get('#search-readiness').innerHTML,/Service configured/);
  assert.equal(c.elements.get('#search-run').disabled,false);
  await c.action('searchRun'); await c.settle();
  const posts = c.requests.filter((r) => r.method === 'POST');
  assert.deepEqual(posts.map((r) => r.url),['/api/sources/search','/api/sources/search/run']);
  assert.equal(JSON.parse(posts[0].body).config.provider,'searxng');
});

test('saved search plans reopen without duplicate IDs while refresh preserves an edited draft', async () => {
  const first = await consoleApp({initialUrl:'http://localhost/sources?view=search'}); await first.submit('web-search-form');
  const c = await consoleApp({initialUrl:'http://localhost/sources?view=search',plans:first.plans});
  assert.match(c.app.innerHTML,/data-act="searchSelect" data-id="shanghai-esg-green-finance" aria-pressed="true"/);
  assert.match(c.app.innerHTML,/id="search-save"[^>]*disabled/);
  await c.submit('web-search-form'); assert.equal(c.requests.filter((r) => r.method === 'POST').length,0);
  c.search('name','Keep my pending revision'); c.plans[0].config.name = 'Changed elsewhere'; c.plans[0].revision = 'external-r3';
  await c.action('sourcesRefresh'); await c.action('panel');
  assert.match(c.app.innerHTML,/id="search-name"[^>]*value="Keep my pending revision"/);
  c.browser.allowLeave = true; await c.action('searchNew'); await c.submit('web-search-form');
  const created = JSON.parse(c.requests.find((r) => r.method === 'POST').body);
  assert.equal(created.config.id,'shanghai-esg-green-finance-2'); assert.equal(created.revision,undefined);
  assert.equal(new Set(c.plans.map((plan) => plan.config.id)).size,2);
});

test('material links restore the plan and date filter and keep their URL when changing views', async () => {
  const plan = searchPlan(); plan.config.start_date = '2026-09-20'; plan.config.end_date = '2026-10-19';
  const items = [{...material(),source_id:'search:shanghai-news',kind:'web_search',title:'October reporting',date_status:'in_range'},{...material(),id:'m2',source_id:'search:other-plan',kind:'web_search',title:'Other plan report',date_status:'in_range'}];
  const c = await consoleApp({plans:[plan],items,initialUrl:'http://localhost/sources?view=materials&source=search%3Ashanghai-news&date_status=in_range'});
  assert.match(c.app.innerHTML,/Current plan: Shanghai reporting/);
  assert.match(c.app.innerHTML,/Plan window: 2026-09-20 to 2026-10-19/);
  assert.match(c.app.innerHTML,/October reporting/); assert.doesNotMatch(c.app.innerHTML,/Other plan report/);
  assert.match(c.requests.find((r) => r.url.startsWith('/api/sources/materials?')).url,/source=search%3Ashanghai-news&offset=0&date_status=in_range/);
  await c.dateFilter('unknown'); assert.equal(c.location.searchParams.get('date_status'),'unknown');
  await c.action('sourceView',{view:'jobs'}); await c.action('sourceView',{view:'materials'});
  assert.equal(c.location.searchParams.get('source'),'search:shanghai-news'); assert.equal(c.location.searchParams.get('date_status'),'unknown');
  const restored = await consoleApp({plans:[plan],items,initialUrl:c.location.href});
  assert.match(restored.app.innerHTML,/<option value="unknown" selected>/);
  assert.match(restored.app.innerHTML,/Current plan: Shanghai reporting/);
  await c.action('sourceAllMaterials'); assert.equal(c.location.searchParams.has('source'),false);
});

test('plan material and recheck actions preserve edited drafts and explicitly continue remaining batches', async () => {
  const c = await consoleApp({plans:[searchPlan()]}); await c.action('sourceView',{view:'search'});
  assert.match(c.app.innerHTML,/data-act="searchMaterials" data-id="shanghai-news"/);
  assert.match(c.app.innerHTML,/data-act="searchRecheck" data-id="shanghai-news"/);
  c.search('name','Unsaved headline plan'); await c.action('searchMaterials',{id:'shanghai-news'});
  assert.equal(c.location.searchParams.get('source'),'search:shanghai-news');
  assert.match(c.app.innerHTML,/Current plan: Shanghai reporting/);
  await c.action('sourceView',{view:'search'}); assert.match(c.app.innerHTML,/value="Unsaved headline plan"/);
  const release = c.holdWrites(); await c.action('searchRecheck',{id:'shanghai-news'}); await c.action('searchRecheck',{id:'shanghai-news'});
  assert.equal(c.requests.filter((r) => r.method === 'POST').length,1); release(); await c.settle();
  const post = c.requests.find((r) => r.method === 'POST');
  assert.equal(post.url,'/api/sources/search/shanghai-news/recheck'); assert.equal(post.headers['X-ISBE-CSRF'],'search-token');
  assert.deepEqual(Object.keys(JSON.parse(post.body)),['request_id']);
  c.jobs[0].state = 'succeeded'; c.jobs[0].stats = {plan_id:'shanghai-news',checked:20,remaining:9,next_cursor:'item:20'};
  await c.tick(); assert.match(c.elements.get('#source-jobs').innerHTML,/Continue checking 9 remaining/);
  assert.match(c.elements.get('#source-jobs').innerHTML,/Checked 20 this batch/);
  await c.action('searchRecheck',{id:'shanghai-news',after:'item:20'});
  const posts = c.requests.filter((r) => r.method === 'POST'); assert.equal(posts.length,2);
  assert.equal(JSON.parse(posts[1].body).after,'item:20');
  await c.action('sourceView',{view:'search'}); assert.match(c.app.innerHTML,/value="Unsaved headline plan"/);
  assert.equal(c.browser.confirmations.length,0);
});

test('uncertain date rechecks never retry automatically and failed checks explain preserved dates', async () => {
  const c = await consoleApp({plans:[searchPlan()],items:[{...material(),kind:'web_search',source_id:'search:shanghai-news',published_at:'2026-10-16',date_check_status:'failed',read_status:'failed',date_status:'in_range'}]});
  await c.action('sourceView',{view:'search'}); c.failures['/api/sources/search/shanghai-news/recheck'] = 503;
  await c.action('searchRecheck',{id:'shanghai-news'}); assert.match(c.app.innerHTML,/Submission is unconfirmed/);
  await c.action('sourcesRefresh'); await c.tick();
  assert.equal(c.requests.filter((r) => r.method === 'POST').length,1);
  await c.action('searchMaterials',{id:'shanghai-news'});
  assert.match(c.elements.get('#source-materials').innerHTML,/previous record is retained/);
  assert.match(c.elements.get('#source-materials').innerHTML,/Original publication：2026-10-16/);
});

test('same-path browser history restores the material plan and date filter without dropping search drafts', async () => {
  const first = searchPlan(), second = searchPlan(); second.config.id = 'second-plan'; second.config.name = 'Second reporting';
  const original = 'http://localhost/sources?view=materials&source=search%3Ashanghai-news&date_status=in_range';
  const c = await consoleApp({plans:[first,second],initialUrl:original,items:[{...material(),source_id:'search:shanghai-news',kind:'web_search',date_status:'in_range',title:'First plan article'},{...material(),id:'m2',source_id:'search:second-plan',kind:'web_search',date_status:'unknown',title:'Second plan article'}]});
  await c.navigate('/sources'); await c.action('sourceView',{view:'search'}); c.search('name','Keep this unsaved plan');
  await c.action('searchMaterials',{id:'second-plan'}); await c.dateFilter('unknown');
  assert.match(c.app.innerHTML,/Current plan: Second reporting/);
  c.location.href = original; await c.windowEvents.popstate({state:{isbeIndex:0}}); await c.settle();
  assert.match(c.app.innerHTML,/Current plan: Shanghai reporting/); assert.doesNotMatch(c.app.innerHTML,/Current plan: Second reporting/);
  assert.match(c.app.innerHTML,/<option value="in_range" selected>/);
  assert.match(c.elements.get('#source-materials').innerHTML,/First plan article/); assert.doesNotMatch(c.elements.get('#source-materials').innerHTML,/Second plan article/);
  await c.action('sourceView',{view:'search'}); assert.match(c.app.innerHTML,/value="Keep this unsaved plan"/);
});

test('failed or interrupted rechecks retry the current batch instead of skipping to its next cursor', async () => {
  for (const [state,after] of [['interrupted','previous-batch'],['failed',null]]) {
    const c = await consoleApp({plans:[searchPlan()],jobs:[{id:'stopped-recheck',operation:'recheck',state,created_at:'2026-10-19T08:00:00Z',stats:{plan_id:'shanghai-news',after,checked:3,remaining:9,next_cursor:'end-of-unfinished-batch'}}]});
    await c.action('sourceView',{view:'jobs'});
    assert.match(c.app.innerHTML,/Retry this batch/); assert.doesNotMatch(c.app.innerHTML,/Continue checking/);
    assert.match(c.app.innerHTML,new RegExp('data-after="'+(after || '')+'"'));
    assert.doesNotMatch(c.app.innerHTML,/data-after="end-of-unfinished-batch"/);
    await c.action('searchRecheck',{id:'shanghai-news',after:after || ''});
    const body = JSON.parse(c.requests.find((r) => r.method === 'POST').body);
    assert.equal(body.after,after || undefined); assert.match(body.request_id,/^[0-9a-f]{32}$/);
  }
});

test('search plans escape imported content and never render configured keys', async () => {
  const plan = searchPlan(); plan.config.name = '<img src=x onerror=bad()>'; plan.config.queries = ['<script>bad()</script>'];
  const c = await consoleApp({plans:[plan],settings:{values:{GITHUB_TOKEN:'MUST_NOT_BE_RENDERED'},configured:{GITHUB_TOKEN:true}}});
  await c.action('sourceView',{view:'search'}); assert.match(c.app.innerHTML,/&lt;img/); assert.doesNotMatch(c.app.innerHTML,/<img src=x/);
  await c.action('searchSelect',{id:'shanghai-news'}); assert.match(c.app.innerHTML,/&lt;script&gt;/); assert.doesNotMatch(c.app.innerHTML,/<script>bad/);
  assert.doesNotMatch(c.app.innerHTML,/MUST_NOT_BE_RENDERED/);
});

test('media weekly uses a completed Sunday, deduplicates report topics and prefers a matching label', async () => {
  for (const [now,end,start] of [['2026-10-19T08:00:00','2026-10-18','2026-10-12'],['2026-10-18T23:59:00','2026-10-11','2026-10-05']]) {
    const c = await consoleApp({plans:[searchPlan()],now,reports:[{topic_id:'existing-esg',label:'Shanghai ESG'},{topic_id:'existing-esg',label:'Older issue'},{topic_id:'other',label:'Other topic'}]});
    await c.action('sourceView',{view:'search'}); await c.action('mediaWeeklyOpen',{id:'shanghai-news'});
    assert.match(c.app.innerHTML,new RegExp('data-weekly="week_end" value="'+end+'"'));
    assert.match(c.app.innerHTML,new RegExp('This issue: '+start+' to '+end));
    assert.equal((c.app.innerHTML.match(/value="existing-esg"/g) || []).length,1);
    assert.match(c.app.innerHTML,/<option value="existing-esg" selected>Shanghai ESG<\/option>/);
    assert.match(c.app.innerHTML,/<option value="shanghai-news">Shanghai ESG \(this search plan\)<\/option>/);
    assert.match(c.app.innerHTML,/Plan window: 2026-10-01 to 2026-10-18/);
    assert.match(c.app.innerHTML,/Content remains search snippets, not a thematic briefing/);
    c.weekly('week_end',now.slice(0,10)); await c.submit('media-weekly-form');
    assert.equal(c.requests.some((r) => r.method === 'POST'),false);
    assert.match(c.elements.get('#source-status').textContent,/completed Sunday before today/);
  }
});

test('media weekly posts the chosen topic and week with CSRF, guards double submit and preserves search drafts', async () => {
  const c = await consoleApp({plans:[searchPlan()],now:'2026-10-19T09:00:00',reports:[{topic_id:'existing-esg',label:'Shanghai ESG'}]});
  await c.action('sourceView',{view:'search'}); c.search('name','Unsaved weekly search draft');
  await c.action('searchMaterials',{id:'shanghai-news'}); await c.dateFilter('in_range');
  assert.match(c.app.innerHTML,/data-act="mediaWeeklyOpen" data-id="shanghai-news"/);
  await c.action('mediaWeeklyOpen',{id:'shanghai-news'}); c.weekly('topic_id','shanghai-news');
  const release = c.holdWrites(), pending = c.submit('media-weekly-form'); await c.settle();
  await c.submit('media-weekly-form');
  const posts = c.requests.filter((r) => r.method === 'POST'); assert.equal(posts.length,1);
  assert.equal(posts[0].url,'/api/sources/search/shanghai-news/weekly'); assert.equal(posts[0].headers['X-ISBE-CSRF'],'search-token');
  assert.deepEqual(JSON.parse(posts[0].body),{topic_id:'shanghai-news',week_end:'2026-10-18'});
  release(); await pending;
  assert.equal(c.location.pathname,'/sources'); assert.equal(c.location.searchParams.get('date_status'),'in_range');
  assert.match(c.app.innerHTML,/href="\/reports\?topic=shanghai-news&amp;period=2026-W42-media">Open material compilation/);
  assert.match(c.app.innerHTML,/This week 11 · Earlier background 29/);
  await c.action('sourceView',{view:'search'}); assert.match(c.app.innerHTML,/value="Unsaved weekly search draft"/);
  assert.equal(c.browser.confirmations.length,0);
});

test('failed media weekly generation keeps chosen fields and drafts without a success link or automatic retry', async () => {
  const c = await consoleApp({plans:[searchPlan()],now:'2026-10-19T09:00:00',failures:{'/api/sources/search/shanghai-news/weekly':503}});
  await c.action('sourceView',{view:'search'}); c.search('queries','Keep this edited query');
  await c.action('mediaWeeklyOpen',{id:'shanghai-news'});
  assert.match(c.app.innerHTML,/<option value="shanghai-news" selected>/);
  c.weekly('week_end','2026-10-11'); await c.submit('media-weekly-form');
  assert.match(c.app.innerHTML,/Read or write failed/); assert.match(c.app.innerHTML,/data-weekly="week_end" value="2026-10-11"/);
  assert.match(c.app.innerHTML,/Keep this edited query/); assert.doesNotMatch(c.app.innerHTML,/Open material compilation|Material compilation created/);
  await c.action('sourcesRefresh'); await c.tick(); assert.equal(c.requests.filter((r) => r.method === 'POST').length,1);
  delete c.failures['/api/sources/search/shanghai-news/weekly']; await c.submit('media-weekly-form');
  assert.match(c.app.innerHTML,/Open material compilation/);
});

test('region and institution are optional plan fields saved with the plan', async () => {
  const c = await consoleApp({plans:[searchPlan()],providers:searchProviders()});
  await c.action('sourceView',{view:'search'}); await c.action('searchSelect',{id:'shanghai-news'});
  const region = c.app.innerHTML.match(/<input id="search-region"[^>]*>/)[0];
  const institution = c.app.innerHTML.match(/<input id="search-institution"[^>]*>/)[0];
  for (const input of [region,institution]) { assert.match(input,/maxlength="60"/); assert.doesNotMatch(input,/required/); assert.match(input,/value=""/); }
  assert.match(c.app.innerHTML,/Region and institution are added to every query as quoted phrases/);
  assert.match(c.app.innerHTML,/id="search-syntax-hint"[^>]*>Syntax as in Google Scholar: spaces mean all words; uppercase OR means either/);
  // Typing and emptying the inputs again leaves a plan saved without them unchanged.
  c.search('region','上海'); assert.equal(c.elements.get('#search-save').disabled,false);
  c.search('region','   '); assert.equal(c.elements.get('#search-save').disabled,true);
  c.search('region',' 上海 '); c.search('institution',' 上海证券交易所 '); await c.submit('web-search-form');
  const post = c.requests.find((r) => r.method === 'POST' && r.url === '/api/sources/search');
  const config = JSON.parse(post.body).config;
  assert.equal(config.region,'上海'); assert.equal(config.institution,'上海证券交易所');
  assert.match(c.app.innerHTML,/id="search-region"[^>]*value="上海"/);
  assert.match(c.app.innerHTML,/id="search-institution"[^>]*value="上海证券交易所"/);
  assert.equal(c.elements.get('#search-save').disabled,true);
});

test('a plan saved with region and institution reopens with them', async () => {
  const plan = searchPlan(); plan.config.region = 'Shanghai'; plan.config.institution = 'SSE';
  const c = await consoleApp({plans:[plan],providers:searchProviders()}); await c.action('sourceView',{view:'search'});
  assert.match(c.app.innerHTML,/id="search-region"[^>]*value="Shanghai"/);
  assert.match(c.app.innerHTML,/id="search-institution"[^>]*value="SSE"/);
});

test('a run lists the constraints the search service did not apply', async () => {
  const stats = {new:2,unexecuted:['phrase','boolean','region','publication_window'],queries:[{query:'Shanghai "green finance" OR ESG',state:'succeeded',found:2}]};
  const c = await consoleApp({jobs:[{id:'search-r2',operation:'search',state:'succeeded',created_at:'2026-10-21T08:00:00Z',stats}]});
  await c.action('sourceView',{view:'jobs'});
  assert.match(c.app.innerHTML,/Not applied by the search service; check in the results: Exact phrase, OR groups, Region, Publication window/);
  const none = await consoleApp({jobs:[{id:'search-old',operation:'search',state:'succeeded',created_at:'2026-10-18T08:00:00Z',stats:{new:1,queries:[]}}]});
  await none.action('sourceView',{view:'jobs'});
  assert.doesNotMatch(none.app.innerHTML,/Not applied by the search service/);
});

test('a material seen elsewhere opens its timeline newest first; the inbox counts hits, materials, versions and articles apart', async () => {
  const at = (day) => `2026-10-${day}T08:00:00Z`;
  const timeline = { identity:{ kind:'url', key:'example.com/news/1' }, materials:[], counts:{ hits:2, materials:3, versions:3 }, events:[
    { kind:'source_version', at:at(18), material_id:'m1', url:'https://example.com/news/1', title:'Revised notice', source_id:'city', source_name:'City news', hash:'h2' },
    { kind:'search_hit', at:at(17), material_id:'m2', url:'https://www.example.com/news/1?utm_source=x', title:'Notice <b>', plan_id:'shanghai', plan_name:'Shanghai ESG', query:'ESG rules', rank:3, run_id:'r2' },
    { kind:'search_hit', at:at(15), material_id:'m2', url:'https://www.example.com/news/1?utm_source=x', title:'Notice', plan_id:'shanghai', plan_name:'Shanghai ESG', query:'ESG rules', rank:1, run_id:'r1' },
  ] };
  const items = [{ ...material(), hit_count:2, version_count:2, identity:{ kind:'url', key:'example.com/news/1' }, duplicates:2, timeline }, { ...material(), id:'m3', url:'https://example.com/news/3', title:'Alone', identity:{ kind:'url', key:'example.com/news/3' }, duplicates:0 }];
  const c = await consoleApp({ items });
  await c.action('sourceView',{ view:'materials' });
  let html = c.elements.get('#source-materials').innerHTML;
  assert.match(html,/2 hits · 2 materials · 3 versions · 2 articles/);
  assert.equal(html.match(/data-act="sourceTimeline"/g).length,1);
  assert.match(html,/data-act="sourceTimeline" data-id="m1" aria-expanded="false">Seen 2 more times · timeline</);
  await c.action('sourceTimeline',{ id:'m1' });
  assert.equal(c.requests.at(-1).url,'/api/sources/materials/m1/timeline');
  html = c.elements.get('#source-materials').innerHTML;
  assert.match(html,/aria-expanded="true">Hide timeline</);
  assert.match(html,/2 hits · 3 materials · 3 versions · The same article is matched by DOI/);
  const order = ['Source collection · City news', 'Search hit · Shanghai ESG · “ESG rules” rank 3', 'Search hit · Shanghai ESG · “ESG rules” rank 1'].map((text) => html.indexOf(text));
  assert.ok(order.every((index) => index > 0) && order[0] < order[1] && order[1] < order[2], String(order));
  assert.match(html,/Notice &lt;b&gt;/); assert.doesNotMatch(html,/Notice <b>/);
  await c.action('sourceTimeline',{ id:'m1' });
  assert.doesNotMatch(c.elements.get('#source-materials').innerHTML,/source-timeline/);
  c.failures['/api/sources/materials/m1/timeline'] = 500;
  await c.action('sourceTimeline',{ id:'m1' });
  assert.match(c.elements.get('#source-materials').innerHTML,/role="alert">Could not load the timeline. Read or write failed</);
});
