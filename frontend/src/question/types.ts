// The question-research contracts as the pages read them; the schemas are in
// docs/contracts/question. Titles, abstracts and reasons come from outside and
// are only ever rendered as text.

export type Lang = 'zh' | 'en';
export type StageName = 'plan' | 'search' | 'screen' | 'expand' | 'graph';
export const STAGES: StageName[] = ['plan', 'search', 'screen', 'expand', 'graph'];

export interface Paper {
  key: string;
  keys: string[];
  title: string;
  year?: number | null;
  authors?: string[];
  venue?: string | null;
  type?: string | null;
  cited_by_count?: number;
  abstract?: string | null;
  keywords?: string[];
  url?: string | null;
  in_archive?: boolean;
}

export type ConceptRole = 'subject' | 'method' | 'context' | 'outcome' | 'landmark';

export interface Concept {
  role: ConceptRole;
  terms: string[];
}

export interface PlanQuery {
  id: string;
  query: string;
  layer: string;
  priority: number;
  limit?: number;
}

/** `question_plan@1`. */
export interface Plan {
  schema: 'question_plan@1';
  run_id: string;
  version: number;
  parent_version?: number | null;
  created_at: string;
  source: 'model' | 'fallback' | 'human';
  question: string;
  lang: Lang;
  engine?: 'openalex' | 'arxiv' | 'plain';
  reformulation: string;
  concepts?: Concept[];
  subject_terms: string[];
  method_terms: string[];
  outcome_terms: string[];
  keyword_layers: Record<string, string[]>;
  queries: PlanQuery[];
  year_from?: number | null;
  year_to?: number | null;
  criteria: { include: string[]; exclude: string[] };
  rationale: string;
  llm?: {
    model: string;
    input_tokens?: number;
    output_tokens?: number;
    duration_ms?: number;
    calls?: number;
  };
}

export type Decision = 'include' | 'exclude' | 'unscreened';

/** `screen_verdict@1`. */
export interface Verdict {
  key: string;
  decision: Decision;
  reason: string;
  score?: number | null;
  model: string;
  batch_id?: string | null;
  plan_version?: number;
  created_at: string;
}

/** `screen_override@1`; `decision: null` revokes the override named in `revokes`. */
export interface Override {
  override_id: string;
  key: string;
  decision: 'include' | 'exclude' | null;
  note?: string | null;
  actor: string;
  revokes?: string | null;
  created_at: string;
}

export type RunStatus = 'queued' | 'running' | 'paused' | 'cancelled' | 'failed' | 'done';
export type DoneStatus = 'completed' | 'partial' | 'cancelled' | 'failed';

interface Envelope<T extends string, D> {
  schema: 'question_event@1';
  run_id: string;
  seq: number;
  at: string;
  type: T;
  data: D;
}

/** `question_event@1`. */
export type QuestionEvent =
  | Envelope<'state', { state: RunStatus }>
  | Envelope<'stage', { stage: StageName; state: 'begin' | 'end' | 'skipped' }>
  | Envelope<'plan', { plan: Plan }>
  | Envelope<'hit', { query_id: string; rank: number; paper: Paper; new: boolean }>
  | Envelope<'dedup', { kept: string; merged: string[] }>
  | Envelope<'verdict', { verdict: Verdict }>
  | Envelope<'override', { override: Override }>
  | Envelope<'expand', { from_key: string; via: 'cites' | 'cited_by'; paper: Paper; new: boolean }>
  | Envelope<'citations', { scope: 'archive' | 'all'; looked_up: number; edges: number }>
  | Envelope<'graph', { fingerprint: string; nodes: number; edges: number; communities: number }>
  | Envelope<
      'error',
      {
        stage: StageName;
        message: string;
        fatal: boolean;
        query_id?: string;
        batch_id?: string;
        key?: string;
        code?: ProblemCode;
        detail?: string;
      }
    >
  | Envelope<'done', { status: DoneStatus; metrics?: unknown }>;

export type EventType = QuestionEvent['type'];
export const EVENT_TYPES: EventType[] = [
  'state',
  'stage',
  'plan',
  'hit',
  'dedup',
  'verdict',
  'override',
  'expand',
  'citations',
  'graph',
  'error',
  'done',
];

/** What kind of failure an `error` event is (docs/question/api.md#problem-codes). */
export type ProblemCode =
  | 'essaies_unreachable'
  | 'essaies_down'
  | 'essaies_not_set'
  | 'openalex_quota'
  | 'query_failed'
  | 'search_failed'
  | 'citations_failed'
  | 'essaies_old'
  | 'openalex_unreachable'
  | 'openalex_down'
  | 'openalex_rejected'
  | 'model_unreachable'
  | 'model_not_configured'
  | 'model_rejected'
  | 'model_failed'
  | 'plan_fallback'
  | 'stage_failed';

/** Why a failed run failed: its fatal error. */
export interface Failure {
  stage: StageName;
  code: ProblemCode | null;
  message: string;
  /** The upstream's own error, without the advice `message` adds. */
  detail?: string | null;
}

/** The search engines a run can use (isbe.question.compose). */
export type Engine = 'openalex' | 'arxiv';
export const ENGINES: Engine[] = ['openalex', 'arxiv'];

/** The plan fields a person can edit (planning.md); edited concepts recompose the queries. */
export type PlanChanges = Partial<Pick<Plan, 'concepts' | 'queries' | 'criteria' | 'year_from' | 'year_to'>>;

/** The expand stage's options of an ask (docs/question/api.md); absent ones take the server's defaults. */
export interface Expansion {
  expand?: boolean;
  expand_per_paper?: number;
  expand_total?: number;
  citations_full?: number;
}

/** The run object of docs/question/api.md: for lists, headers and the first paint. */
export interface Run {
  run_id: string;
  question: string;
  lang: Lang;
  status: RunStatus;
  stage: StageName | null;
  degraded: boolean;
  problems: number;
  /** Null unless the run failed; absent from servers before #90. */
  failure?: Failure | null;
  /** The first coded problem of a run that went on and ended partial; else null. */
  problem?: Failure | null;
  plan_version: number | null;
  /** `ends_at` is null while the countdown is held. */
  review: { on: boolean; active: boolean; ends_at: string | null };
  options: {
    year_from: number | null;
    year_to: number | null;
    engine: Engine;
    rerun_of?: string;
  } & Expansion;
  pausing: boolean;
  cancelling: boolean;
  counts: {
    hits: number;
    candidates: number;
    included: number;
    excluded: number;
    unscreened: number;
    overridden: number;
  };
  last_seq: number;
  created_at: string;
  updated_at: string;
}
