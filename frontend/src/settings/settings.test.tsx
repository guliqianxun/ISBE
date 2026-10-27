// Settings (/settings): the behaviours the classic console's web/settings.test.cjs
// covered before the page moved (#79), against a fake `/api/settings`.
import { act, fireEvent, render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import type { ReactNode } from 'react';
import { createMemoryRouter, Link, Route, RouterProvider, Routes, useLocation } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setCsrfToken } from '../api/client';
import { PrefsProvider, STORAGE_KEY, usePrefs } from '../prefs';
import { SessionProvider, useSession } from '../session';
import { Shell } from '../shell/Shell';
import { ADMIN, fakeServer, MEMBER, type Recorded } from '../test/fakeServer';
import { edit, extraBodyValid, recipients, shown, toggleRemove, type Snapshot } from './model';
import { SettingsPage } from './SettingsPage';

const DEFAULTS: Record<string, Record<string, string>> = {
  anthropic: {
    ISBE_LLM_BASE_URL: 'https://api.anthropic.com',
    ISBE_LLM_MODEL_FAST: 'claude-haiku-5-5',
    ISBE_LLM_MODEL_SMART: 'claude-sonnet-5-5',
  },
  deepseek: {
    ISBE_LLM_BASE_URL: 'https://api.deepseek.com',
    ISBE_LLM_MODEL_FAST: 'deepseek-chat',
    ISBE_LLM_MODEL_SMART: 'deepseek-chat',
  },
  openai: {
    ISBE_LLM_BASE_URL: 'https://api.openai.com/v1',
    ISBE_LLM_MODEL_FAST: 'gpt-5-mini',
    ISBE_LLM_MODEL_SMART: 'gpt-5',
  },
};

const SECRET = /_KEY$|^GITHUB_TOKEN$|^ZEPTOMAIL_TOKEN$|^SMTP_PASSWORD$/;

type Answer = { status?: number; body: unknown };

/** A settings service that applies saves the way the server does. */
function service(
  values: Record<string, string> = {},
  sources: Record<string, string> = {},
  mail: (req: Recorded) => Answer | Promise<Answer> = () => ({
    body: { state: 'accepted', request_id: 'test-id' },
  }),
  user = ADMIN,
) {
  const snapshot = {
    values: {
      EMAIL_TRANSPORT: 'zeptomail_api',
      ISBE_LLM_PROVIDER: 'anthropic',
      ...DEFAULTS.anthropic,
      ISBE_LLM_NO_AUTH: 'false',
      ISBE_LLM_EXTRA_BODY: '{}',
      ISBE_FULLTEXT_EXTRACTOR: values.METRAIL_API_URL ? 'metrail' : 'llm',
      METRAIL_API_URL: '',
      ...values,
    } as Record<string, string>,
    configured: { ANTHROPIC_API_KEY: true, OPENAI_API_KEY: true } as Record<string, boolean>,
    provider_defaults: DEFAULTS,
    llm: { ready: true, error: null as string | null, connection_verified: false },
    sources: {
      ANTHROPIC_API_KEY: 'environment',
      OPENAI_API_KEY: 'file',
      ISBE_FULLTEXT_EXTRACTOR: 'default',
      ...sources,
    } as Record<string, string>,
    csrf_token: 'tok',
    mail: { configured: true, sender: 'sender@example.com' },
  };
  let failSave = false;
  let failLoads = 0;
  const copy = () => ({ body: JSON.parse(JSON.stringify(snapshot)) as unknown });
  const server = fakeServer({
    'GET /api/session': () => ({ body: { mode: 'shared', user, csrf_token: 'tok', llm: { ready: true } } }),
    'GET /api/settings': () =>
      failLoads-- > 0 ? { status: 500, body: { error: 'disk unavailable' } } : copy(),
    'PUT /api/settings': (req) => {
      if (failSave) return { status: 400, body: { error: 'Invalid service URL' } };
      for (const [key, value] of Object.entries(req.body as Record<string, string | null>)) {
        if (SECRET.test(key)) snapshot.configured[key] = !!value;
        else snapshot.values[key] = value ?? '';
        snapshot.sources[key] = value ? 'file' : 'default';
      }
      for (const [key, value] of Object.entries(DEFAULTS[snapshot.values.ISBE_LLM_PROVIDER!]!))
        if (!snapshot.sources[key] || snapshot.sources[key] === 'default') snapshot.values[key] = value;
      if (snapshot.sources.ISBE_FULLTEXT_EXTRACTOR === 'default')
        snapshot.values.ISBE_FULLTEXT_EXTRACTOR = snapshot.values.METRAIL_API_URL ? 'metrail' : 'llm';
      return copy();
    },
    'GET /api/mail': () => ({
      body: {
        mail: snapshot.mail,
        recipients: snapshot.values.ISBE_EMAIL_DEFAULT_RECIPIENTS ?? '',
        csrf_token: 'tok',
      },
    }),
    'POST /api/mail/test': mail,
  });
  return {
    ...server,
    snapshot,
    failSave: (v: boolean) => {
      failSave = v;
    },
    failLoads: (n: number) => {
      failLoads = n;
    },
    puts: () => server.requests.filter((r) => r.method === 'PUT').map((r) => r.body),
    posts: () => server.requests.filter((r) => r.method === 'POST'),
  };
}

function Where() {
  const { pathname } = useLocation();
  return <output aria-label="where">{pathname}</output>;
}

function SignedIn({ children }: { children: ReactNode }) {
  const { state } = useSession();
  return state.status === 'ready' && state.session.user ? children : null;
}

function Lang() {
  const { update } = usePrefs();
  return (
    <button type="button" onClick={() => update({ lang: 'zh' })}>
      to-zh
    </button>
  );
}

