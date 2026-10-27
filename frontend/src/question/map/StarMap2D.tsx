// The 2D star map (Sigma.js + graphology). The store is the run state; this
// view only catches the picture up to it: new stars flare in, verdicts fade
// over 250–600 ms, and under reduced motion every change is a 150 ms fade.
// Two canvases frame sigma's own: behind it the core, the nebula of each
// query's spiral arm and the halos of included stars; in front the focused query's constellation and
// the selected star's pulse. Once the run's graph is built (#55) the stars move to
// its community layout (graphScene.ts): each community a nebula with its name,
// citations as arrows, keywords as small rings, hubs and bridges ringed, and a
// hovered node lights only its neighbours.
import Graph from 'graphology';
import { useEffect, useMemo, useRef, useState } from 'react';
import type Sigma from 'sigma';
import { useT } from '../../i18n';
import { textLang } from '../lang';
import type { RunState } from '../run';
import type { GraphDoc } from '../graph/model';
import { edgeLook, syncEdges } from './edges';
import { findNext } from './find';
import { DEFAULT_KINDS, graphScene, litFor, type GraphLayer, type MapView } from './graphScene';
import { colour } from '../graph/palette';
import { layoutGraph } from './layout';
import { scene, type NodeAttrs, type Scene } from './scene';
import { armPoint, flatten, SKY } from './sky';

export interface StarMapProps {
  state: RunState;
  selected: string | null;
  onSelect: (key: string | null) => void;
  showExcluded?: boolean;
  /** A query to bring forward (from the flow); the rest of the sky dims. */
  focus?: string | null;
  /** Hovering a star focuses the query that found it first. */
  onFocus?: (queryId: string | null) => void;
  /** The run's built graph: the map switches to it (#55). */
  graph?: GraphDoc | null;
  /** What of the graph is shown: edge kinds and the papers the filter keeps. */
  view?: MapView;
}

type Shown = Scene & { graph?: GraphLayer };
const ALL: MapView = { kinds: new Set(DEFAULT_KINDS), keep: null };
/** Stars move from the spiral to the graph over this long; its edges come after. */
const FORM_MS = 1500;

type Anim = { from: Partial<NodeAttrs>; to: NodeAttrs; start: number; ms: number };

const reducedMotion = () =>
  typeof window !== 'undefined' && !!window.matchMedia?.('(prefers-reduced-motion: reduce)').matches;

/** At most this many new stars start their flare per second; the rest fade in together. */
const FLARES_PER_SECOND = 30;

