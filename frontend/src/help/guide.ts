// The report guide's state (Help, `#report-guide`): check the model, confirm a
// topic, then queue one report on `POST /api/tasks` and follow it on
// `GET /api/tasks/<id>` until it stops. Ported from the classic console's
// dialog, with the same rules:
//
// - Opening the guide never submits anything.
// - A submission carries a `request_id`, kept in sessionStorage with the topic
//   until the server answers, so a refresh asks about the same request and a
//   retry reuses it; the server queues one task per id. An unanswered
//   submission is never sent again on its own.
// - While a task is queued or running the marker holds its id instead, so a
//   refresh (in either console: the key is shared) picks the task up again.
// - A failed status read stops polling and says so; it does not restart.
import { api, ApiError, StaleSession } from '../api/client';
import type { MessageKey } from '../i18n';

export const STAGES = ['collect', 'triage', 'enrich', 'digest'] as const;
const STATES = ['queued', 'running', 'succeeded', 'failed', 'interrupted', 'cancelled'];

export interface Job {
  id: string;
  owner: string;
  topic: string;
  state: 'queued' | 'running' | 'succeeded' | 'failed' | 'interrupted' | 'cancelled';
  stage: string;
  error: string | null;
  result: { topic: string; period: string } | null;
  position: number | null;
}

export interface TopicDetail {
  id: string;
  error?: string;
  config?: { arxiv?: { categories?: string[]; include_keywords?: string[] } | null };
}

/** What sessionStorage keeps: a task being followed, or a submission not yet answered. */
export interface Marker {
  id?: string;
  topic: string;
  requestId?: string;
}

export interface GuideState {
  step: 1 | 2 | 3;
  topic: string;
  topics: { id: string; label?: string }[];
  detail: TopicDetail | null;
  model: boolean;
  loading: boolean;
  starting: boolean;
  /** The task status has been read since the guide opened. */
  checked: boolean;
  job: Job | null;
  error: MessageKey | '';
  statusError: MessageKey | '';
  interrupted: boolean;
  observed: Marker | null;
}

/** The server's reason when a topic's window holds no usable papers. */
export const NO_DATA =
  "No papers remain in this topic's current window. Review its sources, keywords and filters in Topics, then try again.";

const START_ERRORS: Record<string, MessageKey> = {
  configuration: 'llmIncomplete',
  invalid_topic: 'guideSourceInvalid',
  unsupported_topic: 'firstArticles',
};

export const active = (job: Job | null | undefined): boolean =>
  job?.state === 'queued' || job?.state === 'running';

export const markerKey = (user: string) => (user === 'local' ? 'isbe.generation' : `isbe.generation.${user}`);

export function readMarker(user: string): Marker | null {
  try {
    const value: unknown = JSON.parse(sessionStorage.getItem(markerKey(user)) || 'null');
    return value && typeof value === 'object' && typeof (value as Marker).topic === 'string'
      ? (value as Marker)
      : null;
  } catch {
    return null;
  }
}

const requestId = () =>
  Array.from(crypto.getRandomValues(new Uint8Array(16)), (v) => v.toString(16).padStart(2, '0')).join('');

const enc = encodeURIComponent;

function isJob(job: unknown): job is Job {
  const j = job as Job;
  return (
    !!j &&
    typeof j === 'object' &&
    typeof j.id === 'string' &&
    !!j.id &&
    typeof j.topic === 'string' &&
    !!j.topic &&
    STATES.includes(j.state) &&
    (j.stage === 'queued' || (STAGES as readonly string[]).includes(j.stage))
  );
}

export class Guide {
  state: GuideState = {
    step: 1,
    topic: '',
    topics: [],
    detail: null,
    model: false,
    // Nothing is shown as missing before the first read answers.
    loading: true,
    starting: false,
    checked: false,
    job: null,
    error: '',
    statusError: '',
    interrupted: false,
    observed: null,
  };
  // Answers to older reads are dropped: one counter for the task status,
  // one for the saved configuration, so neither cancels the other.
  private statusRead = 0;
  private configRead = 0;
  private timer: ReturnType<typeof setTimeout> | undefined;
  private closed = false;
  private listeners = new Set<() => void>();

