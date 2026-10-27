// Help (/help) and its report guide: the behaviours the classic console's
// dialog tests (web/generation.test.cjs) covered before the guide moved here
// (#98), against a fake task queue.
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import type { ReactNode } from 'react';
import { createMemoryRouter, Route, RouterProvider, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { PrefsProvider, STORAGE_KEY } from '../prefs';
import { SessionProvider, useSession } from '../session';
import { ADMIN, fakeServer, MEMBER } from '../test/fakeServer';
import { markerKey, NO_DATA, type Job } from './guide';
import { HelpPage } from './HelpPage';

const paper = (id = 'papers') => ({
  id,
  config: { id, label: `Research ${id}`, arxiv: { categories: ['cs.AI'], include_keywords: ['agents'] } },
  revision: '1',
});

const job = (state: Job['state'] = 'running', topic = 'papers', id = 'job-1', owner = ADMIN.id): Job => ({
  id,
  owner,
  topic,
  state,
  stage: state === 'running' ? 'collect' : state === 'queued' ? 'queued' : 'digest',
  error: null,
  result: state === 'succeeded' ? { topic, period: '2026-W42' } : null,
  position: null,
});

type Topic = { id: string; config: Record<string, unknown> };

/** A task queue that answers the way `isbe/web/tasks.py` does. */
function queue({
  topics = [paper()] as Topic[],
  ready = true,
  user = ADMIN,
  current = null as Job | null,
}: { topics?: Topic[]; ready?: boolean; user?: typeof ADMIN; current?: Job | null } = {}) {
  let statusFails = false;
  let expired = false;
  let configFails = false;
  let post: ((body: { topic: string; request_id: string }) => { status: number; body: unknown }) | null =
    null;
  let hold: Promise<void> | null = null;
  const state = { current };
  const server = fakeServer({
    'GET /api/session': () =>
      configFails
        ? { status: 500, body: { error: '/srv/private/config' } }
        : {
            body: {
              mode: 'shared',
              user: expired ? null : user,
              csrf_token: 'session-token',
              llm: { ready },
            },
          },
    'GET /api/topics': () => ({
      body: { topics: topics.map((t) => ({ id: t.id, label: (t.config as { label?: string }).label })) },
    }),
    ...Object.fromEntries(topics.map((t) => [`GET /api/topics/${t.id}`, () => ({ body: t })])),
    'GET /api/tasks': () => {
      if (statusFails) throw new TypeError('/srv/private/provider-key');
      return { body: { csrf_token: 'generation-token', tasks: state.current ? [state.current] : [] } };
    },
    'POST /api/tasks': async (req) => {
      if (hold) await hold;
      const body = req.body as { topic: string; request_id: string };
      if (post) return post(body);
      state.current = job('running', body.topic, 'job-1', user.id);
      return { status: 202, body: { job: state.current } };
    },
  });
  // `GET /api/tasks/<id>`: any id, answered from the current task.
  const route = server.fetchMock.getMockImplementation()!;
  server.fetchMock.mockImplementation(async (input, init) => {
    const url = String(input);
    if (url.startsWith('/api/tasks/') && (init?.method ?? 'GET') === 'GET') {
      server.requests.push({ method: 'GET', url, headers: {}, body: undefined });
      if (expired) return Response.json({ error: 'Sign in again.' }, { status: 401 });
      if (statusFails) throw new TypeError('/srv/private/provider-key');
      const found = state.current && state.current.id === decodeURIComponent(url.split('/').pop()!);
      return found
        ? Response.json({ csrf_token: 'generation-token', job: state.current })
        : Response.json({ error: 'not found' }, { status: 404 });
    }
    return route(input, init);
  });
  return {
    ...server,
    state,
    posts: () => server.requests.filter((r) => r.method === 'POST'),
    failStatus: (v: boolean) => (statusFails = v),
    expire: () => (expired = true),
    failConfig: (v: boolean) => (configFails = v),
    reply: (status: number, body: unknown) => (post = () => ({ status, body })),
    /** The submission reaches the server but no answer comes back. */
    loseReply: () =>
      (post = () => {
        throw new TypeError('secret provider response');
      }),
    holdPost: () => {
      let release = () => {};
      hold = new Promise((resolve) => (release = resolve));
      return () => {
        hold = null;
        release();
      };
    },
  };
}

function SignedIn({ children }: { children: ReactNode }) {
  const { state } = useSession();
  return state.status === 'ready' && state.session.user ? children : null;
}

function at(path = '/help') {
  const router = createMemoryRouter(
    [
      {
        path: '*',
        element: (
          <Routes>
            <Route path="/help" element={<HelpPage />} />
          </Routes>
        ),
      },
    ],
    { initialEntries: [path] },
  );
  const view = render(
    <PrefsProvider>
      <SessionProvider>
        <SignedIn>
          <RouterProvider router={router} />
        </SignedIn>
      </SessionProvider>
    </PrefsProvider>,
  );
  return { ...view, router };
}

const guide = () => within(document.getElementById('report-guide')!);
const status = () => document.getElementById('generation-status')?.textContent ?? '';
const button = (name: RegExp) => guide().getByRole('button', { name });
const step = (n: number) => screen.findByText(`Step ${n} of 3`);

async function toStart() {
  await step(1);
  await waitFor(() => expect(button(/^Next/)).toBeEnabled());
  fireEvent.click(button(/^Next/));
  await waitFor(() => expect(button(/^Next/)).toBeEnabled());
  fireEvent.click(button(/^Next/));
  await step(3);
}

/** Run the guide's next 2-second status check. */
const poll = () => act(() => vi.advanceTimersByTimeAsync(2000));

beforeEach(() => {
  vi.useFakeTimers({ shouldAdvanceTime: true });
  sessionStorage.clear();
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe('Help', () => {
  it('explains ISBE, holds the guide and, for administrators, the service help', async () => {
    queue();
    at();
    expect(await screen.findByRole('heading', { level: 1, name: 'Help' })).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Start here' })).toBeInTheDocument();
    expect(screen.getByText(/box at the top of the page/)).toBeInTheDocument();
    expect(screen.getByRole('heading', { name: 'Generate a report' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Open the setup guide/ })).toHaveAttribute(
      'href',
      '/help/service',
    );
    await waitFor(() => expect(document.title).toBe('ISBE · Help'));
  });

  it('gives members no service help, settings links or administrator reads', async () => {
    const q = queue({ user: MEMBER });
    at();
    await step(1);
    expect(await screen.findByText(/administrator manages the model/)).toBeInTheDocument();
    expect(screen.queryByRole('link', { name: /setup guide/ })).toBeNull();
    expect(document.querySelector('a[href="/settings"]')).toBeNull();
    await toStart();
    fireEvent.click(button(/^Generate report/));
    await waitFor(() => expect(q.posts()).toHaveLength(1));
    expect(q.requests.some((r) => /^\/api\/(settings|runs|triage|overview|users)/.test(r.url))).toBe(false);
    expect(sessionStorage.getItem(markerKey(MEMBER.id))).not.toBeNull();
    expect(sessionStorage.getItem('isbe.generation')).toBeNull();
  });

  it("hides the guide while an administrator reads another account's workspace", async () => {
    queue();
    at('/help?owner=u9');
    await screen.findByRole('heading', { level: 1, name: 'Help' });
    expect(document.getElementById('report-guide')).toBeNull();
  });
});

describe('report guide', () => {
  it('uses three explicit steps, moves focus to its heading, and starts nothing on opening', async () => {
    const q = queue();
    at('/help#report-guide');
    await step(1);
    expect(await screen.findByText(/connection not verified/)).toBeInTheDocument();
    expect(guide().getByRole('listitem', { current: 'step' })).toHaveTextContent('Check your model');
    fireEvent.click(button(/^Next/));
    expect(document.activeElement).toBe(document.getElementById('guide-title'));
    await step(2);
    expect(await guide().findByText(/arXiv categories and keywords are configured/)).toBeInTheDocument();
    fireEvent.click(button(/^Next/));
    await step(3);
    expect(guide().getByText(/using saved model settings/)).toBeInTheDocument();
    expect(q.posts()).toHaveLength(0);
    expect(document.getElementById('report-guide')!.textContent).not.toMatch(
      /isbe --data|uv run|Docker|\d+%/,
    );
    fireEvent.click(button(/^Back/));
    await step(2);
  });

  it('cannot start without a model, with article-only sources, or with incomplete paper sources', async () => {
    const model = queue({ ready: false });
    const first = at();
    expect(await screen.findByText(/Model configuration incomplete/)).toBeInTheDocument();
    expect(button(/^Next/)).toBeDisabled();
    expect(model.posts()).toHaveLength(0);
    first.unmount();
    for (const [config, text] of [
      [{ rss: { feeds: [{ name: 'News', url: 'https://example.com/feed' }] } }, /article sources only/],
      [{ arxiv: { categories: [], include_keywords: [] } }, /Complete and save/],
    ] as const) {
      vi.unstubAllGlobals();
      const q = queue({ topics: [{ id: 'papers', config }] });
      const view = at();
      await step(1);
      await waitFor(() => expect(button(/^Next/)).toBeEnabled());
      fireEvent.click(button(/^Next/));
      expect(await guide().findByText(text)).toBeInTheDocument();
      expect(button(/^Next/)).toBeDisabled();
      expect(q.posts()).toHaveLength(0);
      view.unmount();
    }
  });

  it('starts once with the CSRF token, topic and a request ID, then follows the stages', async () => {
    const q = queue();
    at();
    await toStart();
    const release = q.holdPost();
    fireEvent.click(button(/^Generate report/));
    expect(await guide().findByRole('button', { name: 'Starting generation…' })).toBeDisabled();
    fireEvent.click(guide().getByRole('button', { name: 'Starting generation…' }));
    expect(status()).toMatch(/Starting generation/);
    release();
    await waitFor(() => expect(status()).toMatch(/Generating a report for “Research papers”/));
    const posts = q.posts();
    expect(posts).toHaveLength(1);
    expect(posts[0]!.headers['X-ISBE-CSRF']).toBe('session-token');
    const body = posts[0]!.body as { topic: string; request_id: string };
    expect(body.topic).toBe('papers');
    expect(body.request_id).toMatch(/^[a-f0-9]{32}$/);
    expect(JSON.parse(sessionStorage.getItem('isbe.generation.u1')!)).toEqual({
      id: 'job-1',
      topic: 'papers',
    });
    const line = document.getElementById('generation-status')!;
    expect(line).toHaveAttribute('role', 'status');
    expect(line).toHaveAttribute('aria-live', 'polite');
    expect(line).toHaveAttribute('aria-atomic', 'true');
    q.state.current = { ...job(), stage: 'enrich' };
    await poll();
    await waitFor(() =>
      expect(document.querySelector('.generation-stages [aria-current="step"]')).toHaveTextContent(
        'Retrieve full text',
      ),
    );
    q.state.current = job('succeeded');
    await poll();
    await waitFor(() => expect(status()).toMatch(/Your report is ready/));
    expect(guide().getByRole('link', { name: /Open report/ })).toHaveAttribute(
      'href',
      '/reports?topic=papers&period=2026-W42',
    );
    expect(vi.getTimerCount()).toBe(0);
    expect(sessionStorage.getItem('isbe.generation.u1')).toBeNull();
    expect(q.posts()).toHaveLength(1);
  });

  it('picks running or finished work up again after a refresh without another submission', async () => {
    sessionStorage.setItem('isbe.generation.u1', JSON.stringify({ id: 'job-1', topic: 'papers' }));
    const q = queue({ current: job('succeeded') });
    const view = at();
    await waitFor(() => expect(status()).toMatch(/Your report is ready/));
    expect(q.requests.some((r) => r.url === '/api/tasks/job-1')).toBe(true);
    expect(q.posts()).toHaveLength(0);
    view.unmount();
    vi.unstubAllGlobals();
    // The task is gone: interrupted, and no polling.
    sessionStorage.setItem('isbe.generation.u1', JSON.stringify({ id: 'job-1', topic: 'papers' }));
    const lost = queue();
    at();
    await waitFor(() => expect(status()).toMatch(/was interrupted or could not be found/));
    expect(guide().getByRole('link', { name: /View existing reports/ })).toHaveAttribute(
      'href',
      '/reports?topic=papers',
    );
    expect(vi.getTimerCount()).toBe(0);
    expect(lost.posts()).toHaveLength(0);
  });

  it('follows the named task from the Tasks page rather than the latest one', async () => {
    const q = queue({ current: { ...job('queued'), position: 2 } });
    at('/help?task=job-1#report-guide');
    await waitFor(() => expect(status()).toMatch(/Queued · Queue position: 2/));
    expect(q.requests.some((r) => r.url === '/api/tasks/job-1')).toBe(true);
    expect(q.posts()).toHaveLength(0);
    expect(vi.getTimerCount()).toBe(1);
  });

  it("does not let another topic's running task block a new topic", async () => {
    queue({ topics: [paper(), paper('other')], current: job('running', 'other') });
    at('/help?topic=papers');
    await toStart();
    fireEvent.click(button(/^Generate report/));
    await waitFor(() => expect(status()).toMatch(/Generating a report for “Research papers”/));
  });

  it('adopts the queued task on busy, and a failed status read stops polling without internals', async () => {
    const q = queue({ topics: [paper(), paper('other')] });
    at();
    await toStart();
    q.reply(409, { code: 'busy', error: 'private internals', job: job('running', 'other') });
    fireEvent.click(button(/^Generate report/));
    await waitFor(() => expect(status()).toMatch(/Generating a report for “Research other”/));
    expect(document.body.textContent).not.toMatch(/private internals/);
    q.state.current = job('running', 'other');
    q.failStatus(true);
    await poll();
    await waitFor(() => expect(status()).toMatch(/Task status is temporarily unavailable/));
    expect(vi.getTimerCount()).toBe(0);
    expect(guide().queryByRole('button', { name: /^Generate report/ })).toBeNull();
    expect(document.body.textContent).not.toMatch(/\/srv\/private|provider-key/);
    q.failStatus(false);
    q.state.current = job('succeeded', 'other');
    fireEvent.click(button(/^Check again/));
    await waitFor(() => expect(status()).toMatch(/Your report is ready/));
  });

  it('checks an unanswered start with a read and never submits it again on its own', async () => {
    const q = queue();
    at();
    await toStart();
    q.loseReply();
    fireEvent.click(button(/^Generate report/));
    await waitFor(() => expect(status()).toMatch(/Submission is unconfirmed/));
    expect(document.body.textContent).not.toMatch(/secret provider response/);
    await poll();
    expect(q.posts()).toHaveLength(1);
  });

  it('reuses the request ID after a refresh, and an old finished task cannot confirm it', async () => {
    const q = queue({ current: job('succeeded') });
    const view = at('/help?new=1');
    await toStart();
    q.loseReply();
    fireEvent.click(button(/^Generate report/));
    await waitFor(() => expect(status()).toMatch(/Submission is unconfirmed/));
    expect(status()).not.toMatch(/Your report is ready/);
    const first = (q.posts()[0]!.body as { request_id: string }).request_id;
    view.unmount();
    vi.unstubAllGlobals();
    const again = queue({ current: job('succeeded') });
    at();
    await waitFor(() => expect(status()).toMatch(/Submission is unconfirmed/));
    expect(again.posts()).toHaveLength(0);
    await waitFor(() => expect(button(/^Generate report/)).toBeEnabled());
    fireEvent.click(button(/^Generate report/));
    await waitFor(() => expect(again.posts()).toHaveLength(1));
    expect((again.posts()[0]!.body as { request_id: string }).request_id).toBe(first);
  });

  it('asks for a new explicit start after a finished report', async () => {
    const q = queue({ current: job('succeeded') });
    at();
    await waitFor(() => expect(status()).toMatch(/Your report is ready/));
    fireEvent.click(button(/^Generate another report/));
    await step(1);
    expect(document.activeElement).toBe(document.getElementById('guide-title'));
    expect(q.posts()).toHaveLength(0);
  });

  it('explains a failure with no data, in both languages, and keeps other failures generic', async () => {
    for (const lang of ['en', 'zh'] as const) {
      localStorage.setItem(STORAGE_KEY, JSON.stringify({ lang }));
      vi.unstubAllGlobals();
      queue({ current: { ...job('failed'), error: NO_DATA } });
      const view = at();
      await waitFor(() =>
        expect(status()).toMatch(lang === 'en' ? /No usable papers remain/ : /没有可用于报告的论文/),
      );
      expect(document.querySelector('a[href="/triage?topic=papers"]')).not.toBeNull();
      expect(status()).not.toMatch(/Your report is ready|报告已生成/);
      view.unmount();
    }
    localStorage.clear();
    vi.unstubAllGlobals();
    // A member's no-data failure has no triage link (triage is administrators').
    queue({ user: MEMBER, current: { ...job('failed', 'papers', 'job-1', MEMBER.id), error: NO_DATA } });
    const member = at();
    await waitFor(() => expect(status()).toMatch(/No usable papers remain/));
    expect(document.querySelector('a[href^="/triage"]')).toBeNull();
    member.unmount();
    vi.unstubAllGlobals();
    queue({ current: { ...job('failed'), error: '/srv/secret token=abc' } });
    const failed = at();
    await waitFor(() => expect(status()).toMatch(/A report could not be created/));
    expect(document.body.textContent).not.toMatch(/\/srv\/secret|token=abc/);
    failed.unmount();
  });

  it('says when the saved configuration could not be read', async () => {
    const q = queue();
    at();
    await step(1);
    q.failConfig(true);
    fireEvent.click(button(/^Check again/));
    expect(await guide().findByText(/Saved configuration could not be checked/)).toBeInTheDocument();
    expect(document.body.textContent).not.toMatch(/\/srv\/private/);
  });

  it('maps refusals to their reasons and forgets the submission', async () => {
    const q = queue();
    at();
    await toStart();
    q.reply(400, { code: 'unsupported_topic', error: 'internal reason' });
    fireEvent.click(button(/^Generate report/));
    expect(await guide().findByRole('alert')).toHaveTextContent(/article sources only/);
    expect(sessionStorage.getItem('isbe.generation.u1')).toBeNull();
    expect(document.body.textContent).not.toMatch(/internal reason/);
  });

  it('starts from what a new address names, not from the task shown before', async () => {
    const q = queue({ topics: [paper(), paper('other')], current: job('running') });
    const { router } = at('/help?task=job-1#report-guide');
    await waitFor(() => expect(status()).toMatch(/Generating a report for “Research papers”/));
    await act(() => router.navigate('/help?topic=other#report-guide'));
    await step(1);
    expect(guide().queryByText(/Research papers/)).toBeNull();
    const reads = q.requests.filter((r) => r.url === '/api/tasks/job-1').length;
    await poll();
    await poll();
    expect(q.requests.filter((r) => r.url === '/api/tasks/job-1')).toHaveLength(reads);
    q.state.current = job('succeeded');
    await act(() => router.navigate('/help?task=job-1#report-guide'));
    await waitFor(() => expect(status()).toMatch(/Your report is ready/));
    await act(() => router.navigate('/help?new=1#report-guide'));
    await step(1);
    expect(document.getElementById('generation-status')).toBeNull();
  });

  it("leaves another account's task alone rather than calling it interrupted", async () => {
    sessionStorage.setItem('isbe.generation.u1', JSON.stringify({ id: 'job-1', topic: 'papers' }));
    const q = queue({ current: job('running', 'papers', 'job-1', MEMBER.id) });
    at('/help?task=job-1#report-guide');
    await step(1);
    await waitFor(() => expect(button(/^Next/)).toBeEnabled());
    expect(document.body.textContent).not.toMatch(/interrupted/);
    expect(sessionStorage.getItem('isbe.generation.u1')).toBeNull();
    expect(q.posts()).toHaveLength(0);
  });

  it('keeps an unanswered submission when the address names a task it cannot follow', async () => {
    const pending = JSON.stringify({ topic: 'papers', requestId: 'r-1' });
    sessionStorage.setItem('isbe.generation.u1', pending);
    queue({ current: job('running', 'papers', 'job-1', MEMBER.id) });
    const { router } = at('/help?task=job-1#report-guide');
    await step(1);
    expect(sessionStorage.getItem('isbe.generation.u1')).toBe(pending);
    await act(() => router.navigate('/help?task=nope#report-guide'));
    await waitFor(() => expect(status()).toMatch(/interrupted/));
    expect(sessionStorage.getItem('isbe.generation.u1')).toBe(pending);
  });

  it('stops following a task, and shows nothing of it, once the session ends', async () => {
    const q = queue({ current: job('running', 'papers', 'job-1') });
    sessionStorage.setItem('isbe.generation.u1', JSON.stringify({ id: 'job-1', topic: 'papers' }));
    at();
    await waitFor(() => expect(status()).toMatch(/Generating a report/));
    q.expire();
    await poll();
    await waitFor(() => expect(document.getElementById('report-guide')).toBeNull());
    expect(document.body.textContent).not.toMatch(/Research papers/);
    expect(vi.getTimerCount()).toBe(0);
  });
});