export function StarMap2D({
  state,
  selected,
  onSelect,
  showExcluded = false,
  focus = null,
  onFocus,
  graph: doc = null,
  view = ALL,
}: StarMapProps) {
  const t = useT();
  const host = useRef<HTMLDivElement>(null);
  const sigma = useRef<Sigma | null>(null);
  // Multi: a pair of papers can be joined by a citation and a coupling at once.
  const graph = useRef(new Graph({ type: 'directed', multi: true }));
  const anims = useRef(new Map<string, Anim>());
  const frame = useRef(0);
  const current = useRef<Shown | null>(null);
  const layout = useMemo(() => (doc ? layoutGraph(doc) : null), [doc]);
  /** The hovered node and its neighbours, in graph mode; the rest dims. */
  const lit = useRef<Set<string> | null>(null);
  const edgeTimer = useRef(0);
  const flares = useRef({ second: 0, n: 0 });
  const select = useRef(onSelect);
  const focusOut = useRef(onFocus);
  /** The queries in focus: the hovered star's, else the flow's. */
  const focused = useRef<Set<string> | null>(null);
  const focusProp = useRef(focus);
  const selectedKey = useRef(selected);
  const pulse = useRef(0);
  const [hover, setHover] = useState<{
    key: string;
    x: number;
    y: number;
    attrs: NodeAttrs;
  } | null>(null);
  const [broken, setBroken] = useState(() => !webgl());
  const refocusRef = useRef<((f: Set<string> | null) => void) | null>(null);
  const hoveredKey = useRef<string | null>(null);
  // A query focused from the flow; a hovered star takes over until it is left.
  useEffect(() => {
    focusProp.current = focus;
    refocusRef.current?.(focus ? new Set([focus]) : null);
  }, [focus]);
  useEffect(() => {
    select.current = onSelect;
    focusOut.current = onFocus;
    selectedKey.current = selected;
  });

  // Sigma touches WebGL as soon as it loads, so it is loaded only where WebGL
  // exists, and only on this page.
  useEffect(() => {
    const el = host.current;
    if (!el || broken) return;
    let renderer: Sigma | null = null;
    let canvas: HTMLCanvasElement | null = null;
    let front: HTMLCanvasElement | null = null;
    let ping: HTMLCanvasElement | null = null;
    let fxFrame = 0;
    let live = true;
    void Promise.all([import('sigma'), import('@sigma/node-border'), import('sigma/rendering')]).then(
      ([
        { default: SigmaClass },
        { createNodeBorderProgram },
        { EdgeArrowProgram, EdgeRectangleProgram },
      ]) => {
        if (!live) return;
        try {
          renderer = new SigmaClass(graph.current, el, {
            defaultNodeType: 'star',
            defaultEdgeType: 'line',
            edgeProgramClasses: { arrow: EdgeArrowProgram, line: EdgeRectangleProgram },
            nodeProgramClasses: {
              star: createNodeBorderProgram({
                borders: [
                  { size: { value: 0.22 }, color: { attribute: 'borderColor' } },
                  { size: { fill: true }, color: { attribute: 'color' } },
                ],
              }),
            },
            zIndex: true,
            // Out of focus, a star keeps its place but fades to a speck.
            nodeReducer: (node, data) => {
              const f = focused.current;
              const attrs = data as unknown as NodeAttrs;
              const l = lit.current;
              if (l ? l.has(node) : !f || attrs.queries.some((q) => f.has(q))) return data;
              return {
                ...data,
                color: fade(data.color),
                borderColor: fade(attrs.borderColor),
                label: null,
                forceLabel: false,
                zIndex: 0,
              } as typeof data;
            },
            // A hovered node keeps only its own edges, lit.
            edgeReducer: (edge, data) =>
              edgeLook(data, graph.current.extremities(edge), lit.current ? hoveredKey.current : null),
            labelColor: { color: SKY.label },
            labelFont: 'IBM Plex Sans, Noto Sans SC, system-ui, sans-serif',
            labelSize: 12,
            labelDensity: 0.4,
            labelGridCellSize: 120,
            labelRenderedSizeThreshold: 4.5,
            itemSizesReference: 'screen',
            minCameraRatio: 0.05,
            maxCameraRatio: 4,
          });
        } catch {
          // A WebGL context that failed after all: the side list still works.
          setBroken(true);
          return;
        }
        const r = renderer;
        sigma.current = r;
        r.setCustomBBox(framed(current.current));
        canvas = document.createElement('canvas');
        canvas.className = 'sky-arcs';
        el.prepend(canvas);
        front = document.createElement('canvas');
        front.className = 'sky-fx';
        el.append(front);
        // The selection's breathing ring has a canvas of its own, so each frame of it
        // redraws one ring, not every mark on the map.
        ping = document.createElement('canvas');
        ping.className = 'sky-fx';
        el.append(ping);
        const back = canvas;
        const fx = front;
        const pinged = ping;
        const drawPing = () => drawPulse(r, pinged, graph.current, selectedKey.current, pulse.current);
        r.on('afterRender', () => {
          drawBack(r, back, graph.current, current.current, focused.current);
          drawFx(r, fx, graph.current, current.current, focused.current, selectedKey.current, !!lit.current);
          drawPing();
        });
        // The selected star's ring breathes; under reduced motion it holds still.
        if (!reducedMotion()) {
          const breathe = (at: number) => {
            pulse.current = (at % 1800) / 1800;
            if (selectedKey.current) drawPing();
            fxFrame = requestAnimationFrame(breathe);
          };
          fxFrame = requestAnimationFrame(breathe);
        }
        const refocus = (f: Set<string> | null) => {
          focused.current = f && f.size ? f : null;
          // A full refresh: the node reducer only runs while indexing.
          r.refresh();
        };
        refocusRef.current = refocus;
        r.on('clickNode', ({ node }) => select.current(node));
        r.on('clickStage', () => select.current(null));
        r.on('enterNode', ({ node }) => {
          const attrs = graph.current.getNodeAttributes(node) as NodeAttrs;
          const p = r.graphToViewport(attrs);
          const layer = current.current?.graph;
          setHover({ key: node, x: p.x, y: p.y, attrs });
          if (layer) {
            // In the graph a node lights its neighbours, not its query's arm.
            hoveredKey.current = node;
            lit.current = litFor(layer, node);
            r.refresh();
            return;
          }
          refocus(new Set(attrs.queries));
          focusOut.current?.(attrs.queries[0] ?? null);
        });
        r.on('leaveNode', () => {
          setHover(null);
          // In the graph, leaving a node never touches the flow's query focus,
          // even when the node already left the map and took its light with it.
          if (current.current?.graph) {
            lit.current = null;
            hoveredKey.current = null;
            r.refresh();
            return;
          }
          refocus(focusProp.current ? new Set([focusProp.current]) : null);
          focusOut.current?.(null);
        });
        r.refresh();
      },
      () => live && setBroken(true),
    );
    return () => {
      live = false;
      cancelAnimationFrame(frame.current);
      window.clearTimeout(edgeTimer.current);
      cancelAnimationFrame(fxFrame);
      renderer?.kill();
      canvas?.remove();
      front?.remove();
      ping?.remove();
      sigma.current = null;
      refocusRef.current = null;
    };
  }, [broken]);

  // Sync the graph with the run state.
  useEffect(() => {
    const next: Shown =
      doc && layout
        ? graphScene(state, doc, layout, view, selected, showExcluded)
        : scene(state, selected, showExcluded);
    // The graph forming: stars travel from the spiral to their communities.
    const forming = !!next.graph && !current.current?.graph;
    current.current = next;
    const g = graph.current;
    // A hovered node that leaves the map (filtered out, say) gets no leaveNode.
    if (hoveredKey.current && !next.nodes.has(hoveredKey.current)) {
      hoveredKey.current = null;
      lit.current = null;
      setHover(null);
    }

    const bbox = sigma.current?.getCustomBBox();
    const want = framed(next);
    if (sigma.current && (bbox?.x.join() !== want.x.join() || bbox?.y.join() !== want.y.join())) {
      sigma.current.setCustomBBox(want);
      sigma.current.refresh();
    }
    const reduce = reducedMotion();
    const now = performance.now();
    for (const key of g.nodes()) if (!next.nodes.has(key)) g.dropNode(key);
    for (const [key, to] of next.nodes) {
      if (!g.hasNode(key)) {
        const second = Math.floor(now / 1000);
        if (flares.current.second !== second) flares.current = { second, n: 0 };
        const flare = !reduce && flares.current.n++ < FLARES_PER_SECOND;
        g.addNode(key, {
          ...to,
          size: flare ? to.size * 1.6 : to.size,
          color: SKY.bg,
          borderColor: SKY.bg,
        });
        anims.current.set(key, {
          from: g.getNodeAttributes(key) as NodeAttrs,
          to,
          start: now,
          ms: reduce ? 150 : 600,
        });
        continue;
      }
      const was = g.getNodeAttributes(key) as NodeAttrs;
      if (
        was.ring === to.ring &&
        was.hue === to.hue &&
        was.color === to.color &&
        was.borderColor === to.borderColor &&
        was.size === to.size &&
        was.x === to.x &&
        was.y === to.y
      ) {
        if (
          was.label !== to.label ||
          was.forceLabel !== to.forceLabel ||
          was.zIndex !== to.zIndex ||
          was.glow !== to.glow
        )
          g.mergeNodeAttributes(key, to);
        continue;
      }
      const ms = reduce ? 150 : forming ? FORM_MS : to.state === 'exclude' ? 600 : 250;
      anims.current.set(key, { from: { ...was }, to, start: now, ms });
      // Label, order and the rest are not animated.
      g.mergeNodeAttributes(key, {
        label: to.label,
        forceLabel: to.forceLabel,
        zIndex: to.zIndex,
        state: to.state,
        glow: to.glow,
        queries: to.queries,
        hue: to.hue,
        ring: to.ring,
        kind: to.kind,
        graphId: to.graphId,
      });
    }
    const tick = () => {
      const at = performance.now();
      for (const [key, a] of anims.current) {
        if (!g.hasNode(key)) {
          anims.current.delete(key);
          continue;
        }
        const k = Math.min(1, (at - a.start) / a.ms);
        const e = a.to.state === 'exclude' ? k * k : 1 - (1 - k) * (1 - k);
        g.mergeNodeAttributes(key, {
          x: mix(a.from.x ?? a.to.x, a.to.x, e),
          y: mix(a.from.y ?? a.to.y, a.to.y, e),
          size: mix(a.from.size ?? a.to.size, a.to.size, e),
          color: mixColor(a.from.color ?? a.to.color, a.to.color, e),
          borderColor: mixColor(a.from.borderColor ?? a.to.borderColor, a.to.borderColor, e),
        });
        if (k >= 1) anims.current.delete(key);
      }
      if (anims.current.size) frame.current = requestAnimationFrame(tick);
    };
    cancelAnimationFrame(frame.current);
    frame.current = requestAnimationFrame(tick);
    // Edges once the stars have (nearly) arrived; at once when they are already there.
    window.clearTimeout(edgeTimer.current);
    const edges = next.graph?.edges ?? [];
    if (forming && !reduce) edgeTimer.current = window.setTimeout(() => syncEdges(g, edges), FORM_MS - 500);
    else syncEdges(g, edges);
  }, [state, selected, showExcluded, doc, layout, view]);

  const star = hover ? state.stars.get(hover.key) : undefined;
  const first = star?.found[0];
  const query =
    first && 'query_id' in first ? state.plan?.queries.find((q) => q.id === first.query_id) : undefined;
  const attrs = hover?.attrs;
  const node = attrs?.graphId ? doc?.nodes.find((n) => n.id === attrs.graphId) : undefined;
  const community =
    node && node.community !== null ? doc?.communities.find((c) => c.id === node.community) : undefined;
  const carriers = attrs?.papers ?? 0;

  // Search: a shown node whose title or label holds the words, selected and brought
  // to the middle of the map; Enter again moves on to the next.
  const [find, setFind] = useState('');
  const [missed, setMissed] = useState(false);
  const searched = useRef('');
  const locate = (text: string) => {
    if (!text.trim()) return;
    // The same words again move on from the selection; new words start afresh.
    const again = searched.current === text.trim();
    searched.current = text.trim();
    const g = graph.current;
    const nodes = g.mapNodes((key, a) => [key, a as NodeAttrs] as [string, NodeAttrs]);
    const hit = findNext(
      nodes,
      (key) => state.stars.get(key)?.paper.title,
      text,
      again ? selectedKey.current : null,
    );
    setMissed(!hit);
    if (!hit) return;
    select.current(hit);
    const r = sigma.current;
    const at = r?.getNodeDisplayData(hit);
    if (r && at)
      void r.getCamera().animate({ x: at.x, y: at.y, ratio: 0.35 }, { duration: reducedMotion() ? 0 : 600 });
  };

  return (
    <div className="sky" data-testid="star-map">
      <div ref={host} className="sky-canvas" aria-label={t('qMap')} role="img" />
      {broken && <p className="sky-note">{t('qMapUnavailable')}</p>}
      {doc && !broken && (
        <form
          className="sky-find"
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            locate(find);
          }}
        >
          <input
            type="search"
            value={find}
            placeholder={t('qMapFind')}
            aria-label={t('qMapFind')}
            onChange={(e) => {
              setFind(e.target.value);
              setMissed(false);
            }}
          />
          {/* Mounted empty, so a miss is announced when its words arrive. */}
          <span className="sky-find-miss" role="status">
            {missed ? t('qMapFindNone') : ''}
          </span>
        </form>
      )}
      {star && hover && attrs && (
        <div className="sky-tip" style={{ left: hover.x + 12, top: hover.y + 12 }} role="tooltip">
          <strong lang={textLang(star.paper.title)}>{star.paper.title}</strong>
          {community ? (
            <span>
              <i className="sky-swatch" style={{ background: colour(community.id) }} />
              {community.label ?? t('qMapCommunity', { n: community.id + 1 })}
              {node?.metrics.bridge ? ` · ${t('qMapBridge')}` : attrs.ring === 1 ? ` · ${t('qMapHub')}` : ''}
            </span>
          ) : (
            query && <span lang={textLang(query.query)}>{query.query}</span>
          )}
          <span>
            {t(STATE_KEYS[attrs.state])}
            {attrs.overridden ? ` · ${t('qOverridden')}` : ''}
          </span>
        </div>
      )}
      {!star && hover && attrs?.kind === 'keyword' && (
        <div className="sky-tip" style={{ left: hover.x + 12, top: hover.y + 12 }} role="tooltip">
          <strong lang={textLang(attrs.label ?? '')}>{attrs.label}</strong>
          <span>{t('qMapKeyword', { n: carriers })}</span>
        </div>
      )}
    </div>
  );
}

