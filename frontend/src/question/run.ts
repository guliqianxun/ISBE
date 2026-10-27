// A run's page state, rebuilt from its `question_event@1` log alone
// (docs/question/hsi-flow.md, section 2). `apply` folds a batch of events into
// a new state and skips any seq it has already seen, so a reconnect that
// resends events never adds a star twice.
import type {
  Decision,
  DoneStatus,
  Override,
  Paper,
  Plan,
  ProblemCode,
  QuestionEvent,
  RunStatus,
  StageName,
  Verdict,
} from './types';
import { STAGES } from './types';

/** `stopped`: begun, then the run ended (failed or cancelled) before the stage did. */
export type StageState = 'pending' | 'begin' | 'end' | 'skipped' | 'stopped';

/** How a paper was found: a query's hit, or expansion from a paper in the run. */
export type Found = { query_id: string; rank: number } | { from_key: string; via: 'cites' | 'cited_by' };

export interface Star {
  key: string;
  paper: Paper;
  /** In arrival order; the first decides the star's track and hue. */
  found: Found[];
  /** Identity keys folded into this star by `dedup`. */
  merged: string[];
  /** Repeat hits after the first. */
  repeats: number;
  verdict: Verdict | null;
  overrides: Override[];
  /** The event that created it, for ordering. */
  seq: number;
}

export interface Problem {
  seq: number;
  stage: StageName;
  message: string;
  query_id?: string;
  batch_id?: string;
  key?: string;
  code?: ProblemCode;
  detail?: string;
}

export interface RunState {
  lastSeq: number;
  /** `empty` until the first event arrives. */
  status: RunStatus | 'empty';
  stages: Record<StageName, StageState>;
  plan: Plan | null;
  /** When the plan event arrived, for the review countdown. */
  planAt: string | null;
  stars: Map<string, Star>;
  /** Every identity key in the run → the key of the star that holds it. */
  keyIndex: Map<string, string>;
  problems: Problem[];
  fatal: Problem | null;
  graph: { fingerprint: string; nodes: number; edges: number; communities: number } | null;
  done: DoneStatus | null;
}

export function emptyRun(): RunState {
  return {
    lastSeq: 0,
    status: 'empty',
    stages: { plan: 'pending', search: 'pending', screen: 'pending', expand: 'pending', graph: 'pending' },
    plan: null,
    planAt: null,
    stars: new Map(),
    keyIndex: new Map(),
    problems: [],
    fatal: null,
    graph: null,
    done: null,
  };
}

export function apply(prev: RunState, events: QuestionEvent[]): RunState {
  const fresh = events.filter((e) => e.seq > prev.lastSeq).sort((a, b) => a.seq - b.seq);
  if (!fresh.length) return prev;
  const s: RunState = {
    ...prev,
    stages: { ...prev.stages },
    stars: new Map(prev.stars),
    keyIndex: new Map(prev.keyIndex),
    problems: [...prev.problems],
  };
  // Stars are copied on their first change in this batch, then changed in place.
  const touched = new Set<string>();
  const edit = (key: string): Star | undefined => {
    const star = s.stars.get(key);
    if (!star || touched.has(key)) return star;
    const copy = {
      ...star,
      found: [...star.found],
      merged: [...star.merged],
      overrides: [...star.overrides],
    };
    s.stars.set(key, copy);
    touched.add(key);
    return copy;
  };
  // A repeat is a paper whose preferred key is known, as the search stage counts
  // it; records that match on another key stay apart until `dedup` folds them.
  const add = (paper: Paper, found: Found, seq: number) => {
    const known = s.keyIndex.get(paper.key);
    if (known !== undefined) {
      const star = edit(known)!;
      star.found.push(found);
      star.repeats += 1;
      for (const k of paper.keys) if (!s.keyIndex.has(k)) s.keyIndex.set(k, known);
      return;
    }
    s.stars.set(paper.key, {
      key: paper.key,
      paper,
      found: [found],
      merged: [],
      repeats: 0,
      verdict: null,
      overrides: [],
      seq,
    });
    touched.add(paper.key);
    s.keyIndex.set(paper.key, paper.key);
    for (const k of paper.keys) if (!s.keyIndex.has(k)) s.keyIndex.set(k, paper.key);
  };

  for (const e of fresh) {
    s.lastSeq = e.seq;
    if (s.status === 'empty') s.status = 'queued';
    switch (e.type) {
      case 'state':
        s.status = e.data.state;
        if (e.data.state === 'failed' || e.data.state === 'cancelled') stopStages(s);
        break;
      case 'stage':
        s.stages[e.data.stage] = e.data.state;
        break;
      case 'plan':
        s.plan = e.data.plan;
        s.planAt = e.at;
        break;
      case 'hit':
        add(e.data.paper, { query_id: e.data.query_id, rank: e.data.rank }, e.seq);
        break;
      case 'expand':
        add(e.data.paper, { from_key: e.data.from_key, via: e.data.via }, e.seq);
        break;
      case 'dedup': {
        const keptKey = s.stars.has(e.data.kept) ? e.data.kept : (s.keyIndex.get(e.data.kept) ?? e.data.kept);
        const kept = edit(keptKey);
        if (!kept) break;
        for (const m of e.data.merged) {
          const otherKey = s.stars.has(m) ? m : (s.keyIndex.get(m) ?? m);
          kept.merged.push(m);
          s.keyIndex.set(m, keptKey);
          if (otherKey === keptKey) continue;
          const other = s.stars.get(otherKey);
          if (other) {
            kept.found.push(...other.found);
            kept.repeats += other.repeats + 1;
            s.stars.delete(otherKey);
            for (const [k, v] of s.keyIndex) if (v === otherKey) s.keyIndex.set(k, keptKey);
          }
        }
        break;
      }
      case 'verdict': {
        const star = edit(s.keyIndex.get(e.data.verdict.key) ?? e.data.verdict.key);
        if (star) star.verdict = e.data.verdict;
        break;
      }
      case 'override': {
        const star = edit(s.keyIndex.get(e.data.override.key) ?? e.data.override.key);
        // The console applies its own override at once; the stream's copy is then a repeat.
        if (star && !star.overrides.some((o) => o.override_id === e.data.override.override_id))
          star.overrides.push(e.data.override);
        break;
      }
      case 'graph':
        s.graph = e.data;
        break;
      case 'error': {
        const { fatal, ...rest } = e.data;
        const problem = { seq: e.seq, ...rest };
        if (fatal) s.fatal = problem;
        else s.problems.push(problem);
        break;
      }
      case 'done':
        s.done = e.data.status;
        break;
    }
  }
  return s;
}

