// The report guide on Help: three steps, then the task's progress. The rules
// are in guide.ts; this draws them as the classic console's dialog did.
import { useEffect, useRef, useState, useSyncExternalStore } from 'react';
import { useT, type MessageKey } from '../i18n';
import { reportHref } from '../home/useHome';
import { useSignedIn } from '../session';
import { active, Guide, NO_DATA, STAGES } from './guide';

const STAGE_KEYS: Record<(typeof STAGES)[number], MessageKey> = {
  collect: 'guideCollect',
  triage: 'guideTriage',
  enrich: 'guideEnrich',
  digest: 'guideDigest',
};

export function ReportGuide({ topic, task, fresh }: { topic: string; task: string; fresh: boolean }) {
  const t = useT();
  const session = useSignedIn();
  const admin = session.user.role === 'admin';
  const [guide] = useState(() => new Guide(session.user.id));
  const s = useSyncExternalStore(guide.subscribe, () => guide.state);
  const title = useRef<HTMLHeadingElement>(null);

  useEffect(() => {
    void guide.open({ topic, task, fresh });
    return () => guide.close();
  }, [guide, topic, task, fresh]);

  // Each move between steps lands on the guide's heading.
  const move = (go: () => void) => {
    go();
    title.current?.focus();
  };

  const job = s.job;
  const busy = s.starting || active(job);
  const done = job?.state === 'succeeded' && !!job.result?.topic && !!job.result?.period;
  const settingsLink = admin && (
    <a className="btn" href="/settings">
      {t('settings')} →
    </a>
  );
  const topicsLink = (
    <a className="btn" href="/topics">
      {t('topicSettings')} →
    </a>
  );
  const shownTopic = job?.topic || s.topic;
  const label = s.topics.find((x) => x.id === shownTopic)?.label || shownTopic;

  let body;
  if (s.loading) {
    body = <p role="status">{t('loading')}</p>;
  } else if (s.step === 1) {
    body = (
      <>
        <h3>{t('guideModel')}</h3>
        <p>{t(admin ? 'firstModel' : 'modelManaged')}</p>
        <p className={s.model ? 'guide-ready' : 'topic-warning'}>
          {t(s.model ? 'llmReady' : admin ? 'llmIncomplete' : 'modelContact')}
        </p>
        {settingsLink}
      </>
    );
  } else if (s.step === 2) {
    const note: MessageKey = !s.topic
      ? 'firstChoose'
      : s.detail?.config && !s.detail.config.arxiv
        ? 'firstArticles'
        : guide.sourceReady()
          ? 'guideSourceReady'
          : 'guideSourceInvalid';
    body = (
      <>
        <h3>{t('guideTopic')}</h3>
        <div className="setting-field">
          <label htmlFor="guide-topic">{t('topic')}</label>
          <select
            id="guide-topic"
            value={s.topic}
            disabled={busy}
            onChange={(e) => guide.choose(e.target.value)}
          >
            <option value="">{t('firstChoose')}</option>
            {s.topics.map((x) => (
              <option key={x.id} value={x.id}>
                {x.label || x.id}
              </option>
            ))}
          </select>
        </div>
        <p>{t(note)}</p>
        {topicsLink}
      </>
    );
  } else {
    const noData = job?.error === NO_DATA;
    const outcome: MessageKey =
      s.statusError ||
      (s.interrupted
        ? 'guideInterrupted'
        : s.starting
          ? 'guideStarting'
          : job?.state === 'queued'
            ? 'taskQueued'
            : job?.state === 'cancelled'
              ? 'taskCancelled'
              : job?.state === 'running'
                ? 'guideRunning'
                : done
                  ? 'guideComplete'
                  : job
                    ? noData
                      ? 'guideNoData'
                      : 'guideFailed'
                    : s.observed?.requestId
                      ? 'taskUnknown'
                      : 'guideStartNote');
    const index = job ? (STAGES as readonly string[]).indexOf(job.stage) : -1;
    body = (
      <>
        <h3>{t('guideGenerate')}</h3>
        <p>
          <b>{label}</b>
        </p>
        <p id="generation-status" role="status" aria-live="polite" aria-atomic="true">
          {t(outcome, { topic: label })}
          {job?.state === 'queued' && ` · ${t('taskPosition', { n: job.position ?? '—' })}`}
        </p>
        {job && !s.interrupted && (
          <ol className="generation-stages">
            {STAGES.map((stage, i) => {
              const state: MessageKey =
                job.state === 'succeeded' || i < index
                  ? 'guideDone'
                  : i === index
                    ? active(job)
                      ? 'guideCurrent'
                      : 'guideStopped'
                    : 'guideWaiting';
              return (
                <li key={stage} aria-current={i === index && job.state === 'running' ? 'step' : undefined}>
                  <span>{t(STAGE_KEYS[stage])}</span>
                  <small>{t(state)}</small>
                </li>
              );
            })}
          </ol>
        )}
        {busy && (
          <>
            <p className="setting-hint">{t('guideBackground')}</p>
            <a className="btn" href="/tasks">
              {t('tasks')}
            </a>
          </>
        )}
        {done ? (
          <a
            className="btn primary"
            href={reportHref({ topic_id: job.result!.topic, period: job.result!.period }, null)}
          >
            {t('guideOpenReport')} →
          </a>
        ) : (
          s.interrupted && (
            <a className="btn" href={`/reports?topic=${encodeURIComponent(s.topic)}`}>
              {t('guideViewReports')} →
            </a>
          )
        )}
        {job?.state === 'failed' && (
          <div className="settings-actions">
            {noData && admin && job.owner === session.user.id && (
              <a className="btn" href={`/triage?topic=${encodeURIComponent(job.topic)}`}>
                {t('guideReviewTriage')} →
              </a>
            )}
            {topicsLink}
            {settingsLink}
          </div>
        )}
      </>
    );
  }

  return (
    <section id="report-guide" className="card help-guide" aria-labelledby="guide-title">
      <header className="guide-head">
        <div>
          <p className="eyebrow">{t('guideStep', { n: s.step })}</p>
          <h2 id="guide-title" ref={title} tabIndex={-1}>
            {t('helpGuide')}
          </h2>
        </div>
      </header>
      <p>{t('guideIntro')}</p>
      <ol className="guide-steps">
        {(['guideModel', 'guideTopic', 'guideGenerate'] as const).map((key, i) => (
          <li key={key} aria-current={s.step === i + 1 ? 'step' : undefined}>
            <span>{i + 1}</span>
            {t(key)}
          </li>
        ))}
      </ol>
      <div className="guide-body">
        {body}
        {s.error && (
          <p role="alert" className="error">
            {t(s.error)}
          </p>
        )}
      </div>
      <footer className="guide-actions">
        {s.step > 1 && !busy && (
          <button type="button" className="btn" onClick={() => move(() => guide.back())}>
            {t('guideBack')}
          </button>
        )}
        <button
          type="button"
          className="btn"
          disabled={s.loading || s.starting}
          onClick={() => void guide.checkAgain()}
        >
          {t('guideCheck')}
        </button>
        {s.step < 3 ? (
          <button
            type="button"
            className="btn primary"
            disabled={!guide.canNext()}
            onClick={() => move(() => guide.next())}
          >
            {t('guideNext')} →
          </button>
        ) : done || active(job) ? (
          <button type="button" className="btn" onClick={() => move(() => guide.again())}>
            {t(active(job) ? 'taskNew' : 'guideAgain')}
          </button>
        ) : (
          <button
            type="button"
            className="btn primary"
            disabled={!guide.canStart()}
            onClick={() => void guide.start()}
          >
            {t(s.starting ? 'guideStarting' : 'guideGenerate')}
          </button>
        )}
      </footer>
    </section>
  );
}
