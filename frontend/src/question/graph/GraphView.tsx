// The knowledge graph of an ended run, drawn to be read (#113): the filter bar, then
// the topic lineage and the citation main paths on a shared kind of year axis. Both
// views are computed from the papers the filter keeps (model.ts), so excluding "AI
// / machine learning" or focusing on statistical methods re-prunes them at once.
import { useId, useMemo, useRef, useState, type KeyboardEvent } from 'react';
import { ApiError } from '../../api/client';
import { useT } from '../../i18n';
import {
  inputs,
  isFiltered,
  kept,
  lineage,
  mainPaths,
  NO_FILTER,
  papers,
  PRESETS,
  related,
  rows,
  type Filter,
  type GraphDoc,
  type GraphNode,
  type Lineage,
  type PathLink,
  type Term,
} from './model';
import { colour } from './palette';

export { colour };

interface Props {
  doc: GraphDoc;
  stale: boolean;
  selected: string | null;
  onSelect: (key: string | null) => void;
  /** Builds the graph again; the page then reads the new one. Absent where there is
   * nothing to build (a replay, a run still going). */
  onRebuild?: () => Promise<void>;
  /** The filter, shared with the star map (#55). */
  filter: Filter;
  onFilter: (f: Filter) => void;
}

export function GraphView({ doc, stale, selected, onSelect, onRebuild, filter, onFilter: setFilter }: Props) {
  const t = useT();
  const heading = useRef<HTMLHeadingElement>(null);
  const [rebuild, setRebuild] = useState<{ busy: boolean; error: 'running' | 'failed' | null }>({
    busy: false,
    error: null,
  });
  const startRebuild = () => {
    if (!onRebuild) return;
    setRebuild({ busy: true, error: null });
    onRebuild().then(
      () => {
        setRebuild({ busy: false, error: null });
        // The banner and its button go: keep the keyboard in the card.
        heading.current?.focus();
      },
      (e: unknown) =>
        setRebuild({ busy: false, error: e instanceof ApiError && e.status === 409 ? 'running' : 'failed' }),
    );
  };
  const all = useMemo(() => papers(doc), [doc]);
  const keep = useMemo(() => kept(doc, filter), [doc, filter]);
  const views = useMemo(() => {
    const inp = inputs(doc, keep);
    return { inp, lineage: lineage(inp), paths: mainPaths(inp) };
  }, [doc, keep]);
  const preset = (which: keyof typeof PRESETS): Term => ({
    ...PRESETS[which],
    label: t(which === 'ai' ? 'qGraphPresetAi' : 'qGraphPresetStats'),
  });
  const toggleFocus = (word: string) => {
    const id = `kw:${word}`;
    setFilter(
      filter.focus.some((x) => x.id === id)
        ? { ...filter, focus: filter.focus.filter((x) => x.id !== id) }
        : { ...filter, focus: [...filter.focus, { id, label: word, words: [word] }] },
    );
  };
  const cov = doc.coverage;

  return (
    <section className="card q-graph" aria-label={t('qGraph')}>
      <div className="card-head">
        <h2 ref={heading} tabIndex={-1}>
          {t('qGraph')}
        </h2>
        <span className="muted q-counts">{t('qGraphShown', { k: keep.size, n: all.length })}</span>
      </div>
      {stale && (
        <div className="q-banner">
          <span role="status">{t(rebuild.busy ? 'qGraphRebuilding' : 'qGraphStale')}</span>{' '}
          {onRebuild && (
            <button type="button" className="btn" onClick={startRebuild} disabled={rebuild.busy}>
              {t('qGraphRebuild')}
            </button>
          )}
        </div>
      )}
      {rebuild.error && (
        <p className="q-banner" role="alert">
          {t(rebuild.error === 'running' ? 'qGraphRebuildRunning' : 'qGraphRebuildFailed')}
        </p>
      )}
      <Filters
        filter={filter}
        onChange={setFilter}
        communities={doc.communities}
        presetAi={preset('ai')}
        presetStats={preset('statistics')}
      />
      {cov && cov.full_citations < cov.papers && (
        <p className="muted q-graph-note">{t('qGraphCoverage', { c: cov.full_citations, n: cov.papers })}</p>
      )}
      <LineageView lineage={views.lineage} focus={filter.focus} onToggle={toggleFocus} />
      <PathsView
        doc={doc}
        keep={keep}
        years={views.inp.years}
        paths={views.paths}
        selected={selected}
        onSelect={onSelect}
      />
    </section>
  );
}

