// A galaxy's shape, computed rather than drawn from a template: every paper
// is a star and the stars settle under simple forces. Stars push each other
// apart; each search pulls the papers it found towards its own hidden anchor
// (so a paper two searches found sits between them); citations between the
// papers pull hardest. The citations then give the main routes, read like a
// river network: the maximum spanning tree of the citation edges (each edge
// weighted by how often its two papers are cited inside the set), rooted at
// the most-cited paper, where an edge's flow is how many papers reach the root
// through it; the edges that carry the most flow are the main routes.
// Positions come out in unit coordinates: inside radius 1 + RIM, centred on
// the citation core. Pure and seeded, so the
// same papers always settle the same way.
import { hash, rng } from './galaxies';

export interface FieldPaper {
  key: string;
  included: boolean;
  /** Citations anywhere (OpenAlex / essaies), for the star's size. */
  cited: number;
  /** The searches that found it. */
  queries: string[];
}

export interface FieldStar {
  key: string;
  x: number;
  y: number;
  included: boolean;
  /** 0–1, from the citation count. */
  size: number;
  /** Times cited by the other papers in the set. */
  degree: number;
  tw: number;
}

export interface Edge {
  a: number;
  b: number;
  /** Papers that reach the root through this edge; 0 off the tree. */
  flow: number;
  main: boolean;
}

export interface Field {
  stars: FieldStar[];
  /** Citation edges as star indexes; `main` marks the trunk. */
  edges: Edge[];
}

/** Beyond this many papers only the most telling ones become stars. */
export const MAX_STARS = 600;
const STEPS = 160;
/** How far past the unit circle the outermost stars may sit. */
export const RIM = 0.08;

export function layoutField(papers: FieldPaper[], citations: [string, string][] = []): Field {
  const kept = [...papers]
    .sort(
      (a, b) => Number(b.included) - Number(a.included) || b.cited - a.cited || a.key.localeCompare(b.key),
    )
    .slice(0, MAX_STARS);
  const index = new Map(kept.map((p, i) => [p.key, i]));
  const n = kept.length;
  if (!n) return { stars: [], edges: [] };

  const links: [number, number][] = [];
  const seen = new Set<string>();
  for (const [from, to] of citations) {
    const a = index.get(from);
    const b = index.get(to);
    if (a === undefined || b === undefined || a === b) continue;
    const id = a < b ? `${a}:${b}` : `${b}:${a}`;
    if (seen.has(id)) continue;
    seen.add(id);
    links.push([a, b]);
  }
  const degree = new Array<number>(n).fill(0);
  // Links run from the citing paper to the cited one.
  for (const [, b] of links) degree[b]! += 1;

  // Papers in the citation web follow their citations more than their searches,
  // and start in the middle, where the heavy ones (the papers the set cites) sink.
  const linked = new Array<boolean>(n).fill(false);
  for (const [a, b] of links) linked[a] = linked[b] = true;
  const mass = degree.map((d, i) => (linked[i] ? 2.5 : 1) + Math.min(10, d) * 0.3);

  // Search anchors on a circle, the other papers near the anchors that found them.
  const queries = [...new Set(kept.flatMap((p) => p.queries))].sort();
  const anchor = new Map(
    queries.map((q, i) => {
      const a = (i / Math.max(1, queries.length)) * 2 * Math.PI;
      return [q, { x: Math.cos(a) * 0.6, y: Math.sin(a) * 0.6 }];
    }),
  );
  const x = new Float64Array(n);
  const y = new Float64Array(n);
  kept.forEach((p, i) => {
    const r = rng(hash(p.key));
    const homes = p.queries.map((q) => anchor.get(q)!).filter(Boolean);
    const hx = homes.length ? homes.reduce((s, h) => s + h.x, 0) / homes.length : 0;
    const hy = homes.length ? homes.reduce((s, h) => s + h.y, 0) / homes.length : 0;
    const home = linked[i] ? 0.15 : 1;
    x[i] = hx * home + (r() - 0.5) * 0.5;
    y[i] = hy * home + (r() - 0.5) * 0.5;
  });

  const repel = 0.25 / n;
  // The more citations there are to go by, the less the searches pull.
  const pull = 0.025 + 0.095 * Math.max(0, 1 - (2 * links.length) / n);
  for (let step = 0; step < STEPS; step++) {
    const heat = 0.1 * (1 - step / STEPS) + 0.005;
    const fx = new Float64Array(n);
    const fy = new Float64Array(n);
    for (let i = 0; i < n; i++)
      for (let j = i + 1; j < n; j++) {
        const dx = x[i]! - x[j]!;
        const dy = y[i]! - y[j]!;
        const d2 = dx * dx + dy * dy + 1e-4;
        const f = repel / d2;
        fx[i]! += dx * f;
        fy[i]! += dy * f;
        fx[j]! -= dx * f;
        fy[j]! -= dy * f;
      }
    kept.forEach((p, i) => {
      const own = linked[i] ? pull * 0.15 : pull;
      for (const q of p.queries) {
        const h = anchor.get(q);
        if (!h) continue;
        fx[i]! += (h.x - x[i]!) * own;
        fy[i]! += (h.y - y[i]!) * own;
      }
      // Gravity holds the galaxy together.
      const g = 0.03 * mass[i]!;
      fx[i]! -= x[i]! * g;
      fy[i]! -= y[i]! * g;
    });
    // Citations are springs with a short rest length, so a hub keeps its papers near, not on top.
    for (const [a, b] of links) {
      const dx = x[b]! - x[a]!;
      const dy = y[b]! - y[a]!;
      const d = Math.hypot(dx, dy) + 1e-6;
      const f = ((d - 0.22) / d) * 0.1;
      fx[a]! += dx * f;
      fy[a]! += dy * f;
      fx[b]! -= dx * f;
      fy[b]! -= dy * f;
    }
    for (let i = 0; i < n; i++) {
      const m = Math.hypot(fx[i]!, fy[i]!);
      const k = m > heat ? heat / m : 1;
      x[i]! += fx[i]! * k;
      y[i]! += fy[i]! * k;
    }
  }

  // Centre on the citation web's weight (so its core sits in the middle, not
  // wherever the uncited papers happen to pile up) or, with no citations, on
  // all the papers; scale so nearly every star is inside the unit circle and
  // pull the few stragglers in to its rim, keeping their direction.
  const anyLinked = linked.some(Boolean);
  const weight = mass.map((m, i) => (!anyLinked || linked[i] ? m : 0));
  const total = weight.reduce((s, m) => s + m, 0);
  const mx = weight.reduce((s, m, i) => s + m * x[i]!, 0) / total;
  const my = weight.reduce((s, m, i) => s + m * y[i]!, 0) / total;
  const radii = Array.from(x, (v, i) => Math.hypot(v - mx, y[i]! - my));
  const scale = 1 / Math.max(1e-6, [...radii].sort((a, b) => a - b)[Math.floor((n - 1) * 0.97)]!);
  const fit = (i: number) => {
    const r = radii[i]! * scale;
    return r > 1 ? (1 + (1 - Math.exp(-(r - 1) * 4)) * RIM) / r : 1;
  };
  const top = Math.max(1, ...kept.map((p) => p.cited));
  const stars = kept.map((p, i) => ({
    key: p.key,
    x: (x[i]! - mx) * scale * fit(i),
    y: (y[i]! - my) * scale * fit(i),
    included: p.included,
    size: Math.log1p(p.cited) / Math.log1p(top),
    degree: degree[i]!,
    tw: rng(hash(p.key) ^ 0x9e37)() * 6.28,
  }));
  return { stars, edges: mainRoutes(links, degree) };
}