/** What the camera frames: the scene's box, or the circle of its radius. */
function framed(s: Scene | null): { x: [number, number]; y: [number, number] } {
  if (s?.bbox) return s.bbox;
  const r = s?.radius ?? 400;
  return { x: [-r, r], y: [-r, r] };
}

/** Whether this browser can give sigma a WebGL context. */
function webgl(): boolean {
  if (typeof WebGLRenderingContext === 'undefined') return false;
  try {
    const canvas = document.createElement('canvas');
    return !!(canvas.getContext('webgl2') ?? canvas.getContext('webgl'));
  } catch {
    return false;
  }
}

export const STATE_KEYS = {
  candidate: 'qCandidate',
  include: 'qIncluded',
  exclude: 'qExcluded',
  unscreened: 'qUnscreened',
} as const;

/** Sizes a canvas to the renderer and returns its context, cleared. */
function prepare(renderer: Sigma, canvas: HTMLCanvasElement): CanvasRenderingContext2D | null {
  const { width, height } = renderer.getDimensions();
  const ratio = window.devicePixelRatio || 1;
  if (canvas.width !== width * ratio || canvas.height !== height * ratio) {
    canvas.width = width * ratio;
    canvas.height = height * ratio;
    canvas.style.width = `${width}px`;
    canvas.style.height = `${height}px`;
  }
  const ctx = canvas.getContext('2d');
  if (!ctx) return null;
  ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
  ctx.clearRect(0, 0, width, height);
  return ctx;
}