// --- filters --------------------------------------------------------------------------

function Filters({
  filter,
  onChange,
  communities,
  presetAi,
  presetStats,
}: {
  filter: Filter;
  onChange: (f: Filter) => void;
  communities: GraphDoc['communities'];
  presetAi: Term;
  presetStats: Term;
}) {
  const t = useT();
  // A four-digit year sets the bound and an empty box clears it; anything else
  // puts the box back to the bound in force.
  const setYear = (bound: 'from' | 'to', box: HTMLInputElement) => {
    const v = box.value.trim();
    const next = v === '' ? null : /^\d{4}$/.test(v) ? Number(v) : undefined;
    if (next === undefined) box.value = String(filter[bound] ?? '');
    else if (next !== filter[bound]) onChange({ ...filter, [bound]: next });
  };
  return (
    <div className="q-graph-filters">
      <TermRow
        label={t('qGraphExclude')}
        kind="exclude"
        terms={filter.exclude}
        preset={presetAi}
        onChange={(exclude) => onChange({ ...filter, exclude })}
      />
      <TermRow
        label={t('qGraphFocus')}
        kind="focus"
        terms={filter.focus}
        preset={presetStats}
        onChange={(focus) => onChange({ ...filter, focus })}
      />
      <div className="q-graph-row">
        <span className="q-graph-label">{t('qGraphCommunities')}</span>
        {communities.map((c) => {
          const off = filter.hidden.includes(c.id);
          return (
            <button
              key={c.id}
              type="button"
              className={`chip q-graph-community ${off ? 'off' : ''}`}
              aria-pressed={!off}
              onClick={() =>
                onChange({
                  ...filter,
                  hidden: off ? filter.hidden.filter((h) => h !== c.id) : [...filter.hidden, c.id],
                })
              }
            >
              <i style={{ background: colour(c.id) }} />
              {c.top_keywords.slice(0, 2).join(', ') || c.id} · {c.size}
            </button>
          );
        })}
      </div>
      <div className="q-graph-row">
        <span className="q-graph-label">{t('qGraphYears')}</span>
        <input
          className="q-graph-year"
          inputMode="numeric"
          aria-label={t('qGraphYearFrom')}
          placeholder="1990"
          defaultValue={filter.from ?? ''}
          key={`from-${filter.from}`}
          onBlur={(e) => setYear('from', e.currentTarget)}
          onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
        />
        –
        <input
          className="q-graph-year"
          inputMode="numeric"
          aria-label={t('qGraphYearTo')}
          placeholder="2026"
          defaultValue={filter.to ?? ''}
          key={`to-${filter.to}`}
          onBlur={(e) => setYear('to', e.currentTarget)}
          onKeyDown={(e) => e.key === 'Enter' && e.currentTarget.blur()}
        />
        <span className="q-spacer" />
        {isFiltered(filter) && (
          <button type="button" className="btn" onClick={() => onChange(NO_FILTER)}>
            {t('qGraphReset')}
          </button>
        )}
      </div>
    </div>
  );
}