/** Kruskal's maximum spanning forest over the citation edges, weighted by the
 * in-set citations of both ends; each tree is rooted at its most-cited paper
 * and an edge is a main route when at least `share` of its tree (and at least
 * three papers: itself and two behind it) flows through it, or when it cites
 * a paper the set cites three times or more. Links run citing → cited. */
export function mainRoutes(links: [number, number][], degree: number[], share = 0.04): Edge[] {
  const w = ([a, b]: [number, number]) => degree[a]! + degree[b]!;
  const order = [...links].sort((p, q) => w(q) - w(p) || p[0] - q[0] || p[1] - q[1]);
  const parent = degree.map((_, i) => i);
  const find = (i: number): number => (parent[i] === i ? i : (parent[i] = find(parent[i]!)));
  const near = degree.map(() => [] as number[]);
  const tree = new Set<string>();
  for (const [a, b] of order) {
    const ra = find(a);
    const rb = find(b);
    if (ra === rb) continue;
    parent[ra] = rb;
    near[a]!.push(b);
    near[b]!.push(a);
    tree.add(`${a}:${b}`);
  }
  // Root each tree at its most-cited paper and count the papers below every node.
  const below = degree.map(() => 1);
  const up = degree.map(() => -1);
  const size = degree.map(() => 0);
  const seen = degree.map(() => false);
  const nodes = degree.map((_, i) => i).sort((p, q) => degree[q]! - degree[p]! || p - q);
  for (const root of nodes) {
    if (seen[root] || !near[root]!.length) continue;
    const visit: number[] = [root];
    seen[root] = true;
    for (let i = 0; i < visit.length; i++)
      for (const m of near[visit[i]!]!)
        if (!seen[m]) {
          seen[m] = true;
          up[m] = visit[i]!;
          visit.push(m);
        }
    for (let i = visit.length - 1; i > 0; i--) below[up[visit[i]!]!]! += below[visit[i]!]!;
    for (const v of visit) size[v] = visit.length;
  }
  return links.map(([a, b]) => {
    if (!tree.has(`${a}:${b}`)) return { a, b, flow: 0, main: false };
    const flow = up[a] === b ? below[a]! : below[b]!;
    // A main route carries a stream of papers, or feeds a paper cited three times or more.
    return { a, b, flow, main: flow >= Math.max(3, share * size[a]!) || degree[b]! >= 3 };
  });
}
