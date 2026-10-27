import { act, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, onTestFinished, vi } from 'vitest';
import { onUnauthorized, setCsrfToken } from '../api/client';
import { setSourceFactory, type Source } from '../api/sse';
import { PrefsProvider, STORAGE_KEY } from '../prefs';
import { fakeServer } from '../test/fakeServer';
import { recordedLog } from '../test/recorded';
import { RECONNECT_MS } from './client';
import graphViews from './graph/graph-views.json';
import type { GraphDoc } from './graph/model';

/** The test graph as GET /graph?form=compact answers it. */
function compact(doc: GraphDoc) {
  const ids = new Map(doc.nodes.map((n, i) => [n.id, i]));
  const kinds = [...new Set(doc.edges.map((e) => e.kind))].sort();
  const { edges, ...rest } = doc;
  return {
    ...rest,
    edge_kinds: kinds,
    edge_rows: edges.map((e) => [kinds.indexOf(e.kind), ids.get(e.source), ids.get(e.target), e.weight]),
  };
}
const GRAPH = compact({
  ...(graphViews as unknown as GraphDoc),
  schema: 'question_graph@1',
  run_id: 'run-1',
  fingerprint: 'f'.repeat(16),
});
import { QuestionPage } from './QuestionPage';
import { RunPage } from './RunPage';
import type { QuestionEvent } from './types';

function at(path: string) {
  return render(
    <PrefsProvider>
      <MemoryRouter initialEntries={[path]}>
        <Routes>
          <Route path="/question" element={<QuestionPage />} />
          <Route path="/question/runs/:id" element={<RunPage />} />
          <Route path="/galaxies" element={<p>Galaxies page</p>} />
        </Routes>
      </MemoryRouter>
    </PrefsProvider>,
  );
}

class FakeSource implements Source {
  listeners = new Map<string, (e: Event) => void>();
  onopen: ((e: Event) => unknown) | null = null;
  onerror: ((e: Event) => unknown) | null = null;
  closed = false;
  constructor(readonly url: string) {}
  addEventListener(type: string, listener: (e: Event) => void) {
    this.listeners.set(type, listener);
  }
  close() {
    this.closed = true;
  }
  send(e: QuestionEvent) {
    const message = new MessageEvent(e.type, { data: JSON.stringify(e), lastEventId: String(e.seq) });
    this.listeners.get(e.type)?.(message);
    // As in a browser: a server event named `error` also reaches `onerror`.
    if (e.type === 'error') this.onerror?.(message);
  }
}

const RUN = { run_id: 'run-1', review: { on: true, active: false, ends_at: null } };

const ev = (seq: number, type: string, data: object): QuestionEvent =>
  ({
    schema: 'question_event@1',
    run_id: 'run-1',
    seq,
    at: '2026-10-24T10:00:00.000Z',
    type,
    data,
  }) as QuestionEvent;

/** A run that failed in its expand stage, as the server sends it. */
const FAILED = [
  ev(1, 'state', { state: 'queued' }),
  ev(2, 'state', { state: 'running' }),
  ev(3, 'stage', { stage: 'plan', state: 'begin' }),
  ev(4, 'stage', { stage: 'plan', state: 'end' }),
  ev(5, 'stage', { stage: 'search', state: 'end' }),
  ev(6, 'stage', { stage: 'screen', state: 'end' }),
  ev(7, 'stage', { stage: 'expand', state: 'begin' }),
  ev(8, 'error', { fatal: true, stage: 'expand', message: 'The expand stage failed' }),
  ev(9, 'state', { state: 'failed' }),
  ev(10, 'done', { status: 'failed' }),
];

const papers = () => within(screen.getByRole('complementary', { name: 'Papers' }));

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ lang: 'en' }));
  setCsrfToken('tok');
});

describe('question page', () => {
  it('asks a question with its options and opens the new run', async () => {
    const { requests } = fakeServer({
      'GET /api/question/runs': () => ({ body: { runs: [] } }),
      'POST /api/question/runs': () => ({ body: { run: { run_id: 'run-1' } } }),
      'GET /api/question/runs/run-1/events': () => ({ body: {} }),
    });
    setSourceFactory((url) => new FakeSource(url));
    at('/question');
    expect(await screen.findByText(/No runs yet/)).toBeInTheDocument();
    await userEvent.type(
      screen.getByLabelText('Research question'),
      'How do green spaces affect mental health?',
    );
    await userEvent.type(screen.getByLabelText('From year'), '2015');
    await userEvent.click(screen.getByLabelText('Review the plan before searching'));
    await userEvent.click(screen.getByRole('button', { name: 'Start searching' }));
    const post = requests.find((r) => r.method === 'POST')!;
    expect(post.headers['X-ISBE-CSRF']).toBe('tok');
    expect(post.body).toEqual({
      question: 'How do green spaces affect mental health?',
      lang: 'en',
      year_from: 2015,
      year_to: null,
      review: false,
    });
    expect(await screen.findByText('The model is writing the search plan…')).toBeInTheDocument();
  });

  it('offers the engines, names the server default, and sends the one picked', async () => {
    const { requests } = fakeServer({
      'GET /api/question/runs': () => ({
        body: { runs: [], engines: ['openalex', 'arxiv'], default_engine: 'arxiv' },
      }),
      'POST /api/question/runs': () => ({ body: { run: { run_id: 'run-1' } } }),
    });
    setSourceFactory((url) => new FakeSource(url));
    at('/question');
    const picker = await screen.findByLabelText('Search engine');
    expect(
      await within(picker).findByRole('option', { name: 'Server default (arXiv index)' }),
    ).toBeInTheDocument();
    expect(
      within(picker)
        .getAllByRole('option')
        .map((o) => o.textContent),
    ).toEqual(['Server default (arXiv index)', 'OpenAlex (all fields)', 'arXiv index']);
    await userEvent.selectOptions(picker, 'openalex');
    await userEvent.type(screen.getByLabelText('Research question'), 'Neural PDE solvers?');
    await userEvent.click(screen.getByRole('button', { name: 'Start searching' }));
    expect(requests.find((r) => r.method === 'POST')!.body).toMatchObject({ engine: 'openalex' });
  });

  it('lists past runs with their status', async () => {
    fakeServer({
      'GET /api/question/runs': () => ({
        body: {
          runs: [
            {
              run_id: 'run-1',
              question: 'Radar nowcasting?',
              lang: 'en',
              status: 'done',
              created_at: '2026-10-23T10:00:00Z',
              counts: { candidates: 420, included: 300 },
            },
            {
              run_id: 'run-2',
              question: 'No model?',
              lang: 'en',
              status: 'done',
              degraded: true,
              problems: 2,
              created_at: '2026-10-23T11:00:00Z',
            },
            {
              run_id: 'run-3',
              question: 'No essaies?',
              lang: 'en',
              status: 'failed',
              failure: { stage: 'plan', code: 'essaies_unreachable', message: 'refused' },
              created_at: '2026-10-23T12:00:00Z',
            },
          ],
        },
      }),
    });
    at('/question');
    const link = await screen.findByRole('link', { name: 'Radar nowcasting?' });
    expect(link).toHaveAttribute('href', '/question/runs/run-1');
    expect(screen.getByText('420 candidates · 300 included')).toBeInTheDocument();
    const [done, partial, failed] = screen.getAllByRole('listitem');
    expect(done).toHaveTextContent('Done');
    expect(partial).toHaveTextContent('Done with 2 problems');
    expect(failed).toHaveTextContent('Run failedessaies could not be reached.');
  });
});