function TermRow({
  label,
  kind,
  terms,
  preset,
  onChange,
}: {
  label: string;
  kind: 'exclude' | 'focus';
  terms: Term[];
  preset: Term;
  onChange: (terms: Term[]) => void;
}) {
  const t = useT();
  const [draft, setDraft] = useState('');
  const add = (term: Term) => {
    if (!terms.some((x) => x.id === term.id)) onChange([...terms, term]);
  };
  const onKey = (e: KeyboardEvent<HTMLInputElement>) => {
    const word = draft.trim();
    if (e.key !== 'Enter' || !word) return;
    add({ id: `word:${word.toLowerCase()}`, label: word, words: [word] });
    setDraft('');
  };
  return (
    <div className="q-graph-row">
      <span className="q-graph-label">{label}</span>
      {terms.map((term) => (
        <span key={term.id} className={`chip q-graph-term ${kind}`} title={term.words.join(', ')}>
          {term.label}
          <button
            type="button"
            className="q-graph-x"
            aria-label={t('qGraphRemove', { t: term.label })}
            onClick={() => onChange(terms.filter((x) => x.id !== term.id))}
          >
            ×
          </button>
        </span>
      ))}
      {!terms.some((x) => x.id === preset.id) && (
        <button type="button" className="chip q-chip-button q-graph-preset" onClick={() => add(preset)}>
          + {preset.label}
        </button>
      )}
      <input
        className="q-graph-input"
        aria-label={`${label}: ${t('qGraphTermPlaceholder')}`}
        placeholder={t('qGraphTermPlaceholder')}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onKeyDown={onKey}
      />
    </div>
  );
}

// --- the year axis ------------------------------------------------------------------

const W = 1000;
const LEFT = 230;
const RIGHT = 24;

/** The x of a year from `lo` to `hi`; older years sit at `lo` (its tick reads "≤ lo"). */
function axis(lo: number, hi: number) {
  const a = Math.min(lo, hi - 1);
  const x = (y: number) => LEFT + ((Math.max(y, a) - a) / (hi - a)) * (W - LEFT - RIGHT);
  const step = hi - a > 30 ? 5 : hi - a > 12 ? 2 : 1;
  const ticks: number[] = [];
  for (let y = Math.ceil(a / step) * step; y <= hi; y += step) ticks.push(y);
  return { x, ticks };
}

function Ticks({
  ticks,
  x,
  top,
  bottom,
  older,
}: {
  ticks: number[];
  x: (y: number) => number;
  top: number;
  bottom: number;
  /** The axis' first year, when older papers are drawn at it. */
  older?: number;
}) {
  return (
    <g className="q-graph-axis" aria-hidden="true">
      {older !== undefined && (
        <text x={x(older)} y={top - 18} textAnchor="start">
          ≤ {older}
        </text>
      )}
      {ticks.map((y) => (
        <g key={y}>
          <line x1={x(y)} x2={x(y)} y1={top} y2={bottom} />
          <text x={x(y)} y={top - 6} textAnchor="middle">
            {y}
          </text>
        </g>
      ))}
    </g>
  );
}

// --- topic lineage ------------------------------------------------------------------

const ROW = 22;

