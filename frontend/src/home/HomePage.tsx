// Home (/): the universe of galaxies, one per question, with a preview card
// for the selected one, and beside it the "Needs you" list and the newest
// topic reports. With no galaxies yet, the sky holds a first-run guide. The
// selection is in the address (`?g=<run id>`), so the ask bar can land on a
// new galaxy.
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router';
import { withOwner } from '../api/client';
import { useT, type MessageKey } from '../i18n';
import { usePrefs } from '../prefs';
import { Reason, UpstreamReason } from '../question/Reason';
import { runStatusKey } from '../question/RunHeader';
import { useOwner } from '../workspace';
import { layout, needs, type Galaxy, type Need, type NeedKind } from './galaxies';
import { color, type Box } from './sky';
import { Universe } from './Universe';
import { useFields } from './useFields';
import { useFocus } from './useFocus';
import { useHealth } from './useHealth';
import { reportHref, useHome, type HomeState } from './useHome';
import { useRuns } from './useRuns';
import { Welcome } from './Welcome';

const runPath = (id: string) => `/question/runs/${encodeURIComponent(id)}`;

const NEED_KEYS: Record<NeedKind, { kind: MessageKey; text: MessageKey; act: MessageKey }> = {
  plan: { kind: 'needPlan', text: 'needPlanText', act: 'needPlanAct' },
  failed: { kind: 'needFailed', text: 'needFailedText', act: 'needOpen' },
  paused: { kind: 'needPaused', text: 'needPausedText', act: 'needOpen' },
  unjudged: { kind: 'needUnjudged', text: 'needUnjudgedText', act: 'needScreenAct' },
  problems: { kind: 'needProblems', text: 'needProblemsText', act: 'needOpen' },
  done: { kind: 'needDone', text: 'needDoneText', act: 'needOpen' },
};

export function HomePage() {
  const t = useT();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const selected = params.get('g');
  // A new galaxy from the ask bar changes `g`: read the list again.
  const { runs, failed } = useRuns(selected);
  const owner = useOwner();
  const home = useHome(owner);
  const galaxies = useMemo(() => layout(runs ?? []), [runs]);
  const guide = runs !== null && !failed && galaxies.length === 0;
  const todo = useMemo(() => needs(runs ?? []), [runs]);
  const flagged = useMemo(
    () => new Set(todo.filter((n) => n.kind !== 'done').map((n) => n.run.run_id)),
    [todo],
  );
  // Stars only for the galaxies the person can see well, the selected one first.
  const [inView, setInView] = useState<string[]>([]);
  const wanted = useMemo(
    () => (selected ? [selected, ...inView.filter((id) => id !== selected)] : inView),
    [selected, inView],
  );
  const fields = useFields(galaxies, wanted);
  const current = galaxies.find((g) => g.id === selected) ?? null;
  const focus = useFocus(current);
  const [cover, setCover] = useState<Box | null>(null);

  useEffect(() => {
    document.title = `ISBE · ${t('home')}`;
  });

  const select = (id: string | null) =>
    setParams(
      (p) => {
        const next = new URLSearchParams(p);
        if (id) next.set('g', id);
        else next.delete('g');
        return next;
      },
      { replace: true },
    );

  return (
    <div className="g-home">
      <div className="g-sky">
        <Universe
          galaxies={galaxies}
          flagged={flagged}
          fields={fields}
          selected={selected}
          focus={focus}
          cover={current ? cover : null}
          onSelect={select}
          onOpen={(id) => navigate(runPath(id))}
          onView={setInView}
        />
        <header className="g-hud">
          <h1>{t('gTitle')}</h1>
          <p>
            {runs === null
              ? t('loading')
              : t(galaxies.length === 1 ? 'gCountOne' : 'gCount', {
                  n: galaxies.length,
                  p: galaxies.reduce((s, g) => s + g.papers, 0),
                })}
          </p>
        </header>
        {failed && (
          <p className="g-empty error" role="alert">
            {t('gLoadFailed')}
          </p>
        )}
        {guide && <Welcome home={home} />}
        {current && <Peek g={current} onClose={() => select(null)} onPlace={setCover} />}
      </div>
      <aside className="g-rail" aria-label={t('homeRail')}>
        <HealthNotice />
        <Reports home={home} owner={owner} guide={guide} />
        <h2 id="needs-h">
          {t('needsYou')} {flagged.size > 0 && <span>{flagged.size}</span>}
        </h2>
        {todo.length === 0 ? (
          <p className="muted">{runs === null ? t('loading') : t('needsNothing')}</p>
        ) : (
          <ul className="g-needs" aria-labelledby="needs-h">
            {todo.map((n) => (
              <NeedItem key={`${n.kind}:${n.run.run_id}`} need={n} onLook={() => select(n.run.run_id)} />
            ))}
          </ul>
        )}
      </aside>
    </div>
  );
}