describe('run page over the API', () => {
  it('builds the page from events, resumes after a drop without duplicates, and pauses', async () => {
    const sources: FakeSource[] = [];
    setSourceFactory((url) => {
      const s = new FakeSource(url);
      sources.push(s);
      return s;
    });
    const { requests } = fakeServer({
      'GET /api/question/runs/run-1': () => ({ body: { run: RUN } }),
      'POST /api/question/runs/run-1/pause': () => ({ body: { ok: true } }),
    });
    const log = recordedLog().slice(0, 60);
    at('/question/runs/run-1');
    const first = sources[0]!;
    expect(first.url).toBe('/api/question/runs/run-1/events');
    act(() => {
      first.onopen?.(new Event('open'));
      log.slice(0, 40).forEach((e) => first.send(e));
    });
    expect(await screen.findByRole('region', { name: 'Search plan' })).toBeInTheDocument();
    // Searching has begun, so the card is folded; opened, it shows concepts and queries.
    expect(screen.queryByText('Concepts')).toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'Search plan' }));
    expect(screen.getByText('Concepts')).toBeInTheDocument();
    expect(
      screen.getByText(/radar echo extrapolation · radar reflectivity extrapolation/),
    ).toBeInTheDocument();
    expect(screen.getAllByRole('listitem').some((li) => li.textContent?.includes('Priority 1'))).toBe(true);
    const seen = await papers().findAllByRole('button');
    expect(screen.getByRole('status')).toHaveTextContent('Running');
    // The recorded plan names no engine, so it was written for OpenAlex.
    expect(screen.getByTitle('Search engine')).toHaveTextContent('OpenAlex (all fields)');

    act(() => first.onerror?.(new Event('error')));
    expect(await screen.findByText('Connection lost, reconnecting…')).toBeInTheDocument();
    await waitFor(() => expect(sources).toHaveLength(2), { timeout: RECONNECT_MS + 1000 });
    const second = sources[1]!;
    expect(first.closed).toBe(true);
    expect(second.url).toBe(`/api/question/runs/run-1/events?after=${log[39]!.seq}`);
    act(() => {
      second.onopen?.(new Event('open'));
      // The server may resend a few events it already sent.
      log.slice(30).forEach((e) => second.send(e));
    });
    await waitFor(() => expect(screen.queryByText('Connection lost, reconnecting…')).toBeNull());
    const hits = log.filter((e) => e.type === 'hit' && e.data.new).length;
    await waitFor(() => expect(papers().getAllByRole('button')).toHaveLength(hits));
    expect(hits).toBeGreaterThan(seen.length);

    await userEvent.click(screen.getByRole('button', { name: 'Pause' }));
    expect(requests.at(-1)).toMatchObject({ method: 'POST', url: '/api/question/runs/run-1/pause' });
    expect(screen.getByRole('button', { name: 'Pausing…' })).toBeDisabled();
    act(() =>
      second.send({
        schema: 'question_event@1',
        run_id: 'run-1',
        seq: 1000,
        at: '2026-10-23T10:01:00.000Z',
        type: 'state',
        data: { state: 'paused' },
      }),
    );
    expect(await screen.findByRole('button', { name: 'Resume' })).toBeInTheDocument();
    expect(screen.getByRole('status')).toHaveTextContent('Paused');
  }, 10_000);
});