function LineageView({
  lineage: l,
  focus,
  onToggle,
}: {
  lineage: Lineage;
  focus: Term[];
  onToggle: (word: string) => void;
}) {
  const t = useT();
  const [hover, setHover] = useState<string | null>(null);
  const arrow = useId();
  const ordered = useMemo(() => rows(l), [l]);
  const lit = useMemo(() => (hover ? related(l, hover) : null), [l, hover]);
  if (!ordered.length)
    return (
      <div className="q-graph-view">
        <h3>{t('qGraphLineage')}</h3>
        <p className="muted">{t('qGraphLineageEmpty')}</p>
      </div>
    );
  const lo = Math.min(...l.keywords.map((k) => k.per_year[0]?.[0] ?? k.onset));
  const hi = Math.max(...l.keywords.map((k) => k.last));
  const { x, ticks } = axis(lo, hi);
  const top = 34;
  const y = new Map(ordered.map((r, i) => [r.kw.id, top + 10 + i * ROW]));
  const byId = new Map(l.keywords.map((k) => [k.id, k]));
  const parentOf = new Map(l.edges.filter((e) => e.kind === 'grows_out_of').map((e) => [e.source, e]));
  const height = top + 20 + ordered.length * ROW;
  const dim = (id: string) => (lit && !lit.has(id) ? 0.15 : 1);
  const focused = new Set(focus.map((f) => f.id));
  return (
    <div className="q-graph-view">
      <h3>{t('qGraphLineage')}</h3>
      <p className="muted q-graph-how">{t('qGraphLineageHow')}</p>
      <svg
        viewBox={`0 0 ${W} ${height}`}
        className="q-graph-svg"
        role="group"
        aria-label={t('qGraphLineage')}
      >
        <defs>
          <marker
            id={arrow}
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="7"
            markerHeight="7"
            orient="auto"
          >
            <path d="M0,0L8,4L0,8z" className="q-graph-head" />
          </marker>
        </defs>
        <Ticks ticks={ticks} x={x} top={top} bottom={height - 6} />
        {l.edges
          .filter((e) => e.kind === 'crosses')
          .map((e) => {
            const a = byId.get(e.source)!;
            const b = byId.get(e.target)!;
            const at = x(Math.max(a.onset, b.onset));
            const [ya, yb] = [y.get(a.id)!, y.get(b.id)!];
            const bend = 18 + Math.abs(ya - yb) / 8;
            const on = !lit || (lit.has(a.id) && lit.has(b.id) && (hover === a.id || hover === b.id));
            return (
              <path
                key={`${e.source}|${e.target}`}
                className="q-graph-cross"
                d={`M${at},${ya} C${at + bend},${ya} ${at + bend},${yb} ${at},${yb}`}
                strokeWidth={0.6 + Math.min(e.weight, 18) / 6}
                // Faint until a keyword is traced: crossings are context, lineage the story.
                opacity={!lit ? 0.3 : on ? 0.9 : 0.06}
              />
            );
          })}
        {ordered.map(({ kw, depth }) => {
          const yy = y.get(kw.id)!;
          const c = colour(kw.community);
          const grow = parentOf.get(kw.id);
          const label = kw.label;
          const tip = [
            t('qGraphKwTitle', { k: label, n: kw.papers, y: kw.onset }),
            grow ? t('qGraphGrowsFrom', { p: grow.target.slice(3), n: grow.weight }) : '',
          ]
            .filter(Boolean)
            .join(' · ');
          return (
            <g
              key={kw.id}
              className={`q-graph-kw ${focused.has(kw.id) ? 'focused' : ''}`}
              opacity={dim(kw.id)}
              tabIndex={0}
              role="button"
              aria-pressed={focused.has(kw.id)}
              aria-label={tip}
              onMouseEnter={() => setHover(kw.id)}
              onMouseLeave={() => setHover(null)}
              onFocus={() => setHover(kw.id)}
              onBlur={() => setHover(null)}
              onClick={() => onToggle(label)}
              onKeyDown={(e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault();
                  onToggle(label);
                }
              }}
            >
              <title>{tip}</title>
              <rect x={0} y={yy - ROW / 2} width={W} height={ROW} className="q-graph-hit" />
              <line
                x1={x(kw.onset)}
                x2={x(kw.last)}
                y1={yy}
                y2={yy}
                stroke={c}
                strokeOpacity={0.35}
                strokeWidth={Math.min(12, 2 + kw.papers / 4)}
                strokeLinecap="round"
              />
              {kw.per_year.map(([yr, n]) => (
                <circle key={yr} cx={x(yr)} cy={yy} r={Math.min(7, 1.5 + n * 1.2)} fill={c} />
              ))}
              <text x={LEFT - 10} y={yy + 4} textAnchor="end" className="q-graph-kw-label">
                {depth > 0 && <tspan className="q-graph-faint">{'· '.repeat(Math.min(depth, 4))}</tspan>}
                {label}
                <tspan className="q-graph-faint"> {kw.papers}</tspan>
              </text>
            </g>
          );
        })}
        {l.edges
          .filter((e) => e.kind === 'grows_out_of')
          .map((e) => {
            const child = byId.get(e.source)!;
            const x0 = x(child.onset);
            const [yp, yc] = [y.get(e.target)!, y.get(e.source)!];
            const on = !lit || (lit.has(e.source) && lit.has(e.target));
            return (
              <path
                key={e.source}
                className="q-graph-grow"
                d={`M${x0},${yp} C${x0 - 26},${yp} ${x0 - 26},${yc} ${x0 - 5},${yc}`}
                strokeWidth={0.8 + Math.min(e.weight, 30) / 15}
                markerEnd={`url(#${arrow})`}
                opacity={on ? 1 : 0.1}
              />
            );
          })}
      </svg>
    </div>
  );
}

// --- citation main paths ------------------------------------------------------------

