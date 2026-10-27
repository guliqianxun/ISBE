// Where each node of the built graph sits on the 2D star map (#55): a galaxy of
// community nebulae. Each community is laid out on its own by ForceAtlas2 over its
// inner edges, scaled to a disc whose area grows with its size, and the discs are
// packed around the largest one, biggest first. Laid out together, the papers of a
// run cite across communities so freely that a force run folds them into one ball;
// apart, each community reads as a cluster and the citations between them show as
// arrows across the gaps. The same graph gives the same layout every time.
import Graph from 'graphology';
import forceAtlas2 from 'graphology-layout-forceatlas2';
import type { GraphDoc } from '../graph/model';
import { jitter } from './sky';

/** The radius the layout is scaled to, in the map's units (the spiral's is about 600). */
export const LAYOUT_RADIUS = 520;
/** How much each edge kind pulls, as graph.py weighs them for communities. */
const PULL: Record<string, number> = { cites: 1, co_citation: 0.5, coupling: 0.5, has_keyword: 0.3 };
const SHARED_CAP = 4;
const GOLDEN = Math.PI * (3 - Math.sqrt(5));
/** A community's disc: this many units per √node before the whole is scaled. */
const DISC = 12;
/** The gap left between two discs, in the same units. */
const GAP = 22;

export type Layout = Map<string, { x: number; y: number }>;

export function layoutGraph(doc: GraphDoc): Layout {
  const groups = new Map<number | null, string[]>();
  for (const n of doc.nodes)
    (groups.get(n.community) ?? groups.set(n.community, []).get(n.community)!).push(n.id);
  const community = new Map(doc.nodes.map((n) => [n.id, n.community]));
  const inner = new Map<number, { s: string; t: string; w: number }[]>();
  for (const e of doc.edges) {
    const pull = PULL[e.kind];
    const c = community.get(e.source);
    if (!pull || c === null || c === undefined || c !== community.get(e.target) || e.source === e.target)
      continue;
    (inner.get(c) ?? inner.set(c, []).get(c)!).push({
      s: e.source,
      t: e.target,
      w: pull * Math.min(e.weight, SHARED_CAP),
    });
  }

  // Each community in its own disc, centred on 0.
  const discs: { id: number; r: number; at: Layout }[] = [];
  const papers = new Set(doc.nodes.filter((n) => n.kind === 'paper').map((n) => n.id));
  for (const [c, ids] of groups) {
    if (c === null) continue;
    // Sized by its papers: its keywords are small and sit among them.
    const r = DISC * Math.sqrt(Math.max(1, ids.filter((id) => papers.has(id)).length));
    discs.push({ id: c, r, at: disc(ids, inner.get(c) ?? [], r) });
  }
  discs.sort((a, b) => b.r - a.r || a.id - b.id);

  // Packed biggest first: each takes the first point of a sunflower spiral where it
  // overlaps no disc already placed.
  const placed: { x: number; y: number; r: number }[] = [];
  const out: Layout = new Map();
  for (const d of discs) {
    let spot = { x: 0, y: 0 };
    for (let i = 0; placed.length && i < 20000; i++) {
      const rad = 6 * Math.sqrt(i);
      const p = { x: rad * Math.cos(i * GOLDEN), y: rad * Math.sin(i * GOLDEN) };
      if (placed.every((q) => Math.hypot(q.x - p.x, q.y - p.y) >= q.r + d.r + GAP)) {
        spot = p;
        break;
      }
    }
    placed.push({ ...spot, r: d.r });
    for (const [id, p] of d.at) out.set(id, { x: spot.x + p.x, y: spot.y + p.y });
  }
  // Nodes with no community have no edge: they wait on a ring round the discs,
  // centred on them (the packing leans to one side), so a stray one does not pull
  // the frame off the communities.
  const loose = groups.get(null) ?? [];
  const mx = placed.length
    ? (Math.min(...placed.map((q) => q.x - q.r)) + Math.max(...placed.map((q) => q.x + q.r))) / 2
    : 0;
  const my = placed.length
    ? (Math.min(...placed.map((q) => q.y - q.r)) + Math.max(...placed.map((q) => q.y + q.r))) / 2
    : 0;
  const edge = Math.max(DISC, ...placed.map((q) => Math.hypot(q.x - mx, q.y - my) + q.r));
  for (const id of loose) {
    const a = jitter(id, 7) * 2 * Math.PI;
    out.set(id, { x: mx + (edge + GAP) * Math.cos(a), y: my + (edge + GAP) * Math.sin(a) });
  }

  // Scaled into the map's circle around the middle of the discs.
  const xs = [...out.values()];
  const cx = (Math.min(...xs.map((p) => p.x)) + Math.max(...xs.map((p) => p.x))) / 2 || 0;
  const cy = (Math.min(...xs.map((p) => p.y)) + Math.max(...xs.map((p) => p.y))) / 2 || 0;
  const far = Math.max(1, ...xs.map((p) => Math.hypot(p.x - cx, p.y - cy)));
  const k = LAYOUT_RADIUS / far;
  for (const [id, p] of out) out.set(id, { x: (p.x - cx) * k, y: (p.y - cy) * k });
  return out;
}

/** One community's nodes, laid out by its inner edges and scaled into a disc of radius `r`. */
function disc(ids: string[], edges: { s: string; t: string; w: number }[], r: number): Layout {
  const g = new Graph({ type: 'undirected', multi: false });
  for (const id of ids) {
    const a = jitter(id, 7) * 2 * Math.PI;
    const d = r * Math.sqrt(jitter(id, 8));
    g.addNode(id, { x: d * Math.cos(a), y: d * Math.sin(a), size: 1 });
  }
  for (const { s, t, w } of edges) {
    const had = g.edge(s, t);
    if (had) g.updateEdgeAttribute(had, 'weight', (v: number | undefined) => (v ?? 0) + w);
    else g.addEdge(s, t, { weight: w });
  }
  if (g.order > 2 && g.size > 0) {
    const big = g.order > 300;
    forceAtlas2.assign(g, {
      iterations: big ? 100 : 200,
      getEdgeWeight: 'weight',
      settings: { ...forceAtlas2.inferSettings(g), gravity: 1, scalingRatio: 6, barnesHutOptimize: big },
    });
  }
  const pts = g.mapNodes((id, a) => ({ id, x: a.x as number, y: a.y as number }));
  const cx = pts.reduce((s, p) => s + p.x, 0) / pts.length;
  const cy = pts.reduce((s, p) => s + p.y, 0) / pts.length;
  // The force run packs the well-cited core tight and flings the rest out; keeping
  // each node's direction and its rank by distance, but spreading the ranks evenly
  // over the disc's area, fills the disc so stars and labels do not pile up.
  const order = pts
    .map((p) => ({ ...p, a: Math.atan2(p.y - cy, p.x - cx), d: Math.hypot(p.x - cx, p.y - cy) }))
    .sort((a, b) => a.d - b.d || (a.id < b.id ? -1 : 1));
  const out: Layout = new Map();
  order.forEach((p, i) => {
    const d = r * Math.sqrt((i + 0.5) / order.length);
    out.set(p.id, { x: d * Math.cos(p.a), y: d * Math.sin(p.a) });
  });
  return out;
}
