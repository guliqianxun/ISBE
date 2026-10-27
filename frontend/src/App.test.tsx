import { act, render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { existsSync, readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { beforeEach, describe, expect, it } from 'vitest';
import { App } from './App';
import { endSession } from './api/client';
import { STORAGE_KEY } from './prefs';
import { NAV, SYSTEM } from './shell/nav';
import { ADMIN, fakeServer, MEMBER } from './test/fakeServer';

const local = { mode: 'local', user: ADMIN, csrf_token: 'tok', llm: { ready: true } };

function at(path: string) {
  window.history.replaceState(null, '', path);
}

describe('app shell', () => {
  beforeEach(() => {
    endSession();
    at('/question');
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ lang: 'en' }));
  });

  it('opens straight into the shell in local mode, with every administrator page', async () => {
    fakeServer({ 'GET /api/session': () => ({ body: local }) });
    render(<App />);
    expect(await screen.findByRole('heading', { name: 'Question research' })).toBeInTheDocument();
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(nav).toHaveTextContent(/Home.*Galaxies.*Reports.*Topics.*Settings.*System/);
    expect(nav).not.toHaveTextContent('Runs & LLM');
    expect(screen.queryByRole('button', { name: 'Sign out' })).toBeNull();
    // A question's pages belong to Galaxies.
    expect(screen.getByRole('link', { name: 'Galaxies' })).toHaveAttribute('aria-current', 'page');
    // Classic pages load as whole pages.
    expect(screen.getByRole('link', { name: 'Reports' })).toHaveAttribute('href', '/reports');
    // The title is set in a passive effect, which may run after findBy has resolved.
    await waitFor(() => expect(document.title).toBe('ISBE · Question research'));

    // The classic pages wait under System.
    await act(() => userEvent.click(screen.getByRole('link', { name: 'System' })));
    expect(await screen.findByRole('heading', { name: 'System' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: /Runs & LLM/ })).toHaveAttribute('href', '/runs');
    expect(screen.getByRole('link', { name: /Overview/ })).toHaveAttribute('href', '/overview');
    expect(screen.queryByRole('link', { name: /Accounts/ })).toBeNull();
    expect(screen.getByRole('link', { name: 'System' })).toHaveAttribute('aria-current', 'page');
  });

  it('signs in with the CSRF token, shows the account, and signs out', async () => {
    let user: typeof MEMBER | null = null;
    const { requests } = fakeServer({
      'GET /api/session': () => ({ body: { mode: 'shared', user, csrf_token: 'tok' } }),
      'POST /api/login': (req) => {
        const ok = (req.body as { password: string }).password === 'right';
        if (ok) user = MEMBER;
        return ok
          ? { body: { mode: 'shared', user, csrf_token: 'tok' } }
          : { status: 401, body: { error: 'Wrong username or password' } };
      },
      'POST /api/logout': () => {
        user = null;
        return { body: { ok: true } };
      },
    });
    render(<App />);
    const name = await screen.findByLabelText('Username');
    await waitFor(() => expect(document.title).toBe('ISBE · Sign in'));
    await userEvent.type(name, 'bo');
    await userEvent.type(screen.getByLabelText('Password'), 'wrong');
    await act(() => userEvent.click(screen.getByRole('button', { name: 'Sign in' })));
    expect(await screen.findByText(/Sign-in failed/)).toBeInTheDocument();

    await userEvent.clear(screen.getByLabelText('Password'));
    await userEvent.type(screen.getByLabelText('Password'), 'right');
    await act(() => userEvent.click(screen.getByRole('button', { name: 'Sign in' })));
    expect(await screen.findByRole('heading', { name: 'Question research' })).toBeInTheDocument();
    const login = requests.filter((r) => r.url === '/api/login').at(-1)!;
    expect(login.headers['X-ISBE-CSRF']).toBe('tok');
    expect(login.body).toEqual({ username: 'bo', password: 'right' });
    // A member sees no administrator pages.
    const nav = screen.getByRole('navigation', { name: 'Main' });
    expect(nav).toHaveTextContent('bo');
    expect(nav).not.toHaveTextContent('Runs & LLM');
    expect(nav).not.toHaveTextContent('Accounts');

    await act(() => userEvent.click(screen.getByRole('button', { name: 'Sign out' })));
    expect(await screen.findByLabelText('Username')).toBeInTheDocument();
    expect(requests.find((r) => r.url === '/api/logout')!.headers['X-ISBE-CSRF']).toBe('tok');
  });

  it('returns to sign-in when the session expires', async () => {
    let user: typeof ADMIN | null = ADMIN;
    fakeServer({
      'GET /api/session': () => ({ body: { mode: 'shared', user, csrf_token: 'tok' } }),
      'POST /api/logout': () => {
        user = null;
        return { status: 401, body: { error: 'Please sign in to continue.' } };
      },
    });
    at('/system');
    render(<App />);
    expect(await screen.findByRole('link', { name: /Accounts/ })).toHaveAttribute('href', '/users');
    await act(() => userEvent.click(screen.getByRole('button', { name: 'Sign out' })));
    expect(await screen.findByText('Your session expired. Please sign in again.')).toBeInTheDocument();
  });

  it('offers a retry when the service is unreachable', async () => {
    let up = false;
    fakeServer({
      'GET /api/session': () => (up ? { body: local } : { status: 502, body: { error: 'bad gateway' } }),
    });
    render(<App />);
    expect(await screen.findByRole('alert')).toHaveTextContent('temporarily unavailable');
    up = true;
    await act(() => userEvent.click(screen.getByRole('button', { name: 'Retry' })));
    expect(await screen.findByRole('heading', { name: 'Question research' })).toBeInTheDocument();
  });

  it('switches language and theme and keeps them for the classic console', async () => {
    fakeServer({ 'GET /api/session': () => ({ body: local }) });
    render(<App />);
    await screen.findByRole('heading', { name: 'Question research' });
    await act(() => userEvent.click(screen.getByRole('button', { name: '中文' })));
    expect(await screen.findByRole('heading', { name: '问题研究' })).toBeInTheDocument();
    expect(document.documentElement.lang).toBe('zh-CN');

    await act(() => userEvent.click(screen.getByRole('button', { name: /外观/ })));
    await act(() => userEvent.click(screen.getByRole('button', { name: '浅色' })));
    await act(() => userEvent.click(screen.getByRole('button', { name: 'Iris' })));
    await act(() => userEvent.click(screen.getByRole('button', { name: '米黄' })));
    const root = document.querySelector('.isbe') as HTMLElement;
    expect(root.dataset.mode).toBe('light');
    expect(root.dataset.paper).toBe('sepia');
    expect(root.style.getPropertyValue('--h')).toBe('290');
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!)).toMatchObject({
      lang: 'zh',
      mode: 'light',
      theme: 'iris',
      paper: 'sepia',
    });
    await act(() => userEvent.click(screen.getByRole('button', { name: '关闭外观面板' })));
    expect(screen.queryByRole('complementary')).toBeNull();
  });

  it('marks another workspace an administrator is reading', async () => {
    at('/question?owner=u2');
    fakeServer({ 'GET /api/session': () => ({ body: { ...local, mode: 'shared' } }) });
    render(<App />);
    expect(await screen.findByRole('note')).toHaveTextContent('Viewing the workspace of account u2');
  });

  it('says so for a path it does not know', async () => {
    at('/question-nope');
    fakeServer({ 'GET /api/session': () => ({ body: local }) });
    render(<App />);
    await waitFor(() => expect(screen.getByText('There is no such page.')).toBeInTheDocument());
    expect(screen.getByRole('heading', { level: 1, name: 'Page not found' })).toBeInTheDocument();
    // The title is set in an effect, after the page is drawn.
    await waitFor(() => expect(document.title).toBe('ISBE · Page not found'));
  });
});

