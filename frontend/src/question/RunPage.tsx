// The run page (/question/runs/:id): header, plan card, the search flow, the
// result once the run ends (the screened reading list and its export), then the
// star map beside the paper list. Everything on it is rebuilt from the run's events; reloading
// replays them from seq 1.
import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useParams, useSearchParams } from 'react-router';
import { ApiError } from '../api/client';
import { useT } from '../i18n';
import {
  createRun,
  httpRun,
  replayLog,
  replayRun,
  REPLAY_PREFIX,
  type Downloads,
  type RunClient,
} from './client';
import { Flow } from './Flow';
import { GraphView } from './graph/GraphView';
import { rankByKey, type GraphDoc } from './graph/model';
import { useGraphFilter } from './graph/useGraphFilter';
import { colour } from './graph/palette';
import { DEFAULT_KINDS, EDGE_KINDS, mapViewOf } from './map/graphScene';
import { StarMap2D } from './map/StarMap2D';
import { PlanCard } from './PlanCard';
import { counts, inReview, type RunState } from './run';
import { RunHeader } from './RunHeader';
import { SidePanel, type Overrule } from './SidePanel';
import type { Override, PlanChanges, Run } from './types';
import { useRun } from './useRun';

export function RunPage() {
  const { id = '' } = useParams();
  // A new id is a new run: start from an empty state.
  return <Run key={id} id={id} />;
}