/** Viewport pixels per graph unit, measured on both axes. */
function scale(renderer: Sigma) {
  const c = renderer.graphToViewport({ x: 0, y: 0 });
  const x = renderer.graphToViewport({ x: 100, y: 0 });
  const y = renderer.graphToViewport({ x: 0, y: 100 });
  return { c, sx: Math.abs(x.x - c.x) / 100, sy: Math.abs(y.y - c.y) / 100 };
}

/** Behind the stars: the glowing core, a nebula along each arm, and halos. */
function drawBack(
  renderer: Sigma,
  canvas: HTMLCanvasElement,
  graph: Graph,
  s: Shown | null,
  focused: Set<string> | null,
) {
  const ctx = prepare(renderer, canvas);
  if (!ctx || !s) return;
  const { c, sx, sy } = scale(renderer);
  ctx.globalCompositeOperation = 'lighter';
  if (s.graph) {
    // Each community a nebula in its colour, where its papers are.
    for (const spot of s.graph.communities) {
      const p = renderer.graphToViewport(spot);
      const r = spot.r * 1.5 * Math.min(sx, sy);
      const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
      g.addColorStop(0, `${spot.colour}${focused ? '0c' : '26'}`);
      g.addColorStop(0.55, `${spot.colour}${focused ? '06' : '12'}`);
      g.addColorStop(1, `${spot.colour}00`);
      ctx.fillStyle = g;
      ctx.fillRect(p.x - r, p.y - r, 2 * r, 2 * r);
    }
    drawHalos(renderer, ctx, graph, s, focused);
    ctx.globalCompositeOperation = 'source-over';
    return;
  }
  // The core: where the papers every query agrees on gather.
  const coreR = 150 * Math.min(sx, sy);
  const core = ctx.createRadialGradient(c.x, c.y, 0, c.x, c.y, coreR);
  core.addColorStop(0, `${SKY.mark.slice(0, 7)}${focused ? '10' : '2a'}`);
  core.addColorStop(0.4, `${SKY.mark.slice(0, 7)}${focused ? '06' : '10'}`);
  core.addColorStop(1, `${SKY.mark.slice(0, 7)}00`);
  ctx.fillStyle = core;
  ctx.fillRect(c.x - coreR, c.y - coreR, 2 * coreR, 2 * coreR);
  // Each arm is a trail of nebula puffs in its query's hue, widening outwards.
  for (const track of s.tracks) {
    const lit = !focused || focused.has(track.queryId);
    const failed = s.failed.has(track.queryId);
    const hex = failed ? '#d9a441' : (s.hues.get(track.queryId) ?? SKY.edge).slice(0, 7);
    const puffs = 16;
    for (let i = 0; i < puffs; i++) {
      const t = (i + 0.5) / puffs;
      const p = armPoint(track, t);
      const x = c.x + p.x * sx;
      const y = c.y + p.y * sy;
      const r = (26 + 52 * t) * Math.min(sx, sy);
      const g = ctx.createRadialGradient(x, y, 0, x, y, r);
      const strength = lit ? (focused ? 0x40 : 0x1c) * (1 - 0.5 * t) : 4;
      g.addColorStop(0, `${hex}${Math.round(strength).toString(16).padStart(2, '0')}`);
      g.addColorStop(1, `${hex}00`);
      ctx.fillStyle = g;
      ctx.fillRect(x - r, y - r, 2 * r, 2 * r);
    }
  }
  ctx.globalCompositeOperation = 'source-over';
  // A failed query's arm is traced with a dashed line, so it reads as broken.
  for (const track of s.tracks) {
    if (!s.failed.has(track.queryId)) continue;
    ctx.beginPath();
    for (let i = 0; i <= 40; i++) {
      const p = armPoint(track, i / 40);
      if (i) ctx.lineTo(c.x + p.x * sx, c.y + p.y * sy);
      else ctx.moveTo(c.x + p.x * sx, c.y + p.y * sy);
    }
    ctx.setLineDash([4, 4]);
    ctx.lineWidth = 1;
    ctx.strokeStyle = 'oklch(.80 .13 75)';
    ctx.stroke();
  }
  ctx.setLineDash([]);
  ctx.globalCompositeOperation = 'lighter';
  drawHalos(renderer, ctx, graph, s, focused);
  ctx.globalCompositeOperation = 'source-over';
}

