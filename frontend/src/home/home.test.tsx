import { act, render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { MemoryRouter, Route, Routes, useLocation } from 'react-router';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { setCsrfToken } from '../api/client';
import { PrefsProvider, STORAGE_KEY } from '../prefs';
import { SessionProvider, useSession } from '../session';
import type { Run } from '../question/types';
import { AskBar, langOf } from '../shell/AskBar';
import { ADMIN, fakeServer, MEMBER } from '../test/fakeServer';
import { FRESH_MS, layout, needs, shortName } from './galaxies';
import { GalaxiesPage } from './GalaxiesPage';
import { HomePage } from './HomePage';
import { HEALTH_MS, type Upstream } from './useHealth';
import { layoutField, mainRoutes } from './field';

const NOW = Date.parse('2026-10-23T12:00:00Z');

function run(id: string, over: Partial<Run> = {}, counts: Partial<Run['counts']> = {}): Run {
  return {
    run_id: id,
    question: `Question ${id}?`,
    lang: 'en',
    status: 'done',
    stage: null,
    degraded: false,
    problems: 0,
    plan_version: 1,
    review: { on: true, active: false, ends_at: null },
    options: { year_from: null, year_to: null, engine: 'arxiv' },
    pausing: false,
    cancelling: false,
    counts: { hits: 0, candidates: 100, included: 20, excluded: 70, unscreened: 0, overridden: 0, ...counts },
    last_seq: 1,
    created_at: '2026-10-01T00:00:00Z',
    updated_at: '2026-10-01T00:00:00Z',
    ...over,
  };
}

describe('galaxies', () => {
  it('names a galaxy after the first clause of its question', () => {
    expect(shortName('  How do  radar echoes extrapolate? And why')).toBe('How do radar echoes extrapolate');
    expect(shortName('雷达回波外推有哪些方法？还有别的吗')).toBe('雷达回波外推有哪些方法');
    expect(shortName('x'.repeat(60), 10)).toBe(`${'x'.repeat(9)}…`);
  });

  it('lays galaxies out oldest first, apart, and keeps them put when a new one arrives', () => {
    const a = run('a', { created_at: '2026-10-01T00:00:00Z' }, { candidates: 900 });
    const b = run('b', { created_at: '2026-10-02T00:00:00Z' });
    const c = run('c', { created_at: '2026-10-03T00:00:00Z' }, { candidates: 400 });
    const two = layout([b, a]);
    const three = layout([c, a, b]);
    expect(three.map((g) => g.id)).toEqual(['a', 'b', 'c']);
    expect(three.slice(0, 2).map((g) => [g.x, g.y])).toEqual(two.map((g) => [g.x, g.y]));
    for (const g of three)
      for (const o of three) if (g !== o) expect(Math.hypot(g.x - o.x, g.y - o.y)).toBeGreaterThan(g.r + o.r);
    expect(three[0]!.r).toBeGreaterThan(three[1]!.r);
  });

  it('reads what needs the person off the runs, most urgent first', () => {
    const recent = new Date(NOW - 3600_000).toISOString();
    const old = new Date(NOW - FRESH_MS - 1).toISOString();
    const list = needs(
      [
        run('fresh', { updated_at: recent }),
        run('stale', { updated_at: old }),
        run('unjudged', { updated_at: old }, { unscreened: 12 }),
        run('plan', { status: 'running', review: { on: true, active: true, ends_at: null } }),
        run('failed', { status: 'failed', problems: 2 }),
        run('partial', { problems: 3, updated_at: recent }),
        run('working', { status: 'running', stage: 'search' }),
      ],
      NOW,
    );
    expect(list.map((n) => [n.kind, n.run.run_id, n.n])).toEqual([
      ['plan', 'plan', 0],
      ['failed', 'failed', 2],
      ['unjudged', 'unjudged', 12],
      ['problems', 'partial', 3],
      ['done', 'fresh', 100],
    ]);
  });

  it('asks to judge the papers of a partial run, and keeps its reason', () => {
    const problem = {
      stage: 'plan' as const,
      code: 'model_not_configured' as const,
      message: 'No model plan',
    };
    const [need] = needs(
      [run('no-model', { problems: 2, degraded: true, problem }, { unscreened: 300 })],
      NOW,
    );
    expect([need?.kind, need?.n, need?.run.problem?.code]).toEqual(['unjudged', 300, 'model_not_configured']);
  });

  it('shows a partial run on Home with its reason', async () => {
    const problem = {
      stage: 'plan',
      code: 'model_not_configured',
      message: 'No model plan (no key)',
    } as const;
    serve({
      'GET /api/question/runs': () => ({
        body: { runs: [run('p1', { problems: 2, degraded: true, problem }, { unscreened: 300 })] },
      }),
    });
    at('/');
    const rail = await screen.findByRole('complementary', { name: 'Reports and what needs you' });
    const [item] = await within(rail).findAllByRole('listitem');
    expect(item).toHaveTextContent('No model is configured. Configure a model');
    expect(item).toHaveTextContent('Reason: No model plan (no key)');
    expect(item).toHaveTextContent('300 papers are not judged yet.');
    expect(within(item!).getByRole('link', { name: 'Screen' })).toBeInTheDocument();
  });

  it('tells a Chinese question from an English one', () => {
    expect(langOf('雷达外推 nowcasting')).toBe('zh');
    expect(langOf('radar nowcasting')).toBe('en');
  });
});

function Where() {
  const { pathname, search } = useLocation();
  return <output aria-label="where">{pathname + search}</output>;
}

function SignedIn({ children }: { children: ReactNode }) {
  const { state } = useSession();
  return state.status === 'ready' && state.session.user ? children : null;
}

function at(path: string) {
  return render(
    <PrefsProvider>
      <SessionProvider>
        <SignedIn>
          <MemoryRouter initialEntries={[path]}>
            <AskBar />
            <Routes>
              <Route path="/" element={<HomePage />} />
              <Route path="/galaxies" element={<GalaxiesPage />} />
              <Route path="*" element={null} />
            </Routes>
            <Where />
          </MemoryRouter>
        </SignedIn>
      </SessionProvider>
    </PrefsProvider>,
  );
}

const OVERVIEW = {
  topics: [],
  reports: [],
  services: { papers: { source: 'essaies', ready: true }, llm: 'openai' },
};

/** The fake API with a signed-in administrator and an empty overview, plus `routes`. */
function serve(routes: Parameters<typeof fakeServer>[0], user: typeof ADMIN = ADMIN, llm = true) {
  return fakeServer({
    'GET /api/session': () => ({ body: { mode: 'shared', user, csrf_token: 'tok', llm: { ready: llm } } }),
    'GET /api/home': () => ({ body: OVERVIEW }),
    ...routes,
  });
}

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ lang: 'en' }));
  setCsrfToken('tok');
});

