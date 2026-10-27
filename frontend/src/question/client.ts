// How the question pages reach a run. `httpRun` talks to the `/api/question/*`
// routes of docs/question/api.md; `replayRun` (development builds only) plays a
// recorded event log through the same interface, so a whole run can be walked
// without a backend.
import { api, ApiError, StaleSession } from '../api/client';
import { subscribe } from '../api/sse';
import { decode, type GraphDoc, type WireGraph } from './graph/model';
import {
  EVENT_TYPES,
  type Engine,
  type Expansion,
  type Lang,
  type Override,
  type Plan,
  type PlanChanges,
  type QuestionEvent,
  type Run,
} from './types';

export interface RunHandlers {
  onEvent: (e: QuestionEvent) => void;
  /** False while the connection is down and being retried. */
  onStatus: (open: boolean) => void;
  /** The run does not exist (any more): nothing more will come. */
  onMissing?: () => void;
  /** Reconnecting gave up; `retry` starts over. `refused`: the server answered with a
   * 4xx that a later try cannot change, rather than not answering. Until then
   * `onStatus(false)` stands. */
  onLost?: (retry: () => void, refused: boolean) => void;
}

export interface RunClient {
  /** Events after `after`, then live ones until `close()`. */
  subscribe(after: number, handlers: RunHandlers): { close(): void };
  /** The run object, for the first paint; null where there is none (a replay). */
  get(): Promise<Run | null>;
  start(): Promise<void>;
  hold(): Promise<void>;
  /** Run edited plan: only under review; the search starts at once. */
  savePlan(version: number, changes: PlanChanges): Promise<void>;
  /** Edit plan and rerun: a new run from that plan version with `changes`; its id. */
  rerun(version: number, changes: PlanChanges): Promise<string>;
  pause(): Promise<void>;
  resume(): Promise<void>;
  cancel(): Promise<void>;
  /** Delete the run and everything in it; only once it has come to rest. */
  remove(): Promise<void>;
  /** Include or exclude paper `key` by hand; the `screen_override@1` saved. */
  override(key: string, decision: 'include' | 'exclude', note: string): Promise<Override>;
  /** Revert to the model verdict: the record that revokes `overrideId`. */
  revoke(overrideId: string, note: string): Promise<Override>;
  /** Where the paper set and the graph download, overrides applied; null where
   * there are none (a replay). */
  downloads: Downloads | null;
  /** The run's knowledge graph as last built, and whether it is stale; null before
   * the graph stage has run, or where there is none (a replay). */
  graph(): Promise<{ graph: GraphDoc; stale: boolean } | null>;
  /** Builds the graph of an ended run again (after overrides made it stale). */
  rebuildGraph(): Promise<void>;
}

const base = (id: string) => `/api/question/runs/${encodeURIComponent(id)}`;

/** How long a dropped stream waits before it reconnects by itself; it doubles on
 * each failed try, up to `RECONNECT_MAX_MS`, and gives up after `RECONNECT_TRIES`
 * failed tries in a row (waits of 2 + 4 + 8 + 16 s, about half a minute). */
export const RECONNECT_MS = 2000;
export const RECONNECT_MAX_MS = 30_000;
export const RECONNECT_TRIES = 5;

/** A 4xx a later try cannot fix: stop retrying at once. */
const final = (err: unknown) =>
  err instanceof ApiError &&
  err.status >= 400 &&
  err.status < 500 &&
  err.status !== 408 &&
  err.status !== 429;