function at(path = '/settings', { shell = false } = {}) {
  const page = (
    <>
      <Lang />
      <Link to="/runs">to-runs</Link>
      <Link to="/settings">to-settings</Link>
      <Routes>
        <Route path="/settings" element={<SettingsPage />} />
        <Route path="*" element={null} />
      </Routes>
      <Where />
    </>
  );
  const router = createMemoryRouter([{ path: '*', element: shell ? <Shell>{page}</Shell> : page }], {
    initialEntries: ['/runs', path],
    initialIndex: 1,
  });
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

const field = (key: string) =>
  document.getElementById(`setting-${key}`) as HTMLInputElement | HTMLSelectElement | null;
const value = (key: string) => field(key)?.value;
const keys = () => ['ANTHROPIC_API_KEY', 'DEEPSEEK_API_KEY', 'OPENAI_API_KEY'].filter((k) => field(k));

async function ready() {
  await screen.findByRole('button', { name: 'Save settings' });
}

async function choose(key: string, v: string) {
  await userEvent.selectOptions(field(key) as HTMLSelectElement, v);
}

async function type(key: string, text: string) {
  const input = field(key) as HTMLInputElement;
  await userEvent.clear(input);
  if (text) await userEvent.type(input, text.replaceAll('{', '{{').replaceAll('[', '[['));
}

async function save() {
  await userEvent.click(screen.getByRole('button', { name: 'Save settings' }));
  await screen.findByRole('button', { name: 'Save settings' });
}

const status = () => document.getElementById('settings-status')?.textContent ?? '';

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ lang: 'en' }));
  setCsrfToken('tok');
});

afterEach(() => {
  vi.restoreAllMocks();
});

describe('settings model', () => {
  const s: Snapshot = {
    values: { ISBE_LLM_PROVIDER: 'anthropic', ...DEFAULTS.anthropic, ISBE_FULLTEXT_EXTRACTOR: 'llm' },
    configured: {},
    sources: { ISBE_LLM_MODEL_FAST: 'environment' },
    provider_defaults: DEFAULTS,
  };

  it('keeps environment values, unchanged secrets and untouched fields out of the edits', () => {
    expect(edit(s, {}, 'ISBE_LLM_MODEL_FAST', 'x')).toEqual({});
    expect(edit(s, {}, 'OPENAI_API_KEY', '')).toEqual({});
    expect(edit(s, { ISBE_ARTICLE_DNS: 'cloudflare' }, 'ISBE_ARTICLE_DNS', '')).toEqual({});
    expect(edit(s, {}, 'SMTP_HOST', 'smtp.example.com')).toEqual({ SMTP_HOST: 'smtp.example.com' });
  });

  it('pins a default chosen on purpose, and clears a field to its default with null', () => {
    expect(edit(s, {}, 'ISBE_FULLTEXT_EXTRACTOR', 'llm')).toEqual({ ISBE_FULLTEXT_EXTRACTOR: 'llm' });
    expect(edit({ ...s, sources: { ISBE_LLM_BASE_URL: 'file' } }, {}, 'ISBE_LLM_BASE_URL', '')).toEqual({
      ISBE_LLM_BASE_URL: null,
    });
    expect(shown(s, { ISBE_LLM_PROVIDER: 'openai' }, 'ISBE_LLM_MODEL_SMART')).toBe('gpt-5');
    expect(shown(s, {}, 'ANTHROPIC_API_KEY')).toBe('');
    // Cleared, it stays empty while the person types, not refilled with the default.
    expect(shown(s, { ISBE_LLM_MODEL_SMART: null }, 'ISBE_LLM_MODEL_SMART')).toBe('');
  });

  it('toggles a secret removal, checks the extra body and dedupes recipients', () => {
    expect(toggleRemove({}, 'OPENAI_API_KEY')).toEqual({ OPENAI_API_KEY: null });
    expect(toggleRemove({ OPENAI_API_KEY: null }, 'OPENAI_API_KEY')).toEqual({});
    expect(extraBodyValid({ ISBE_LLM_EXTRA_BODY: '[]' })).toBe(false);
    expect(extraBodyValid({ ISBE_LLM_EXTRA_BODY: 'nope' })).toBe(false);
    expect(extraBodyValid({ ISBE_LLM_EXTRA_BODY: '{"a":1}' })).toBe(true);
    expect(recipients('a@x.com; A@x.com, b@x.com  ')).toBe('A@x.com, b@x.com');
  });
});

