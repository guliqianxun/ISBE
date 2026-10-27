// The run as a pipeline: one card per query with its hit count, the merge
// that folds duplicates, and the screening card with the score distribution
// and the best included papers. Thin connectors run from every query into the
// merge and on into screening; each query's dot has the hue of its stars.
import { useLayoutEffect, useMemo, useRef, useState } from 'react';
import { useT, type MessageKey } from '../i18n';
import { textLang } from './lang';
import { hue, tracks } from './map/sky';
import { counts, screening, type RunState, type Star } from './run';

const LAYER_KEYS: Record<string, MessageKey> = {
  canonical: 'qLayerCanonical',
  mainstream: 'qLayerMainstream',
  emerging: 'qLayerEmerging',
  cross_domain: 'qLayerCrossDomain',
  method_centric: 'qLayerMethodCentric',
  sideways: 'qLayerSideways',
  original: 'qLayerOriginal',
};
const BINS = 10;
/** Past this many queries the cards sit in two columns. */
const WIDE = 6;
const TOP = 6;

export interface FlowProps {
  state: RunState;
  onSelect: (key: string) => void;
  /** The query lit on the map; hovering a card lights it. */
  focus?: string | null;
  onFocus?: (queryId: string | null) => void;
}

export function Flow({ state, onSelect, focus = null, onFocus }: FlowProps) {
  const t = useT();
  const queries = useMemo(() => state.plan?.queries ?? [], [state.plan]);
  const hues = useMemo(() => {
    const byId = tracks(queries);
    return new Map(queries.map((q) => [q.id, hue(byId.get(q.id)?.index ?? 99)]));
  }, [queries]);
  const hits = useMemo(() => {
    const n = new Map<string, number>();
    for (const star of state.stars.values())
      for (const f of star.found) if ('query_id' in f) n.set(f.query_id, (n.get(f.query_id) ?? 0) + 1);
    return n;
  }, [state.stars]);
  const failed = new Set(state.problems.map((p) => p.query_id).filter(Boolean));
  const c = counts(state);
  const search = state.stages.search;
  const screen = state.stages.screen;

  const box = useRef<HTMLDivElement>(null);
  const lines = useConnectors(box, queries.length);

  return (
    <section className="card q-flow" aria-label={t('qFlow')}>
      <div className="q-flow-grid" ref={box}>
        <svg className="q-flow-lines" aria-hidden="true">
          {/* Drawn under the cards: lines from the first column pass beneath the second. */}
          {lines.map((d, i) => (
            <path key={i} d={d} />
          ))}
        </svg>
        <ol
          className="q-flow-queries"
          data-wide={queries.length > WIDE || undefined}
          aria-label={t('qFlowQueries')}
        >
          {queries.map((q) => {
            const n = hits.get(q.id) ?? 0;
            const state_ = failed.has(q.id)
              ? 'failed'
              : n > 0
                ? 'found'
                : search === 'begin'
                  ? 'busy'
                  : 'idle';
            return (
              <li
                key={q.id}
                className="q-node q-query"
                data-node="in"
                data-state={state_}
                data-focus={focus === q.id || undefined}
                style={{ '--q-hue': hues.get(q.id) } as React.CSSProperties}
                onMouseEnter={() => onFocus?.(q.id)}
                onMouseLeave={() => onFocus?.(null)}
              >
                <div className="q-query-head">
                  <i className="q-hue" style={{ background: hues.get(q.id) }} />
                  <span className="q-query-layer">
                    {LAYER_KEYS[q.layer] ? t(LAYER_KEYS[q.layer]!) : layerName(q.layer)}
                  </span>
                  <span className="q-badge" title={t('qHitsTitle')}>
                    {state_ === 'failed' ? t('qQueryFailed') : n.toLocaleString()}
                  </span>
                </div>
                <code className="q-query-text" lang={textLang(q.query)}>
                  {q.query}
                </code>
              </li>
            );
          })}
        </ol>
        <div className="q-node q-merge" data-node="merge" data-state={search}>
          <h3>{t('qMerge')}</h3>
          <p className="q-big">{c.searched.toLocaleString()}</p>
          <p className="muted">{t('qMergeDetail', { hits: c.hits, folded: c.hits - c.searched })}</p>
          {c.expanded > 0 && (
            <p className="muted" data-expanded>
              {t('qMergeExpanded', { n: c.expanded })}
            </p>
          )}
        </div>
        <Screening state={state} stage={screen} onSelect={onSelect} />
      </div>
    </section>
  );
}

