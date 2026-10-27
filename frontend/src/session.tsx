// Who is signed in. Local mode has one implicit administrator; shared mode
// needs an account, and a 401 from any call returns to the sign-in form.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react';
import { api, endSession, onUnauthorized, setCsrfToken } from './api/client';
import type { MessageKey } from './i18n';

export interface User {
  id: string;
  username: string;
  role: 'admin' | 'member';
  active: boolean;
}

export interface Session {
  mode: 'local' | 'shared';
  user: User | null;
  csrf_token: string;
  llm?: { ready: boolean };
  mail?: { configured: boolean };
}

type State =
  | { status: 'loading' }
  | { status: 'unavailable' }
  | { status: 'ready'; session: Session; notice: MessageKey | '' };

interface SessionContext {
  state: State;
  reload: () => Promise<void>;
  signIn: (username: string, password: string) => Promise<boolean>;
  signOut: () => Promise<boolean>;
}

const Context = createContext<SessionContext | null>(null);

const isSession = (value: unknown): value is Session =>
  !!value &&
  typeof value === 'object' &&
  ['local', 'shared'].includes((value as Session).mode) &&
  typeof (value as Session).csrf_token === 'string';

async function fetchSession(): Promise<Session> {
  const session = await api.get<unknown>('/api/session');
  if (!isSession(session)) throw new Error('unexpected session');
  setCsrfToken(session.csrf_token);
  return session;
}

export function SessionProvider({ children }: { children: ReactNode }) {
  const [state, setState] = useState<State>({ status: 'loading' });

  const reload = useCallback(async () => {
    try {
      setState({ status: 'ready', session: await fetchSession(), notice: '' });
    } catch {
      setState({ status: 'unavailable' });
    }
  }, []);

  useEffect(() => {
    onUnauthorized(() => {
      endSession();
      setState((current) =>
        current.status === 'ready'
          ? {
              status: 'ready',
              session: { ...current.session, user: null, csrf_token: '' },
              notice: 'sessionExpired',
            }
          : current,
      );
      // A fresh token for the sign-in form.
      fetchSession()
        .then((session) => setState({ status: 'ready', session, notice: 'sessionExpired' }))
        .catch(() => setState({ status: 'unavailable' }));
    });
    fetchSession()
      .then((session) => setState({ status: 'ready', session, notice: '' }))
      .catch(() => setState({ status: 'unavailable' }));
  }, []);

  const signIn = useCallback(async (username: string, password: string) => {
    try {
      // The token may have changed if the server restarted since the page loaded.
      await fetchSession();
      const session = await api.post<unknown>('/api/login', { username, password });
      if (!isSession(session) || !session.user) return false;
      setCsrfToken(session.csrf_token);
      setState({ status: 'ready', session, notice: '' });
      return true;
    } catch {
      return false;
    }
  }, []);

  const signOut = useCallback(async () => {
    try {
      await api.post('/api/logout', {});
    } catch {
      return false;
    }
    endSession();
    try {
      setState({ status: 'ready', session: await fetchSession(), notice: '' });
    } catch {
      setState({ status: 'unavailable' });
    }
    return true;
  }, []);

  const value = useMemo(() => ({ state, reload, signIn, signOut }), [state, reload, signIn, signOut]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function useSession(): SessionContext {
  const value = useContext(Context);
  if (!value) throw new Error('useSession outside SessionProvider');
  return value;
}

/** The signed-in session; only for components rendered inside the shell. */
export function useSignedIn(): Session & { user: User } {
  const { state } = useSession();
  if (state.status !== 'ready' || !state.session.user) throw new Error('useSignedIn without a user');
  return state.session as Session & { user: User };
}
