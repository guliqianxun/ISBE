// Home's universe as data: every question run is a galaxy, laid out on a
// golden-angle spiral oldest first (so a new run never moves the others), and
// the "Needs you" list is read off the runs' states. Pure, so it is tested
// without a canvas.
import type { Run } from '../question/types';

export interface Galaxy {
  id: string;
  run: Run;
  name: string;
  /** Stable per run: the hue of its stars. */
  hue: number;
  x: number;
  y: number;
  /** World units; grows with the papers gathered. */
  r: number;
  papers: number;
  included: number;
  /** Still gathering or screening. */
  busy: boolean;
}

export type NeedKind = 'plan' | 'failed' | 'problems' | 'unjudged' | 'paused' | 'done';

export interface Need {
  kind: NeedKind;
  run: Run;
  /** How many: problems, unjudged papers, or papers found. */
  n: number;
}

/** FNV-1a over the string. */
export function hash(s: string): number {
  let h = 2166136261;
  for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 16777619);
  return h >>> 0;
}

/** A small seeded generator (mulberry32), so a galaxy's stars sit still across renders. */
export function rng(seed: number): () => number {
  let a = seed >>> 0;
  return () => {
    a = (a + 0x6d2b79f5) >>> 0;
    let t = a;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}

export const radius = (papers: number) => 48 + Math.sqrt(Math.max(papers, 20)) * 2.6;

/** A short name from the question: its first clause, at most `max` characters. */
export function shortName(question: string, max = 42): string {
  const q = question.trim().replace(/\s+/g, ' ');
  const clause = q.split(/[?？。;；]/)[0]!.trim() || q;
  return clause.length > max ? `${clause.slice(0, max - 1).trimEnd()}…` : clause;
}

const ACTIVE = new Set(['queued', 'running', 'paused']);
export const isActive = (run: Run) => ACTIVE.has(run.status);

const GOLDEN = Math.PI * (3 - Math.sqrt(5));

export function layout(runs: Run[]): Galaxy[] {
  const sorted = [...runs].sort(
    (a, b) => a.created_at.localeCompare(b.created_at) || a.run_id.localeCompare(b.run_id),
  );
  const out: Galaxy[] = [];
  sorted.forEach((run, i) => {
    const papers = run.counts.candidates;
    const r = radius(papers);
    const g: Galaxy = {
      id: run.run_id,
      run,
      name: shortName(run.question),
      hue: hash(run.run_id) % 360,
      x: 0,
      y: 0,
      r,
      papers,
      included: run.counts.included,
      busy: run.status === 'queued' || run.status === 'running',
    };
    // Walk outward along the spiral until the galaxy clears every earlier one.
    for (let k = i * 3; ; k++) {
      const d = 150 * Math.sqrt(k);
      const a = k * GOLDEN;
      const x = Math.cos(a) * d;
      const y = Math.sin(a) * d * 0.72;
      if (out.every((o) => Math.hypot(o.x - x, (o.y - y) / 0.8) > (o.r + r) * 1.35 + 40)) {
        g.x = x;
        g.y = y;
        break;
      }
    }
    out.push(g);
  });
  return out;
}

/** Runs finished within this long still show as "just finished". */
export const FRESH_MS = 24 * 3600 * 1000;

export function needs(runs: Run[], now = Date.now()): Need[] {
  const out: Need[] = [];
  for (const run of runs) {
    if (run.review.active) out.push({ kind: 'plan', run, n: 0 });
    else if (run.status === 'failed') out.push({ kind: 'failed', run, n: run.problems });
    else if (run.status === 'paused') out.push({ kind: 'paused', run, n: 0 });
    else if (run.status === 'done') {
      // Unjudged papers come first; the card also says why (`run.problem`), which may be the model's absence.
      if (run.counts.unscreened > 0) out.push({ kind: 'unjudged', run, n: run.counts.unscreened });
      else if (run.problems > 0) out.push({ kind: 'problems', run, n: run.problems });
      else if (now - Date.parse(run.updated_at) < FRESH_MS)
        out.push({ kind: 'done', run, n: run.counts.candidates });
    }
  }
  const order: NeedKind[] = ['plan', 'failed', 'paused', 'unjudged', 'problems', 'done'];
  return out.sort(
    (a, b) =>
      order.indexOf(a.kind) - order.indexOf(b.kind) || b.run.updated_at.localeCompare(a.run.updated_at),
  );
}
