// What the 2D map draws once the run's graph is built (#55, star-map.md 1.1–1.3):
// each kept paper at its place in the community layout, hued by community, sized by
// PageRank and lit by citations; keyword nodes as small rings; citations as arrows.
// The search spiral (`scene`) gives the look of each star's screening state; this
// moves and recolours it. Plain data, so it can be checked without WebGL.
import type { RunState } from '../run';
import { screening } from '../run';
import { isFiltered, kept, type Filter, type GraphDoc, type GraphNode } from '../graph/model';
import { colour } from '../graph/palette';
import { LAYOUT_RADIUS, type Layout } from './layout';
import { HALOS, scene, shortTitle, type NodeAttrs, type Scene } from './scene';
import { flatten, jitter, SKY, starLook } from './sky';

/** The edge kinds of a graph, in the order the map's edge filter lists them. */
export const EDGE_KINDS = ['cites', 'has_keyword', 'co_citation', 'coupling', 'keyword_cooccur'] as const;
export type EdgeKind = (typeof EDGE_KINDS)[number];
/** On until turned off: the directed citations and the paper–keyword links. The
 * derived similarity kinds are many and undirected, so they start off. */
export const DEFAULT_KINDS: EdgeKind[] = ['cites', 'has_keyword'];

export interface EdgeAttrs {
  key: string;
  source: string;
  target: string;
  kind: EdgeKind;
  color: string;
  size: number;
  /** Sigma's edge program: `arrow` for citations, `line` for the rest. */
  type: 'arrow' | 'line';
  /** Its colour while one of its ends is hovered. */
  hot: string;
}

/** The citations from one community to another, drawn as one arrow at overview. */
export interface Flow {
  from: number;
  to: number;
  count: number;
}

export interface CommunitySpot {
  id: number;
  label: string | null;
  colour: string;
  /** The centroid of its shown papers and their spread (rms distance). */
  x: number;
  y: number;
  r: number;
  papers: number;
}

export interface GraphLayer {
  edges: EdgeAttrs[];
  communities: CommunitySpot[];
  /** Node → its shown neighbours, for the hover highlight. */
  neighbours: Map<string, Set<string>>;
  /** Citations between communities, one per ordered pair, most first. */
  flows: Flow[];
}

export interface MapView {
  /** Edge kinds drawn. */
  kinds: ReadonlySet<string>;
  /** Papers the shared graph filter keeps (`kept` in graph/model.ts); null for all. */
  keep: ReadonlySet<string> | null;
}

/** Stars beyond the graph (excluded, or included after it was built) wait on a rim. */
const RIM = LAYOUT_RADIUS * 1.22;

/** The map's view of the graph: the edge kinds drawn, and the papers the filter keeps. */
export function mapViewOf(
  doc: GraphDoc | null | undefined,
  filter: Filter,
  kinds: ReadonlySet<string>,
): MapView {
  return { kinds, keep: doc && isFiltered(filter) ? kept(doc, filter) : null };
}

/** The node and its neighbours a hover lights; null when nothing is hovered. */
export function litFor(layer: GraphLayer | undefined, node: string | null): Set<string> | null {
  if (!layer || !node) return null;
  return new Set([node, ...(layer.neighbours.get(node) ?? [])]);
}