describe('a failed run', () => {
  it('says what failed, what to do and why, in the chosen language', async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ lang: 'zh' }));
    const sources: FakeSource[] = [];
    setSourceFactory((url) => {
      const s = new FakeSource(url);
      sources.push(s);
      return s;
    });
    fakeServer({ 'GET /api/question/runs/run-1': () => ({ body: { run: RUN } }) });
    at('/question/runs/run-1');
    const ev = (seq: number, type: string, data: unknown) =>
      ({
        schema: 'question_event@1',
        run_id: 'run-1',
        seq,
        at: '2026-10-24T10:00:00.000Z',
        type,
        data,
      }) as QuestionEvent;
    act(() => {
      sources[0]!.onopen?.(new Event('open'));
      [
        ev(1, 'state', { state: 'queued' }),
        ev(2, 'state', { state: 'running' }),
        ev(3, 'stage', { stage: 'plan', state: 'begin' }),
        ev(4, 'error', {
          stage: 'plan',
          fatal: true,
          code: 'model_unreachable',
          message: 'The model could not be reached (question_plan call to m: [Errno 111] Connection refused)',
        }),
        ev(5, 'state', { state: 'failed' }),
        ev(6, 'done', { status: 'failed' }),
      ].forEach((e) => sources[0]!.send(e));
    });
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('连不上模型');
    expect(alert).toHaveTextContent('确认模型服务已启动、ISBE_LLM_BASE_URL 指向它');
    expect(alert).toHaveTextContent('连不上模型。确认模型服务已启动');
    expect(alert).toHaveTextContent('原因：The model could not be reached');
    expect(within(alert).getByText(/^The model could not be reached/)).toHaveAttribute('lang', 'en');
  });

  it('lists the problems of a run that went on in the chosen language too', async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ lang: 'zh' }));
    const sources: FakeSource[] = [];
    setSourceFactory((url) => {
      const s = new FakeSource(url);
      sources.push(s);
      return s;
    });
    fakeServer({ 'GET /api/question/runs/run-1': () => ({ body: { run: RUN } }) });
    at('/question/runs/run-1');
    const ev = (seq: number, type: string, data: unknown) =>
      ({
        schema: 'question_event@1',
        run_id: 'run-1',
        seq,
        at: '2026-10-24T10:00:00.000Z',
        type,
        data,
      }) as QuestionEvent;
    act(() => {
      sources[0]!.onopen?.(new Event('open'));
      [
        ev(1, 'state', { state: 'running' }),
        ev(2, 'error', {
          stage: 'expand',
          fatal: false,
          code: 'essaies_down',
          message:
            'essaies is not answering citation lookups (essaies answered HTTP 503), so 12 papers were not looked up',
          detail: 'essaies answered HTTP 503: down',
        }),
        ev(3, 'error', { stage: 'search', fatal: false, message: 'An older problem without a code' }),
        ev(4, 'state', { state: 'done' }),
        ev(5, 'done', { status: 'partial' }),
      ].forEach((e) => sources[0]!.send(e));
    });
    await userEvent.click(await screen.findByRole('button', { name: '2 处问题' }));
    const items = within(screen.getByRole('dialog')).getAllByRole('listitem');
    expect(items[0]).toHaveTextContent('essaies 没有正常应答。essaies 连续出错或超时');
    expect(items[0]).toHaveTextContent('原因：essaies answered HTTP 503: down');
    // Without a code the console still speaks first; the server's words are the reason.
    expect(items[1]).toHaveTextContent('这一阶段出错了。原因：An older problem without a code');
    expect(within(items[1]!).getByText('An older problem without a code')).toHaveAttribute('lang', 'en');
  });
});

let overridden = '';