  constructor(private readonly user: string) {}

  subscribe = (listener: () => void) => {
    this.listeners.add(listener);
    return () => this.listeners.delete(listener);
  };

  private set(change: Partial<GuideState>) {
    this.state = { ...this.state, ...change };
    for (const listener of this.listeners) listener();
  }

  sourceReady(): boolean {
    const { detail, topic } = this.state;
    return (
      detail?.id === topic &&
      !detail.error &&
      !!detail.config?.arxiv?.categories?.length &&
      !!detail.config.arxiv.include_keywords?.length
    );
  }

  canStart(): boolean {
    const s = this.state;
    return (
      s.checked &&
      !s.loading &&
      !s.starting &&
      !s.statusError &&
      !s.error &&
      !active(s.job) &&
      s.model &&
      this.sourceReady()
    );
  }

  canNext(): boolean {
    const s = this.state;
    return !s.loading && !s.error && (s.step === 1 ? s.model : this.sourceReady());
  }

  private remember(marker: Marker | null) {
    this.state = { ...this.state, observed: marker };
    try {
      if (marker) sessionStorage.setItem(markerKey(this.user), JSON.stringify(marker));
      else sessionStorage.removeItem(markerKey(this.user));
    } catch {
      // Private mode: the guide still works, only a refresh forgets.
    }
  }

  /** Stop following task `id`; the saved marker goes only if it names that task. */
  private forget(id: string) {
    if (readMarker(this.user)?.id === id) this.remember(null);
    else this.state = { ...this.state, observed: null };
  }

  /** Take in a task from the server (`null`: none). Throws on a malformed one. */
  private adopt(job: Job | null) {
    if (job !== null && !isJob(job)) throw new Error('unexpected task');
    if (active(job)) this.remember({ id: job!.id, topic: job!.topic });
    else if (job) this.remember(null);
    this.set({
      job,
      interrupted: job?.state === 'interrupted',
      topic: job ? job.topic : this.state.topic,
      checked: true,
      statusError: '',
      ...(job || this.state.observed ? { step: 3 as const } : {}),
    });
  }

  /** Open on `topic`, or on task `task` (the Tasks page's "Check"); `fresh` starts over. */
  async open({ topic = '', task = '', fresh = false }: { topic?: string; task?: string; fresh?: boolean }) {
    ++this.statusRead;
    ++this.configRead;
    clearTimeout(this.timer);
    this.closed = false;
    let observed = fresh ? null : readMarker(this.user);
    if (task) observed = { id: task, topic: '' };
    else if (topic && observed && topic !== observed.topic) observed = null;
    // A new address starts from what it names, not from the task shown before.
    this.set({
      observed,
      topic: topic || observed?.topic || '',
      step: 1,
      checked: false,
      job: null,
      interrupted: false,
      error: '',
      statusError: '',
    });
    if (fresh) this.set({ checked: true });
    else await this.check();
    await this.load();
  }

  close() {
    this.closed = true;
    ++this.statusRead;
    ++this.configRead;
    clearTimeout(this.timer);
  }

  private schedule() {
    clearTimeout(this.timer);
    if (!this.closed && active(this.state.job) && !this.state.statusError)
      this.timer = setTimeout(() => void this.check(), 2000);
  }