export function graphScene(
  state: RunState,
  doc: GraphDoc,
  layout: Layout,
  view: MapView,
  selected: string | null,
  showExcluded = false,
): Scene & { graph: GraphLayer } {
  const base = scene(state, selected, showExcluded);
  const byId = new Map(doc.nodes.map((n) => [n.id, n]));
  const papers = doc.nodes.filter((n) => n.kind === 'paper');
  const maxRank = Math.max(1e-12, ...papers.map((n) => n.metrics.pagerank ?? 0));
  let maxCitations = 0;
  for (const star of state.stars.values())
    maxCitations = Math.max(maxCitations, star.paper.cited_by_count ?? 0);
  // Hubs: the papers in the top 5 % by PageRank carry one ring; bridges two.
  const ranked = papers.map((n) => n.metrics.pagerank ?? 0).sort((a, b) => b - a);
  const hubFloor = ranked[Math.max(0, Math.ceil(0.05 * ranked.length) - 1)] ?? Infinity;

  const nodes = new Map<string, NodeAttrs>();
  for (const [key, was] of base.nodes) {
    const star = state.stars.get(key)!;
    const node = byId.get(key) ?? star.paper.keys?.map((k) => byId.get(k)).find(Boolean);
    const isSelected = key === selected;
    if (!node || node.kind !== 'paper') {
      // Not in the graph: on the rim while no filter is on; excluded ones only when asked.
      if (view.keep || (was.state === 'exclude' && !showExcluded)) continue;
      const a = jitter(key, 9) * 2 * Math.PI;
      const r = RIM + 40 * jitter(key, 10);
      nodes.set(key, { ...was, x: r * Math.cos(a), y: r * Math.sin(a), glow: 0, ring: 0, kind: 'paper' });
      continue;
    }
    if (view.keep && !view.keep.has(node.id)) continue;
    const hue = colour(node.community);
    const look = starLook(star, hue, maxCitations, isSelected);
    const pr = node.metrics.pagerank ?? 0;
    const share = Math.sqrt(pr / maxRank);
    const at = layout.get(node.id) ?? { x: 0, y: 0 };
    nodes.set(key, {
      ...was,
      x: at.x,
      y: at.y,
      size: (2 + 5.5 * share) * (isSelected ? 1.3 : 1),
      color: flatten(showExcluded && look.hollow ? `${look.border.slice(0, 7)}88` : look.color),
      borderColor: flatten(look.border),
      label: screening(star) === 'exclude' && !isSelected ? null : (was.label ?? shortTitle(star)),
      hue,
      glow: share,
      ring: node.metrics.bridge ? 2 : pr >= hubFloor && pr > 0 ? 1 : 0,
      kind: 'paper',
      graphId: node.id,
    });
  }

  // Keyword nodes carried by a shown paper; their size by how many shown papers carry them.
  const shownIds = new Map<string, string>(); // graph id → map key
  for (const [key, n] of nodes) if (n.graphId) shownIds.set(n.graphId, key);
  const carriers = new Map<string, number>();
  for (const e of doc.edges)
    if (e.kind === 'has_keyword' && shownIds.has(e.source))
      carriers.set(e.target, (carriers.get(e.target) ?? 0) + 1);
  const maxCarried = Math.max(1, ...carriers.values());
  for (const [id, count] of carriers) {
    const kw = byId.get(id) as GraphNode;
    const at = layout.get(id) ?? { x: 0, y: 0 };
    const isSelected = id === selected;
    nodes.set(id, {
      x: at.x,
      y: at.y,
      papers: count,
      size: 2 + 3 * Math.sqrt(count / maxCarried) + (isSelected ? 2 : 0),
      // A ring: the sky inside, the keyword grey around it.
      color: SKY.bg,
      borderColor: isSelected ? SKY.mark : flatten(`${SKY.keyword.slice(0, 7)}cc`),
      zIndex: isSelected ? 2 : 0,
      label: kw.label,
      forceLabel: isSelected,
      state: 'include',
      hollow: true,
      dashed: false,
      overridden: false,
      queries: [],
      hue: SKY.keyword,
      glow: 0,
      ring: 0,
      kind: 'keyword',
      graphId: id,
    });
    shownIds.set(id, id);
  }

  const edges: EdgeAttrs[] = [];
  const neighbours = new Map<string, Set<string>>();
  const link = (a: string, b: string) => {
    (neighbours.get(a) ?? neighbours.set(a, new Set()).get(a)!).add(b);
    (neighbours.get(b) ?? neighbours.set(b, new Set()).get(b)!).add(a);
  };
  const flows = new Map<string, Flow>();
  for (const e of doc.edges) {
    const s = shownIds.get(e.source);
    const t = shownIds.get(e.target);
    if (!s || !t || s === t) continue;
    if (!view.kinds.has(e.kind)) continue;
    link(s, t);
    const kind = e.kind as EdgeKind;
    const from = byId.get(e.source)?.community ?? null;
    const to = byId.get(e.target)?.community ?? null;
    if (kind === 'cites') {
      const hex = colour(from).slice(0, 7);
      // Between communities, a citation is a faint thread; their sum is the flow
      // arrow drawn across the gap, and the thread lights when an end is hovered.
      const across = from !== null && to !== null && from !== to;
      if (across) {
        const f =
          flows.get(`${from}>${to}`) ??
          flows.set(`${from}>${to}`, { from, to, count: 0 }).get(`${from}>${to}`)!;
        f.count++;
      }
      edges.push({
        key: `${kind}:${s}>${t}`,
        source: s,
        target: t,
        kind,
        color: flatten(`${hex}${across ? '1c' : '66'}`),
        size: across ? 0.5 : 0.8,
        type: 'arrow',
        hot: flatten(`${hex}dd`),
      });
      continue;
    }
    // A keyword link into another community's disc is a faint thread too.
    const alpha = kind === 'has_keyword' ? (from !== to ? '0e' : '22') : '38';
    edges.push({
      key: `${kind}:${s}>${t}`,
      source: s,
      target: t,
      kind,
      color: flatten(`${SKY.edge.slice(0, 7)}${alpha}`),
      size: kind === 'has_keyword' ? 0.4 : Math.min(2, 0.4 + 0.3 * Math.log2(1 + e.weight)),
      type: 'line',
      hot: flatten(`${SKY.edge.slice(0, 7)}aa`),
    });
  }

  // Each community's nebula sits where its shown papers are.
  const communities: CommunitySpot[] = [];
  for (const c of doc.communities) {
    const pts = [...nodes.values()].filter(
      (n) => n.kind === 'paper' && n.graphId && byId.get(n.graphId)?.community === c.id,
    );
    if (!pts.length) continue;
    const x = pts.reduce((s, p) => s + p.x, 0) / pts.length;
    const y = pts.reduce((s, p) => s + p.y, 0) / pts.length;
    const r = Math.sqrt(pts.reduce((s, p) => s + (p.x - x) ** 2 + (p.y - y) ** 2, 0) / pts.length);
    communities.push({
      id: c.id,
      label: c.label,
      colour: colour(c.id),
      x,
      y,
      r: Math.max(r, 24),
      papers: pts.length,
    });
  }
  const halos = [...nodes]
    .filter(([, n]) => n.kind === 'paper' && n.state !== 'exclude' && n.glow > 0.05)
    .sort((a, b) => b[1].glow - a[1].glow)
    .slice(0, HALOS)
    .map(([k]) => k);
  // The camera frames the graph, with a margin for labels and halos; the rim
  // joins it only when excluded papers are asked for.
  const framedNodes = [...nodes.values()].filter((n) => n.graphId || showExcluded);
  const xs = framedNodes.map((n) => n.x);
  const ys = framedNodes.map((n) => n.y);
  const pad = 40;
  const bbox = xs.length
    ? {
        x: [Math.min(...xs) - pad, Math.max(...xs) + pad] as [number, number],
        y: [Math.min(...ys) - pad, Math.max(...ys) + pad] as [number, number],
      }
    : undefined;
  return {
    nodes,
    bbox,
    tracks: [],
    hues: new Map(),
    failed: new Set(),
    // The camera frames the graph; the rim joins it only when shown.
    radius: [...nodes.values()].some((n) => !n.graphId) ? RIM + 60 : LAYOUT_RADIUS * 1.1,
    halos,
    graph: {
      edges,
      communities,
      neighbours,
      flows: [...flows.values()].sort((a, b) => b.count - a.count || a.from - b.from || a.to - b.to),
    },
  };
}
