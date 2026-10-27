// `/api/health` for Home: read on arrival and every minute while the tab is in
// view, and again when it comes back, so a down upstream shows before anyone
// asks a question (docs/question/api.md#health). A hidden tab asks nothing. A
// failed read shows nothing: Home has its own error for a server that is gone.
import { useEffect, useState } from 'react';
import { api } from '../api/client';

export interface Upstream {
  configured: boolean;
  state: 'ok' | 'down' | 'unknown';
  checked_at: string | null;
  error: string | null;
}

export interface Health {
  ok: boolean;
  worker: { alive: boolean };
  /** `papers`: the paper source the settings choose, essaies or the OpenAlex API. */
  upstreams: { papers: Upstream & { source: 'openalex' | 'essaies' }; model: Upstream };
}

export const HEALTH_MS = 60_000;

export function useHealth(): Health | null {
  const [health, setHealth] = useState<Health | null>(null);
  useEffect(() => {
    let live = true;
    const read = () =>
      api.get<Health>('/api/health').then(
        (h) => live && setHealth(h),
        () => live && setHealth(null),
      );
    let timer: number | undefined;
    const start = () => {
      window.clearInterval(timer);
      timer = undefined;
      if (document.visibilityState === 'hidden') return;
      void read();
      timer = window.setInterval(read, HEALTH_MS);
    };
    start();
    document.addEventListener('visibilitychange', start);
    return () => {
      live = false;
      window.clearInterval(timer);
      document.removeEventListener('visibilitychange', start);
    };
  }, []);
  return health;
}
