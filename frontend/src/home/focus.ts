// A selected galaxy, read from its run's graph (#115): the field of every
// candidate (field.ts) shows how big a galaxy is, but zoomed in it is a
// burst of threads with nothing to read. Focused, the galaxy is drawn as a
// map instead: each community is a labelled sector, a paper's distance from
// the core is its year (growth rings, the oldest papers in the middle), and
// only the citation main paths are drawn, from the cited paper out to the
// citing one. Pure and deterministic, so it is tested without a canvas.
import type { GraphDoc, GraphNode } from '../question/graph/model';
import { colour } from '../question/graph/palette';
import { hash } from './galaxies';

export interface FocusStar {
  id: string;
  /** Unit coordinates: inside radius 1. */
  x: number;
  y: number;
  community: number | null;
  year: number | null;
  title: string;
  cited: number;
  /** 0–1: √PageRank over the largest. */
  weight: number;
  /** Order by PageRank, 0 first: which papers earn a label. */
  rank: number;
  /** Its sector's index in `sectors`. */
  sector: number;
}

export interface FocusSector {
  /** Null for "other": the papers with no community, and the small communities folded together. */
  community: number | null;
  label: string | null;
  keywords: string[];
  colour: string;
  /** Radians, clockwise from the top (screen coordinates). */
  a0: number;
  a1: number;
  papers: number;
}

export interface FocusRing {
  year: number;
  r: number;
}

export interface FocusLink {
  /** Star indexes: from the cited paper to the citing one. */
  from: number;
  to: number;
  /** 0–1, the path's share of the main flow. */
  weight: number;
}

export interface FocusMap {
  stars: FocusStar[];
  sectors: FocusSector[];
  rings: FocusRing[];
  links: FocusLink[];
  /** Star indexes by rank, 0 first. */
  byRank: number[];
  /** The angle left open at the top for the year labels. */
  axis: number;
}

/** GET /graph?form=map (isbe/question/graph_io.py `map_form`): the kept papers as
 * `[id, community, year, title, cited_by_count, pagerank]`, the communities and
 * the main paths. */
export interface MapWire {
  schema: 'question_graph_map@1';
  run_id: string;
  fingerprint: string;
  papers: [string, number | null, number | null, string, number, number][];
  communities: GraphDoc['communities'];
  main_paths: NonNullable<GraphDoc['main_paths']>;
}

/** The map form read as a graph with paper nodes only, as `focusMap` takes it. */
export function fromWire(w: MapWire): GraphDoc {
  return {
    schema: 'question_graph@1',
    run_id: w.run_id,
    fingerprint: w.fingerprint,
    nodes: w.papers.map(([id, community, year, title, cited, pagerank]) => ({
      id,
      kind: 'paper',
      label: title,
      community,
      metrics: { pagerank },
      decision: 'include',
      paper: { key: id, title, year, cited_by_count: cited },
    })),
    edges: [],
    communities: w.communities,
    main_paths: w.main_paths,
  };
}

/** Radius of the oldest papers and of the newest (the undated sit at 1). */
export const CORE = 0.16;
export const OUTER = 0.94;
/** The gap at the top that carries the year labels, and the gap between sectors. */
const AXIS_GAP = 0.32;
const GAP = 0.05;
/** A sector is never narrower than this, however few its papers. */
const MIN_SECTOR = 0.22;
/** Beyond this many sectors the smallest communities share one, "other". */
export const MAX_SECTORS = 10;
const GOLDEN = (Math.sqrt(5) - 1) / 2;

/** Where a year sits: by the papers' cumulative share, so the rings follow the
 * literature's growth and a decade with few papers takes little room. A paper
 * sits in the middle of its year's band; a ring (`edge`) at the band's inner
 * edge, so every paper inside the ring labelled y is older than y. */
export function yearRadius(years: number[], edge = false): (y: number | null) => number {
  const sorted = [...years].sort((a, b) => a - b);
  const n = sorted.length;
  if (!n) return () => 1;
  const below = (y: number) => {
    let lo = 0;
    let hi = n;
    while (lo < hi) {
      const mid = (lo + hi) >> 1;
      if (sorted[mid]! < y) lo = mid + 1;
      else hi = mid;
    }
    return lo;
  };
  return (y) => {
    if (y === null) return 1;
    const same = below(y + 1) - below(y);
    const share = (below(y) + (edge ? 0 : same / 2)) / n;
    // Equal shares take equal areas, so the old core is not crowded.
    return Math.sqrt(CORE ** 2 + (OUTER ** 2 - CORE ** 2) * share);
  };
}

/** Rings at round years inside the papers' span: decades, or every five years
 * when the span is short. */
export function rings(years: number[], at: (y: number) => number): FocusRing[] {
  if (!years.length) return [];
  const lo = Math.min(...years);
  const hi = Math.max(...years);
  const step = hi - lo < 25 ? 5 : 10;
  const out: FocusRing[] = [];
  for (let y = Math.ceil(lo / step) * step; y <= hi; y += step) out.push({ year: y, r: at(y) });
  // Rings closer than this would print their years on top of each other; the
  // newer ring is kept, as the old core holds few papers.
  const kept: FocusRing[] = [];
  for (const ring of out.reverse()) if (!kept.length || kept.at(-1)!.r - ring.r >= 0.05) kept.push(ring);
  return kept.reverse();
}