/** A run that ended leaves no stage at work. */
function stopStages(s: RunState) {
  for (const st of STAGES) if (s.stages[st] === 'begin') s.stages[st] = 'stopped';
}

/** `o` folded in outside the event log: an override the console saved itself, shown
 * before (or without) its `override` event; that event is then a repeat. */
export function withOverride(prev: RunState, o: Override): RunState {
  const key = prev.keyIndex.get(o.key) ?? o.key;
  const star = prev.stars.get(key);
  if (!star || star.overrides.some((x) => x.override_id === o.override_id)) return prev;
  const stars = new Map(prev.stars);
  stars.set(key, { ...star, overrides: [...star.overrides, o] });
  return { ...prev, stars };
}

/** A star's override in force: the latest one not revoked, or null. */
export function activeOverride(star: Star): Override | null {
  const revoked = new Set(star.overrides.filter((o) => o.decision === null).map((o) => o.revokes));
  for (let i = star.overrides.length - 1; i >= 0; i--) {
    const o = star.overrides[i]!;
    if (o.decision !== null && !revoked.has(o.override_id)) return o;
  }
  return null;
}

export type Screening = 'candidate' | Decision;

/** The decision in force: a person's override, else the model's verdict. */
export function screening(star: Star): Screening {
  return activeOverride(star)?.decision ?? star.verdict?.decision ?? 'candidate';
}

/** The stage shown in the header: the earliest one still at work. */
export function currentStage(s: RunState): StageName | null {
  return STAGES.find((st) => s.stages[st] === 'begin') ?? null;
}

export interface Counts {
  candidates: number;
  included: number;
  excluded: number;
  unscreened: number;
  /** Search hits, repeats included; papers the expansion added are not hits. */
  hits: number;
  /** Candidates the search found (the rest came from citation expansion). */
  searched: number;
  /** Candidates only the citation expansion found. */
  expanded: number;
}

export function counts(s: RunState): Counts {
  const c: Counts = {
    candidates: s.stars.size,
    included: 0,
    excluded: 0,
    unscreened: 0,
    hits: 0,
    searched: 0,
    expanded: 0,
  };
  for (const star of s.stars.values()) {
    const hits = star.found.filter((f) => 'query_id' in f).length;
    c.hits += hits;
    if (hits > 0) c.searched += 1;
    else c.expanded += 1;
    const d = screening(star);
    if (d === 'include') c.included += 1;
    else if (d === 'exclude') c.excluded += 1;
    else if (d === 'unscreened') c.unscreened += 1;
  }
  return c;
}

/** Whether the plan still waits for review: it arrived and searching has not begun. */
export function inReview(s: RunState): boolean {
  return (
    s.plan !== null && s.stages.search === 'pending' && (s.status === 'running' || s.status === 'paused')
  );
}

/** Which actions of hsi-flow.md section 3 the status allows. */
export function actions(s: RunState) {
  const live = s.status === 'running' || s.status === 'paused';
  return {
    pause: s.status === 'running',
    resume: s.status === 'paused',
    cancel: s.status === 'queued' || live,
    rerun: s.plan !== null && !inReview(s) && s.status !== 'queued',
  };
}