/** Says before anyone asks that new questions would fail or search badly: an
 * upstream is down or not set, or the worker that runs them is gone. */
function HealthNotice() {
  const t = useT();
  const health = useHealth();
  if (!health) return null;
  const { papers, model } = health.upstreams;
  const lines: { key: MessageKey; error?: string | null }[] = [];
  if (!health.worker.alive) lines.push({ key: 'healthWorkerDown' });
  if (!papers.configured) lines.push({ key: 'healthEssaiesNotSet' });
  else if (papers.state === 'down')
    lines.push({
      key: papers.source === 'essaies' ? 'healthEssaiesDown' : 'healthOpenalexDown',
      error: papers.error,
    });
  if (!model.configured) lines.push({ key: 'healthModelNotSet' });
  else if (model.state === 'down')
    lines.push({
      key: /HTTP 40[13]\b/.test(model.error ?? '') ? 'healthModelRejected' : 'healthModelDown',
      error: model.error,
    });
  if (!lines.length) return null;
  return (
    <div className="g-health error" role="status">
      {lines.map(({ key, error }) => (
        <p key={key}>
          <strong>{t(key)}</strong>
          {error && <UpstreamReason text={error} />}
        </p>
      ))}
    </div>
  );
}

function NeedItem({ need, onLook }: { need: Need; onLook: () => void }) {
  const t = useT();
  const keys = NEED_KEYS[need.kind];
  // A failed or partial run says why, as the run page does; unjudged papers are still counted.
  const why =
    need.kind === 'failed' ? need.run.failure : need.run.status === 'done' ? need.run.problem : null;
  return (
    <li className="g-need" data-kind={need.kind}>
      <span className="g-need-kind">
        <i />
        {t(keys.kind)}
      </span>
      <button type="button" className="g-need-name" onClick={onLook}>
        {need.run.question}
      </button>
      {why && (
        <p>
          <Reason code={why.code} message={why.message} detail={why.detail} />
        </p>
      )}
      {(!why || need.kind === 'unjudged') && <p>{t(keys.text, { n: need.n })}</p>}
      <Link className="btn g-small" to={runPath(need.run.run_id)}>
        {t(keys.act)}
      </Link>
    </li>
  );
}