describe('settings page', () => {
  it('sends members home', async () => {
    service({}, {}, undefined, MEMBER);
    at();
    expect(await screen.findByLabelText('where')).toHaveTextContent('/');
    expect(screen.queryByRole('heading', { name: 'Settings' })).toBeNull();
  });

  it('loads without the overview; drafts survive a language change', async () => {
    const api = service();
    at();
    await ready();
    expect(api.requests.map((r) => r.url)).toEqual(['/api/session', '/api/settings']);
    expect(field('ANTHROPIC_API_KEY')).toBeDisabled();
    expect(value('ANTHROPIC_API_KEY')).toBe('');
    await choose('ISBE_LLM_PROVIDER', 'openai');
    await userEvent.click(screen.getByText('Advanced model options'));
    await type('ISBE_LLM_MODEL_SMART', '<draft-model>');
    await type('OPENAI_API_KEY', 'replacement-secret');
    await userEvent.click(screen.getByRole('button', { name: 'to-zh' }));
    expect(value('ISBE_LLM_MODEL_SMART')).toBe('<draft-model>');
    expect(value('OPENAI_API_KEY')).toBe('replacement-secret');
    expect(screen.getByLabelText('API 密钥')).toBe(field('OPENAI_API_KEY'));
    await userEvent.click(screen.getByRole('button', { name: '保存设置' }));
    await screen.findByText(/设置已保存/);
    expect(api.puts()).toEqual([
      {
        ISBE_LLM_PROVIDER: 'openai',
        ISBE_LLM_MODEL_SMART: '<draft-model>',
        OPENAI_API_KEY: 'replacement-secret',
      },
    ]);
    expect(api.requests.find((r) => r.method === 'PUT')!.headers['X-ISBE-CSRF']).toBe('tok');
    expect(value('OPENAI_API_KEY')).toBe('');
  });

  it('keeps the original-page DNS choice and saves, discards through the usual flow', async () => {
    const api = service({ ISBE_ARTICLE_DNS: 'system' }, { ISBE_ARTICLE_DNS: 'default' });
    at();
    await ready();
    expect(value('ISBE_ARTICLE_DNS')).toBe('system');
    expect(screen.getByText(/Cloudflare receives public domain names/)).toBeInTheDocument();
    await choose('ISBE_ARTICLE_DNS', 'cloudflare');
    await save();
    expect(api.puts()).toEqual([{ ISBE_ARTICLE_DNS: 'cloudflare' }]);
    expect(screen.getByRole('button', { name: 'Save settings' })).toBeDisabled();
    await choose('ISBE_ARTICLE_DNS', 'system');
    await userEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
    expect(value('ISBE_ARTICLE_DNS')).toBe('cloudflare');
  });

  it('does not save previous defaults or untouched secrets when the provider changes', async () => {
    const api = service();
    at();
    await ready();
    await choose('ISBE_LLM_PROVIDER', 'openai');
    await save();
    expect(api.puts()).toEqual([{ ISBE_LLM_PROVIDER: 'openai' }]);
  });

  it('follows provider defaults before saving and after reload', async () => {
    const api = service();
    const view = at();
    await ready();
    const defaults = (p: string) => {
      for (const [k, v] of Object.entries(DEFAULTS[p]!)) expect(value(k)).toBe(v);
    };
    defaults('anthropic');
    for (const p of ['openai', 'deepseek', 'anthropic', 'openai']) {
      await choose('ISBE_LLM_PROVIDER', p);
      defaults(p);
    }
    await save();
    defaults('openai');
    expect(api.puts()).toEqual([{ ISBE_LLM_PROVIDER: 'openai' }]);
    view.unmount();
    at();
    await ready();
    defaults('openai');
    await choose('ISBE_LLM_PROVIDER', 'anthropic');
    defaults('anthropic');
  });

  it('keeps file, environment and explicit values across provider switches', async () => {
    const api = service(
      { ISBE_LLM_BASE_URL: 'http://localhost:9000/v1', ISBE_LLM_MODEL_FAST: 'env-model' },
      { ISBE_LLM_BASE_URL: 'file', ISBE_LLM_MODEL_FAST: 'environment' },
    );
    const view = at();
    await ready();
    await type('ISBE_LLM_MODEL_SMART', DEFAULTS.anthropic!.ISBE_LLM_MODEL_SMART!);
    await choose('ISBE_LLM_PROVIDER', 'openai');
    expect(value('ISBE_LLM_BASE_URL')).toBe('http://localhost:9000/v1');
    expect(field('ISBE_LLM_MODEL_FAST')).toBeDisabled();
    expect(value('ISBE_LLM_MODEL_FAST')).toBe('env-model');
    expect(value('ISBE_LLM_MODEL_SMART')).toBe('claude-sonnet-5-5');
    await save();
    expect(api.puts()).toEqual([{ ISBE_LLM_PROVIDER: 'openai', ISBE_LLM_MODEL_SMART: 'claude-sonnet-5-5' }]);
    view.unmount();
    at();
    await ready();
    await choose('ISBE_LLM_PROVIDER', 'deepseek');
    expect(value('ISBE_LLM_BASE_URL')).toBe('http://localhost:9000/v1');
    expect(value('ISBE_LLM_MODEL_SMART')).toBe('claude-sonnet-5-5');
    await type('ISBE_LLM_BASE_URL', '');
    await type('ISBE_LLM_MODEL_SMART', '');
    await save();
    expect(value('ISBE_LLM_BASE_URL')).toBe('https://api.deepseek.com');
    expect(value('ISBE_LLM_MODEL_SMART')).toBe('deepseek-chat');
    await choose('ISBE_LLM_PROVIDER', 'openai');
    expect(value('ISBE_LLM_BASE_URL')).toBe('https://api.openai.com/v1');
    expect(value('ISBE_LLM_MODEL_SMART')).toBe('gpt-5');
  });

  it('says plainly when saved settings are incomplete and never claims a connection test', async () => {
    const api = service();
    at();
    await ready();
    await choose('ISBE_LLM_PROVIDER', 'openai');
    api.snapshot.llm = { ready: false, error: 'OPENAI_API_KEY is not set', connection_verified: false };
    await save();
    expect(status()).toMatch(/Settings saved/);
    expect(document.getElementById('llm-readiness')).toHaveTextContent(
      'Model configuration incomplete; complete it in Settings: OPENAI_API_KEY is not set',
    );
    await choose('ISBE_LLM_NO_AUTH', 'true');
    await type('ISBE_LLM_BASE_URL', 'http://localhost:11434/v1');
    expect(document.getElementById('llm-readiness')).toHaveTextContent(/Unsaved changes/);
    api.snapshot.llm = { ready: true, error: null, connection_verified: false };
    await save();
    expect(document.getElementById('llm-readiness')).toHaveTextContent(
      'Model configuration complete; connection not verified',
    );
    expect(screen.getByText(/Authorization is omitted even if an old key exists/)).toBeInTheDocument();
    expect(api.puts().at(-1)).toEqual({
      ISBE_LLM_NO_AUTH: 'true',
      ISBE_LLM_BASE_URL: 'http://localhost:11434/v1',
    });
  });

  it('keeps a key removal through a failed save and never sends invalid JSON', async () => {
    const api = service();
    at();
    await ready();
    await userEvent.click(screen.getByText('Advanced model options'));
    await type('ISBE_LLM_EXTRA_BODY', '[]');
    await userEvent.click(screen.getByRole('button', { name: 'Save settings' }));
    expect(api.puts()).toEqual([]);
    expect(status()).toBe('Extra request parameters must be a JSON object.');
    await waitFor(() => expect(field('ISBE_LLM_EXTRA_BODY')).toHaveFocus());
    await type('ISBE_LLM_EXTRA_BODY', '{}');
    await choose('ISBE_LLM_PROVIDER', 'openai');
    await userEvent.click(screen.getByRole('button', { name: 'Remove saved key' }));
    api.failSave(true);
    await save();
    expect(status()).toBe('Invalid service URL');
    expect(screen.getByText('Key will be removed on save')).toBeInTheDocument();
    expect(field('OPENAI_API_KEY')).toBeDisabled();
    api.failSave(false);
    await save();
    expect(api.puts().at(-1)).toEqual({ ISBE_LLM_PROVIDER: 'openai', OPENAI_API_KEY: null });
    expect(status()).toMatch(/Settings saved/);
  });

  it('shows the one key of the chosen provider, with its own source, lock and draft', async () => {
    const api = service();
    at();
    await ready();
    const chip = (key: string) => field(key)!.closest('.setting-field')!.querySelector('.chip')!.textContent;
    expect(keys()).toEqual(['ANTHROPIC_API_KEY']);
    expect(field('ANTHROPIC_API_KEY')).toBeDisabled();
    expect(chip('ANTHROPIC_API_KEY')).toBe('Environment · configured');
    await choose('ISBE_LLM_PROVIDER', 'deepseek');
    expect(keys()).toEqual(['DEEPSEEK_API_KEY']);
    expect(field('DEEPSEEK_API_KEY')).toBeEnabled();
    expect(chip('DEEPSEEK_API_KEY')).toBe('Default · not set');
    await type('DEEPSEEK_API_KEY', 'deepseek-draft');
    await choose('ISBE_LLM_PROVIDER', 'openai');
    expect(keys()).toEqual(['OPENAI_API_KEY']);
    expect(value('OPENAI_API_KEY')).toBe('');
    expect(chip('OPENAI_API_KEY')).toBe('Local settings · configured');
    await type('OPENAI_API_KEY', 'openai-draft');
    await choose('ISBE_LLM_PROVIDER', 'deepseek');
    expect(value('DEEPSEEK_API_KEY')).toBe('deepseek-draft');
    await choose('ISBE_LLM_PROVIDER', 'openai');
    expect(value('OPENAI_API_KEY')).toBe('openai-draft');
    await save();
    expect(api.puts()).toEqual([
      { ISBE_LLM_PROVIDER: 'openai', DEEPSEEK_API_KEY: 'deepseek-draft', OPENAI_API_KEY: 'openai-draft' },
    ]);
    expect(value('OPENAI_API_KEY')).toBe('');
    await choose('ISBE_LLM_PROVIDER', 'deepseek');
    expect(value('DEEPSEEK_API_KEY')).toBe('');
    expect(chip('DEEPSEEK_API_KEY')).toBe('Local settings · configured');
    expect(document.body.innerHTML).not.toMatch(/deepseek-draft|openai-draft/);
  });

  it('asks before leaving with unsaved edits, and guards a reload', async () => {
    service();
    at();
    await ready();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    const unload = () => {
      const e = new Event('beforeunload', { cancelable: true });
      window.dispatchEvent(e);
      return e.defaultPrevented;
    };
    expect(unload()).toBe(false);
    await userEvent.click(screen.getByText('Advanced model options'));
    await type('ISBE_LLM_MODEL_SMART', 'new-model');
    expect(unload()).toBe(true);
    await userEvent.click(screen.getByRole('link', { name: 'to-runs' }));
    expect(screen.getByLabelText('where')).toHaveTextContent('/settings');
    expect(confirm).toHaveBeenCalledWith(
      'You have unsaved service settings. Discard these changes and leave?',
    );
    confirm.mockReturnValue(true);
    await userEvent.click(screen.getByRole('link', { name: 'to-runs' }));
    expect(screen.getByLabelText('where')).toHaveTextContent('/runs');
    expect(unload()).toBe(false);
  });

  it('asks once before a classic page loads, then lets the page go', async () => {
    service();
    at();
    await ready();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    await userEvent.click(screen.getByText('Advanced model options'));
    await type('ISBE_LLM_MODEL_SMART', 'new-model');
    const a = document.createElement('a');
    a.href = '/reports';
    a.textContent = 'classic';
    a.addEventListener('click', (e) => e.preventDefault()); // jsdom does not load pages
    document.body.append(a);
    await userEvent.click(a);
    const e = new Event('beforeunload', { cancelable: true });
    window.dispatchEvent(e);
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(e.defaultPrevented).toBe(false);
    a.remove();
  });

  it('asks before going Back with unsaved edits', async () => {
    service();
    const { router } = at();
    await ready();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    await userEvent.click(screen.getByText('Advanced model options'));
    await type('ISBE_LLM_MODEL_SMART', 'new-model');
    await act(() => router.navigate(-1));
    expect(confirm).toHaveBeenCalledTimes(1);
    expect(screen.getByLabelText('where')).toHaveTextContent('/settings');
    confirm.mockReturnValue(true);
    await act(() => router.navigate(-1));
    await waitFor(() => expect(screen.getByLabelText('where')).toHaveTextContent('/runs'));
    expect(confirm).toHaveBeenCalledTimes(2);
  });

  it('asks again when the person said leave but a new run or sign-out failed', async () => {
    const server = service();
    const { router } = at('/settings', { shell: true });
    await ready();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    await userEvent.click(screen.getByText('Advanced model options'));
    await type('ISBE_LLM_MODEL_SMART', 'new-model');
    // The run cannot be created (the fake server has no such route): still here.
    await userEvent.type(
      screen.getByRole('searchbox', { name: 'Ask a research question' }),
      'How do radar echoes extrapolate?{Enter}',
    );
    await waitFor(() =>
      expect(server.requests.filter((r) => r.url === '/api/question/runs')).toHaveLength(1),
    );
    expect(screen.getByLabelText('where')).toHaveTextContent('/settings');
    confirm.mockReturnValue(false);
    await act(() => router.navigate(-1));
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(screen.getByLabelText('where')).toHaveTextContent('/settings');
    // Signing out fails the same way.
    confirm.mockReturnValue(true);
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    await waitFor(() => expect(server.requests.some((r) => r.url === '/api/logout')).toBe(true));
    confirm.mockReturnValue(false);
    await userEvent.click(screen.getByRole('link', { name: 'to-runs' }));
    expect(confirm).toHaveBeenCalledTimes(4);
    expect(screen.getByLabelText('where')).toHaveTextContent('/settings');
  });

  it('does not ask for a link opened in a new tab', async () => {
    service();
    at();
    await ready();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    await userEvent.click(screen.getByText('Advanced model options'));
    await type('ISBE_LLM_MODEL_SMART', 'new-model');
    const link = screen.getByRole('link', { name: 'to-runs' });
    link.addEventListener('click', (e) => e.preventDefault()); // jsdom opens no tabs
    fireEvent.click(link, { ctrlKey: true });
    fireEvent.click(link, { button: 1 });
    expect(confirm).not.toHaveBeenCalled();
  });

  it('asks before signing out or asking a new question with unsaved edits', async () => {
    const server = service();
    at('/settings', { shell: true });
    await ready();
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(false);
    await userEvent.click(screen.getByText('Advanced model options'));
    await type('ISBE_LLM_MODEL_SMART', 'new-model');
    await userEvent.click(screen.getByRole('button', { name: 'Sign out' }));
    await userEvent.type(
      screen.getByRole('searchbox', { name: 'Ask a research question' }),
      'How do radar echoes extrapolate?{Enter}',
    );
    expect(confirm).toHaveBeenCalledTimes(2);
    expect(server.requests.filter((r) => r.method === 'POST')).toEqual([]);
    expect(screen.getByLabelText('where')).toHaveTextContent('/settings');
  });

  it('defaults full text to LLM extraction and explains the paper services', async () => {
    service();
    at();
    await ready();
    expect(value('ISBE_FULLTEXT_EXTRACTOR')).toBe('llm');
    expect(field('METRAIL_API_URL')).toBeNull();
    for (const text of [
      /provider, fast model, and key above/,
      /does not run PDF OCR or recognize image content/,
      /Keep panels together without extra vision-model calls/,
      /public arXiv API by default/,
      /each topic uses metrail when enabled with a URL, otherwise LLM/,
    ])
      expect(screen.getByText(text)).toBeInTheDocument();
    const advanced = document.getElementById('settings-sources-advanced')!;
    expect(within(advanced).getByText('Advanced source options')).toBeInTheDocument();
    expect(advanced.querySelector('#setting-ARXIV_API_URL')).not.toBeNull();
    expect(advanced.querySelector('#setting-GITHUB_API_URL')).not.toBeNull();
    await userEvent.click(screen.getByRole('button', { name: 'to-zh' }));
    expect(screen.getByRole('option', { name: 'LLM 提取（arXiv HTML）' })).toBeInTheDocument();
    expect(screen.getByText(/组图保留多个面板，不增加视觉模型调用/)).toBeInTheDocument();
  });

  it('searches the OpenAlex API by default, with an optional key and email, or essaies', async () => {
    const api = service({ ISBE_PAPER_SOURCE: 'openalex', OPENALEX_API_URL: 'https://api.openalex.org' });
    at();
    await ready();
    expect(value('ISBE_PAPER_SOURCE')).toBe('openalex');
    expect((field('OPENALEX_API_KEY') as HTMLInputElement).type).toBe('password');
    expect(
      screen.getByText(/share a free daily budget with everyone on your IP.*openalex.org\/settings\/api/),
    ).toBeInTheDocument();
    const advanced = document.getElementById('settings-sources-advanced')!;
    expect(advanced.querySelector('#setting-OPENALEX_API_URL')).not.toBeNull();
    await type('OPENALEX_API_KEY', 'oa-key');
    await type('OPENALEX_MAILTO', 'me@example.org');
    await save();
    expect(api.puts().at(-1)).toEqual({ OPENALEX_API_KEY: 'oa-key', OPENALEX_MAILTO: 'me@example.org' });
    // An essaies URL kept for later does not switch the source away from OpenAlex;
    // undoing the URL undoes the pin too.
    await type('ESSAIES_URL', 'x');
    await type('ESSAIES_URL', '');
    expect(screen.getByRole('button', { name: 'Save settings' })).toBeDisabled();
    await type('ESSAIES_URL', 'http://later.invalid');
    await save();
    expect(api.puts().at(-1)).toEqual({ ESSAIES_URL: 'http://later.invalid', ISBE_PAPER_SOURCE: 'openalex' });
    await choose('ISBE_PAPER_SOURCE', 'essaies');
    expect(field('OPENALEX_API_KEY')).toBeNull();
    expect(field('OPENALEX_MAILTO')).toBeNull();
    expect(advanced.querySelector('#setting-ESSAIES_URL')).toBeNull();
    await type('ESSAIES_URL', 'http://essaies.invalid');
    expect(screen.getByText(/the arXiv search engine needs essaies/)).toBeInTheDocument();
    await save();
    expect(api.puts().at(-1)).toEqual({
      ISBE_PAPER_SOURCE: 'essaies',
      ESSAIES_URL: 'http://essaies.invalid',
    });
  });

  it('shows the metrail URL only for metrail and keeps its draft across switches and saves', async () => {
    const api = service();
    at();
    await ready();
    await choose('ISBE_FULLTEXT_EXTRACTOR', 'metrail');
    expect(value('METRAIL_API_URL')).toBe('');
    expect(screen.getByText(/This mode needs a service URL/)).toBeInTheDocument();
    await type('METRAIL_API_URL', 'http://localhost:9000');
    await choose('ISBE_FULLTEXT_EXTRACTOR', 'off');
    expect(screen.getByText(/Keep existing text and card caches/)).toBeInTheDocument();
    expect(field('METRAIL_API_URL')).toBeNull();
    await choose('ISBE_FULLTEXT_EXTRACTOR', 'metrail');
    expect(value('METRAIL_API_URL')).toBe('http://localhost:9000');
    await save();
    expect(api.puts().at(-1)).toEqual({
      ISBE_FULLTEXT_EXTRACTOR: 'metrail',
      METRAIL_API_URL: 'http://localhost:9000',
    });
    await choose('ISBE_FULLTEXT_EXTRACTOR', 'llm');
    await save();
    expect(api.puts().at(-1)).toEqual({ ISBE_FULLTEXT_EXTRACTOR: 'llm' });
    await choose('ISBE_FULLTEXT_EXTRACTOR', 'metrail');
    await type('METRAIL_API_URL', 'http://localhost:9001');
    await choose('ISBE_FULLTEXT_EXTRACTOR', 'llm');
    await save();
    expect(api.puts().at(-1)).toEqual({ METRAIL_API_URL: 'http://localhost:9001' });
    await choose('ISBE_FULLTEXT_EXTRACTOR', 'metrail');
    expect(value('METRAIL_API_URL')).toBe('http://localhost:9001');
  });

  it('takes the metrail default and environment locks from the snapshot', async () => {
    const api = service({ METRAIL_API_URL: 'http://existing-parser:9000' }, { METRAIL_API_URL: 'file' });
    const view = at();
    await ready();
    expect(value('ISBE_FULLTEXT_EXTRACTOR')).toBe('metrail');
    expect(value('METRAIL_API_URL')).toBe('http://existing-parser:9000');
    await choose('ISBE_FULLTEXT_EXTRACTOR', 'off');
    await save();
    expect(api.puts().at(-1)).toEqual({ ISBE_FULLTEXT_EXTRACTOR: 'off' });
    view.unmount();

    const locked = service({ ISBE_FULLTEXT_EXTRACTOR: 'off' }, { ISBE_FULLTEXT_EXTRACTOR: 'environment' });
    at();
    await ready();
    expect(field('ISBE_FULLTEXT_EXTRACTOR')).toBeDisabled();
    expect(value('ISBE_FULLTEXT_EXTRACTOR')).toBe('off');
    expect(field('METRAIL_API_URL')).toBeNull();
    expect(screen.getByRole('button', { name: 'Save settings' })).toBeDisabled();
    expect(locked.puts()).toEqual([]);
  });

  it('keeps a deliberate LLM choice when a hidden metrail URL is saved', async () => {
    const api = service();
    at();
    await ready();
    await choose('ISBE_FULLTEXT_EXTRACTOR', 'metrail');
    await type('METRAIL_API_URL', 'http://later-parser:9000');
    await choose('ISBE_FULLTEXT_EXTRACTOR', 'llm');
    expect(screen.queryByText(/No global method is fixed yet/)).toBeNull();
    await save();
    expect(api.puts().at(-1)).toEqual({
      ISBE_FULLTEXT_EXTRACTOR: 'llm',
      METRAIL_API_URL: 'http://later-parser:9000',
    });
    expect(value('ISBE_FULLTEXT_EXTRACTOR')).toBe('llm');
    expect(screen.queryByText(/No global method is fixed yet/)).toBeNull();
  });

  it('saves mail sender settings without showing the token, respecting locks', async () => {
    const api = service({}, { ZEPTOMAIL_API_URL: 'environment' });
    at();
    await ready();
    expect(field('ZEPTOMAIL_API_URL')).toBeDisabled();
    expect(field('ZEPTOMAIL_TOKEN')).toHaveAttribute('type', 'password');
    await type('ZEPTOMAIL_TOKEN', 'private-mail-token');
    await type('SMTP_FROM', 'sender@example.com');
    await save();
    expect(api.puts()[0]).toMatchObject({
      ZEPTOMAIL_TOKEN: 'private-mail-token',
      SMTP_FROM: 'sender@example.com',
    });
    expect(document.body.innerHTML).not.toMatch(/private-mail-token/);
  });

  it('shows the SMTP fields for SMTP and keeps their drafts across method switches', async () => {
    const api = service({ EMAIL_TRANSPORT: 'smtp' });
    at();
    await ready();
    expect(field('SMTP_PASSWORD')).toHaveAttribute('type', 'password');
    expect(field('ZEPTOMAIL_API_URL')).toBeNull();
    await type('SMTP_HOST', 'smtp.example.com');
    await type('SMTP_PASSWORD', 'smtp-private-password');
    await choose('EMAIL_TRANSPORT', 'zeptomail_api');
    expect(field('ZEPTOMAIL_API_URL')).not.toBeNull();
    expect(field('SMTP_HOST')).toBeNull();
    await choose('EMAIL_TRANSPORT', 'smtp');
    expect(value('SMTP_HOST')).toBe('smtp.example.com');
    await save();
    expect(api.puts()[0]).toMatchObject({
      SMTP_HOST: 'smtp.example.com',
      SMTP_PASSWORD: 'smtp-private-password',
    });
    expect(document.body.innerHTML).not.toMatch(/smtp-private-password/);
  });

  it('says when settings cannot be read, and reads them again on Retry', async () => {
    const api = service();
    api.failLoads(1);
    at();
    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('Could not load service settings. disk unavailable');
    await userEvent.click(within(alert).getByRole('button', { name: 'Retry' }));
    await ready();
    const heading = screen.getByRole('heading', { name: 'Settings', level: 1 });
    await waitFor(() => expect(heading).toHaveFocus());
  });

  it('says the server could not be reached when the connection fails', async () => {
    const api = service();
    const answer = api.fetchMock.getMockImplementation()!;
    api.fetchMock.mockImplementation((input, init) =>
      String(input).startsWith('/api/settings')
        ? Promise.reject(new TypeError('Failed to fetch'))
        : answer(input, init),
    );
    at();
    expect(await screen.findByRole('alert')).toHaveTextContent(
      'Could not load service settings. The server could not be reached.',
    );
  });
});