// The server's side lives in the Python package; the Docker build stage has only frontend/.
const serverRoutes = resolve(__dirname, '../../isbe/web/routes.py');
const classicApp = resolve(__dirname, '../../web/app.js');

describe('routing between the consoles', () => {
  it.skipIf(!existsSync(serverRoutes))('routes in React exactly the paths the server sends here', () => {
    const routes = readFileSync(serverRoutes, 'utf-8');
    const declared = /REACT_PATHS = \(([^)]*)\)/
      .exec(routes)![1]!
      .match(/"[^"]+"/g)!
      .map((s) => JSON.parse(s));
    const home = /REACT_HOME = "([^"]+)"/.exec(routes)![1];
    const routed = [...NAV, ...SYSTEM].filter((item) => item.react).map((item) => item.href);
    expect(new Set(routed)).toEqual(new Set([home, ...declared]));
  });

  it.skipIf(!existsSync(classicApp))('draws the same sidebar in the classic console', () => {
    const app = readFileSync(classicApp, 'utf-8');
    const literal = /const NAV = (\[[\s\S]*?\n {2}\]);/.exec(app)![1]!;
    // The classic console's list is a plain literal; read it as data.
    const classic: unknown = new Function(`return ${literal}`)();
    expect(classic).toEqual(NAV);
    // Every page under System in the sidebar is a page System lists.
    const system = NAV.find((item) => item.href === '/system')!;
    expect(new Set(system.also)).toEqual(
      new Set(SYSTEM.filter((item) => !item.react && !item.soon).map((item) => item.href)),
    );
  });
});
