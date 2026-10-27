// The run header: question, status, stage, counts, the problems chip and the
// Pause / Resume / Cancel buttons, Try again on a failed run, and Delete once the
// run has come to rest. A button reads "Pausing…" until the `state`
// event confirms it, because the worker stops at the next query or batch.
import { useState } from 'react';
import { ApiError } from '../api/client';
import { Dialog } from '../components/Dialog';
import { useT, type MessageKey } from '../i18n';
import { textLang } from './lang';
import { Reason, UpstreamReason } from './Reason';
import { actions, counts, currentStage, type Problem, type RunState, type StageState } from './run';
import type { Engine, Run, RunStatus, StageName } from './types';
import { STAGES } from './types';

export const STATUS_KEYS: Record<RunStatus | 'empty', MessageKey> = {
  empty: 'loading',
  queued: 'qQueued',
  running: 'qRunning',
  paused: 'qPaused',
  cancelled: 'qCancelled',
  failed: 'qFailed',
  done: 'qDone',
};

/** A run's status as a person reads it: a plan under review waits for them, it is
 * not "running". */
export function runStatusKey(run: Pick<Run, 'status'> & { review?: Run['review'] }): MessageKey {
  return run.review?.active ? 'qAwaitingPlan' : STATUS_KEYS[run.status];
}

export const STAGE_KEYS: Record<StageName, MessageKey> = {
  plan: 'qStagePlan',
  search: 'qStageSearch',
  screen: 'qStageScreen',
  expand: 'qStageExpand',
  graph: 'qStageGraph',
};

/** Each stage's state in words, for screen readers (the dot shows it on screen). */
const STAGE_STATE_KEYS: Record<StageState | 'paused', MessageKey> = {
  pending: 'qStagePending',
  begin: 'qStageBegin',
  end: 'qStageEnd',
  skipped: 'qStageSkipped',
  stopped: 'qStageStopped',
  paused: 'qStagePaused',
};

export const ENGINE_KEYS: Record<Engine, MessageKey> = {
  openalex: 'qEngineOpenalex',
  arxiv: 'qEngineArxiv',
};

export interface RunControls {
  pause(): Promise<void>;
  resume(): Promise<void>;
  cancel(): Promise<void>;
}

const ENDED: readonly string[] = ['done', 'failed', 'cancelled'];