const LANE = 110;
const LANE_MIN = 44;
const LABELS = 10;
const LANE_LABEL = 30; // characters

function author(n: GraphNode, year: number) {
  const who = n.paper?.authors?.[0]?.split(/\s+/).pop();
  return who ? `${who} ${year}` : n.label.slice(0, 24);
}

function PathsView({
  doc,
  keep,
  years,
  paths,
  selected,
  onSelect,
}: {
  doc: GraphDoc;
  keep: Set<string>;
  years: Map<string, number>;
  paths: PathLink[];
  selected: string | null;
  onSelect: (key: string | null) => void;
}) {
  const t = useT();
  const [hover, setHover] = useState<string | null>(null);
  const arrow = useId();
  const nodes = useMemo(
    () => papers(doc).filter((n) => keep.has(n.id) && years.has(n.id)),
    [doc, keep, years],
  );
  const layout = useMemo(() => {
    // One lane per community that has papers shown, in community order; papers
    // without one share a last lane.
    const lanes = [...new Set(nodes.map((n) => n.community ?? -1))].sort((a, b) =>
      a < 0 ? 1 : b < 0 ? -1 : a - b,
    );
    // A lane grows with its papers, so a one-paper community takes little room.
    const bands: { c: number; top: number; height: number; size: number }[] = [];
    const pos = new Map<string, number>();
    let at = 0;
    for (const c of lanes) {
      // Within a lane, the most central papers first, spread over its height.
      const here = nodes
        .filter((n) => (n.community ?? -1) === c)
        .sort((a, b) => (b.metrics.pagerank ?? 0) - (a.metrics.pagerank ?? 0) || (a.id < b.id ? -1 : 1));
      const height = Math.max(LANE_MIN, Math.min(LANE, 30 + here.length * 1.2));
      here.forEach((n, i) => pos.set(n.id, at + 12 + ((i * 37) % (height - 24))));
      bands.push({ c, top: at, height, size: here.length });
      at += height;
    }
    return { bands, pos, height: at };
  }, [nodes]);
  if (!paths.length)
    return (
      <div className="q-graph-view">
        <h3>{t('qGraphPaths')}</h3>
        <p className="muted">{t('qGraphPathsEmpty')}</p>
      </div>
    );
  // The axis starts at the third-oldest paper, so a few classics from decades
  // before do not squeeze the rest; they sit at its left edge.
  const ys = [...years.values()].sort((a, b) => a - b);
  const lo = ys[Math.min(2, ys.length - 1)]!;
  const { x, ticks } = axis(lo, ys[ys.length - 1]!);
  const top = 34;
  const py = (id: string) => top + layout.pos.get(id)!;
  const height = top + layout.height + 8;
  const onPath = new Set(paths.flatMap((p) => [p.source, p.target]));
  const touching = hover
    ? new Set(
        paths.filter((p) => p.source === hover || p.target === hover).flatMap((p) => [p.source, p.target]),
      )
    : null;
  const prMax = Math.max(1e-9, ...nodes.map((n) => n.metrics.pagerank ?? 0));
  const byId = new Map(nodes.map((n) => [n.id, n]));
  // Names for the most central papers on the paths, skipping any that would
  // overlap one already placed (about 6 px a character, 12 px high).
  const named: { id: string; text: string; x: number; y: number }[] = [];
  for (const id of [...onPath]
    .filter((id) => byId.has(id))
    .sort((a, b) => (byId.get(b)!.metrics.pagerank ?? 0) - (byId.get(a)!.metrics.pagerank ?? 0))) {
    if (named.length === LABELS) break;
    const text = author(byId.get(id)!, years.get(id)!);
    const at = { id, text, x: x(years.get(id)!) + 7, y: py(id) - 6 };
    const w = text.length * 6;
    if (named.some((o) => Math.abs(o.y - at.y) < 12 && at.x < o.x + o.text.length * 6 && o.x < at.x + w))
      continue;
    named.push(at);
  }
  const comm = new Map(doc.communities.map((c) => [c.id, c]));
  const hovered = hover ? byId.get(hover) : null;
  return (
    <div className="q-graph-view">
      <h3>{t('qGraphPaths')}</h3>
      <p className="muted q-graph-how">{t('qGraphPathsHow', { m: paths.length })}</p>
      <p className="q-graph-hover" aria-live="polite">
        {hovered ? hovered.label : ' '}
      </p>
      <svg viewBox={`0 0 ${W} ${height}`} className="q-graph-svg" role="group" aria-label={t('qGraphPaths')}>
        <defs>
          <marker
            id={arrow}
            viewBox="0 0 8 8"
            refX="7"
            refY="4"
            markerWidth="6"
            markerHeight="6"
            orient="auto"
          >
            <path d="M0,0L8,4L0,8z" className="q-graph-head" />
          </marker>
        </defs>
        <Ticks ticks={ticks} x={x} top={top} bottom={height - 4} older={ys[0]! < lo ? lo : undefined} />
        {layout.bands.map(({ c, top: at, height: h, size }) => {
          const info = c >= 0 ? comm.get(c) : undefined;
          const name = info ? info.top_keywords.slice(0, 2).join(', ') || String(c) : t('qGraphNoCommunity');
          return (
            <g key={c}>
              <rect
                x={LEFT}
                y={top + at}
                width={W - LEFT - RIGHT}
                height={h - 6}
                fill={colour(c >= 0 ? c : null)}
                fillOpacity={0.07}
              />
              <text
                x={LEFT - 10}
                y={top + at + 16}
                textAnchor="end"
                fill={colour(c >= 0 ? c : null)}
                className="q-graph-lane"
              >
                <title>{name}</title>
                {name.length > LANE_LABEL ? `${name.slice(0, LANE_LABEL - 1)}…` : name}
              </text>
              <text x={LEFT - 10} y={top + at + 31} textAnchor="end" className="q-graph-faint">
                {size}
              </text>
            </g>
          );
        })}
        {[...paths].reverse().map((p) => {
          const [x1, y1, x2, y2] = [
            x(years.get(p.source)!),
            py(p.source),
            x(years.get(p.target)!),
            py(p.target),
          ];
          const on = !touching || p.source === hover || p.target === hover;
          const mid = (x1 + x2) / 2;
          return (
            <path
              key={`${p.source}|${p.target}`}
              className="q-graph-flow"
              d={`M${x1},${y1} C${mid},${y1} ${mid},${y2} ${x2},${y2}`}
              strokeWidth={0.6 + 2.4 * p.weight}
              strokeOpacity={on ? 0.25 + 0.6 * p.weight : 0.05}
              markerEnd={`url(#${arrow})`}
            />
          );
        })}
        {nodes.map((n) => {
          const r = 2 + 7 * Math.sqrt((n.metrics.pagerank ?? 0) / prMax);
          const lit = touching ? touching.has(n.id) : onPath.has(n.id);
          return (
            <circle
              key={n.id}
              className={`q-graph-paper ${n.id === selected ? 'selected' : ''}`}
              cx={x(years.get(n.id)!)}
              cy={py(n.id)}
              r={r}
              fill={colour(n.community)}
              fillOpacity={lit ? 1 : 0.22}
              // Papers on the paths are buttons; the faded rest open by mouse
              // only, and the paper list beside the map reaches every paper.
              {...(onPath.has(n.id)
                ? {
                    tabIndex: 0,
                    role: 'button',
                    'aria-label': n.label,
                    onFocus: () => setHover(n.id),
                    onBlur: () => setHover(null),
                    onKeyDown: (e: KeyboardEvent<SVGCircleElement>) => {
                      if (e.key === 'Enter' || e.key === ' ') {
                        e.preventDefault();
                        onSelect(n.id);
                      }
                    },
                  }
                : { 'aria-hidden': true })}
              onMouseEnter={() => setHover(n.id)}
              onMouseLeave={() => setHover(null)}
              onClick={() => onSelect(n.id)}
            >
              <title>{n.label}</title>
            </circle>
          );
        })}
        {named.map((n) => (
          <text key={n.id} x={n.x} y={n.y} className="q-graph-name" aria-hidden="true">
            {n.text}
          </text>
        ))}
      </svg>
    </div>
  );
}
