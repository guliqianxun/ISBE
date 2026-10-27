// The selected galaxy's map (focus.ts), read from its run's graph in the slim
// `form=map` shape. Only the selected galaxy is loaded, and only once its run
// is done. A map is kept for the session so selecting the galaxy again draws
// at once, but the graph is read again on every selection (and when the
// run's counts move), since a rebuild changes it without changing the counts.
// A run with no graph yet keeps its star field and is asked again next time.
import { useEffect, useState } from 'react';
import { api } from '../api/client';
import { focusMap, fromWire, type FocusMap, type MapWire } from './focus';
import type { Galaxy } from './galaxies';
import { version } from './useFields';

const kept = new Map<string, FocusMap>();

/** Forget every map (tests). */
export function forgetFocus() {
  kept.clear();
}

export async function loadFocus(id: string): Promise<FocusMap | null> {
  const got = await api.get<{ graph: MapWire; stale: boolean } | null>(
    `/api/question/runs/${encodeURIComponent(id)}/graph?form=map`,
    { missingOk: true },
  );
  return got && got.graph.papers.length ? focusMap(fromWire(got.graph)) : null;
}

export function useFocus(g: Galaxy | null): FocusMap | null {
  const [, landed] = useState(0);
  const id = g && !g.busy ? g.id : null;
  const v = g ? version(g) : '';
  useEffect(() => {
    if (!id) return;
    let live = true;
    loadFocus(id).then(
      (map) => {
        if (map) kept.set(id, map);
        else kept.delete(id);
        if (live) landed((n) => n + 1);
      },
      // Unreadable: whatever was drawn stays, and the next selection asks again.
      () => undefined,
    );
    return () => {
      live = false;
    };
  }, [id, v]);
  return id ? (kept.get(id) ?? null) : null;
}