function Peek({ g, onClose, onPlace }: { g: Galaxy; onClose: () => void; onPlace: (b: Box) => void }) {
  const t = useT();
  const { run } = g;
  // Where the card sits over the sky, so the camera frames the galaxy beside it.
  const card = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const el = card.current;
    if (!el) return;
    const place = () => onPlace({ x: el.offsetLeft, y: el.offsetTop, w: el.offsetWidth, h: el.offsetHeight });
    place();
    const ro = typeof ResizeObserver === 'undefined' ? null : new ResizeObserver(place);
    ro?.observe(el);
    ro?.observe(el.parentElement!);
    return () => ro?.disconnect();
  }, [onPlace]);
  return (
    <div className="g-peek" role="region" aria-label={g.name} ref={card}>
      <div className="g-peek-head">
        <i className="g-dot" style={{ background: color(g, 70) }} />
        <div>
          <h2>{g.name}</h2>
          <p>{run.question}</p>
        </div>
        <button type="button" className="g-x" aria-label={t('closeDialog')} onClick={onClose}>
          ×
        </button>
      </div>
      <dl className="g-stats">
        <div>
          <dt>{t('gPapers')}</dt>
          <dd>{g.papers.toLocaleString()}</dd>
        </div>
        <div>
          <dt>{t('qIncluded')}</dt>
          <dd>{g.included.toLocaleString()}</dd>
        </div>
        <div>
          <dt>{t('gUnjudged')}</dt>
          <dd>{run.counts.unscreened.toLocaleString()}</dd>
        </div>
      </dl>
      <p className="g-status" data-done={run.status === 'done' || undefined}>
        {t(runStatusKey(run))}
      </p>
      <div className="g-peek-foot">
        <span
          className="g-switch"
          role="switch"
          aria-checked="false"
          aria-disabled="true"
          title={t('gFollowSoon')}
        >
          <span className="g-track" />
          {t('gFollow')}
        </span>
        <span className="g-grow" />
        <Link className="btn primary g-small" to={runPath(run.run_id)}>
          {t('gOpen')}
        </Link>
      </div>
    </div>
  );
}

/** How many of the newest reports Home lists; "All reports" has the rest. */
export const HOME_REPORTS = 3;

/** The newest topic reports, each opening on the Reports page. With the
 * first-run guide on screen, the guide carries the "Set up a topic" link. */
function Reports({ home, owner, guide }: { home: HomeState; owner: string | null; guide: boolean }) {
  const t = useT();
  const { prefs } = usePrefs();
  const plural = new Intl.PluralRules(prefs.lang);
  const count = (n: number, one: MessageKey, other: MessageKey) =>
    t(plural.select(n) === 'one' ? one : other, { n: n.toLocaleString(prefs.lang) });
  const labels = new Map(home.state === 'ready' ? home.home.topics.map((x) => [x.id, x.label || x.id]) : []);
  // Retry removes its own button; focus goes to the section's heading.
  const heading = useRef<HTMLHeadingElement>(null);
  return (
    <section className="g-reports" aria-labelledby="reports-h">
      <h2 id="reports-h" ref={heading} tabIndex={-1}>
        {t('homeReports')}
      </h2>
      {home.state === 'loading' ? (
        <p className="muted">{t('loading')}</p>
      ) : home.state === 'failed' ? (
        <div className="error" role="alert">
          <p>
            {t('homeReportsFailed')} {home.message ?? t('unreachable')}
          </p>
          <button
            type="button"
            className="btn g-small"
            onClick={() => {
              heading.current?.focus();
              home.retry();
            }}
          >
            {t('retry')}
          </button>
        </div>
      ) : home.home.reports.length === 0 ? (
        <p className="muted">
          {t('homeReportsEmpty')} {!guide && <a href="/topics">{t('welcomeStep3Act')}</a>}
        </p>
      ) : (
        <ul className="g-report-list">
          {home.home.reports.slice(0, HOME_REPORTS).map((r) => (
            <li key={`${r.topic_id}/${r.period}`}>
              <a href={reportHref(r, owner)}>
                <span>{labels.get(r.topic_id) ?? r.label ?? r.topic_id}</span>
                <small>
                  {r.period} ·{' '}
                  {r.media
                    ? count(r.materials ?? 0, 'homeReportMaterial', 'homeReportMaterials')
                    : count(r.papers, 'homeReportPaper', 'homeReportPapers')}
                </small>
              </a>
            </li>
          ))}
        </ul>
      )}
      <a className="btn g-small" href={withOwner('/reports', owner)}>
        {t('homeReportsAll')}
      </a>
    </section>
  );
}