  async check() {
    const request = ++this.statusRead;
    const id = this.state.job?.id || this.state.observed?.id;
    try {
      let job: Job | null;
      let foreign = false;
      if (id) {
        const data = await api.get<{ job: Job } | null>(`/api/tasks/${enc(id)}`, { missingOk: true });
        job = data?.job ?? null;
        // An administrator may read anyone's task; the guide follows only one's
        // own and leaves another account's alone, as if none was named.
        foreign = !!job && job.owner !== this.user;
        if (foreign) job = null;
      } else {
        const data = await api.get<{ tasks: Job[] }>('/api/tasks');
        const topic = this.state.topic;
        // An unanswered submission has no id yet: say so rather than guess.
        job = this.state.observed?.requestId
          ? null
          : (data.tasks.find((j) => j.owner === this.user && (!topic || j.topic === topic)) ?? null);
      }
      if (request !== this.statusRead) return;
      if (foreign) this.forget(id!);
      this.adopt(job);
      if (id && !job && !foreign) {
        this.forget(id);
        this.set({ interrupted: true, step: 3 });
      }
    } catch (e) {
      if (e instanceof StaleSession || request !== this.statusRead) return;
      this.set({ checked: false, statusError: 'guideUnavailable', step: 3 });
    }
    this.schedule();
  }

  async load() {
    const request = ++this.configRead;
    this.set({ loading: true, error: '' });
    try {
      const [session, list] = await Promise.all([
        api.get<{ llm?: { ready?: boolean } }>('/api/session'),
        api.get<{ topics: { id: string; label?: string }[] }>('/api/topics'),
      ]);
      if (request !== this.configRead) return;
      const topic = this.state.topic || this.state.job?.topic || list.topics[0]?.id || '';
      this.set({ model: session.llm?.ready === true, topics: list.topics, topic, detail: null });
      if (topic) {
        const detail = await api.get<TopicDetail>(`/api/topics/${enc(topic)}`);
        if (request !== this.configRead) return;
        this.set({ detail });
      }
    } catch (e) {
      if (!(e instanceof StaleSession) && request === this.configRead) this.set({ error: 'guideLoadFailed' });
    } finally {
      if (request === this.configRead) this.set({ loading: false });
    }
    this.schedule();
  }

  async start() {
    if (!this.canStart()) return;
    const topic = this.state.topic;
    const id = this.state.observed?.requestId || requestId();
    this.remember({ topic, requestId: id });
    this.set({ starting: true, error: '', interrupted: false });
    try {
      const data = await api.post<{ job?: Job }>('/api/tasks', { topic, request_id: id });
      if (!data.job) throw new Error('unexpected answer');
      this.adopt(data.job);
    } catch (e) {
      if (e instanceof StaleSession || this.closed) return;
      const body = e instanceof ApiError ? (e.body as { code?: string; job?: Job } | undefined) : undefined;
      if (e instanceof ApiError && e.status === 409 && body?.code === 'busy' && body.job) {
        // Already queued for this topic: follow that task.
        try {
          this.adopt(body.job);
        } catch {
          await this.check();
        }
      } else if (e instanceof ApiError && e.status < 500 && !(e.status === 409 && body?.code === 'busy')) {
        this.remember(null);
        this.set({ error: START_ERRORS[body?.code ?? ''] ?? 'guideStartFailed' });
      } else {
        // No clear answer: ask, never resubmit.
        await this.check();
      }
    } finally {
      this.set({ starting: false });
      if (!this.closed) {
        if (this.state.detail?.id !== this.state.topic) await this.load();
        this.schedule();
      }
    }
  }

  next() {
    if (this.canNext() && this.state.step < 3) this.set({ step: (this.state.step + 1) as 2 | 3 });
  }

  back() {
    if (!this.state.starting && !active(this.state.job) && this.state.step > 1)
      this.set({ step: (this.state.step - 1) as 1 | 2 });
  }

  async checkAgain() {
    if (this.state.loading || this.state.starting) return;
    await this.check();
    await this.load();
  }

  /** Start over on step 1; a task still running keeps its marker. */
  again() {
    if (this.state.starting) return;
    clearTimeout(this.timer);
    this.set({ job: null, observed: null, topic: '', step: 1, interrupted: false });
    void this.load();
  }

  choose(topic: string) {
    if (this.state.starting || active(this.state.job)) return;
    this.set({ topic, job: null, observed: null, interrupted: false });
    void this.load();
  }
}