const yearOf = (n: GraphNode) => {
  const y = n.paper?.year;
  return typeof y === 'number' && Number.isFinite(y) ? y : null;
};

export function focusMap(doc: GraphDoc): FocusMap {
  const papers = doc.nodes
    .filter((n) => n.kind === 'paper' && n.decision !== 'exclude')
    .sort((a, b) => (b.metrics.pagerank ?? 0) - (a.metrics.pagerank ?? 0) || (a.id < b.id ? -1 : 1));
  const years = papers.map(yearOf).filter((y): y is number => y !== null);
  const at = yearRadius(years);
  const ringAt = yearRadius(years, true);
  const top = Math.max(1e-12, ...papers.map((n) => n.metrics.pagerank ?? 0));

  // Sectors: the communities by size, then "other" (null): the papers without
  // a community and, past MAX_SECTORS, the smallest communities.
  const sizes = new Map<number, number>();
  for (const n of papers) if (n.community !== null) sizes.set(n.community, (sizes.get(n.community) ?? 0) + 1);
  const big = [...sizes.keys()].sort((a, b) => sizes.get(b)! - sizes.get(a)! || a - b);
  const fold =
    big.length > MAX_SECTORS || (big.length === MAX_SECTORS && papers.some((n) => n.community === null));
  const own = new Set(fold ? big.slice(0, MAX_SECTORS - 1) : big);
  const groups = new Map<number | null, GraphNode[]>();
  for (const n of papers) {
    const c = n.community !== null && own.has(n.community) ? n.community : null;
    (groups.get(c) ?? groups.set(c, []).get(c)!).push(n);
  }
  const order = [...groups.keys()].sort((a, b) =>
    a === null ? 1 : b === null ? -1 : groups.get(b)!.length - groups.get(a)!.length || a - b,
  );
  const info = new Map(doc.communities.map((c) => [c.id, c]));
  const room = 2 * Math.PI - AXIS_GAP - GAP * Math.max(0, order.length - 1);
  const floor = Math.min(MIN_SECTOR, room / Math.max(1, order.length));
  // Each sector gets its floor, and the rest of the room by its papers.
  const spare = room - floor * order.length;
  let a = -Math.PI / 2 + AXIS_GAP / 2;
  const sectors: FocusSector[] = [];
  const stars: FocusStar[] = [];
  const rank = new Map(papers.map((n, i) => [n.id, i]));
  for (const c of order) {
    const members = groups.get(c)!;
    const width = floor + (spare * members.length) / papers.length;
    const meta = c === null ? undefined : info.get(c);
    sectors.push({
      community: c,
      label: meta?.label ?? null,
      keywords: meta?.top_keywords.slice(0, 3) ?? [],
      colour: colour(c),
      a0: a,
      a1: a + width,
      papers: members.length,
    });
    // Oldest first; consecutive papers take angles a golden step apart, so
    // papers of one year spread across the sector instead of piling up.
    const byYear = [...members].sort(
      (p, q) => (yearOf(p) ?? 1e9) - (yearOf(q) ?? 1e9) || (p.id < q.id ? -1 : 1),
    );
    const margin = Math.min(0.06, width * 0.08);
    const offset = (hash(String(c)) % 1000) / 1000;
    byYear.forEach((n, i) => {
      const f = (offset + i * GOLDEN) % 1;
      const angle = a + margin + f * (width - 2 * margin);
      // A little give along the radius, so a crowded year reads as a band.
      const r = Math.min(1, at(yearOf(n)) + ((hash(n.id) % 1000) / 1000 - 0.5) * 0.025);
      stars.push({
        id: n.id,
        x: Math.cos(angle) * r,
        y: Math.sin(angle) * r,
        community: n.community,
        year: yearOf(n),
        title: n.paper?.title ?? n.label,
        cited: n.paper?.cited_by_count ?? 0,
        weight: Math.sqrt((n.metrics.pagerank ?? 0) / top),
        rank: rank.get(n.id)!,
        sector: sectors.length - 1,
      });
    });
    a += width + GAP;
  }

  const index = new Map(stars.map((s, i) => [s.id, i]));
  const links: FocusLink[] = [];
  for (const p of doc.main_paths ?? []) {
    const from = index.get(p.source);
    const to = index.get(p.target);
    if (from === undefined || to === undefined || from === to) continue;
    links.push({ from, to, weight: p.weight });
  }
  const byRank = stars.map((_, i) => i).sort((p, q) => stars[p]!.rank - stars[q]!.rank);
  return { stars, sectors, rings: rings(years, ringAt), links, byRank, axis: -Math.PI / 2 };
}

/** A paper's label on the map: its title, cut to `max` characters, and its year. */
export function starLabel(s: FocusStar, max = 34): string {
  const t = s.title.replace(/\s+/g, ' ').trim();
  const cut = t.length > max ? `${t.slice(0, max - 1).trimEnd()}…` : t;
  return s.year === null ? cut : `${cut} · ${s.year}`;
}