export function httpRun(id: string): RunClient {
  const post = async (action: string) => {
    await api.post(`${base(id)}/${action}`);
  };
  return {
    subscribe(after, handlers) {
      let last = after;
      let closed = false;
      // `done` arrived: the server ends the stream after the events that follow it.
      let ended = false;
      let wait = RECONNECT_MS;
      let tries = 0;
      let timer: ReturnType<typeof setTimeout> | undefined;
      let sub: { close(): void } | null = null;
      const stop = () => {
        closed = true;
        clearTimeout(timer);
        document.removeEventListener('visibilitychange', onVisible);
        sub?.close();
      };
      const giveUp = (refused = false) => {
        clearTimeout(timer);
        handlers.onLost?.(() => {
          if (closed) return;
          tries = 0;
          wait = RECONNECT_MS;
          open();
        }, refused);
      };
      // A hidden tab waits to be seen again before it tries.
      const onVisible = () => {
        if (document.hidden || closed) return;
        document.removeEventListener('visibilitychange', onVisible);
        open();
      };
      const later = () => {
        timer = setTimeout(() => {
          if (closed) return;
          if (document.hidden) document.addEventListener('visibilitychange', onVisible);
          else open();
        }, wait);
        wait = Math.min(wait * 2, RECONNECT_MAX_MS);
      };
      // EventSource hides why a connection failed. The run itself is read through
      // the API client instead: a 401 there ends the session, a 404 ends the page,
      // another 4xx gives up at once, and anything else is retried, more slowly
      // each time, `RECONNECT_TRIES` times.
      const retry = async () => {
        tries += 1;
        try {
          const found = await api.get<{ run: Run } | null>(base(id), { missingOk: true });
          if (closed) return;
          if (found === null) {
            stop();
            handlers.onMissing?.();
            return;
          }
        } catch (err) {
          if (err instanceof StaleSession) {
            stop();
            return;
          }
          if (closed) return;
          if (final(err)) {
            giveUp(true);
            return;
          }
        }
        if (closed) return;
        if (tries >= RECONNECT_TRIES) giveUp();
        else later();
      };
      // The browser's own retry resends Last-Event-ID; after an error that
      // closed the stream, a new one resumes with ?after=<last seq>.
      const open = () => {
        sub = subscribe<QuestionEvent>(`${base(id)}/events`, {
          types: EVENT_TYPES,
          after: last ? String(last) : undefined,
          onEvent: ({ data }) => {
            if (data.seq > last) last = data.seq;
            if (data.type === 'done') ended = true;
            handlers.onEvent(data);
          },
          onStatus: (ok) => {
            if (closed) return;
            if (ok) {
              wait = RECONNECT_MS;
              tries = 0;
              handlers.onStatus(true);
              return;
            }
            sub?.close();
            if (ended) {
              stop();
              return;
            }
            handlers.onStatus(false);
            void retry();
          },
        });
      };
      open();
      return { close: stop };
    },
    get: async () => (await api.get<{ run: Run } | null>(base(id), { missingOk: true }))?.run ?? null,
    start: () => post('start'),
    hold: () => post('hold'),
    savePlan: async (version, changes) => {
      await api.post(`${base(id)}/plan`, { version, changes });
    },
    rerun: async (version, changes) =>
      (await api.post<{ run: Run }>(`${base(id)}/rerun`, { version, changes })).run.run_id,
    pause: () => post('pause'),
    resume: () => post('resume'),
    cancel: () => post('cancel'),
    remove: async () => {
      await api.delete(base(id));
    },
    override: async (key, decision, note) =>
      (await api.post<{ override: Override }>(`${base(id)}/overrides`, { key, decision, note })).override,
    revoke: async (overrideId, note) =>
      (
        await api.post<{ override: Override }>(
          `${base(id)}/overrides/${encodeURIComponent(overrideId)}/revoke`,
          { note },
        )
      ).override,
    downloads: {
      csv: `${base(id)}/export.csv`,
      json: `${base(id)}/export.json`,
      graphJson: `${base(id)}/graph.json`,
      graphml: `${base(id)}/graph.graphml`,
    },
    graph: async () => {
      const got = await api.get<{ graph: WireGraph; stale: boolean } | null>(
        `${base(id)}/graph?form=compact`,
        {
          missingOk: true,
        },
      );
      return got && { graph: decode(got.graph), stale: got.stale };
    },
    rebuildGraph: async () => {
      await api.post(`${base(id)}/graph`, {});
    },
  };
}

export interface Downloads {
  /** The paper set, overrides applied. */
  csv: string;
  json: string;
  /** The graph as last built: `question_graph@1`, and GraphML for Gephi or Cytoscape. */
  graphJson: string;
  graphml: string;
}

export interface RunList {
  runs: Run[];
  /** The engines the server offers and the one a run gets when it names none. */
  engines?: Engine[];
  default_engine?: Engine;
}

export async function listRuns(): Promise<RunList> {
  const body = await api.get<RunList | null>('/api/question/runs', { missingOk: true });
  return body ?? { runs: [] };
}

export interface Ask extends Expansion {
  question: string;
  lang: Lang;
  year_from: number | null;
  year_to: number | null;
  review: boolean;
  /** Left out: the server's default engine. */
  engine?: Engine;
  /** Makes a retried submit return the run it already made. */
  request_id?: string;
}

export async function createRun(ask: Ask): Promise<string> {
  const body = await api.post<{ run: Run }>('/api/question/runs', ask);
  return body.run.run_id;
}

// --- replay -----------------------------------------------------------------

export const REPLAY_PREFIX = 'replay-';

/** Plays `log` as a live run: one event every `stepMs`, the plan review waits for
 * Start search or an edited plan, and pause, resume and cancel act at the next step
 * the way the worker does at the next query or batch. */