describe('test email', () => {
  async function open() {
    await userEvent.click(screen.getByRole('button', { name: 'Send test email' }));
    return screen.findByRole('textbox', { name: 'Recipients for this email' });
  }

  it('starts from the saved defaults; what is typed for one send never overwrites them', async () => {
    const api = service();
    at();
    await ready();
    await type('ISBE_EMAIL_DEFAULT_RECIPIENTS', 'saved@example.com');
    await save();
    let to = await open();
    expect(to).toHaveValue('saved@example.com');
    await waitFor(() => expect(to).toHaveFocus());
    await userEvent.clear(to);
    await userEvent.type(to, 'temporary@example.com; TEMPORARY@example.com');
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    await userEvent.click(screen.getByRole('button', { name: 'Confirm send' }));
    await screen.findByText(/inbox delivery is not confirmed/);
    expect(confirm.mock.calls[0]![0]).toBe(
      'Submit this email?\nSubject: ISBE · 邮件配置测试 / Email test\nRecipients for this email: TEMPORARY@example.com',
    );
    expect(api.posts().map((r) => [r.url, r.body, r.headers['X-ISBE-CSRF']])).toEqual([
      ['/api/mail/test', { recipients: 'TEMPORARY@example.com' }, 'tok'],
    ]);
    expect(screen.getByText(/Request ID: test-id/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirm send' })).toBeDisabled();
    const opener = screen.getByRole('button', { name: 'Send test email' });
    expect(opener).toHaveAttribute('aria-controls', 'mail-form');
    await userEvent.click(screen.getByRole('button', { name: 'Close email form' }));
    await waitFor(() => expect(opener).toHaveFocus());
    expect(opener).not.toHaveAttribute('aria-controls');
    to = await open();
    expect(to).toHaveValue('saved@example.com');
    expect(api.puts()).toHaveLength(1);
  });

  it('waits for unsaved changes, since the test uses the saved setup', async () => {
    service();
    at();
    await ready();
    await type('ISBE_EMAIL_DEFAULT_RECIPIENTS', 'new@example.com');
    const opener = screen.getByRole('button', { name: 'Send test email' });
    expect(opener).toBeDisabled();
    expect(opener).toHaveAccessibleDescription(
      'Save or discard your changes first; the test uses the saved setup.',
    );
    await save();
    expect(opener).toBeEnabled();
    expect(opener).not.toHaveAttribute('aria-describedby');
  });

  it('holds an open test email while there are unsaved changes', async () => {
    service();
    at();
    await ready();
    const to = await open();
    // The form focuses its recipients once loaded; edit after that.
    await waitFor(() => expect(to).toHaveFocus());
    await type('SMTP_FROM_NAME', 'Draft name');
    const send = screen.getByRole('button', { name: 'Confirm send' });
    expect(send).toBeDisabled();
    expect(send).toHaveAccessibleDescription(
      'Save or discard your changes first; the test uses the saved setup.',
    );
  });

  it('asks first, and sends nothing when the person cancels', async () => {
    const api = service();
    at();
    await ready();
    const to = await open();
    await userEvent.clear(to);
    await userEvent.click(screen.getByRole('button', { name: 'Confirm send' }));
    expect(screen.getByText('Enter at least one recipient.')).toBeInTheDocument();
    await userEvent.type(to, 'reader@example.com');
    vi.spyOn(window, 'confirm').mockReturnValue(false);
    await userEvent.click(screen.getByRole('button', { name: 'Confirm send' }));
    expect(api.posts()).toEqual([]);
  });

  it('blocks a second send and leaving while one is pending; an unknown result is not retried', async () => {
    let finish: (a: Answer) => void = () => {};
    const api = service({}, {}, () => new Promise<Answer>((resolve) => (finish = resolve)));
    at();
    await ready();
    const to = await open();
    await userEvent.type(to, 'reader@example.com');
    const confirm = vi.spyOn(window, 'confirm').mockReturnValue(true);
    await userEvent.click(screen.getByRole('button', { name: 'Confirm send' }));
    expect(screen.getByRole('button', { name: 'Submitting…' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Send test email' })).toBeDisabled();
    const calls = confirm.mock.calls.length;
    await userEvent.click(screen.getByRole('link', { name: 'to-runs' }));
    expect(screen.getByLabelText('where')).toHaveTextContent('/settings');
    expect(confirm.mock.calls.length).toBe(calls);
    await act(async () => finish({ body: { state: 'unknown' } }));
    expect(await screen.findByText(/Result unknown/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirm send' })).toBeDisabled();
    expect(api.posts()).toHaveLength(1);
  });

  it('reports a lost connection as unknown and a provider rejection as failed', async () => {
    for (const [answer, text] of [
      [
        () => {
          throw new TypeError('connection lost');
        },
        /Result unknown/,
      ],
      [
        () => ({ body: { state: 'failed', message: 'Provider rejected authentication' } }),
        /Sending failed\. Provider rejected authentication/,
      ],
      [() => ({ status: 503, body: { error: 'upstream' } }), /Result unknown/],
      [
        () => ({ status: 400, body: { error: 'Too many recipients' } }),
        /Sending failed\. Too many recipients/,
      ],
    ] as const) {
      const api = service({}, {}, answer as () => Answer);
      const view = at();
      await ready();
      const to = await open();
      await userEvent.type(to, 'reader@example.com');
      vi.spyOn(window, 'confirm').mockReturnValue(true);
      await userEvent.click(screen.getByRole('button', { name: 'Confirm send' }));
      expect(await screen.findByText(text)).toBeInTheDocument();
      expect(api.posts()).toHaveLength(1);
      view.unmount();
      vi.restoreAllMocks();
    }
  });

  it('says when mail is not set up and does not offer to send', async () => {
    const api = service();
    api.snapshot.mail = { configured: false, sender: '' };
    at();
    await ready();
    await open();
    expect(screen.getByText('not set', { selector: '.error' })).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Confirm send' })).toBeDisabled();
  });
});

describe('settings file', () => {
  const pick = (name: string, text: string) =>
    userEvent.upload(
      document.querySelector('input[type=file]') as HTMLInputElement,
      new File([text], name, { type: 'application/json' }),
    );
  const note = () => document.getElementById('settings-file-note')?.textContent ?? '';

  it('fills the form from a file, saves nothing until Save, and Discard undoes it', async () => {
    // The real snapshot names every setting; this one names the ones used here.
    const api = service(
      { ESSAIES_URL: '', ISBE_LLM_TIMEOUT_S: '900' },
      {
        ESSAIES_URL: 'default',
        ISBE_LLM_MODEL_FAST: 'default',
        ISBE_LLM_TIMEOUT_S: 'default',
        DEEPSEEK_API_KEY: 'default',
      },
    );
    at();
    await ready();
    await pick(
      'lab.json',
      JSON.stringify({
        format: 'isbe-settings',
        version: 1,
        settings: {
          ISBE_LLM_PROVIDER: 'openai',
          ISBE_LLM_BASE_URL: 'http://llm.invalid/v1',
          ISBE_LLM_MODEL_FAST: 'fast-model',
          ISBE_LLM_EXTRA_BODY: { chat_template_kwargs: { enable_thinking: false } },
          OPENAI_API_KEY: 'sk-imported',
          DEEPSEEK_API_KEY: 'sk-other-provider',
          ANTHROPIC_API_KEY: 'sk-locked',
          ESSAIES_URL: 'http://essaies.invalid',
          MYSTERY: 'x',
        },
      }),
    );
    // Every name is listed, the other provider's key too: it has no field on the page.
    await waitFor(() =>
      expect(note()).toMatch(
        'Filled in from lab.json: DEEPSEEK_API_KEY, ESSAIES_URL, ISBE_LLM_BASE_URL, ISBE_LLM_EXTRA_BODY, ' +
          'ISBE_LLM_MODEL_FAST, ISBE_LLM_PROVIDER, OPENAI_API_KEY. Review them, then save.',
      ),
    );
    expect(note()).not.toContain('sk-');
    expect(note()).toContain('Set by the environment, not imported: ANTHROPIC_API_KEY.');
    expect(note()).toContain('Not settings, skipped: MYSTERY.');
    expect(value('ISBE_LLM_PROVIDER')).toBe('openai');
    expect(value('OPENAI_API_KEY')).toBe('sk-imported');
    expect(value('ESSAIES_URL')).toBe('http://essaies.invalid');
    expect(value('ISBE_LLM_EXTRA_BODY')).toBe('{"chat_template_kwargs":{"enable_thinking":false}}');
    expect(document.getElementById('settings-advanced')).toHaveAttribute('open');
    expect(status()).toBe('Unsaved changes');
    expect(screen.getByRole('button', { name: 'Export JSON' })).toBeDisabled();
    expect(screen.getByRole('button', { name: 'Export JSON' })).toHaveAccessibleDescription(
      'Save or discard your changes before exporting.',
    );
    expect(api.puts()).toEqual([]);

    await userEvent.click(screen.getByRole('button', { name: 'Discard changes' }));
    expect(value('ESSAIES_URL')).toBe('');
    expect(note()).toBe('');
    expect(document.getElementById('settings-file-hint')).toHaveTextContent(/only fills in the form/);

    await pick('same.json', '{"ESSAIES_URL": ""}');
    await waitFor(() =>
      expect(note()).toBe(
        'Nothing to change: same.json matches what the page already has. Already the same: ESSAIES_URL.',
      ),
    );

    await pick('lab.json', '{"ESSAIES_URL": "http://essaies.invalid", "ISBE_LLM_TIMEOUT_S": 600.0}');
    await waitFor(() => expect(value('ESSAIES_URL')).toBe('http://essaies.invalid'));
    await save();
    expect(api.puts()).toEqual([{ ESSAIES_URL: 'http://essaies.invalid', ISBE_LLM_TIMEOUT_S: '600' }]);
  });

  it('says why a file cannot be imported, in the page language, and changes nothing', async () => {
    service({ ESSAIES_URL: '' });
    at();
    await ready();
    await pick('broken.json', '{"ESSAIES_URL": [1]}');
    await waitFor(() =>
      expect(note()).toBe(
        'Could not import broken.json: ESSAIES_URL must be a string, number, boolean or null.',
      ),
    );
    expect(document.getElementById('settings-file-note')).toHaveClass('error');
    expect(status()).toBe('');
    await userEvent.click(screen.getByRole('button', { name: 'to-zh' }));
    await pick('x.json', 'nope');
    await waitFor(() => expect(note()).toBe('无法导入 x.json：不是有效的 JSON。'));
    await pick('big.json', ' '.repeat(64 * 1024 + 1));
    await waitFor(() => expect(note()).toBe('无法导入 big.json：文件超过 64 KB。'));
  });

  it('exports the settings in effect without secrets', async () => {
    service({ ESSAIES_URL: 'http://e.invalid' }, { ESSAIES_URL: 'file' });
    at();
    await ready();
    let blob: Blob | undefined;
    const create = vi.fn((b: Blob) => {
      blob = b;
      return 'blob:x';
    });
    Object.assign(URL, { createObjectURL: create, revokeObjectURL: vi.fn() });
    const click = vi.spyOn(HTMLAnchorElement.prototype, 'click').mockImplementation(() => {});
    await userEvent.click(screen.getByRole('button', { name: 'Export JSON' }));
    expect(click).toHaveBeenCalledOnce();
    const doc = JSON.parse(await blob!.text()) as Record<string, unknown>;
    expect(doc).toEqual({
      format: 'isbe-settings',
      version: 1,
      settings: { ESSAIES_URL: 'http://e.invalid' },
      secrets_omitted: ['ANTHROPIC_API_KEY', 'OPENAI_API_KEY'],
    });
  });
});
