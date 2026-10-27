// What Home shows besides the galaxies: the newest topic reports, and whether
// the services a first run needs are set up (`GET /api/home`, a slim view of
// the classic overview). Home reads it once; a failure can be retried.
import { useCallback, useEffect, useState } from 'react';
import { api, ApiError, withOwner } from '../api/client';

export interface ReportCover {
  topic_id: string;
  period: string;
  kind: string;
  created_at: string;
  papers: number;
  media: boolean;
  materials: number | null;
  /** The report's title; a survey's topic_id is a question run, not a topic. */
  label?: string | null;
}

export interface HomeView {
  topics: { id: string; label?: string }[];
  reports: ReportCover[];
  /** Absent keys are unknown (a member sees only `llm`). */
  /** `papers`: the paper source and whether it is set up (essaies needs its URL). */
  services: { papers?: { source: 'openalex' | 'essaies'; ready: boolean }; llm?: string | null };
}

export type HomeState =
  | { state: 'loading' }
  | { state: 'failed'; message: string | null; retry: () => void }
  | { state: 'ready'; home: HomeView };

const isHome = (v: unknown): v is HomeView =>
  !!v &&
  typeof v === 'object' &&
  Array.isArray((v as HomeView).topics) &&
  Array.isArray((v as HomeView).reports) &&
  !!(v as HomeView).services &&
  typeof (v as HomeView).services === 'object';

/** Home's reports and service flags for `owner`'s workspace. */
export function useHome(owner: string | null): HomeState {
  const [value, setValue] = useState<HomeView | { failed: string | null } | null>(null);
  const [attempt, setAttempt] = useState(0);
  useEffect(() => {
    let live = true;
    api.get<unknown>(withOwner('/api/home', owner)).then(
      (v) => {
        if (live) setValue(isHome(v) ? v : { failed: '' });
      },
      (e: unknown) => {
        // No answer at all (offline, server down): null, said as "could not be reached".
        if (live) setValue({ failed: e instanceof ApiError ? e.message : null });
      },
    );
    return () => {
      live = false;
    };
  }, [owner, attempt]);
  const retry = useCallback(() => {
    setValue(null);
    setAttempt((n) => n + 1);
  }, []);
  if (value === null) return { state: 'loading' };
  if ('failed' in value) return { state: 'failed', message: value.failed, retry };
  return { state: 'ready', home: value };
}

/** The classic Reports page for one report. */
export const reportHref = (r: Pick<ReportCover, 'topic_id' | 'period'>, owner: string | null) =>
  withOwner(`/reports?topic=${encodeURIComponent(r.topic_id)}&period=${encodeURIComponent(r.period)}`, owner);