describe('home', () => {
  it('shows each run as a galaxy, previews the selected one, and lists what needs you', async () => {
    serve({
      'GET /api/question/runs': () => ({
        body: {
          runs: [
            run(
              'r1',
              { question: 'Which radar nowcasting methods work?' },
              { candidates: 240, included: 31, unscreened: 5 },
            ),
            run('r2', {
              status: 'running',
              stage: 'plan',
              review: { on: true, active: true, ends_at: null },
            }),
          ],
        },
      }),
    });
    at('/');
    expect(await screen.findByText('2 galaxies · 340 papers')).toBeInTheDocument();
    const rail = screen.getByRole('complementary', { name: 'Reports and what needs you' });
    const items = within(within(rail).getByRole('list', { name: /Needs you/ })).getAllByRole('listitem');
    expect(items.map((li) => li.dataset.kind)).toEqual(['plan', 'unjudged']);
    expect(items[0]).toHaveTextContent('The search plan is waiting for you');
    expect(within(items[0]!).getByRole('link', { name: 'Review plan' })).toHaveAttribute(
      'href',
      '/question/runs/r2',
    );
    expect(items[1]).toHaveTextContent('5 papers are not judged yet.');

    // Picking a need selects its galaxy and opens the preview card.
    await act(() => userEvent.click(within(items[1]!).getByRole('button', { name: /radar nowcasting/ })));
    expect(screen.getByLabelText('where')).toHaveTextContent('/?g=r1');
    const peek = screen.getByRole('region', { name: 'Which radar nowcasting methods work' });
    expect(peek).toHaveTextContent('240');
    expect(peek).toHaveTextContent('31');
    expect(within(peek).getByRole('switch', { name: 'Follow' })).toHaveAttribute('aria-disabled', 'true');
    expect(within(peek).getByRole('link', { name: 'Open galaxy' })).toHaveAttribute(
      'href',
      '/question/runs/r1',
    );

    // The galaxies are also a plain list, for keyboards and screen readers.
    await act(() =>
      userEvent.click(
        within(screen.getByRole('list', { name: 'All galaxies' })).getByRole('button', {
          name: 'Question r2?',
        }),
      ),
    );
    expect(screen.getByLabelText('where')).toHaveTextContent('/?g=r2');
    await act(() => userEvent.click(screen.getByRole('button', { name: 'Close' })));
    expect(screen.getByLabelText('where')).toHaveTextContent(/^\/$/);
    expect(screen.queryByRole('region', { name: 'Question r2' })).toBeNull();
  });

  it('says why a run failed and what to do, and warns when essaies is down', async () => {
    const up = { configured: true, state: 'ok', checked_at: '2026-10-24T00:00:00Z', error: null };
    serve({
      'GET /api/question/runs': () => ({
        body: {
          runs: [
            run('f1', {
              status: 'failed',
              problems: 1,
              failure: {
                stage: 'plan',
                code: 'essaies_unreachable',
                message:
                  'essaies could not be reached: [Errno 113] No route to host. Check that essaies is running…',
                detail: 'essaies could not be reached: [Errno 113] No route to host',
              },
            }),
            run('f2', {
              status: 'failed',
              problems: 1,
              failure: { stage: 'expand', code: null, message: 'Older text' },
            }),
          ],
        },
      }),
      'GET /api/health': () => ({
        body: {
          ok: false,
          worker: { alive: true },
          upstreams: {
            papers: {
              ...up,
              source: 'essaies',
              state: 'down',
              error: 'essaies could not be reached: [Errno 113] No route to host',
            },
            model: up,
          },
        },
      }),
    });
    at('/');
    const rail = await screen.findByRole('complementary', { name: 'Reports and what needs you' });
    expect(await within(rail).findByRole('status')).toHaveTextContent(
      'essaies is not answering now, so new questions will fail.Reason: essaies could not be reached',
    );
    const items = within(rail).getAllByRole('listitem');
    expect(items[0]).toHaveTextContent(
      'essaies could not be reached. Check that essaies is running and that ESSAIES_URL points to it',
    );
    expect(items[0]).toHaveTextContent(
      /Reason: essaies could not be reached: \[Errno 113\] No route to hostOpen$/,
    );
    expect(items[1]).toHaveTextContent('Older text');
    expect(items[1]).not.toHaveTextContent('This run failed.');
  });

  it('says nothing about health while every upstream answers', async () => {
    const up = { configured: true, state: 'ok', checked_at: null, error: null };
    serve({
      'GET /api/question/runs': () => ({ body: { runs: [] } }),
      'GET /api/health': () => ({
        body: {
          ok: true,
          worker: { alive: true },
          upstreams: { papers: { ...up, source: 'essaies' }, model: up },
        },
      }),
    });
    at('/');
    expect(await screen.findByText('Nothing needs you right now.')).toBeInTheDocument();
    const rail = screen.getByRole('complementary', { name: 'Reports and what needs you' });
    expect(within(rail).queryByRole('status')).toBeNull();
  });

  it('says when an upstream is not set, the model is down or the worker is gone', async () => {
    const off = { configured: false, state: 'unknown', checked_at: null, error: null };
    const down = { configured: true, state: 'down', checked_at: null, error: 'llm: Connection refused' };
    serve({
      'GET /api/question/runs': () => ({ body: { runs: [] } }),
      'GET /api/health': () => ({
        body: {
          ok: false,
          worker: { alive: false },
          upstreams: { papers: { ...off, source: 'essaies' }, model: down },
        },
      }),
    });
    at('/');
    const rail = await screen.findByRole('complementary', { name: 'Reports and what needs you' });
    const notice = await within(rail).findByRole('status');
    const lines = notice.querySelectorAll('p');
    expect([...lines].map((p) => p.textContent)).toEqual([
      'The background worker is not running, so new questions will wait in the queue.',
      'No essaies address (ESSAIES_URL) is set, so new questions will fail.',
      'The last model call failed, so new questions may fail.Reason: llm: Connection refused',
    ]);
  });

  it('says a refused key is a key to fix, not a model to wait for', async () => {
    const up = { configured: true, state: 'ok', checked_at: null, error: null };
    const refused = {
      ...up,
      state: 'down',
      error: 'ProviderError: HTTP 401: model provider rejected the request',
    };
    serve({
      'GET /api/question/runs': () => ({ body: { runs: [] } }),
      'GET /api/health': () => ({
        body: {
          ok: false,
          worker: { alive: true },
          upstreams: { papers: { ...up, source: 'essaies' }, model: refused },
        },
      }),
    });
    at('/');
    const rail = await screen.findByRole('complementary', { name: 'Reports and what needs you' });
    expect(await within(rail).findByRole('status')).toHaveTextContent(
      'The model refused the last call (a wrong API key or model name)',
    );
  });

  it('reads health every minute while the tab is in view, and not while it is hidden', async () => {
    vi.useFakeTimers({ shouldAdvanceTime: true });
    let hidden = false;
    vi.spyOn(document, 'visibilityState', 'get').mockImplementation(() => (hidden ? 'hidden' : 'visible'));
    const up: Upstream = { configured: true, state: 'ok', checked_at: null, error: null };
    let essaies: Upstream = { ...up, state: 'down', error: 'refused' };
    const { requests } = serve({
      'GET /api/question/runs': () => ({ body: { runs: [] } }),
      'GET /api/health': () => ({
        body: {
          ok: true,
          worker: { alive: true },
          upstreams: { papers: { ...essaies, source: 'essaies' }, model: up },
        },
      }),
    });
    const reads = () => requests.filter((r) => r.url === '/api/health').length;
    try {
      at('/');
      const rail = await screen.findByRole('complementary', { name: 'Reports and what needs you' });
      expect(await within(rail).findByRole('status')).toHaveTextContent('essaies is not answering now');
      expect(reads()).toBe(1);
      essaies = up;
      await act(() => vi.advanceTimersByTimeAsync(HEALTH_MS));
      expect(reads()).toBe(2);
      expect(within(rail).queryByRole('status')).toBeNull();
      hidden = true;
      act(() => void document.dispatchEvent(new Event('visibilitychange')));
      await act(() => vi.advanceTimersByTimeAsync(3 * HEALTH_MS));
      expect(reads()).toBe(2);
      hidden = false;
      await act(async () => void document.dispatchEvent(new Event('visibilitychange')));
      expect(reads()).toBe(3);
    } finally {
      vi.useRealTimers();
      vi.restoreAllMocks();
    }
  });

  it('counts one galaxy in the singular', async () => {
    serve({ 'GET /api/question/runs': () => ({ body: { runs: [run('r1')] } }) });
    at('/');
    expect(await screen.findByText('1 galaxy · 100 papers')).toBeInTheDocument();
  });

  it('guides a first run when there are no galaxies', async () => {
    serve({ 'GET /api/question/runs': () => ({ body: { runs: [] } }) }, ADMIN, false);
    at('/');
    const guide = await screen.findByRole('region', { name: 'Start here' });
    expect(guide).toHaveTextContent(/search plan.*judge each one.*citations/);
    expect(guide).toHaveTextContent('By default OpenAlex, covering all fields');
    expect(guide).toHaveTextContent('About 15 minutes');
    // The model is not set up; paper search is.
    expect(within(guide).getByText('Language model').nextElementSibling).toHaveTextContent('Not set up');
    expect(await within(guide).findByText('Paper search (essaies)')).toBeInTheDocument();
    expect(within(guide).getByText('Paper search (essaies)').nextElementSibling).toHaveTextContent(
      /^Set up$/,
    );
    expect(within(guide).getByRole('link', { name: 'Open Settings' })).toHaveAttribute('href', '/settings');
    expect(within(guide).getByRole('link', { name: 'Set up a topic' })).toHaveAttribute('href', '/topics');
    expect(screen.getByText('Nothing needs you right now.')).toBeInTheDocument();
  });

  it('names the OpenAlex API as the paper source, ready with nothing set up', async () => {
    serve(
      {
        'GET /api/question/runs': () => ({ body: { runs: [] } }),
        'GET /api/home': () => ({
          body: { ...OVERVIEW, services: { papers: { source: 'openalex', ready: true }, llm: 'openai' } },
        }),
      },
      ADMIN,
    );
    at('/');
    const guide = await screen.findByRole('region', { name: 'Start here' });
    expect(
      (await within(guide).findByText('Paper search (OpenAlex API)')).nextElementSibling,
    ).toHaveTextContent(/^Set up$/);
    expect(within(guide).queryByText('Paper search (essaies)')).toBeNull();
  });

  it('warns when the OpenAlex API is down', async () => {
    const up: Upstream = { configured: true, state: 'ok', checked_at: null, error: null };
    serve({
      'GET /api/question/runs': () => ({ body: { runs: [] } }),
      'GET /api/health': () => ({
        body: {
          ok: false,
          worker: { alive: true },
          upstreams: {
            papers: {
              ...up,
              state: 'down',
              source: 'openalex',
              error: 'ConnectError: [Errno 101] Network is unreachable',
            },
            model: up,
          },
        },
      }),
    });
    at('/');
    const rail = await screen.findByRole('complementary', { name: 'Reports and what needs you' });
    expect(await within(rail).findByRole('status')).toHaveTextContent(
      'The OpenAlex API is not answering now, so new questions will fail.',
    );
    expect(within(rail).getByRole('status')).not.toHaveTextContent('essaies');
  });

  it('tells a member who sets the services up', async () => {
    serve(
      {
        'GET /api/question/runs': () => ({ body: { runs: [] } }),
        'GET /api/home': () => ({ body: { ...OVERVIEW, services: { llm: 'configured' } } }),
      },
      MEMBER,
    );
    at('/');
    const guide = await screen.findByRole('region', { name: 'Start here' });
    expect(guide).toHaveTextContent('An administrator sets up the language model');
    expect(within(guide).queryByRole('link', { name: 'Open Settings' })).toBeNull();
    // A member's overview does not say whether the paper source is set up.
    expect(within(guide).queryByText(/^Paper search/)).toBeNull();
  });

  it('lists the newest topic reports and opens them on Reports', async () => {
    serve({
      'GET /api/question/runs': () => ({ body: { runs: [run('r1')] } }),
      'GET /api/home': () => ({
        body: {
          ...OVERVIEW,
          topics: [{ id: 'nowcasting', label: 'Radar nowcasting' }],
          reports: [
            {
              topic_id: 'nowcasting',
              period: '2026-W43',
              kind: 'weekly',
              created_at: 'x',
              papers: 1,
              media: false,
              materials: 0,
            },
            {
              topic_id: 'run-20261026-f7946162',
              period: '2026-10-26',
              kind: 'survey',
              created_at: 'x',
              papers: 20,
              media: false,
              materials: 0,
              label: '综述：大风集合预报',
            },
            {
              topic_id: 'press',
              period: '2026-W42',
              kind: 'weekly',
              created_at: 'x',
              papers: 0,
              media: true,
              materials: 12,
            },
            {
              topic_id: 'a',
              period: '2026-W41',
              kind: 'weekly',
              created_at: 'x',
              papers: 0,
              media: true,
              materials: 12,
            },
            {
              topic_id: 'b',
              period: '2026-W30',
              kind: 'weekly',
              created_at: 'x',
              papers: 0,
              media: true,
              materials: 12,
            },
          ],
        },
      }),
    });
    at('/');
    const box = await screen.findByRole('region', { name: 'Reports' });
    const link = await within(box).findByRole('link', { name: /Radar nowcasting/ });
    expect(link).toHaveAttribute('href', '/reports?topic=nowcasting&period=2026-W43');
    expect(link).toHaveTextContent('2026-W43 · 1 paper');
    // A survey's topic is a question run: it is named by its title.
    expect(within(box).getByRole('link', { name: /综述：大风集合预报/ })).toHaveTextContent(
      '2026-10-26 · 20 papers',
    );
    // Only the newest three; the rest are on Reports.
    expect(within(box).getAllByRole('listitem')).toHaveLength(3);
    expect(within(box).queryByText(/2026-W30/)).toBeNull();
    expect(within(box).getByRole('link', { name: 'All reports' })).toHaveAttribute('href', '/reports');
    // With galaxies, the sky shows them, not the guide.
    expect(screen.queryByRole('region', { name: 'Start here' })).toBeNull();
  });

  it('says why the reports cannot be read and reads them again on Retry', async () => {
    let calls = 0;
    serve({
      'GET /api/question/runs': () => ({ body: { runs: [run('r1')] } }),
      'GET /api/home': () =>
        ++calls === 1 ? { status: 500, body: { error: 'database is locked' } } : { body: OVERVIEW },
    });
    at('/');
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('The reports could not be read. database is locked');
    expect(screen.getByRole('link', { name: 'All reports' })).toHaveAttribute('href', '/reports');
    await userEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    expect(await screen.findByText(/No reports yet/)).toBeInTheDocument();
    expect(screen.queryByRole('alert')).toBeNull();
    expect(screen.getByRole('heading', { name: 'Reports' })).toHaveFocus();
    expect(calls).toBe(2);
  });

  it('says the server could not be reached when there is no answer', async () => {
    serve({
      'GET /api/question/runs': () => ({ body: { runs: [run('r1')] } }),
      'GET /api/home': () => {
        throw new TypeError('Failed to fetch');
      },
    });
    at('/');
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'The reports could not be read. The server could not be reached.',
    );
  });

  it('keeps the paper search row when the home read fails for an administrator', async () => {
    serve(
      {
        'GET /api/question/runs': () => ({ body: { runs: [] } }),
        'GET /api/home': () => ({ status: 500, body: { error: 'boom' } }),
      },
      ADMIN,
      false,
    );
    at('/');
    const guide = await screen.findByRole('region', { name: 'Start here' });
    expect((await within(guide).findByText('Paper search')).nextElementSibling).toHaveTextContent(
      'Could not be checked',
    );
  });

  it('points to topics when there are no reports yet', async () => {
    serve({ 'GET /api/question/runs': () => ({ body: { runs: [run('r1')] } }) });
    at('/');
    const box = await screen.findByRole('region', { name: 'Reports' });
    expect(await within(box).findByText(/No reports yet/)).toBeInTheDocument();
    expect(within(box).getByRole('link', { name: 'Set up a topic' })).toHaveAttribute('href', '/topics');
  });

  it('asks from the bar and lands on the new galaxy', async () => {
    let runs: Run[] = [];
    const { requests } = serve({
      'GET /api/question/runs': () => ({ body: { runs } }),
      'POST /api/question/runs': () => {
        runs = [run('new-1', { status: 'queued', question: '雷达回波外推有哪些方法？' })];
        return { body: { run: runs[0] } };
      },
    });
    at('/galaxies');
    await screen.findByText(/No galaxies yet/);
    await userEvent.type(
      screen.getByRole('searchbox', { name: 'Ask a research question' }),
      '雷达回波外推有哪些方法？',
    );
    await act(() => userEvent.click(screen.getByRole('button', { name: 'Ask' })));
    expect(await screen.findByRole('region', { name: '雷达回波外推有哪些方法' })).toBeInTheDocument();
    expect(screen.getByLabelText('where')).toHaveTextContent('/?g=new-1');
    const post = requests.find((r) => r.method === 'POST')!;
    expect(post.body).toMatchObject({ question: '雷达回波外推有哪些方法？', lang: 'zh', review: true });
    expect(post.headers['X-ISBE-CSRF']).toBe('tok');
    expect(screen.getByRole('searchbox')).toHaveValue('');
  });

  it('says why an ask failed and keeps the question', async () => {
    serve({
      'GET /api/question/runs': () => ({ body: { runs: [] } }),
      'POST /api/question/runs': () => ({ status: 503, body: { error: 'The queue is full.' } }),
    });
    at('/');
    await userEvent.type(await screen.findByRole('searchbox'), 'radar nowcasting');
    await act(() => userEvent.click(screen.getByRole('button', { name: 'Ask' })));
    expect(await screen.findByRole('alert')).toHaveTextContent('The queue is full.');
    expect(screen.getByRole('searchbox')).toHaveValue('radar nowcasting');
  });
});