/** The soft halo of the brightest stars, in their hue (additive). */
function drawHalos(
  renderer: Sigma,
  ctx: CanvasRenderingContext2D,
  graph: Graph,
  s: Scene,
  focused: Set<string> | null,
) {
  for (const key of s.halos) {
    if (!graph.hasNode(key)) continue;
    const n = graph.getNodeAttributes(key) as NodeAttrs;
    if (focused && !n.queries.some((q) => focused.has(q))) continue;
    const p = renderer.graphToViewport(n);
    const r = renderer.scaleSize(n.size) * (2.2 + 3.5 * n.glow);
    const g = ctx.createRadialGradient(p.x, p.y, 0, p.x, p.y, r);
    const alpha = Math.round(255 * (0.12 + 0.3 * n.glow))
      .toString(16)
      .padStart(2, '0');
    g.addColorStop(0, `${n.hue.slice(0, 7)}${alpha}`);
    g.addColorStop(1, `${n.hue.slice(0, 7)}00`);
    ctx.fillStyle = g;
    ctx.fillRect(p.x - r, p.y - r, 2 * r, 2 * r);
  }
  ctx.globalCompositeOperation = 'source-over';
}

/** In front of the stars: the focused query's constellation, joining its most
 * cited included papers in order around the sky, and the selected star's ring. */