function Run({ id }: { id: string }) {
  const t = useT();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const [client, setClient] = useState<RunClient | null>(
    id.startsWith(REPLAY_PREFIX) ? null : () => httpRun(id),
  );
  const [missing, setMissing] = useState(false);
  const banner = useRef<HTMLDivElement>(null);
  const { state, connection, retry, local } = useRun(missing ? null : client, () => setMissing(true));
  const [showExcluded, setShowExcluded] = useState(false);
  // The query in focus: hovered on a flow card or on a star; it lights its track.
  const [focus, setFocus] = useState<string | null>(null);
  const selected = params.get('paper');
  // The run object, read once: the review's own end time and whether it is held.
  const [run, setRun] = useState<Run | null>(null);
  useEffect(() => {
    let live = true;
    client?.get().then(
      (r) => {
        if (!live) return;
        setRun(r);
        // Only the API answers null, and only for a run that does not exist.
        if (r === null && !id.startsWith(REPLAY_PREFIX)) setMissing(true);
      },
      () => {},
    );
    return () => {
      live = false;
    };
  }, [client, id]);

  useEffect(() => {
    if (!id.startsWith(REPLAY_PREFIX)) return;
    let live = true;
    void replayLog(id).then((log) => {
      if (!live) return;
      // ?step=<ms> sets the replay's pace (40 ms an event by default).
      if (log) setClient(replayRun(log, Number(params.get('step')) || undefined));
      else setMissing(true);
    });
    return () => {
      live = false;
    };
    // The pace is read once, when the replay starts.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  // The knowledge graph, read once the run has built it (its `graph` event) or has
  // ended (`isbe question-graph` builds one without events), and again whenever a
  // rebuild gives it a new fingerprint. An override made here marks it stale
  // locally (`keep`), rather than reading the whole graph again to learn that.
  const [graph, setGraph] = useState<{ graph: GraphDoc; stale: boolean } | null>(null);
  const [graphFailed, setGraphFailed] = useState(false);
  const [graphTry, setGraphTry] = useState(0);
  const ended = state.status === 'done' || state.status === 'cancelled' || state.status === 'failed';
  const built = state.graph?.fingerprint ?? (ended ? 'ended' : null);
  useEffect(() => {
    if (!client || !built) return;
    let live = true;
    client.graph().then(
      (g) => {
        if (!live) return;
        setGraph(g);
        setGraphFailed(false);
      },
      () => live && setGraphFailed(true),
    );
    return () => {
      live = false;
    };
  }, [client, built, graphTry]);

  // PageRank by paper key, for the paper list's sort.
  const rank = useMemo(
    () => (graph ? rankByKey(graph.graph, state.keyIndex) : undefined),
    [graph, state.keyIndex],
  );

  // One filter for the graph card and the star map (#55); the edge kinds are the map's.
  const [filter, setFilter] = useGraphFilter(graph?.graph.fingerprint);
  const [kinds, setKinds] = useState<ReadonlySet<string>>(() => new Set(DEFAULT_KINDS));
  const doc = graph?.graph;
  const mapView = useMemo(() => mapViewOf(doc, filter, kinds), [doc, filter, kinds]);

  useEffect(() => {
    document.title = `ISBE · ${state.plan?.question ?? run?.question ?? t('question')}`;
  });

  const select = useMemo(
    () => (key: string | null) =>
      setParams(
        (p) => {
          const next = new URLSearchParams(p);
          if (key) next.set('paper', key);
          else next.delete('paper');
          return next;
        },
        { replace: true },
      ),
    [setParams],
  );

  if (missing) {
    return (
      <div className="card error" role="alert">
        <p>{t('qRunMissing')}</p>
        <Link to="/galaxies">{t('qBackToRuns')}</Link>
      </div>
    );
  }

  // A run asked with "review: false" passes its plan stage without waiting.
  const reviewing = inReview(state) && run?.review.on !== false;
  const save = async (version: number, changes: PlanChanges) => {
    if (!client) return;
    if (reviewing) {
      await client.savePlan(version, changes);
      return;
    }
    // A run past its review keeps its plan; the edit becomes a new run.
    const runId = await client.rerun(version, changes);
    navigate(`/question/runs/${encodeURIComponent(runId)}`);
  };
  const controls = client ?? {
    pause: noop,
    resume: noop,
    cancel: noop,
    start: noop,
    hold: noop,
    remove: noop,
  };
  const remove = async () => {
    try {
      await controls.remove();
    } catch (err) {
      // Deleted meanwhile (another tab): it is gone either way.
      if (err instanceof ApiError && err.status === 404) {
        setMissing(true);
        return;
      }
      throw err;
    }
    navigate('/galaxies');
  };
  // A failed run may have no plan to rerun: ask its question again with its options.
  const tryAgain = async () => {
    if (!run) return;
    const { expand, expand_per_paper, expand_total, citations_full } = run.options;
    const expansion = Object.fromEntries(
      Object.entries({ expand, expand_per_paper, expand_total, citations_full }).filter(
        ([, v]) => v !== undefined,
      ),
    );
    const next = await createRun({
      ...expansion,
      question: run.question,
      lang: run.lang,
      year_from: run.options.year_from,
      year_to: run.options.year_to,
      review: run.review.on,
      engine: run.options.engine,
    });
    navigate(`/question/runs/${encodeURIComponent(next)}`);
  };
  // A saved override shows at once; its `override` event from the stream is then a repeat.
  const overrule: Overrule | undefined = client
    ? {
        override: async (key, decision, note) => keep(await client.override(key, decision, note)),
        revoke: async (overrideId, note) => keep(await client.revoke(overrideId, note)),
      }
    : undefined;
  const keep = (o: Override) => {
    local(o);
    setGraph((g) => (g && !g.stale ? { ...g, stale: true } : g));
    return o;
  };

  return (
    <div className="q-run">
      <RunHeader
        state={state}
        controls={controls}
        engine={run?.options?.engine}
        question={run?.question}
        reviewing={reviewing}
        onDelete={client && !id.startsWith(REPLAY_PREFIX) ? remove : undefined}
        onTryAgain={run && !id.startsWith(REPLAY_PREFIX) ? tryAgain : undefined}
      />
      {/* Focus lands here from Retry, whose button goes away, so the keyboard
          stays at the banner instead of falling back to the page start. */}
      <div className="q-connection" ref={banner} tabIndex={-1}>
        {connection === 'retrying' && (
          <p className="q-banner" role="status">
            {t('qReconnecting')}
          </p>
        )}
        {connection === 'lost' && (
          <p className="q-banner" role="alert">
            {t('qConnectionLost')}{' '}
            <button
              type="button"
              className="btn"
              onClick={() => {
                banner.current?.focus();
                retry?.();
              }}
            >
              {t('retry')}
            </button>
          </p>
        )}
        {connection === 'refused' && (
          <p className="q-banner" role="alert">
            {t('qRunRefused')}
          </p>
        )}
      </div>
      {state.plan ? (
        <PlanCard
          // Remounted once the run object arrives, to start from its review state.
          key={run ? 'run' : 'events'}
          plan={state.plan}
          reviewing={reviewing}
          onStart={controls.start}
          onSave={save}
        />
      ) : (
        (state.status === 'queued' || state.status === 'running' || state.status === 'empty') && (
          <section className="card q-plan q-skeleton" aria-busy="true">
            <h2>{t('qPlan')}</h2>
            <p className="muted">{state.status === 'queued' ? t('qQueued') : t('qPlanning')}</p>
          </section>
        )
      )}
      {state.plan && <Flow state={state} onSelect={select} focus={focus} onFocus={setFocus} />}
      {(state.status === 'done' || state.status === 'cancelled') && (
        <Result state={state} downloads={client?.downloads ?? null} hasGraph={!!graph} />
      )}
      {graphFailed && !graph && (
        <p className="q-banner" role="alert">
          {t('qGraphFailed')}{' '}
          <button type="button" className="btn" onClick={() => setGraphTry((n) => n + 1)}>
            {t('retry')}
          </button>
        </p>
      )}
      {graph && (
        <GraphView
          doc={graph.graph}
          filter={filter}
          onFilter={setFilter}
          stale={graph.stale}
          selected={selected}
          onSelect={select}
          onRebuild={
            client && ended
              ? async () => {
                  await client.rebuildGraph();
                  // Read the new graph before the banner goes, so it does not
                  // offer Rebuild again in between.
                  setGraph(await client.graph());
                  setGraphFailed(false);
                }
              : undefined
          }
        />
      )}
      <section className="q-body" aria-label={t('qMapStage')}>
        <div className={`card q-map ${state.fatal ? 'q-map-failed' : ''}`}>
          <div className="q-map-tools">
            <h2>{t('qMapStage')}</h2>
            <label className="q-check">
              <input
                type="checkbox"
                checked={showExcluded}
                onChange={(e) => setShowExcluded(e.target.checked)}
              />
              {t('qShowExcluded')}
            </label>
            {graph && (
              <div className="q-map-kinds" role="group" aria-label={t('qMapEdges')}>
                {EDGE_KINDS.map((k) => (
                  <button
                    key={k}
                    type="button"
                    className={`chip ${kinds.has(k) ? '' : 'off'}`}
                    aria-pressed={kinds.has(k)}
                    onClick={() =>
                      setKinds((was) => {
                        const next = new Set(was);
                        if (!next.delete(k)) next.add(k);
                        return next;
                      })
                    }
                  >
                    {t(`qEdge_${k}`)}
                  </button>
                ))}
              </div>
            )}
          </div>
          <StarMap2D
            state={state}
            selected={selected}
            onSelect={select}
            showExcluded={showExcluded}
            focus={focus}
            onFocus={setFocus}
            graph={graph?.graph ?? null}
            view={mapView}
          />
          {graph ? <GraphLegend doc={graph.graph} hidden={filter.hidden} keep={mapView.keep} /> : <Legend />}
        </div>
        <SidePanel state={state} selected={selected} onSelect={select} overrule={overrule} rank={rank} />
      </section>
    </div>
  );
}

const noop = async () => {};

/** What a finished run gives: the screened reading list, said plainly, and its
 * downloads: the paper set, and the graph once one is built. */
function Result({
  state,
  downloads,
  hasGraph,
}: {
  state: RunState;
  downloads: Downloads | null;
  hasGraph: boolean;
}) {
  const t = useT();
  const c = counts(state);
  return (
    <section className="card q-result" aria-label={t('qResult')}>
      <div className="card-head">
        <h2>{t('qResult')}</h2>
        {downloads && c.candidates > 0 && (
          <nav className="q-downloads" aria-label={t('qDownloads')}>
            <a className="btn" href={downloads.csv} download>
              {t('qExportCsv')}
            </a>
            <a className="btn" href={downloads.json} download>
              {t('qExportJson')}
            </a>
            {hasGraph && (
              <>
                <a className="btn" href={downloads.graphJson} download>
                  {t('qExportGraphJson')}
                </a>
                <a className="btn" href={downloads.graphml} download>
                  {t('qExportGraphml')}
                </a>
              </>
            )}
          </nav>
        )}
      </div>
      {c.candidates === 0 ? (
        <p>{t(state.status === 'cancelled' ? 'qResultCancelledEmpty' : 'qResultEmpty')}</p>
      ) : (
        <>
          <p>
            {state.status === 'cancelled'
              ? t('qResultCancelled', {
                  i: c.included,
                  n: c.candidates,
                  x: c.excluded,
                  u: c.candidates - c.included - c.excluded,
                })
              : t('qResultList', { i: c.included, n: c.candidates, x: c.excluded })}
          </p>
          <p className="muted">{t('qResultHow')}</p>
        </>
      )}
    </section>
  );
}

/** The legend once the map shows the graph: its encoding, then the communities by name. */
function GraphLegend({
  doc,
  hidden,
  keep,
}: {
  doc: GraphDoc;
  hidden: number[];
  keep: ReadonlySet<string> | null;
}) {
  const t = useT();
  // Each community's papers as the map shows them (the filter may keep fewer).
  const size = new Map<number, number>();
  for (const n of doc.nodes)
    if (n.kind === 'paper' && n.community !== null && (!keep || keep.has(n.id)))
      size.set(n.community, (size.get(n.community) ?? 0) + 1);
  const shown = doc.communities.filter((c) => !hidden.includes(c.id) && size.get(c.id));
  return (
    <ul className="sky-legend" aria-label={t('qLegend')}>
      <li className="muted">{t('qLegendGraphSize')}</li>
      <li className="muted">{t('qLegendBrightness')}</li>
      <li className="muted">{t('qLegendRings')}</li>
      <li className="muted">{t('qLegendArrows')}</li>
      <li className="muted">{t('qLegendKeywordNode')}</li>
      {shown.slice(0, 10).map((c) => (
        <li key={c.id}>
          <i className="sky-swatch" style={{ background: colour(c.id) }} />
          {c.label ?? t('qMapCommunity', { n: c.id + 1 })} · {size.get(c.id)}
        </li>
      ))}
      {shown.length > 10 && (
        <li className="muted">{t('qLegendMoreCommunities', { n: shown.length - 10 })}</li>
      )}
      {doc.communities.length > 10 && <li className="muted">{t('qLegendColoursRepeat')}</li>}
    </ul>
  );
}

function Legend() {
  const t = useT();
  return (
    <ul className="sky-legend" aria-label={t('qLegend')}>
      <li>
        <i className="sky-dot bright" /> {t('qIncluded')}
      </li>
      <li>
        <i className="sky-dot" /> {t('qCandidate')}
      </li>
      <li>
        <i className="sky-dot hollow" /> {t('qExcluded')}
      </li>
      <li>
        <i className="sky-dot dashed" /> {t('qUnscreened')}
      </li>
      <li className="muted">{t('qLegendBrightness')}</li>
      <li className="muted">{t('qLegendHue')}</li>
    </ul>
  );
}