export function RunHeader({
  state,
  controls,
  engine,
  question,
  onDelete,
  onTryAgain,
  reviewing = false,
}: {
  state: RunState;
  controls: RunControls;
  /** What was asked (the run object's `question`): the title before a plan exists. */
  question?: string;
  /** Ask the same question again as a new run; offered once the run has failed. */
  onTryAgain?: () => Promise<void>;
  /** Delete the run; the button shows once the run has ended. */
  onDelete?: () => Promise<void>;
  /** The run's engine when no plan names one yet (the run object's `options.engine`). */
  engine?: Engine;
  /** The plan waits for the person: nothing runs until they start it. */
  reviewing?: boolean;
}) {
  const t = useT();
  // What was asked for, and in which status: it is pending until the status moves.
  const [request, setRequest] = useState<{ what: 'pause' | 'resume' | 'cancel'; from: string } | null>(null);
  const asked = request && request.from === state.status ? request.what : null;
  const [confirm, setConfirm] = useState(false);
  const [confirmDelete, setConfirmDelete] = useState(false);
  const [deleting, setDeleting] = useState(false);
  const [retrying, setRetrying] = useState(false);
  const [showProblems, setShowProblems] = useState(false);
  // What failed and, for a refused Try again, the server's own words: set together,
  // so a reason never outlives the failure it explains.
  const [failure, setFailure] = useState({ error: '', refused: '' });
  const { error, refused } = failure;
  const setError = (error: string, refused = '') => setFailure({ error, refused });
  const can = actions(state);
  const c = counts(state);
  const stage = currentStage(state);
  // A plan without `engine` was written for OpenAlex (question_plan@1).
  const planEngine = state.plan ? (state.plan.engine ?? 'openalex') : undefined;
  const shownEngine =
    planEngine === 'plain' ? undefined : ((state.plan?.engine as Engine | undefined) ?? engine ?? planEngine);

  const act = async (what: 'pause' | 'resume' | 'cancel') => {
    setRequest({ what, from: state.status });
    setError('');
    try {
      await controls[what]();
    } catch {
      setRequest(null);
      setError(t('actionFailed'));
    }
  };

  const remove = async () => {
    setConfirmDelete(false);
    setDeleting(true);
    setError('');
    try {
      await onDelete?.();
    } catch (err) {
      setDeleting(false);
      setError(t(err instanceof ApiError && err.status === 409 ? 'qDeleteBusy' : 'actionFailed'));
    }
  };

  const tryAgain = async () => {
    setRetrying(true);
    setError('');
    try {
      await onTryAgain?.();
    } catch (err) {
      setRetrying(false);
      setError(t('actionFailed'), err instanceof ApiError ? err.message : '');
    }
  };

  const title = state.plan?.question ?? question ?? '';
  // A paused run's stage is not at work: it waits for Resume.
  const paused = state.status === 'paused';
  const problems = state.problems.length;
  const status = reviewing
    ? t('qAwaitingPlan')
    : state.status === 'done' && (state.done === 'partial' || problems)
      ? t('qDoneDegraded', { n: problems })
      : t(STATUS_KEYS[state.status]);

  return (
    <header className="q-run-head">
      <h1 className="q-run-question" lang={title ? textLang(title) : undefined}>
        {title}
      </h1>
      <div className="q-run-meta">
        <span
          className={`chip ${state.status === 'failed' ? 'danger' : state.status === 'done' ? 'solid' : ''}`}
          role="status"
        >
          {status}
        </span>
        {stage && !reviewing && (
          <span className="chip plain">
            {paused ? `${t(STAGE_KEYS[stage])} · ${t('qStagePaused')}` : t(STAGE_KEYS[stage])}
          </span>
        )}
        {shownEngine && (
          <span className="chip plain" title={t('qEngine')}>
            {t(ENGINE_KEYS[shownEngine])}
          </span>
        )}
        {problems > 0 && (
          <button type="button" className="chip warn q-chip-button" onClick={() => setShowProblems(true)}>
            {t('qProblems', { n: problems })}
          </button>
        )}
        <span className="muted q-counts">
          {t('qCounts', { n: c.candidates, i: c.included, x: c.excluded })}
        </span>
        <span className="q-spacer" />
        {can.pause && !reviewing && (
          <button
            type="button"
            className="btn"
            disabled={asked === 'pause'}
            onClick={() => void act('pause')}
          >
            {asked === 'pause' ? t('qPausing') : t('qPause')}
          </button>
        )}
        {can.resume && (
          <button
            type="button"
            className="btn"
            disabled={asked === 'resume'}
            onClick={() => void act('resume')}
          >
            {t('qResume')}
          </button>
        )}
        {can.cancel && (
          <button
            type="button"
            className="btn"
            disabled={asked === 'cancel'}
            onClick={() => setConfirm(true)}
          >
            {asked === 'cancel' ? t('qCancelling') : t('qCancel')}
          </button>
        )}
        {onTryAgain && state.status === 'failed' && (
          <button type="button" className="btn primary" disabled={retrying} onClick={() => void tryAgain()}>
            {t('qTryAgain')}
          </button>
        )}
        {onDelete && ENDED.includes(state.status) && (
          <button type="button" className="btn" disabled={deleting} onClick={() => setConfirmDelete(true)}>
            {t('qDelete')}
          </button>
        )}
      </div>
      <ol className="q-stages" aria-label={t('progress')}>
        {/* The graph stage is not built yet (#51): the server reports it skipped, so
            it is left out rather than shown as a step that never happens. */}
        {STAGES.filter(
          (s) => s !== 'graph' || state.stages.graph === 'begin' || state.stages.graph === 'end',
        ).map((s) => {
          // Under review the plan is written: the step is done, the search waits for the person.
          const at = reviewing && s === 'plan' ? 'end' : state.stages[s];
          const stage = paused && at === 'begin' ? 'paused' : at;
          return (
            <li key={s} data-state={stage}>
              <span>{t(STAGE_KEYS[s])}</span>
              <span className="sr-only">{t(STAGE_STATE_KEYS[stage])}</span>
            </li>
          );
        })}
      </ol>
      {error && (
        <p className="error" role="alert">
          {error}
          {refused && (
            <>
              {' '}
              <UpstreamReason text={refused} />
            </>
          )}
        </p>
      )}
      {state.fatal && (
        <p className="error" role="alert">
          <Reason code={state.fatal.code} message={state.fatal.message} detail={state.fatal.detail} />
        </p>
      )}
      <Dialog
        open={confirm}
        title={t('qCancel')}
        onClose={() => setConfirm(false)}
        actions={
          <>
            <button type="button" className="btn" onClick={() => setConfirm(false)}>
              {t('closeDialog')}
            </button>
            <button
              type="button"
              className="btn primary"
              onClick={() => {
                setConfirm(false);
                void act('cancel');
              }}
            >
              {t('qCancel')}
            </button>
          </>
        }
      >
        <p>{t('qCancelConfirm')}</p>
      </Dialog>
      <Dialog
        open={confirmDelete}
        title={t('qDelete')}
        onClose={() => setConfirmDelete(false)}
        actions={
          <>
            <button type="button" className="btn" onClick={() => setConfirmDelete(false)}>
              {t('qKeepRun')}
            </button>
            <button type="button" className="btn danger" onClick={() => void remove()}>
              {t('qDelete')}
            </button>
          </>
        }
      >
        <p>{t('qDeleteConfirm')}</p>
      </Dialog>
      <Dialog
        open={showProblems}
        title={t('qProblems', { n: problems })}
        onClose={() => setShowProblems(false)}
      >
        <ul className="q-problems">
          {grouped(state.problems).map(({ problem: p, times }) => (
            <li key={p.seq}>
              <span className="chip plain">{t(STAGE_KEYS[p.stage])}</span>{' '}
              {times > 1 && <span className="chip plain">{t('qTimes', { n: times })}</span>}{' '}
              <Reason code={p.code} message={p.message} detail={p.detail} />
            </li>
          ))}
        </ul>
      </Dialog>
    </header>
  );
}

/** Problems in order, a coded one that repeats in its stage (one per failed batch)
 * shown once with how often, and its latest words; failed queries stay apart. */
export function grouped(problems: Problem[]): { problem: Problem; times: number }[] {
  const out: { problem: Problem; times: number }[] = [];
  const at = new Map<string, number>();
  for (const p of problems) {
    // A failed query is its own line: the person may rewrite that query.
    const key = p.code ? `${p.stage}:${p.code}:${p.query_id ?? ''}` : null;
    const i = key === null ? undefined : at.get(key);
    if (i === undefined) {
      if (key !== null) at.set(key, out.length);
      out.push({ problem: p, times: 1 });
    } else {
      const g = out[i];
      if (g) out[i] = { problem: { ...p, seq: g.problem.seq }, times: g.times + 1 };
    }
  }
  return out;
}