function drawFx(
  renderer: Sigma,
  canvas: HTMLCanvasElement,
  graph: Graph,
  s: Shown | null,
  focused: Set<string> | null,
  selected: string | null,
  hovering = false,
) {
  const ctx = prepare(renderer, canvas);
  if (!ctx || !s) return;
  // Over the stars, so they read; a hovered node shows its own citations instead.
  if (s.graph && !hovering && !focused) drawFlows(renderer, ctx, s.graph);
  if (s.graph) drawGraphMarks(renderer, ctx, graph, s.graph, !!focused);
  if (focused && focused.size === 1) {
    const [query] = focused;
    const hex = (s.hues.get(query!) ?? SKY.edge).slice(0, 7);
    const stars = s.halos
      .filter((k) => graph.hasNode(k) && (graph.getNodeAttribute(k, 'queries') as string[]).includes(query!))
      .slice(0, 9)
      .map((k) => graph.getNodeAttributes(k) as NodeAttrs)
      .sort((a, b) => Math.hypot(a.x, a.y) - Math.hypot(b.x, b.y))
      .map((n) => renderer.graphToViewport(n));
    if (stars.length > 1) {
      ctx.beginPath();
      stars.forEach((p, i) => (i ? ctx.lineTo(p.x, p.y) : ctx.moveTo(p.x, p.y)));
      ctx.strokeStyle = `${hex}dd`;
      ctx.lineWidth = 1;
      ctx.setLineDash([3, 3]);
      ctx.stroke();
      ctx.setLineDash([]);
      for (const p of stars) {
        ctx.beginPath();
        ctx.arc(p.x, p.y, 2, 0, 2 * Math.PI);
        ctx.fillStyle = SKY.mark;
        ctx.fill();
      }
    }
  }
  if (selected && graph.hasNode(selected)) {
    const n = graph.getNodeAttributes(selected) as NodeAttrs;
    const p = renderer.graphToViewport(n);
    const base = renderer.scaleSize(n.size) + 5;
    ctx.lineWidth = 1.2;
    ctx.strokeStyle = SKY.mark;
    ctx.beginPath();
    ctx.arc(p.x, p.y, base, 0, 2 * Math.PI);
    ctx.stroke();
  }
}

