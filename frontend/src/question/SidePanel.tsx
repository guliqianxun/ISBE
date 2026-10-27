// The side panel next to the map: the selected paper's provenance with the
// Include / Exclude / Revert buttons (a person's decision wins over the model's,
// hsi-flow.md section 3), then the paper list.
import { useId, useMemo, useState } from 'react';
import { useT } from '../i18n';
import { usePrefs } from '../prefs';
import { textLang } from './lang';
import { STATE_KEYS } from './map/StarMap2D';
import { activeOverride, screening, type RunState, type Screening, type Star } from './run';
import type { Override } from './types';

type Sort = 'verdict' | 'year' | 'citations' | 'pagerank';
const ORDER = { include: 0, unscreened: 1, candidate: 2, exclude: 3 };
/** Rows drawn at once; "Show more" draws the next batch. */
const ROWS = 200;
const SHOWN: (Screening | 'all')[] = ['all', 'include', 'exclude', 'candidate', 'unscreened'];

export interface Overrule {
  override(key: string, decision: 'include' | 'exclude', note: string): Promise<Override>;
  revoke(overrideId: string, note: string): Promise<Override>;
}

export interface SidePanelProps {
  state: RunState;
  selected: string | null;
  onSelect: (key: string | null) => void;
  /** Saves a person's decision; without it the paper card only shows provenance. */
  overrule?: Overrule;
  /** PageRank by paper key, once the graph is built; papers not in it rank last. */
  rank?: Map<string, number>;
}

