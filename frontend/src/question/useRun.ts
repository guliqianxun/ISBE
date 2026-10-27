// A run's live state: subscribes to its events, folds them into `RunState` at
// most once per frame (a search pushes hundreds of hits a second), and resumes
// from the last seq after the connection drops. `connection` is `lost` once
// reconnecting gave up (`refused` when the server said no rather than nothing);
// `retry` then starts over. `local` folds in an override
// the console saved itself, without waiting for the stream.
import { useCallback, useEffect, useRef, useState } from 'react';
import { apply, emptyRun, withOverride, type RunState } from './run';
import type { Override, QuestionEvent } from './types';
import type { RunClient } from './client';

export function useRun(client: RunClient | null, onMissing?: () => void) {
  const [state, setState] = useState<RunState>(emptyRun);
  const [connection, setConnection] = useState<'open' | 'retrying' | 'lost' | 'refused'>('open');
  const [retry, setRetry] = useState<(() => void) | null>(null);
  const last = useRef(0);
  // Read when it happens, so a new callback each render does not resubscribe.
  const missing = useRef(onMissing);
  useEffect(() => {
    missing.current = onMissing;
  });

  useEffect(() => {
    if (!client) return;
    let pending: QuestionEvent[] = [];
    let frame = 0;
    const flush = () => {
      frame = 0;
      const batch = pending;
      pending = [];
      setState((s) => apply(s, batch));
    };
    const sub = client.subscribe(last.current, {
      onEvent: (e) => {
        if (e.seq > last.current) last.current = e.seq;
        pending.push(e);
        if (!frame) frame = requestAnimationFrame(flush);
      },
      onStatus: (open) => setConnection(open ? 'open' : 'retrying'),
      onMissing: () => {
        setConnection('open');
        missing.current?.();
      },
      onLost: (again, refused) => {
        setConnection(refused ? 'refused' : 'lost');
        setRetry(() => () => {
          setConnection('retrying');
          again();
        });
      },
    });
    return () => {
      cancelAnimationFrame(frame);
      sub.close();
    };
  }, [client]);

  const local = useCallback((o: Override) => setState((s) => withOverride(s, o)), []);

  return { state, connection, retry, local };
}