export function replayRun(log: QuestionEvent[], stepMs = 40): RunClient {
  let sent = 0;
  let held = true;
  const made: Override[] = [];
  let paused = false;
  let cancel = false;
  let timer: ReturnType<typeof setTimeout> | undefined;
  const extra: QuestionEvent[] = [];
  let listener: RunHandlers | null = null;
  let seq = 0;
  const runId = log[0]?.run_id ?? 'replay';
  const now = () => new Date().toISOString();
  const synth = (e: Pick<QuestionEvent, 'type' | 'data'>): QuestionEvent =>
    ({ schema: 'question_event@1', run_id: runId, seq: ++seq, at: now(), ...e }) as QuestionEvent;
  const emit = (e: QuestionEvent) => {
    seq = Math.max(seq, e.seq);
    listener?.onEvent(e);
  };

  const step = () => {
    timer = undefined;
    if (!listener) return;
    for (const e of extra.splice(0)) emit(e);
    if (cancel) {
      emit(synth({ type: 'state', data: { state: 'cancelled' } }));
      emit(synth({ type: 'done', data: { status: 'cancelled' } }));
      return;
    }
    // Timers fire every few ms at best, so a faster pace sends several events a tick.
    const burst = Math.max(1, Math.round(4 / stepMs));
    for (let i = 0; i < burst; i++) {
      if (paused || sent >= log.length) return;
      // Hold before searching begins until a person starts it.
      const next = log[sent]!;
      if (held && next.type === 'stage' && next.data.stage === 'search' && next.data.state === 'begin')
        return;
      sent += 1;
      emit({ ...next, seq: ++seq, at: next.type === 'plan' ? now() : next.at } as QuestionEvent);
    }
    timer = setTimeout(step, stepMs * burst);
  };
  const kick = () => {
    if (timer === undefined) timer = setTimeout(step, 0);
  };

  return {
    subscribe(after, handlers) {
      // A replay starts over: there is no stored log to resume from.
      void after;
      listener = handlers;
      handlers.onStatus(true);
      kick();
      return {
        close() {
          listener = null;
          clearTimeout(timer);
          timer = undefined;
        },
      };
    },
    async start() {
      held = false;
      clearTimeout(timer);
      timer = undefined;
      kick();
    },
    async hold() {
      held = true;
    },
    get: async () => null,
    async savePlan(version, changes) {
      held = false;
      const plan = [...log].reverse().find((e) => e.type === 'plan')!.data as { plan: Plan };
      extra.push(
        synth({
          type: 'plan',
          data: {
            plan: {
              ...plan.plan,
              ...changes,
              version: version + 1,
              parent_version: version,
              source: 'human',
            },
          },
        }),
      );
      clearTimeout(timer);
      timer = undefined;
      kick();
    },
    rerun: async () => {
      throw new Error('a replay cannot be rerun');
    },
    async pause() {
      paused = true;
      extra.push(synth({ type: 'state', data: { state: 'paused' } }));
      clearTimeout(timer);
      timer = setTimeout(step, stepMs);
    },
    async resume() {
      paused = false;
      extra.push(synth({ type: 'state', data: { state: 'running' } }));
      clearTimeout(timer);
      timer = undefined;
      kick();
    },
    remove: async () => {
      throw new Error('a replay cannot be deleted');
    },
    async cancel() {
      cancel = true;
      clearTimeout(timer);
      timer = undefined;
      kick();
    },
    async override(key, decision, note) {
      const o: Override = {
        override_id: `o${made.length + 1}`,
        key,
        decision,
        note,
        actor: 'replay',
        revokes: null,
        created_at: now(),
      };
      made.push(o);
      emit(synth({ type: 'override', data: { override: o } }));
      return o;
    },
    async revoke(overrideId, note) {
      const target = made.find((o) => o.override_id === overrideId);
      if (!target) throw new Error('no such override');
      const o: Override = {
        override_id: `o${made.length + 1}`,
        key: target.key,
        decision: null,
        note,
        actor: 'replay',
        revokes: overrideId,
        created_at: now(),
      };
      made.push(o);
      emit(synth({ type: 'override', data: { override: o } }));
      return o;
    },
    downloads: null,
    graph: async () => null,
    rebuildGraph: async () => {
      throw new Error('a replay has no graph to build');
    },
  };
}

/** The recorded log of `replay-<sample>` (tests/fixtures/question/<sample>/events.json),
 * in development builds only: production builds leave the fixtures out. */
export const replayLog: (id: string) => Promise<QuestionEvent[] | null> = import.meta.env.DEV
  ? async (id) => {
      if (!id.startsWith(REPLAY_PREFIX)) return null;
      const logs = import.meta.glob<{ default: QuestionEvent[] }>(
        '../../../tests/fixtures/question/*/events.json',
      );
      const load = logs[`../../../tests/fixtures/question/${id.slice(REPLAY_PREFIX.length)}/events.json`];
      return load ? (await load()).default : null;
    }
  : async () => null;