export function SidePanel({ state, selected, onSelect, overrule, rank }: SidePanelProps) {
  const t = useT();
  const [sort, setSort] = useState<Sort>('verdict');
  const [only, setOnly] = useState<Screening | 'all'>('all');
  const [rows, setRows] = useState(ROWS);
  const stars = useMemo(() => {
    const all = [...state.stars.values()].filter((s) => only === 'all' || screening(s) === only);
    const cites = (s: Star) => s.paper.cited_by_count ?? 0;
    const pr = (s: Star) => rank?.get(s.key) ?? -1;
    all.sort((a, b) =>
      sort === 'pagerank' && rank
        ? pr(b) - pr(a) || cites(b) - cites(a)
        : sort === 'year'
          ? (b.paper.year ?? 0) - (a.paper.year ?? 0) || cites(b) - cites(a)
          : sort === 'citations'
            ? cites(b) - cites(a)
            : ORDER[screening(a)] - ORDER[screening(b)] || cites(b) - cites(a),
    );
    return all;
  }, [state.stars, sort, only, rank]);
  const star = selected ? state.stars.get(state.keyIndex.get(selected) ?? selected) : undefined;

  return (
    <aside className="q-side" aria-label={t('qPapers')}>
      {star && (
        <PaperCard
          key={star.key}
          state={state}
          star={star}
          overrule={overrule}
          onClose={() => onSelect(null)}
        />
      )}
      <div className="q-side-head">
        <h2>
          {t('qPapers')} <span className="muted">{stars.length}</span>
        </h2>
        <label>
          <span className="sr-only">{t('qSort')}</span>
          <select
            value={sort}
            onChange={(e) => {
              setSort(e.target.value as Sort);
              setRows(ROWS);
            }}
            aria-label={t('qSort')}
          >
            <option value="verdict">{t('qSortVerdict')}</option>
            <option value="year">{t('qSortYear')}</option>
            <option value="citations">{t('qSortCitations')}</option>
            {rank && <option value="pagerank">{t('qSortPagerank')}</option>}
          </select>
        </label>
        <label>
          <span className="sr-only">{t('qShowOnly')}</span>
          <select
            value={only}
            onChange={(e) => {
              setOnly(e.target.value as Screening | 'all');
              setRows(ROWS);
            }}
            aria-label={t('qShowOnly')}
          >
            {SHOWN.map((v) => (
              <option key={v} value={v}>
                {v === 'all' ? t('qAllPapers') : t(STATE_KEYS[v])}
              </option>
            ))}
          </select>
        </label>
      </div>
      {stars.length === 0 ? (
        <p className="muted">{t(state.stars.size > 0 ? 'qNoneMatch' : 'qNoPapers')}</p>
      ) : (
        <ul className="q-papers">
          {stars.slice(0, rows).map((s) => {
            const st = screening(s);
            return (
              <li key={s.key} data-state={st} aria-current={s.key === star?.key ? 'true' : undefined}>
                <button type="button" onClick={() => onSelect(s.key)}>
                  <span className="q-paper-title" lang={textLang(s.paper.title)}>
                    {s.paper.title}
                  </span>
                  <span className="muted">
                    {[
                      s.paper.year,
                      s.paper.cited_by_count !== undefined && t('qCites', { n: s.paper.cited_by_count }),
                      t(STATE_KEYS[st]),
                    ]
                      .filter(Boolean)
                      .join(' · ')}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
      {stars.length > rows && (
        <p className="q-more muted">
          {t('qShowing', { shown: rows, n: stars.length })}{' '}
          <button type="button" className="link" onClick={() => setRows(rows + ROWS)}>
            {t('qShowMore', { n: Math.min(ROWS, stars.length - rows) })}
          </button>
        </p>
      )}
    </aside>
  );
}

function PaperCard({
  state,
  star,
  overrule,
  onClose,
}: {
  state: RunState;
  star: Star;
  overrule?: Overrule;
  onClose(): void;
}) {
  const t = useT();
  const { prefs } = usePrefs();
  const when = (at: string) =>
    new Date(at).toLocaleString(prefs.lang === 'zh' ? 'zh-CN' : 'en', {
      dateStyle: 'medium',
      timeStyle: 'short',
    });
  const queries = new Map((state.plan?.queries ?? []).map((q) => [q.id, q]));
  const override = activeOverride(star);
  const decision = screening(star);
  const [note, setNote] = useState('');
  const titleId = useId();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const { paper } = star;
  const act = async (what: () => Promise<Override>) => {
    setBusy(true);
    setError('');
    try {
      await what();
      setNote('');
    } catch {
      setError(t('actionFailed'));
    } finally {
      setBusy(false);
    }
  };
  return (
    // Named by its heading, which carries the title's own language.
    <section className="q-paper" aria-labelledby={titleId}>
      <div className="q-side-head">
        <h3 id={titleId} lang={textLang(paper.title)}>
          {paper.title}
        </h3>
        <button type="button" className="btn ui-icon" aria-label={t('closeDialog')} onClick={onClose}>
          ×
        </button>
      </div>
      <p className="muted">
        {[paper.authors?.slice(0, 3).join(', '), paper.year, paper.venue].filter(Boolean).join(' · ')}
      </p>
      <h4>{t('qFoundBy')}</h4>
      <ul>
        {star.found.map((f, i) =>
          'query_id' in f ? (
            <li key={i}>
              <code lang={textLang(queries.get(f.query_id)?.query ?? f.query_id)}>
                {queries.get(f.query_id)?.query ?? f.query_id}
              </code>{' '}
              #{f.rank}
            </li>
          ) : (
            <li key={i}>
              <WithTitle
                text={t(f.via === 'cites' ? 'qCitedBy' : 'qReferenceOf', { title: TITLE })}
                title={
                  state.stars.get(state.keyIndex.get(f.from_key) ?? f.from_key)?.paper.title ?? f.from_key
                }
              />
            </li>
          ),
        )}
      </ul>
      {star.merged.length > 0 && (
        <>
          <h4>{t('qMergedFrom')}</h4>
          <p className="muted">{star.merged.join(', ')}</p>
        </>
      )}
      <h4>{t('qModelVerdict')}</h4>
      {star.verdict ? (
        <p>
          <span className="chip plain">{t(STATE_KEYS[star.verdict.decision])}</span>{' '}
          <span lang={textLang(star.verdict.reason)}>{star.verdict.reason}</span>
          <span className="muted"> · {star.verdict.model}</span>
        </p>
      ) : (
        <p className="muted">{t('qNotScreenedYet')}</p>
      )}
      {overrule && (
        <div className="q-overrule" role="group" aria-label={t('qYourDecision')}>
          <h4>{t('qYourDecision')}</h4>
          <label className="q-field">
            <span className="sr-only">{t('qOverrideNote')}</span>
            <input
              value={note}
              maxLength={400}
              placeholder={t('qOverrideNote')}
              aria-label={t('qOverrideNote')}
              onChange={(e) => setNote(e.target.value)}
            />
          </label>
          <div className="q-review">
            {decision !== 'include' && (
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => void act(() => overrule.override(star.key, 'include', note))}
              >
                {t('qDoInclude')}
              </button>
            )}
            {decision !== 'exclude' && (
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => void act(() => overrule.override(star.key, 'exclude', note))}
              >
                {t('qDoExclude')}
              </button>
            )}
            {override && (
              <button
                type="button"
                className="btn"
                disabled={busy}
                onClick={() => void act(() => overrule.revoke(override.override_id, note))}
              >
                {t('qRevert')}
              </button>
            )}
          </div>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
        </div>
      )}
      {star.overrides.length > 0 && (
        <>
          <h4>{t('qOverrides')}</h4>
          <ol className="q-override-log">
            {[...star.overrides].reverse().map((o) => (
              <li key={o.override_id}>
                <span className={`chip ${o === override ? 'warn' : 'plain'}`}>
                  {o.decision ? t(STATE_KEYS[o.decision]) : t('qReverted')}
                </span>{' '}
                {/* The local-mode account is named "local"; to the person it is them. */}
                {o.actor === 'local' ? t('qYou') : o.actor} ·{' '}
                <time dateTime={o.created_at}>{when(o.created_at)}</time>
                {o.note ? ` · ${o.note}` : ''}
              </li>
            ))}
          </ol>
        </>
      )}
      {paper.url && /^https?:\/\//i.test(paper.url) && (
        <p>
          <a href={paper.url} target="_blank" rel="noreferrer noopener">
            {t('qOpenPaper')}
          </a>
        </p>
      )}
    </section>
  );
}

const TITLE = '{title}';

/** A console sentence around a paper title, the title marked with its own language. */
function WithTitle({ text, title }: { text: string; title: string }) {
  const [before, after = ''] = text.split(TITLE);
  return (
    <>
      {before}
      <span lang={textLang(title)}>{title}</span>
      {after}
    </>
  );
}
