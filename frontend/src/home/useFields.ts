// Each galaxy's papers and citations, settled into a field (field.ts). Only
// the galaxies the person can see well enough to show stars are loaded, the
// selected one first and one at a time; the settling runs in a worker so the
// page stays responsive. Fields are kept for the session, so coming back to
// Home does not read every run again. A running galaxy is settled again as
// papers arrive, at most every REFRESH_MS, and a load in flight is never
// dropped for a newer count, so it always finishes. A failed load is tried
// again later.
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { layoutField, type Field, type FieldPaper } from './field';
import type { Galaxy } from './galaxies';

/** A run's star field as `/field` gives it: no titles or abstracts. */
interface FieldReply {
  papers: { key: string; decision: string | null; cited: number; queries: string[] }[];
  citations: [string, string][];
}

const base = (id: string) => `/api/question/runs/${encodeURIComponent(id)}`;

/** A running galaxy is settled again at most this often. */
export const REFRESH_MS = 15_000;
/** The first retry after a failed load; it doubles up to a minute. */
export const RETRY_MS = 5_000;

/** What a galaxy's field depends on; when it changes the field is redone. */
export const version = (g: Galaxy) => `${g.papers}:${g.included}:${g.run.counts.excluded}:${g.run.status}`;

let worker: Worker | null | undefined;
let asked = 0;
const waiting = new Map<number, (field: Field | null) => void>();

/** Settle in the worker when there is one, else here. */
export function settle(papers: FieldPaper[], citations: [string, string][]): Promise<Field> {
  if (worker === undefined) {
    try {
      worker =
        typeof Worker === 'undefined'
          ? null
          : new Worker(new URL('./field.worker.ts', import.meta.url), { type: 'module' });
    } catch {
      worker = null;
    }
    worker?.addEventListener('message', (e: MessageEvent<{ id: number; field: Field }>) => {
      waiting.get(e.data.id)?.(e.data.field);
      waiting.delete(e.data.id);
    });
    worker?.addEventListener('error', () => {
      // A worker that cannot start: settle here from now on.
      worker = null;
      for (const done of waiting.values()) done(null);
      waiting.clear();
    });
  }
  const w = worker;
  if (!w) return Promise.resolve(layoutField(papers, citations));
  const id = ++asked;
  return new Promise<Field | null>((done) => {
    waiting.set(id, done);
    w.postMessage({ id, papers, citations });
  }).then((field) => field ?? layoutField(papers, citations));
}

export async function loadField(id: string): Promise<Field> {
  // Citation edges are empty until the run has gathered them (#50).
  const reply = await api.get<FieldReply | null>(`${base(id)}/field`, { missingOk: true });
  return settle(
    (reply?.papers ?? []).map((row) => ({
      key: row.key,
      included: row.decision === 'include',
      cited: row.cited,
      queries: row.queries,
    })),
    reply?.citations ?? [],
  );
}

interface Kept {
  v: string;
  field: Field;
  at: number;
}

/** Settled fields for this session, by run id. */
const kept = new Map<string, Kept>();
/** Failed loads: how many in a row, and when to try again. */
const failed = new Map<string, { n: number; at: number }>();

/** Forget everything kept (tests). */
export function forgetFields() {
  inFlight = null;
  kept.clear();
  failed.clear();
}

/** The next galaxy to load among `wanted` (in order), or how long until one is due. */
export function nextLoad(
  galaxies: Galaxy[],
  wanted: string[],
  now: number,
): { id: string; v: string } | { wait: number } | null {
  let wait = Infinity;
  for (const id of wanted) {
    const g = galaxies.find((x) => x.id === id);
    if (!g || g.papers <= 0) continue;
    const v = version(g);
    const have = kept.get(id);
    if (have?.v === v) continue;
    let due = 0;
    const f = failed.get(id);
    if (f) due = f.at;
    else if (have && g.busy) due = have.at + REFRESH_MS;
    if (due <= now) return { id, v };
    wait = Math.min(wait, due - now);
  }
  return wait < Infinity ? { wait } : null;
}

/** The load in flight, shared by every Home, so leaving and coming back never loads twice. */
let inFlight: Promise<void> | null = null;

export function useFields(galaxies: Galaxy[], wanted: string[]): Map<string, Field> {
  const snapshot = () => new Map([...kept].map(([id, f]) => [id, f.field]));
  const [fields, setFields] = useState(snapshot);
  // Bumped when a load lands, or when a wait for a due galaxy is over.
  const [tick, bump] = useState(0);
  const wantedKey = wanted.join(' ');
  useEffect(() => {
    let live = true;
    const landed = () => {
      if (!live) return;
      setFields(snapshot());
      bump((n) => n + 1);
    };
    if (inFlight) {
      void inFlight.then(landed);
      return () => {
        live = false;
      };
    }
    const next = nextLoad(galaxies, wantedKey ? wantedKey.split(' ') : [], Date.now());
    if (!next) return;
    if ('wait' in next) {
      const timer = setTimeout(landed, next.wait);
      return () => {
        live = false;
        clearTimeout(timer);
      };
    }
    // Not cancelled when the counts move on: the result is kept under the
    // version it was asked for, and a newer one is loaded after it.
    const { id, v } = next;
    inFlight = loadField(id)
      .then(
        (field) => {
          kept.set(id, { v, field, at: Date.now() });
          failed.delete(id);
        },
        () => {
          const n = (failed.get(id)?.n ?? 0) + 1;
          failed.set(id, { n, at: Date.now() + Math.min(60_000, RETRY_MS * 2 ** (n - 1)) });
        },
      )
      .finally(() => {
        inFlight = null;
      });
    void inFlight.then(landed);
    return () => {
      live = false;
    };
  }, [galaxies, wantedKey, tick]);
  return fields;
}