/** The selected star's breathing ring, alone on its canvas. */
function drawPulse(
  renderer: Sigma,
  canvas: HTMLCanvasElement,
  graph: Graph,
  selected: string | null,
  phase: number,
) {
  const ctx = prepare(renderer, canvas);
  if (!ctx || !selected || !graph.hasNode(selected) || phase <= 0) return;
  const n = graph.getNodeAttributes(selected) as NodeAttrs;
  const p = renderer.graphToViewport(n);
  const base = renderer.scaleSize(n.size) + 5;
  ctx.lineWidth = 1.2;
  ctx.strokeStyle = SKY.mark;
  ctx.globalAlpha = 1 - phase;
  ctx.beginPath();
  ctx.arc(p.x, p.y, base + 14 * phase, 0, 2 * Math.PI);
  ctx.stroke();
  ctx.globalAlpha = 1;
}

/** The citations between communities, one curved arrow per ordered pair from the
 * edge of one nebula to the other, as wide as the count's log (capped) and labelled with it.
 * A→B and B→A bend to opposite sides, so both read. */
function drawFlows(renderer: Sigma, ctx: CanvasRenderingContext2D, layer: GraphLayer) {
  const spots = new Map(layer.communities.map((c) => [c.id, c]));
  const { sx, sy } = scale(renderer);
  const k = Math.min(sx, sy);
  ctx.font = '600 11px IBM Plex Sans, Noto Sans SC, system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.textBaseline = 'middle';
  for (const f of layer.flows) {
    const a = spots.get(f.from);
    const b = spots.get(f.to);
    if (!a || !b) continue;
    const pa = renderer.graphToViewport(a);
    const pb = renderer.graphToViewport(b);
    const d = Math.hypot(pb.x - pa.x, pb.y - pa.y);
    // Close nebulae (a filter can pull them together) still get their arrow: the
    // ends shrink back from the centres until it fits.
    if (d < 16) continue;
    const fit = Math.min(1, (d - 8) / ((a.r + b.r) * k));
    const ra = a.r * k * fit;
    const rb = b.r * k * fit;
    const ux = (pb.x - pa.x) / d;
    const uy = (pb.y - pa.y) / d;
    const start = { x: pa.x + ux * ra, y: pa.y + uy * ra };
    const end = { x: pb.x - ux * rb, y: pb.y - uy * rb };
    // Bend to the left of the direction of travel, so A→B and B→A part; a
    // community lying between the two may still be crossed.
    const bend = 0.32 * (d - ra - rb);
    const mid = { x: (start.x + end.x) / 2 + uy * bend, y: (start.y + end.y) / 2 - ux * bend };
    const width = Math.min(8, 1 + Math.log2(f.count));
    const hex = a.colour.slice(0, 7);
    ctx.strokeStyle = `${hex}99`;
    ctx.fillStyle = `${hex}bb`;
    ctx.lineWidth = width;
    ctx.beginPath();
    ctx.moveTo(start.x, start.y);
    ctx.quadraticCurveTo(mid.x, mid.y, end.x, end.y);
    ctx.stroke();
    // The head, along the curve's last tangent.
    const tx = end.x - mid.x;
    const ty = end.y - mid.y;
    const tl = Math.hypot(tx, ty) || 1;
    const hx = tx / tl;
    const hy = ty / tl;
    const h = 6 + 2 * width;
    ctx.beginPath();
    ctx.moveTo(end.x, end.y);
    ctx.lineTo(end.x - hx * h - hy * h * 0.5, end.y - hy * h + hx * h * 0.5);
    ctx.lineTo(end.x - hx * h + hy * h * 0.5, end.y - hy * h - hx * h * 0.5);
    ctx.closePath();
    ctx.fill();
    // The count, on the curve's middle.
    const label = { x: (start.x + 2 * mid.x + end.x) / 4, y: (start.y + 2 * mid.y + end.y) / 4 };
    ctx.lineWidth = 3;
    ctx.strokeStyle = SKY.bg;
    ctx.strokeText(String(f.count), label.x, label.y);
    ctx.fillStyle = SKY.label;
    ctx.fillText(String(f.count), label.x, label.y);
  }
  ctx.textBaseline = 'alphabetic';
}

