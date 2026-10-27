// The question page (/question/): the question box, its options, and the
// history of runs (docs/question/hsi-flow.md, section 5).
import { useEffect, useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router';
import { ApiError } from '../api/client';
import { useT } from '../i18n';
import { usePrefs } from '../prefs';
import { createRun, listRuns, REPLAY_PREFIX } from './client';
import { PROBLEM_KEYS } from './Reason';
import { ENGINE_KEYS, runStatusKey } from './RunHeader';
import { ENGINES, type Engine, type Lang, type Run } from './types';

const SAMPLE = {
  zh: '基于深度学习的雷达回波外推降水临近预报（0–2 小时）有哪些主要方法？',
  en: 'Which deep learning methods work best for precipitation nowcasting?',
};

const year = (v: string) => (/^\d{4}$/.test(v.trim()) ? Number(v.trim()) : null);

export function QuestionPage() {
  const t = useT();
  const { prefs } = usePrefs();
  const navigate = useNavigate();
  const [question, setQuestion] = useState('');
  const [lang, setLang] = useState<Lang>(prefs.lang);
  const [from, setFrom] = useState('');
  const [to, setTo] = useState('');
  const [review, setReview] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [runs, setRuns] = useState<Run[] | null>(null);
  const [engines, setEngines] = useState<Engine[]>(ENGINES);
  // '' is the server's default, named when the server says which it is.
  const [defaultEngine, setDefaultEngine] = useState<Engine | null>(null);
  const [engine, setEngine] = useState<Engine | ''>('');

  useEffect(() => {
    document.title = `ISBE · ${t('question')}`;
  });
  useEffect(() => {
    let live = true;
    listRuns().then(
      (list) => {
        if (!live) return;
        setRuns(list.runs);
        if (list.engines?.length) setEngines(list.engines.filter((e) => ENGINES.includes(e)));
        if (list.default_engine) setDefaultEngine(list.default_engine);
      },
      () => live && setRuns([]),
    );
    return () => {
      live = false;
    };
  }, []);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!question.trim()) return;
    setBusy(true);
    setError('');
    try {
      const id = await createRun({
        question: question.trim(),
        lang,
        year_from: year(from),
        year_to: year(to),
        review,
        ...(engine ? { engine } : {}),
      });
      navigate(`/question/runs/${encodeURIComponent(id)}`);
    } catch (err) {
      setError(err instanceof ApiError ? err.message : t('actionFailed'));
      setBusy(false);
    }
  };

  return (
    <>
      <header className="head">
        <h1 className="title">{t('questionTitle')}</h1>
      </header>
      <form className="card q-ask" onSubmit={(e) => void submit(e)}>
        <label className="q-field q-question">
          <span className="sr-only">{t('qQuestion')}</span>
          <textarea
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            placeholder={t('qAskPlaceholder')}
            rows={3}
            maxLength={2000}
          />
        </label>
        <div className="q-options">
          <fieldset className="q-field">
            <legend>{t('qYearRange')}</legend>
            <input
              aria-label={t('qYearFrom')}
              inputMode="numeric"
              value={from}
              onChange={(e) => setFrom(e.target.value)}
              placeholder="2012"
            />
            <span aria-hidden="true">–</span>
            <input
              aria-label={t('qYearTo')}
              inputMode="numeric"
              value={to}
              onChange={(e) => setTo(e.target.value)}
            />
          </fieldset>
          <label className="q-field">
            <span>{t('qRunLang')}</span>
            <select value={lang} onChange={(e) => setLang(e.target.value as Lang)}>
              <option value="zh">中文</option>
              <option value="en">English</option>
            </select>
          </label>
          <label className="q-field">
            <span>{t('qEngine')}</span>
            <select value={engine} onChange={(e) => setEngine(e.target.value as Engine | '')}>
              <option value="">
                {defaultEngine
                  ? t('qEngineDefaultIs', { engine: t(ENGINE_KEYS[defaultEngine]) })
                  : t('qEngineDefault')}
              </option>
              {engines.map((e) => (
                <option key={e} value={e}>
                  {t(ENGINE_KEYS[e])}
                </option>
              ))}
            </select>
          </label>
          <label className="q-check">
            <input type="checkbox" checked={review} onChange={(e) => setReview(e.target.checked)} />
            {t('qReviewPlan')}
          </label>
          <button type="submit" className="btn primary" disabled={busy || !question.trim()}>
            {t('qAsk')}
          </button>
        </div>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
      </form>
      <section className="card">
        <div className="card-head">
          <h2>{t('qHistory')}</h2>
        </div>
        {runs === null ? (
          <p className="muted">{t('loading')}</p>
        ) : runs.length === 0 ? (
          <p className="muted">
            {t('qHistoryEmpty')}{' '}
            <button type="button" className="link" onClick={() => setQuestion(SAMPLE[lang])}>
              {SAMPLE[lang]}
            </button>
          </p>
        ) : (
          <ul className="q-history">
            {runs.map((r) => (
              <li key={r.run_id}>
                <Link to={`/question/runs/${encodeURIComponent(r.run_id)}`}>{r.question}</Link>
                <span className={r.status === 'failed' || r.degraded ? 'chip warn' : 'chip plain'}>
                  {r.status === 'done' && r.degraded
                    ? t('qDoneDegraded', { n: r.problems })
                    : t(runStatusKey(r))}
                </span>
                {r.status === 'failed' && r.failure?.code && PROBLEM_KEYS[r.failure.code] && (
                  <span className="muted">{t(PROBLEM_KEYS[r.failure.code].what)}</span>
                )}
                {r.counts?.candidates !== undefined && (
                  <span className="muted">
                    {t('qCountsShort', { n: r.counts.candidates, i: r.counts.included ?? 0 })}
                  </span>
                )}
                <time className="muted" dateTime={r.created_at}>
                  {r.created_at.slice(0, 10)}
                </time>
              </li>
            ))}
          </ul>
        )}
        {import.meta.env.DEV && (
          <p className="muted">
            <Link to={`/question/runs/${REPLAY_PREFIX}radar-nowcasting-dl`}>{t('qReplay')}</Link>
          </p>
        )}
      </section>
    </>
  );
}
