// The question runs behind Home and Galaxies, refreshed while any run is
// still working so the galaxies fill in as papers arrive. A failed read is
// tried again, less often each time, until one succeeds.
import { useEffect, useState } from 'react';
import { listRuns } from '../question/client';
import type { Run } from '../question/types';
import { isActive } from './galaxies';

/** How often the list is read again while a run is working. */
export const POLL_MS = 4000;
/** The longest wait between tries after failures. */
export const RETRY_MAX_MS = 60_000;

export function useRuns(key?: unknown): { runs: Run[] | null; failed: boolean } {
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    let live = true;
    let timer: ReturnType<typeof setTimeout> | undefined;
    let fails = 0;
    const load = () =>
      listRuns().then(
        (list) => {
          if (!live) return;
          setRuns(list.runs);
          setFailed(false);
          fails = 0;
          if (list.runs.some(isActive)) timer = setTimeout(load, POLL_MS);
        },
        () => {
          if (!live) return;
          setFailed(true);
          setRuns((r) => r ?? []);
          fails += 1;
          timer = setTimeout(load, Math.min(RETRY_MAX_MS, POLL_MS * 2 ** (fails - 1)));
        },
      );
    void load();
    return () => {
      live = false;
      clearTimeout(timer);
    };
  }, [key]);
  return { runs, failed };
}