describe('galaxies page', () => {
  it('lists the galaxies newest first and opens one on Home', async () => {
    serve({
      'GET /api/question/runs': () => ({
        body: {
          runs: [
            run('old', { created_at: '2026-10-01T00:00:00Z' }),
            run('bad', {
              created_at: '2026-09-30T00:00:00Z',
              status: 'failed',
              failure: { stage: 'plan', code: 'model_rejected', message: 'refused' },
            }),
            run(
              'new',
              { created_at: '2026-10-05T00:00:00Z', status: 'running' },
              { candidates: 12, included: 0 },
            ),
          ],
        },
      }),
    });
    at('/galaxies');
    const table = await screen.findByRole('table', { name: 'Galaxies' });
    const rows = within(table).getAllByRole('row').slice(1);
    expect(rows.map((r) => r.textContent)).toEqual([
      expect.stringMatching(/^Question new.*12.*0.*Running.*2026-10-05$/),
      expect.stringMatching(/^Question old.*100.*20.*Done.*2026-10-01$/),
      expect.stringMatching(/Run failed · The model refused the request2026-09-30$/),
    ]);
    await act(() => userEvent.click(within(rows[1]!).getByRole('button')));
    expect(screen.getByLabelText('where')).toHaveTextContent('/?g=old');
  });
});