describe('plan edits over the API', () => {
  function serve(review: { on: boolean; active: boolean; ends_at: string | null }) {
    const sources: FakeSource[] = [];
    setSourceFactory((url) => {
      const s = new FakeSource(url);
      sources.push(s);
      return s;
    });
    const server = fakeServer({
      'GET /api/question/runs/run-1': () => ({ body: { run: { run_id: 'run-1', review } } }),
      'POST /api/question/runs/run-1/start': () => ({ body: { run: {} } }),
      'POST /api/question/runs/run-1/overrides': (req) => ({
        status: 201,
        body: {
          override: {
            override_id: 'o1',
            actor: 'ada',
            revokes: null,
            created_at: '2026-10-24T18:00:00.000Z',
            ...(req.body as object),
          },
        },
      }),
      'POST /api/question/runs/run-1/overrides/o1/revoke': () => ({
        body: {
          override: {
            override_id: 'o2',
            key: overridden,
            decision: null,
            actor: 'ada',
            revokes: 'o1',
            note: '',
            created_at: '2026-10-24T18:01:00.000Z',
          },
        },
      }),
      'POST /api/question/runs/run-1/plan': () => ({ body: { run: {} } }),
      'GET /api/question/runs/run-1/graph?form=compact': () => ({ body: { graph: GRAPH, stale: false } }),
      'POST /api/question/runs/run-1/graph': () => ({
        body: { graph: { fingerprint: 'f'.repeat(16) }, cached: false },
      }),
      'POST /api/question/runs/run-1/rerun': () => ({ status: 202, body: { run: { run_id: 'run-2' } } }),
    });
    return { sources, ...server };
  }
  const upTo = (type: string, stage?: string) => {
    const log = recordedLog();
    const i = log.findIndex(
      (e) => e.type === type && (!stage || (e.data as { stage?: string }).stage === stage),
    );
    return log.slice(0, i + 1);
  };

  it('sends only the edited fields of the plan version under review', async () => {
    const { sources, requests } = serve({ on: true, active: true, ends_at: null });
    at('/question/runs/run-1');
    act(() => upTo('plan').forEach((e) => sources[0]!.send(e)));
    await userEvent.click(await screen.findByRole('button', { name: 'Edit plan' }));
    const query = screen.getByLabelText('Query 2');
    await userEvent.clear(query);
    await userEvent.type(query, 'radar extrapolation');
    await userEvent.click(screen.getByRole('button', { name: 'Run edited plan' }));
    const post = requests.find((r) => r.url === '/api/question/runs/run-1/plan')!;
    const body = post.body as { version: number; changes: { queries: { query: string }[] } };
    expect(Object.keys(body.changes)).toEqual(['queries']);
    expect(body.version).toBe(1);
    expect(body.changes.queries[1]!.query).toBe('radar extrapolation');
  });

  it('waits for the person: no countdown, the search starts on Start search', async () => {
    // Fake timers before the page mounts, so a countdown it starts is a fake timer too.
    vi.useFakeTimers({ shouldAdvanceTime: true });
    onTestFinished(() => {
      vi.useRealTimers();
    });
    const { sources, requests } = serve({ on: true, active: true, ends_at: null });
    at('/question/runs/run-1');
    act(() => upTo('plan').forEach((e) => sources[0]!.send(e)));
    expect(await screen.findByText('The search waits until you start it.')).toBeInTheDocument();
    expect(screen.queryByRole('timer')).toBeNull();
    // Waiting for the person is not "running": no stage chip, no Pause.
    expect(screen.getByRole('status')).toHaveTextContent('Waiting for you to start');
    expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull();
    const steps = within(screen.getByRole('list', { name: 'Progress' })).getAllByRole('listitem');
    expect(steps[0]).toHaveAttribute('data-state', 'end');
    // An hour passes on fake timers: still nothing starts by itself.
    await act(() => vi.advanceTimersByTimeAsync(3_600_000));
    expect(requests.some((r) => r.url === '/api/question/runs/run-1/start')).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: 'Start search' }));
    expect(requests.some((r) => r.url === '/api/question/runs/run-1/start')).toBe(true);
  });

  it('includes, excludes and reverts a paper by hand, and offers the CSV once done', async () => {
    const { sources, requests } = serve({ on: true, active: false, ends_at: null });
    at('/question/runs/run-1');
    const log = upTo('done');
    act(() => log.forEach((e) => sources[0]!.send(e)));
    const result = await screen.findByRole('region', { name: 'Result' });
    // The paper set and the graph download; each link is the run's own route.
    const links = () =>
      within(within(result).getByRole('navigation', { name: 'Downloads' }))
        .getAllByRole('link')
        .map((a) => [a.textContent, a.getAttribute('href')]);
    await waitFor(() => expect(links()).toHaveLength(4));
    expect(links()).toEqual([
      ['Papers CSV', '/api/question/runs/run-1/export.csv'],
      ['Papers JSON', '/api/question/runs/run-1/export.json'],
      ['Graph JSON', '/api/question/runs/run-1/graph.json'],
      ['Graph GraphML', '/api/question/runs/run-1/graph.graphml'],
    ]);
    // The paper list sorts by PageRank once the graph is read.
    expect(
      within(papers().getByLabelText('Sort')).getByRole('option', { name: 'By PageRank' }),
    ).toBeInTheDocument();
    // Every verdict can be reached from the list: filter to the excluded papers.
    await userEvent.selectOptions(papers().getByLabelText('Show only'), 'exclude');
    const excludedRows = papers()
      .getAllByRole('button')
      .filter((b) => b.closest('li'));
    const excludedTotal = log.filter(
      (e) => e.type === 'verdict' && e.data.verdict.decision === 'exclude',
    ).length;
    expect(excludedRows.length).toBe(Math.min(200, excludedTotal));
    if (excludedTotal > 200) {
      expect(papers().getByText(`Showing 200 of ${excludedTotal}.`)).toBeInTheDocument();
      await userEvent.click(papers().getByRole('button', { name: /^Show \d+ more$/ }));
      expect(
        papers()
          .getAllByRole('button')
          .filter((b) => b.closest('li')).length,
      ).toBe(Math.min(400, excludedTotal));
    }
    expect(excludedRows.every((b) => b.textContent!.includes('Excluded'))).toBe(true);
    // A filter that matches nothing says so, not "No papers yet".
    await userEvent.selectOptions(papers().getByLabelText('Show only'), 'unscreened');
    expect(papers().getByText('No papers match this filter.')).toBeInTheDocument();
    await userEvent.selectOptions(papers().getByLabelText('Show only'), 'all');
    const kept = log.find((e) => e.type === 'verdict' && e.data.verdict.decision === 'include')!;
    overridden = kept.type === 'verdict' ? kept.data.verdict.key : '';
    const row = papers().getAllByRole('button')[0]!;
    await userEvent.click(row);
    const card = within(screen.getByRole('group', { name: 'Your decision' }));
    // The first row is an included paper (sorted by verdict), so only Exclude is offered.
    expect(card.queryByRole('button', { name: 'Include' })).toBeNull();
    await userEvent.type(card.getByLabelText('Note (optional)'), 'off topic');
    await userEvent.click(card.getByRole('button', { name: 'Exclude' }));
    const post = requests.find((r) => r.url === '/api/question/runs/run-1/overrides')!;
    expect(post.body).toMatchObject({ decision: 'exclude', note: 'off topic' });
    overridden = (post.body as { key: string }).key;
    // The graph built before the override now reads stale, without reading it again.
    const graphCard = await screen.findByRole('region', { name: 'Knowledge graph' });
    expect(within(graphCard).getByRole('status')).toHaveTextContent('This graph predates');
    const graphReads = () => requests.filter((r) => r.method === 'GET' && r.url.includes('/graph?')).length;
    expect(graphReads()).toBe(1);
    // Rebuild builds it on the server and reads the new one; the banner goes.
    await userEvent.click(within(graphCard).getByRole('button', { name: 'Rebuild graph' }));
    await waitFor(() => expect(graphReads()).toBe(2));
    expect(requests.some((r) => r.method === 'POST' && r.url === '/api/question/runs/run-1/graph')).toBe(
      true,
    );
    await waitFor(() => expect(within(graphCard).queryByRole('status')).toBeNull());
    // Shown at once, without waiting for the stream's override event.
    expect(await screen.findByRole('button', { name: 'Revert to model verdict' })).toBeInTheDocument();
    expect(screen.getByText(/off topic/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Revert to model verdict' }));
    expect(requests.some((r) => r.url === '/api/question/runs/run-1/overrides/o1/revoke')).toBe(true);
    expect(await screen.findByText('Reverted to model verdict')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Revert to model verdict' })).toBeNull();
  });

  it('reruns an edited plan once the search has started', async () => {
    const { sources, requests } = serve({ on: true, active: false, ends_at: null });
    at('/question/runs/run-1');
    act(() => upTo('stage', 'search').forEach((e) => sources[0]!.send(e)));
    await userEvent.click(await screen.findByRole('button', { name: 'Search plan' }));
    await userEvent.click(screen.getByRole('button', { name: 'Edit plan and rerun' }));
    await userEvent.type(screen.getByLabelText('Query 1'), ' 2024');
    await userEvent.click(screen.getByRole('button', { name: 'Edit plan and rerun' }));
    const post = requests.find((r) => r.url === '/api/question/runs/run-1/rerun')!;
    expect(Object.keys((post.body as { changes: object }).changes)).toEqual(['queries']);
    expect(requests.some((r) => r.url === '/api/question/runs/run-1/hold')).toBe(false);
    await waitFor(() => expect(sources.at(-1)!.url).toBe('/api/question/runs/run-2/events'));
  });
});

describe('run page replaying a recorded run', () => {
  it('waits for review, then runs to the end; a star can be selected from the list', async () => {
    fakeServer({});
    at('/question/runs/replay-radar-nowcasting-dl?step=0.2');
    // The recorded log is a lazily loaded module: give a busy machine time to load it.
    expect(
      await screen.findByText('The search waits until you start it.', undefined, { timeout: 5000 }),
    ).toBeInTheDocument();
    expect(papers().getByText('No papers yet.')).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button', { name: 'Start search' }));
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Done'), { timeout: 8000 });
    expect(screen.queryByText('The search waits until you start it.')).toBeNull();
    // The graph stage is not built: it is not shown as a step.
    const steps = within(screen.getByRole('list', { name: 'Progress' })).getAllByRole('listitem');
    expect(steps.map((s) => s.textContent)).not.toContain('Building the graph');
    expect(screen.getByRole('region', { name: 'Result' })).toHaveTextContent('screened reading list');
    const log = recordedLog();
    const included = log.filter((e) => e.type === 'verdict' && e.data.verdict.decision === 'include').length;
    expect(screen.getByText(new RegExp(`· ${included} included ·`))).toBeInTheDocument();
    // The flow: a card per query with its hits, the merge, and screening.
    const flow = within(screen.getByRole('region', { name: 'Search flow' }));
    const plan = log.find((e) => e.type === 'plan')!;
    const cards = within(flow.getByRole('list', { name: 'Queries' })).getAllByRole('listitem');
    expect(cards).toHaveLength(plan.type === 'plan' ? plan.data.plan.queries.length : 0);
    const hits = log.filter((e) => e.type === 'hit' && e.data.query_id === 'q1').length;
    expect(cards[0]).toHaveTextContent(String(hits));
    const stars = screen.getByText(/candidates ·/).textContent!.match(/^(\d+)/)![1]!;
    expect(flow.getByText(`${included} / ${stars}`)).toBeInTheDocument();
    expect(flow.getByText('Inclusion probability')).toBeInTheDocument();
    const best = flow.getAllByRole('button')[0]!;
    await userEvent.click(best);
    expect(screen.getByRole('region', { name: best.textContent! })).toHaveTextContent('Model verdict');
    await userEvent.click(
      within(screen.getByRole('region', { name: best.textContent! })).getByRole('button', { name: 'Close' }),
    );
    const row = papers().getAllByRole('button')[0]!;
    const title = row.querySelector('.q-paper-title')!.textContent!;
    await userEvent.click(row);
    expect(screen.getByRole('region', { name: title })).toHaveTextContent('Found by');
  }, 15_000);

  it('edits the plan under review, and cancels after confirmation', async () => {
    fakeServer({});
    at('/question/runs/replay-radar-nowcasting-dl?step=0.2');
    await userEvent.click(await screen.findByRole('button', { name: 'Edit plan' }));
    const query = screen.getByLabelText('Query 1');
    await userEvent.clear(query);
    await userEvent.type(query, 'radar nowcasting');
    await userEvent.click(screen.getByRole('button', { name: 'Run edited plan' }));
    expect(await screen.findByText(/Version 2 · Edited by hand/)).toBeInTheDocument();
    await waitFor(() => expect(papers().getAllByRole('button').length).toBeGreaterThan(0));
    await userEvent.click(screen.getByRole('button', { name: 'Cancel' }));
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent('cannot continue');
    await userEvent.click(within(dialog).getByRole('button', { name: 'Cancel' }));
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Cancelled'));
    expect(screen.queryByRole('button', { name: 'Pause' })).toBeNull();
    // A cancelled run says so; it does not claim a screened reading list.
    const result = screen.getByRole('region', { name: 'Result' });
    expect(result).toHaveTextContent('This run was cancelled.');
    expect(result).not.toHaveTextContent('screened reading list');
  }, 15_000);
});

describe('run page end states', () => {
  // Reconnect waits run on fake timers; real time still moves for the page's own work.
  beforeEach(() => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
  });
  afterEach(() => {
    vi.useRealTimers();
    onUnauthorized(() => {});
  });
  function sources() {
    const list: FakeSource[] = [];
    setSourceFactory((url) => {
      const s = new FakeSource(url);
      list.push(s);
      return s;
    });
    return list;
  }
  const pass = (ms: number) => act(() => vi.advanceTimersByTimeAsync(ms));
  const runGets = (requests: { method: string; url: string }[]) =>
    requests.filter((r) => r.method === 'GET' && r.url === '/api/question/runs/run-1').length;
  const running = async (list: FakeSource[]) => {
    act(() => {
      list[0]!.onopen?.(new Event('open'));
      FAILED.slice(0, 4).forEach((e) => list[0]!.send(e));
    });
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('Running'));
  };

  it('says a run that does not exist is not there, and stops asking', async () => {
    const list = sources();
    const { requests } = fakeServer({});
    at('/question/runs/run-gone');
    expect(await screen.findByText('There is no such run. It may have been deleted.')).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to galaxies' })).toHaveAttribute('href', '/galaxies');
    // The stream closes in the effect clean-up, which may run just after the view renders.
    await waitFor(() => expect(list[0]!.closed).toBe(true));
    await pass(60_000);
    expect(list).toHaveLength(1);
    expect(requests.filter((r) => r.url.endsWith('/events'))).toHaveLength(0);
  });

  it('ends the page when the run goes away while it is open', async () => {
    const list = sources();
    let gone = false;
    fakeServer({
      'GET /api/question/runs/run-1': () =>
        gone ? { status: 404, body: { error: 'No such question run' } } : { body: { run: RUN } },
    });
    at('/question/runs/run-1');
    await running(list);
    gone = true;
    act(() => list[0]!.onerror?.(new Event('error')));
    expect(await screen.findByText('There is no such run. It may have been deleted.')).toBeInTheDocument();
    await pass(60_000);
    expect(list).toHaveLength(1);
  });

  it('hands a 401 on the stream to the session layer and stops', async () => {
    const list = sources();
    const ended = vi.fn();
    onUnauthorized(ended);
    let signedIn = true;
    fakeServer({
      'GET /api/question/runs/run-1': () =>
        signedIn ? { body: { run: RUN } } : { status: 401, body: { error: 'Sign in first' } },
    });
    at('/question/runs/run-1');
    await running(list);
    signedIn = false;
    act(() => list[0]!.onerror?.(new Event('error')));
    await waitFor(() => expect(ended).toHaveBeenCalledTimes(1));
    await pass(60_000);
    expect(list).toHaveLength(1);
    expect(list[0]!.closed).toBe(true);
  });

  it('retries a dropped stream more slowly each time, then gives up with a retry button', async () => {
    const list = sources();
    let down = false;
    const { requests } = fakeServer({
      'GET /api/question/runs/run-1': () => {
        if (down) throw new TypeError('Failed to fetch');
        return { body: { run: RUN } };
      },
    });
    at('/question/runs/run-1');
    await running(list);
    const base = runGets(requests);
    down = true;
    // Each reopened stream fails again at once, as EventSource does with no server.
    const fail = () => act(() => list.at(-1)!.onerror?.(new Event('error')));
    await fail();
    expect(await screen.findByText('Connection lost, reconnecting…')).toBeInTheDocument();
    const waits = [2000, 4000, 8000, 16000];
    for (const [i, ms] of waits.entries()) {
      await pass(ms - 100);
      expect(list).toHaveLength(i + 1);
      await pass(100);
      expect(list).toHaveLength(i + 2);
      await fail();
    }
    // The fifth failed try gives up: no more requests, an error and a way to try again.
    await waitFor(() => expect(screen.getByRole('alert')).toHaveTextContent('Cannot reach the server'));
    expect(screen.queryByText('Connection lost, reconnecting…')).toBeNull();
    expect(runGets(requests) - base).toBe(5);
    await pass(120_000);
    expect(list).toHaveLength(5);
    expect(runGets(requests) - base).toBe(5);
    down = false;
    await userEvent.click(within(screen.getByRole('alert')).getByRole('button', { name: 'Retry' }));
    expect(list).toHaveLength(6);
    // The button goes away; focus stays at the banner rather than the page start.
    expect(document.activeElement).toHaveClass('q-connection');
    act(() => {
      list[5]!.onopen?.(new Event('open'));
    });
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
    // An open stream starts the count over: the next drop waits 2 s again.
    await fail();
    await pass(2000);
    expect(list).toHaveLength(7);
  });

  it('waits while the tab is hidden and tries again once it is seen', async () => {
    const list = sources();
    let down = true;
    fakeServer({
      'GET /api/question/runs/run-1': () => {
        if (down) throw new TypeError('Failed to fetch');
        return { body: { run: RUN } };
      },
    });
    down = false;
    at('/question/runs/run-1');
    await running(list);
    down = true;
    const hidden = vi.spyOn(document, 'hidden', 'get').mockReturnValue(true);
    try {
      act(() => list[0]!.onerror?.(new Event('error')));
      await pass(120_000);
      expect(list).toHaveLength(1);
      down = false;
      hidden.mockReturnValue(false);
      act(() => {
        document.dispatchEvent(new Event('visibilitychange'));
      });
      expect(list).toHaveLength(2);
    } finally {
      hidden.mockRestore();
    }
  });

  it('retries a 5xx but gives up at once on a 403', async () => {
    const list = sources();
    let status = 200;
    const { requests } = fakeServer({
      'GET /api/question/runs/run-1': () =>
        status === 200 ? { body: { run: RUN } } : { status, body: { error: 'nope' } },
    });
    at('/question/runs/run-1');
    await running(list);
    status = 503;
    act(() => list[0]!.onerror?.(new Event('error')));
    await pass(2000);
    expect(list).toHaveLength(2);
    status = 403;
    const before = runGets(requests);
    act(() => list[1]!.onerror?.(new Event('error')));
    // The server answered: it refused, which a retry cannot change.
    await waitFor(() =>
      expect(screen.getByRole('alert')).toHaveTextContent('The server refused to show this run'),
    );
    expect(within(screen.getByRole('alert')).queryByRole('button')).toBeNull();
    await pass(60_000);
    expect(runGets(requests) - before).toBe(1);
    expect(list).toHaveLength(2);
  });

  it('shows a failed run as failed, with no stage still at work and no false connection loss', async () => {
    const list = sources();
    fakeServer({ 'GET /api/question/runs/run-1': () => ({ body: { run: RUN } }) });
    at('/question/runs/run-1');
    act(() => {
      list[0]!.onopen?.(new Event('open'));
      FAILED.forEach((e) => list[0]!.send(e));
    });
    expect(await screen.findByText('Run failed')).toBeInTheDocument();
    // The server's `error` event is a problem of the run, not a dropped connection.
    expect(screen.queryByText('Connection lost, reconnecting…')).toBeNull();
    expect(screen.getAllByText('The expand stage failed').length).toBeGreaterThan(0);
    // "Expanding citations" is only the stage list's entry, marked stopped (in words
    // too, for screen readers): no header chip.
    const stages = screen.getByRole('list', { name: 'Progress' });
    const expand = within(stages).getByText('Expanding citations').closest('li')!;
    expect(expand).toHaveAttribute('data-state', 'stopped');
    expect(expand).toHaveTextContent('Expanding citationsstopped');
    expect(screen.getAllByText('Expanding citations')).toHaveLength(1);
    // The server ends the stream after `done`: no banner, no reconnect.
    act(() => list[0]!.onerror?.(new Event('error')));
    await pass(60_000);
    expect(screen.queryByText('Connection lost, reconnecting…')).toBeNull();
    expect(list).toHaveLength(1);
    expect(screen.queryByRole('button', { name: 'Cancel' })).toBeNull();
  });

  function ended(deleteAnswer: () => { status?: number; body: unknown }) {
    const list = sources();
    const server = fakeServer({
      'GET /api/question/runs': () => ({ body: { runs: [] } }),
      'GET /api/question/runs/run-1': () => ({ body: { run: RUN } }),
      'DELETE /api/question/runs/run-1': deleteAnswer,
    });
    at('/question/runs/run-1');
    act(() => FAILED.forEach((e) => list[0]!.send(e)));
    return server;
  }

  it('deletes a run that has ended, after asking', async () => {
    const list = sources();
    const { requests } = fakeServer({
      'GET /api/question/runs': () => ({ body: { runs: [] } }),
      'GET /api/question/runs/run-1': () => ({ body: { run: RUN } }),
      'DELETE /api/question/runs/run-1': () => ({ status: 204, body: null }),
    });
    at('/question/runs/run-1');
    await running(list);
    expect(screen.queryByRole('button', { name: 'Delete run' })).toBeNull();
    act(() => FAILED.slice(4).forEach((e) => list[0]!.send(e)));
    await userEvent.click(await screen.findByRole('button', { name: 'Delete run' }));
    const dialog = screen.getByRole('dialog');
    expect(dialog).toHaveTextContent('deleted for good');
    expect(requests.some((r) => r.method === 'DELETE')).toBe(false);
    await userEvent.click(within(dialog).getByRole('button', { name: 'Delete run' }));
    expect(await screen.findByText('Galaxies page')).toBeInTheDocument();
    expect(requests.find((r) => r.method === 'DELETE')!.url).toBe('/api/question/runs/run-1');
  });

  it('keeps the run when the dialog is closed or the delete fails', async () => {
    const { requests } = ended(() => ({ status: 500, body: { error: 'locked' } }));
    await userEvent.click(await screen.findByRole('button', { name: 'Delete run' }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Keep run' }));
    expect(requests.some((r) => r.method === 'DELETE')).toBe(false);
    await userEvent.click(screen.getByRole('button', { name: 'Delete run' }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete run' }));
    expect(
      await screen.findByText('The action could not be completed. Please try again.'),
    ).toBeInTheDocument();
    expect(screen.getByText('Run failed')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Delete run' })).toBeEnabled();
  });

  it('says a run that is still stopping cannot be deleted yet, and shows a run deleted elsewhere as gone', async () => {
    let answer = { status: 409, body: { error: 'still stopping' } };
    ended(() => answer);
    await userEvent.click(await screen.findByRole('button', { name: 'Delete run' }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete run' }));
    expect(await screen.findByText('This run is still stopping. Try again in a moment.')).toBeInTheDocument();
    answer = { status: 404, body: { error: 'No such question run' } };
    await userEvent.click(screen.getByRole('button', { name: 'Delete run' }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete run' }));
    expect(await screen.findByText('There is no such run. It may have been deleted.')).toBeInTheDocument();
  });
});

describe('result card of a run cancelled before it searched', () => {
  it('says nothing was found and offers no export', async () => {
    const sources: FakeSource[] = [];
    setSourceFactory((url) => {
      const s = new FakeSource(url);
      sources.push(s);
      return s;
    });
    fakeServer({ 'GET /api/question/runs/run-1': () => ({ body: { run: RUN } }) });
    at('/question/runs/run-1');
    await waitFor(() => expect(sources.length).toBe(1));
    act(() =>
      [
        ev(1, 'state', { state: 'queued' }),
        ev(2, 'state', { state: 'cancelled' }),
        ev(3, 'done', { status: 'cancelled' }),
      ].forEach((e) => sources[0]!.send(e)),
    );
    const result = await screen.findByRole('region', { name: 'Result' });
    expect(result).toHaveTextContent('cancelled before it found any papers');
    expect(within(result).queryByRole('link', { name: 'Export CSV' })).toBeNull();
  });
});

describe('run page header before and without a plan', () => {
  function serve(run: object, routes: Parameters<typeof fakeServer>[0] = {}) {
    const list: FakeSource[] = [];
    setSourceFactory((url) => {
      const s = new FakeSource(url);
      list.push(s);
      return s;
    });
    const server = fakeServer({ 'GET /api/question/runs/run-1': () => ({ body: { run } }), ...routes });
    return { list, ...server };
  }
  const ASKED = {
    ...RUN,
    question: '城市绿地对心理健康有什么影响？',
    lang: 'zh',
    options: {
      year_from: 2015,
      year_to: null,
      engine: 'arxiv',
      expand: false,
      expand_per_paper: 5,
      expand_total: 50,
      citations_full: 0,
    },
  };
  const PLANNING = [
    ev(1, 'state', { state: 'queued' }),
    ev(2, 'state', { state: 'running' }),
    ev(3, 'stage', { stage: 'plan', state: 'begin' }),
  ];

  it('titles a run in its plan step with the question asked, marked as Chinese', async () => {
    const { list } = serve(ASKED);
    at('/question/runs/run-1');
    act(() => PLANNING.forEach((e) => list[0]!.send(e)));
    const title = await screen.findByRole('heading', { level: 1, name: ASKED.question });
    // The console is in English; the question is not.
    expect(title).toHaveAttribute('lang', 'zh');
    await waitFor(() => expect(document.title).toBe(`ISBE · ${ASKED.question}`));
  });

  it('marks a paused stage as paused, in words and by its look', async () => {
    const { list } = serve(ASKED);
    at('/question/runs/run-1');
    act(() => [...PLANNING, ev(4, 'state', { state: 'paused' })].forEach((e) => list[0]!.send(e)));
    expect(await screen.findByText('Planning · paused')).toBeInTheDocument();
    const steps = within(screen.getByRole('list', { name: 'Progress' })).getAllByRole('listitem');
    expect(steps[0]).toHaveAttribute('data-state', 'paused');
    expect(steps[0]).toHaveTextContent('paused');
    expect(steps[0]).not.toHaveTextContent('in progress');
  });

  it('offers Try again on a run that failed before its plan, with the same question and options', async () => {
    const { list, requests } = serve(ASKED, {
      'POST /api/question/runs': () => ({ status: 202, body: { run: { run_id: 'run-2' } } }),
    });
    at('/question/runs/run-1');
    act(() =>
      [
        ...PLANNING,
        ev(4, 'error', { stage: 'plan', fatal: true, code: 'model_rejected', message: 'HTTP 401' }),
        ev(5, 'state', { state: 'failed' }),
        ev(6, 'done', { status: 'failed' }),
      ].forEach((e) => list[0]!.send(e)),
    );
    expect(await screen.findByRole('heading', { level: 1, name: ASKED.question })).toBeInTheDocument();
    await userEvent.click(await screen.findByRole('button', { name: 'Try again' }));
    const post = requests.find((r) => r.method === 'POST' && r.url === '/api/question/runs')!;
    expect(post.body).toEqual({
      question: ASKED.question,
      lang: 'zh',
      year_from: 2015,
      year_to: null,
      review: true,
      engine: 'arxiv',
      // The expand stage's options too: a retry does not turn back on what the person turned off.
      expand: false,
      expand_per_paper: 5,
      expand_total: 50,
      citations_full: 0,
    });
    await waitFor(() =>
      expect(list.some((s) => s.url.startsWith('/api/question/runs/run-2/events'))).toBe(true),
    );
  });

  it('keeps Try again disabled while it asks, and says why the server refused', async () => {
    let answer: (v: { status: number; body: object }) => void = () => {};
    const { list } = serve(ASKED, {
      'POST /api/question/runs': () => new Promise((r) => (answer = r)),
      'DELETE /api/question/runs/run-1': () => ({ status: 409, body: { error: 'still stopping' } }),
    });
    at('/question/runs/run-1');
    act(() =>
      [
        ...PLANNING,
        ev(4, 'error', { stage: 'plan', fatal: true, code: 'model_rejected', message: 'HTTP 401' }),
        ev(5, 'state', { state: 'failed' }),
      ].forEach((e) => list[0]!.send(e)),
    );
    const button = await screen.findByRole('button', { name: 'Try again' });
    await userEvent.click(button);
    expect(button).toBeDisabled();
    await act(async () => answer({ status: 500, body: { error: 'The queue is full' } }));
    const alert = await screen.findByText(/The action could not be completed/);
    expect(alert).toHaveTextContent('Reason: The queue is full');
    expect(within(alert).getByText('The queue is full')).toHaveAttribute('lang', 'en');
    expect(button).toBeEnabled();
    // A later failure shows its own words only, not the retry's reason.
    await userEvent.click(screen.getByRole('button', { name: 'Delete run' }));
    await userEvent.click(within(screen.getByRole('dialog')).getByRole('button', { name: 'Delete run' }));
    const busy = await screen.findByText(/This run is still stopping/);
    expect(busy).not.toHaveTextContent('The queue is full');
  });

  it('marks a paper title inside a console sentence with its own language', async () => {
    const { list } = serve(ASKED);
    at('/question/runs/run-1?paper=b');
    const plan = recordedLog().find((e) => e.type === 'plan')!;
    const q = (plan.data as { plan: { queries: { id: string }[] } }).plan.queries[0]!.id;
    act(() =>
      [
        ...PLANNING,
        plan,
        ev(5, 'hit', {
          query_id: q,
          rank: 1,
          new: true,
          paper: { key: 'a', keys: ['a'], title: '城市绿地与心理健康' },
        }),
        ev(6, 'expand', {
          from_key: 'a',
          via: 'cites',
          new: true,
          paper: { key: 'b', keys: ['b'], title: 'Green space and stress' },
        }),
      ]
        .map((e, i) => ({ ...e, seq: i + 1 }) as QuestionEvent)
        .forEach((e) => list[0]!.send(e)),
    );
    const card = await screen.findByRole('region', { name: 'Green space and stress' });
    expect(within(card).getByRole('heading', { name: 'Green space and stress' })).toHaveAttribute(
      'lang',
      'en',
    );
    expect(within(card).getByText('城市绿地与心理健康')).toHaveAttribute('lang', 'zh');
    expect(within(card).getByText('城市绿地与心理健康').parentElement).toHaveTextContent(
      'Cites 城市绿地与心理健康',
    );
  });

  it('offers no Try again while the run is still going', async () => {
    const { list } = serve(ASKED);
    at('/question/runs/run-1');
    act(() => PLANNING.forEach((e) => list[0]!.send(e)));
    expect(await screen.findByRole('button', { name: 'Pause' })).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: 'Try again' })).toBeNull();
  });

  it('names every query layer in Chinese and marks the model text as English', async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ lang: 'zh' }));
    const { list } = serve(ASKED);
    at('/question/runs/run-1');
    const plan = recordedLog().find((e) => e.type === 'plan')!;
    const data = plan.data as {
      plan: { queries: { layer: string; query: string }[]; reformulation: string };
    };
    const layered = {
      ...data.plan,
      question: ASKED.question,
      queries: data.plan.queries.map((q, i) => (i === 0 ? { ...q, layer: 'method_centric' } : q)),
    };
    act(() => [...PLANNING, ev(4, 'plan', { plan: layered })].forEach((e) => list[0]!.send(e)));
    const flow = await screen.findByRole('region', { name: '检索流程' });
    expect(within(flow).getByText('方法导向')).toBeInTheDocument();
    expect(flow).not.toHaveTextContent('Method centric');
    const query = within(flow).getByText(layered.queries[0]!.query);
    expect(query).toHaveAttribute('lang', 'en');
    expect(screen.getByText(data.plan.reformulation)).toHaveAttribute('lang', 'en');
  });
});

describe('knowledge graph of an ended run', () => {
  it('reads the graph once the run ends, and offers Retry when that fails', async () => {
    const sources: FakeSource[] = [];
    setSourceFactory((url) => {
      const s = new FakeSource(url);
      sources.push(s);
      return s;
    });
    let answer: { status?: number; body: unknown } = { status: 500, body: { error: 'boom' } };
    const { requests } = fakeServer({
      'GET /api/question/runs/run-1': () => ({ body: { run: RUN } }),
      'GET /api/question/runs/run-1/graph?form=compact': () => answer,
    });
    at('/question/runs/run-1');
    await waitFor(() => expect(sources.length).toBe(1));
    expect(requests.some((r) => r.url.endsWith('/graph'))).toBe(false);
    act(() =>
      [ev(1, 'state', { state: 'cancelled' }), ev(2, 'done', { status: 'cancelled' })].forEach((e) =>
        sources[0]!.send(e),
      ),
    );
    expect(await screen.findByText('The knowledge graph could not be read.')).toBeInTheDocument();
    answer = { body: { graph: GRAPH, stale: true } };
    await userEvent.click(screen.getByRole('button', { name: 'Retry' }));
    const card = await screen.findByRole('region', { name: 'Knowledge graph' });
    expect(card).toHaveTextContent('23 of 23 papers shown');
    expect(card).toHaveTextContent('This graph predates');
    expect(screen.queryByText('The knowledge graph could not be read.')).toBeNull();
  });
});