function Screening({
  state,
  stage,
  onSelect,
}: {
  state: RunState;
  stage: RunState['stages']['screen'];
  onSelect: (key: string) => void;
}) {
  const t = useT();
  const { bins, top, scored, c } = useMemo(() => {
    const bins = Array.from({ length: BINS }, () => ({ include: 0, exclude: 0 }));
    const included: Star[] = [];
    let scored = 0;
    for (const star of state.stars.values()) {
      const d = screening(star);
      if (d === 'include') included.push(star);
      const score = star.verdict?.score;
      if (score == null || (d !== 'include' && d !== 'exclude')) continue;
      scored += 1;
      bins[Math.min(BINS - 1, Math.floor(score * BINS))]![d] += 1;
    }
    const score = (s: Star) => s.verdict?.score ?? 0;
    const cites = (s: Star) => s.paper.cited_by_count ?? 0;
    included.sort((a, b) => score(b) - score(a) || cites(b) - cites(a));
    return { bins, top: included.slice(0, TOP), scored, c: counts(state) };
  }, [state]);
  const tallest = Math.max(1, ...bins.map((b) => b.include + b.exclude));
  const judged = c.included + c.excluded;

  return (
    <div className="q-node q-screen" data-node="screen" data-state={stage}>
      <div className="q-screen-head">
        <h3>{t('qScreening')}</h3>
        <span className="q-badge">
          {c.included.toLocaleString()} / {c.candidates.toLocaleString()}
        </span>
      </div>
      {stage === 'pending' && judged === 0 ? (
        <p className="muted">{t('qScreenWaiting')}</p>
      ) : (
        <>
          <p className="muted">
            {t('qScreenSummary', { i: c.included, x: c.excluded, n: c.candidates - judged })}
          </p>
          {scored > 0 && (
            <figure className="q-hist">
              <figcaption>{t('qScreenScore')}</figcaption>
              <div className="q-hist-bars">
                {bins.map((b, i) => (
                  <div
                    key={i}
                    className="q-hist-bin"
                    title={`${(i / BINS).toFixed(1)}–${((i + 1) / BINS).toFixed(1)}: ${t('qScreenBin', { i: b.include, x: b.exclude })}`}
                  >
                    <span className="exclude" style={{ height: `${(100 * b.exclude) / tallest}%` }} />
                    <span className="include" style={{ height: `${(100 * b.include) / tallest}%` }} />
                  </div>
                ))}
              </div>
              <div className="q-hist-axis">
                <span>0</span>
                <span>0.5</span>
                <span>1</span>
              </div>
              <div className="q-hist-key">
                <span>
                  <i className="include" /> {t('qIncluded')}
                </span>
                <span>
                  <i className="exclude" /> {t('qExcluded')}
                </span>
              </div>
            </figure>
          )}
          {top.length > 0 && (
            <table className="q-top">
              <thead>
                <tr>
                  <th>{t('qTitle')}</th>
                  <th>{t('qScore')}</th>
                </tr>
              </thead>
              <tbody>
                {top.map((s) => (
                  <tr key={s.key}>
                    <td>
                      <button type="button" className="link" onClick={() => onSelect(s.key)}>
                        <span lang={textLang(s.paper.title)}>{s.paper.title}</span>
                      </button>
                    </td>
                    <td>{s.verdict?.score != null ? s.verdict.score.toFixed(2) : '–'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
        </>
      )}
    </div>
  );
}

/** `method_centric` → `Method centric`, for layers without their own text. */
function layerName(layer: string): string {
  const words = layer.replace(/_/g, ' ');
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** Curves from each query card into the merge node and on into screening,
 * measured from the laid-out cards and redrawn when the canvas resizes. */
function useConnectors(box: React.RefObject<HTMLDivElement | null>, n: number): string[] {
  const [lines, setLines] = useState<string[]>([]);
  useLayoutEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === 'undefined') return;
    const draw = () => {
      const at = el.getBoundingClientRect();
      const rect = (node: Element) => {
        const r = node.getBoundingClientRect();
        return { l: r.left - at.left, r: r.right - at.left, y: r.top - at.top + r.height / 2 };
      };
      const merge = el.querySelector('[data-node="merge"]');
      const screen = el.querySelector('[data-node="screen"]');
      if (!merge || !screen) return;
      const m = rect(merge);
      const s = rect(screen);
      const curve = (x1: number, y1: number, x2: number, y2: number) => {
        const k = (x2 - x1) / 2;
        return `M${x1},${y1} C${x1 + k},${y1} ${x2 - k},${y2} ${x2},${y2}`;
      };
      const next = [...el.querySelectorAll('[data-node="in"]')].map((q) => {
        const r = rect(q);
        return curve(r.r, r.y, m.l, m.y);
      });
      // Side by side: merge feeds screening to its right; stacked, nothing to draw.
      if (s.l > m.r) next.push(curve(m.r, m.y, s.l, m.y));
      setLines(next);
    };
    draw();
    const ro = new ResizeObserver(draw);
    ro.observe(el);
    return () => ro.disconnect();
  }, [box, n]);
  return lines;
}