describe('fields', () => {
  const paper = (key: string, queries: string[], cited = 0, included = true) => ({
    key,
    queries,
    cited,
    included,
  });

  it('settles the papers by their searches: one search per side, a shared paper between', () => {
    const papers = [
      ...Array.from({ length: 12 }, (_, i) => paper(`a${i}`, ['q1'])),
      ...Array.from({ length: 12 }, (_, i) => paper(`b${i}`, ['q2'])),
      paper('both', ['q1', 'q2']),
    ];
    const f = layoutField(papers);
    expect(f.stars).toHaveLength(25);
    expect(f.edges).toEqual([]);
    const mean = (p: string) => {
      const s = f.stars.filter((x) => x.key.startsWith(p));
      return { x: s.reduce((t, v) => t + v.x, 0) / s.length, y: s.reduce((t, v) => t + v.y, 0) / s.length };
    };
    const a = mean('a');
    const b = mean('b');
    const both = f.stars.find((s) => s.key === 'both')!;
    const d = (p: { x: number; y: number }, q: { x: number; y: number }) => Math.hypot(p.x - q.x, p.y - q.y);
    expect(d(a, b)).toBeGreaterThan(0.6);
    expect(d(both, a)).toBeLessThan(d(a, b));
    expect(d(both, b)).toBeLessThan(d(a, b));
    for (const s of f.stars) expect(Math.hypot(s.x, s.y)).toBeLessThanOrEqual(1.7);
    // Seeded: the same papers settle the same way.
    expect(layoutField([...papers].reverse()).stars).toEqual(f.stars);
  });

  it('keeps citations between known papers only, once each, and lights the trunk', () => {
    const papers = ['p0', 'p1', 'p2', 'p3', 'p4', 'p5'].map((k) => paper(k, ['q1']));
    const f = layoutField(papers, [
      ['p1', 'p0'],
      ['p2', 'p0'],
      ['p3', 'p0'],
      ['p0', 'p1'],
      ['p4', 'p3'],
      ['p5', 'p4'],
      ['p5', 'outside'],
      ['p2', 'p2'],
    ]);
    const key = (i: number) => f.stars[i]!.key;
    const edges = f.edges.map((e) => [key(e.a), key(e.b), e.main].join(' '));
    expect(edges).toHaveLength(5);
    expect(f.stars.find((s) => s.key === 'p0')!.degree).toBe(3);
    // p0, cited three times, sits on the trunk; the far end of the chain does not.
    expect(edges.filter((e) => e.endsWith('true')).every((e) => e.includes('p0') || e.includes('p3'))).toBe(
      true,
    );
    expect(edges).toContain('p5 p4 false');
  });

  it('reads the main routes like a river: the edges most papers flow through to the hub', () => {
    // Hub 0 is cited by 1–4 and 11–13; 1 is cited by 5–10, so seven papers reach 0 through 1.
    const links: [number, number][] = [
      ...[1, 2, 3, 4, 11, 12, 13].map((i): [number, number] => [i, 0]),
      ...[5, 6, 7, 8, 9, 10].map((i): [number, number] => [i, 1]),
      [5, 6],
      [14, 13],
    ];
    const degree = Array.from({ length: 15 }, (_, i) => links.filter(([, b]) => b === i).length);
    const edges = mainRoutes(links, degree);
    const at = (a: number, b: number) => edges.find((e) => e.a === a && e.b === b)!;
    expect(at(1, 0)).toMatchObject({ flow: 7, main: true });
    // A lone citation of a much-cited paper still feeds the trunk; one of a little-cited paper does not.
    expect(at(2, 0)).toMatchObject({ flow: 1, main: true });
    expect(at(14, 13)).toMatchObject({ flow: 1, main: false });
    // The edge that closes a cycle is off the tree.
    expect(at(5, 6)).toMatchObject({ flow: 0, main: false });
    expect(edges.filter((e) => e.flow > 0)).toHaveLength(14);
  });

  it('keeps the most telling papers when there are too many', () => {
    const many = Array.from({ length: 700 }, (_, i) => paper(`k${i}`, ['q1'], i, i % 7 === 0));
    const f = layoutField(many);
    expect(f.stars).toHaveLength(600);
    expect(f.stars.filter((s) => s.included)).toHaveLength(100);
  });
});