/** In the graph: rings round hubs (one) and bridges (two), and each community's
 * name over its nebula, with a halo of sky so it reads over the stars. */
function drawGraphMarks(
  renderer: Sigma,
  ctx: CanvasRenderingContext2D,
  graph: Graph,
  layer: GraphLayer,
  dim: boolean,
) {
  ctx.lineWidth = 0.8;
  ctx.strokeStyle = `${SKY.mark.slice(0, 7)}66`;
  graph.forEachNode((_key, a) => {
    const n = a as NodeAttrs;
    if (!n.ring) return;
    const p = renderer.graphToViewport(n);
    const r = renderer.scaleSize(n.size);
    for (let i = 1; i <= n.ring; i++) {
      ctx.beginPath();
      ctx.arc(p.x, p.y, r + 2.5 * i, 0, 2 * Math.PI);
      ctx.stroke();
    }
  });
  if (dim) return;
  ctx.font = '600 12px IBM Plex Sans, Noto Sans SC, system-ui, sans-serif';
  ctx.textAlign = 'center';
  ctx.lineJoin = 'round';
  for (const spot of layer.communities) {
    if (!spot.label || spot.papers < 3) continue;
    const p = renderer.graphToViewport(spot);
    const y = p.y - spot.r * scale(renderer).sy - 6;
    ctx.lineWidth = 3;
    ctx.strokeStyle = SKY.bg;
    ctx.strokeText(spot.label, p.x, y);
    ctx.fillStyle = spot.colour;
    ctx.fillText(spot.label, p.x, y);
  }
}

/** A star out of focus: sunk most of the way into the sky. */
const fade = (color: string | undefined) => flatten(color ?? SKY.bg, 0.18);

const mix = (a: number, b: number, k: number) => a + (b - a) * k;

function rgba(hex: string): number[] {
  const v = [1, 3, 5, 7].map((i) => parseInt(hex.slice(i, i + 2) || 'ff', 16));
  return v;
}

function mixColor(a: string, b: string, k: number): string {
  if (a === b || k >= 1) return b;
  const [x, y] = [rgba(a), rgba(b)];
  return `#${x
    .map((v, i) =>
      Math.round(mix(v, y[i]!, k))
        .toString(16)
        .padStart(2, '0'),
    )
    .join('')}`;
}
