// The graph view's data (#113): the run's `question_graph@1` from GET /graph, the
// reader's filter, and the two views the page draws from what passes it: the topic
// lineage (how keywords grow out of and cross each other over time) and the
// citation main paths. Both are computed here, the same way isbe/question/graph.py
// computes the copies in the stored graph, so a filter re-prunes them at once;
// graph-views.json (built by tests/py/question/test_graph.py) keeps the two equal.

export interface GraphPaperRecord {
  key: string;
  title: string;
  /** Every key the paper is known by (a DOI, an arXiv id, an OpenAlex id). */
  keys?: string[] | null;
  year?: number | null;
  authors?: string[] | null;
  keywords?: string[] | null;
  /** Citations anywhere (OpenAlex / essaies). */
  cited_by_count?: number | null;
}

export interface GraphNode {
  id: string;
  kind: 'paper' | 'keyword';
  label: string;
  community: number | null;
  metrics: { degree?: number; pagerank?: number; betweenness?: number; bridge?: boolean };
  paper?: GraphPaperRecord;
  decision?: 'include' | 'exclude' | 'unscreened';
  origin?: 'seed' | 'expansion';
}

export interface GraphEdge {
  source: string;
  target: string;
  kind: string;
  weight: number;
}

export interface Community {
  id: number;
  label: string | null;
  size: number;
  top_keywords: string[];
}

export interface LineageKeyword {
  id: string;
  label: string;
  papers: number;
  onset: number;
  last: number;
  per_year: [number, number][];
  community: number | null;
}

export interface LineageEdge {
  source: string;
  target: string;
  kind: 'grows_out_of' | 'crosses';
  weight: number;
}

export interface Lineage {
  keywords: LineageKeyword[];
  edges: LineageEdge[];
}

export interface PathLink {
  /** The cited (older) paper: knowledge flows from it. */
  source: string;
  /** The citing paper. */
  target: string;
  /** Its search path count over the largest, 0–1. */
  weight: number;
}

export interface GraphDoc {
  schema: string;
  run_id: string;
  fingerprint: string;
  nodes: GraphNode[];
  edges: GraphEdge[];
  communities: Community[];
  lineage?: Lineage;
  main_paths?: PathLink[];
  coverage?: { papers: number; dated: number; full_citations: number };
}

/** GET /graph?form=compact (#57): the same graph with each edge as
 * `[kind, source, target, weight]`, indexes into `edge_kinds` and `nodes`, and the
 * paper records without abstracts (isbe/question/graph_io.py). */
export interface WireGraph extends Omit<GraphDoc, 'edges'> {
  edge_kinds: string[];
  edge_rows: [number, number, number, number][];
}

/** A compact graph as the page reads it: its edges spelled out again. */
export function decode(wire: WireGraph): GraphDoc {
  const { edge_kinds: kinds, edge_rows: rows, ...rest } = wire;
  const ids = wire.nodes.map((n) => n.id);
  const edges = rows.map(([k, s, t, w]) => {
    const kind = kinds[k];
    const source = ids[s];
    const target = ids[t];
    if (kind === undefined || source === undefined || target === undefined)
      throw new Error(`graph edge [${k}, ${s}, ${t}] points past the graph`);
    return { source, target, kind, weight: w };
  });
  return { ...rest, edges };
}

// The method's parameters, as in graph.py.
export const LINEAGE_MIN = 5;
export const LINEAGE_KEYWORDS = 32;
export const CROSS_MIN = 3;
export const CROSSINGS = 24;
export const FLOW_MIN = 2;
export const MAIN_PATHS = 90;

/** A filter term: what the reader sees, and the words it matches. */
export interface Term {
  id: string;
  label: string;
  words: string[];
}

/** Ready-made terms for the commonest asks, such as "no AI, statistics please". */
export const PRESETS: Record<'ai' | 'statistics', Omit<Term, 'label'>> = {
  ai: {
    id: 'preset:ai',
    words: [
      'machine learning',
      'deep learning',
      'neural network',
      'artificial intelligence',
      'convolutional',
      'lstm',
      'transformer',
      'random forest',
      'gradient boosting',
      'graph neural',
      'data-driven',
    ],
  },
  statistics: {
    id: 'preset:statistics',
    words: [
      'statistic',
      'bayesian',
      'regression',
      'model output statistics',
      'emos',
      'quantile',
      'post-processing',
      'postprocessing',
      'calibration',
      'probabilistic',
      'analog',
      'copula',
      'kernel density',
    ],
  },
};

export interface Filter {
  /** Papers matching any of these are left out. */
  exclude: Term[];
  /** When any: only papers matching one of these stay. */
  focus: Term[];
  /** Communities left out. */
  hidden: number[];
  from: number | null;
  to: number | null;
}

export const NO_FILTER: Filter = { exclude: [], focus: [], hidden: [], from: null, to: null };

export function isFiltered(f: Filter): boolean {
  return f.exclude.length + f.focus.length + f.hidden.length > 0 || f.from !== null || f.to !== null;
}

/** Keywords folded as graph.py folds them: spaces collapsed, case folded
 * (`toLowerCase` where Python has `casefold`: "ß" stays as it is). */
export function fold(word: string): string {
  return word.split(/\s+/).filter(Boolean).join(' ').toLowerCase();
}

/** What a term is matched against: the title and the keywords. */
function text(n: GraphNode): string {
  return fold([n.paper?.title ?? n.label, ...(n.paper?.keywords ?? [])].join(' • '));
}

const escape = (s: string) => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Does the paper's text hold one of the words, at the start of a word? ("statistic"
 * matches "statistical"; "ai" does not match "rain"). */
export function matches(n: GraphNode, terms: Term[]): boolean {
  const words = terms
    .flatMap((t) => t.words)
    .map(fold)
    .filter(Boolean);
  if (!words.length) return false;
  const re = new RegExp(`(^|[^\\p{L}\\p{N}])(${words.map(escape).join('|')})`, 'u');
  return re.test(text(n));
}

export function papers(doc: GraphDoc): GraphNode[] {
  return doc.nodes.filter((n) => n.kind === 'paper');
}

const yearOf = (n: GraphNode): number | null =>
  typeof n.paper?.year === 'number' && Number.isInteger(n.paper.year) ? n.paper.year : null;

/** The papers the filter keeps, by id. */
export function kept(doc: GraphDoc, f: Filter): Set<string> {
  const hidden = new Set(f.hidden);
  const out = new Set<string>();
  for (const n of papers(doc)) {
    const y = yearOf(n);
    if (f.from !== null && (y === null || y < f.from)) continue;
    if (f.to !== null && (y === null || y > f.to)) continue;
    if (n.community !== null && hidden.has(n.community)) continue;
    if (matches(n, f.exclude)) continue;
    if (f.focus.length && !matches(n, f.focus)) continue;
    out.add(n.id);
  }
  return out;
}

/** The inputs both views need, over the kept papers. */
export interface Inputs {
  years: Map<string, number>;
  /** paper id → its keyword words (folded), for the keyword nodes it links to. */
  carried: Map<string, Set<string>>;
  community: Map<string, number | null>;
  /** citing → cited, between kept papers. */
  cites: [string, string][];
}

export function inputs(doc: GraphDoc, keep: Set<string>): Inputs {
  const years = new Map<string, number>();
  const community = new Map<string, number | null>();
  const carried = new Map<string, Set<string>>();
  for (const n of papers(doc)) {
    if (!keep.has(n.id)) continue;
    const y = yearOf(n);
    if (y !== null) years.set(n.id, y);
    community.set(n.id, n.community);
    carried.set(n.id, new Set());
  }
  // Every keyword of the paper, as graph.py's lineage reads them: the keyword nodes
  // are only the run's most common, and a filtered set has its own.
  for (const n of papers(doc))
    if (keep.has(n.id))
      for (const k of n.paper?.keywords ?? []) if (k.trim()) carried.get(n.id)!.add(fold(k));
  const cites: [string, string][] = [];
  for (const e of doc.edges)
    if (e.kind === 'cites' && keep.has(e.source) && keep.has(e.target)) cites.push([e.source, e.target]);
  return { years, carried, community, cites };
}

const cmp = (a: string, b: string) => (a < b ? -1 : a > b ? 1 : 0);

/** graph.py `_lineage`: see docs/question/graph.md. */
export function lineage({ years, carried, community, cites }: Inputs): Lineage {
  const holders = new Map<string, string[]>();
  for (const key of [...years.keys()].sort(cmp))
    for (const w of carried.get(key) ?? []) {
      if (!holders.has(w)) holders.set(w, []);
      holders.get(w)!.push(key);
    }
  const words = [...holders.keys()]
    .filter((w) => holders.get(w)!.length >= LINEAGE_MIN)
    .sort((a, b) => holders.get(b)!.length - holders.get(a)!.length || cmp(a, b))
    .slice(0, LINEAGE_KEYWORDS);
  const kw = new Map<string, LineageKeyword>();
  for (const w of words) {
    const ys = holders
      .get(w)!
      .map((k) => years.get(k)!)
      .sort((a, b) => a - b);
    const per = new Map<number, number>();
    for (const y of ys) per.set(y, (per.get(y) ?? 0) + 1);
    const votes = new Map<number, number>();
    for (const k of holders.get(w)!) {
      const c = community.get(k);
      if (c !== null && c !== undefined) votes.set(c, (votes.get(c) ?? 0) + 1);
    }
    const best = [...votes].sort((a, b) => b[1] - a[1] || a[0] - b[0])[0];
    kw.set(w, {
      id: `kw:${w}`,
      label: w,
      papers: ys.length,
      onset: ys[1]!, // at least LINEAGE_MIN years
      last: ys[ys.length - 1]!,
      per_year: [...per].sort((a, b) => a[0] - b[0]),
      community: best ? best[0] : null,
    });
  }
  const on = new Map(words.map((w) => [w, new Set(holders.get(w))]));
  const of = new Map<string, Set<string>>();
  for (const w of words)
    for (const k of holders.get(w)!) {
      if (!of.has(k)) of.set(k, new Set());
      of.get(k)!.add(w);
    }
  const flow = new Map<string, number>();
  const pair = (c: string, p: string) => `${c}\u0000${p}`;
  for (const [a, b] of cites) {
    const mine = of.get(a);
    if (!mine) continue;
    for (const c of mine)
      for (const p of of.get(b) ?? [])
        if (!mine.has(p)) flow.set(pair(c, p), (flow.get(pair(c, p)) ?? 0) + 1);
  }
  const edges: LineageEdge[] = [];
  const parent = new Map<string, string>();
  for (const c of words) {
    let best: { score: number; p: string } | null = null;
    for (const p of words) {
      const n = flow.get(pair(c, p)) ?? 0;
      if (kw.get(p)!.onset >= kw.get(c)!.onset || n < FLOW_MIN) continue;
      const score = n / Math.sqrt(on.get(c)!.size * on.get(p)!.size);
      if (!best || score > best.score || (score === best.score && p < best.p)) best = { score, p };
    }
    if (best) {
      parent.set(c, best.p);
      edges.push({
        source: `kw:${c}`,
        target: `kw:${best.p}`,
        kind: 'grows_out_of',
        weight: flow.get(pair(c, best.p))!,
      });
    }
  }
  const crossing: [number, string, string][] = [];
  words.forEach((a, i) => {
    for (const b of words.slice(i + 1)) {
      let n = 0;
      for (const k of on.get(a)!) if (on.get(b)!.has(k)) n++;
      if (n >= CROSS_MIN && parent.get(a) !== b && parent.get(b) !== a) {
        const [x, y] = [`kw:${a}`, `kw:${b}`].sort(cmp);
        crossing.push([n, x!, y!]);
      }
    }
  });
  crossing.sort((p, q) => q[0] - p[0] || cmp(p[1], q[1]) || cmp(p[2], q[2]));
  for (const [n, a, b] of crossing.slice(0, CROSSINGS))
    edges.push({ source: a, target: b, kind: 'crosses', weight: n });
  return { keywords: words.map((w) => kw.get(w)!), edges };
}

/** graph.py `_main_paths`: the citation links most source-to-sink paths run
 * through (search path count), knowledge flowing from the cited paper to the citing
 * one. Path counts grow exponentially with the depth of the citations, so they are
 * BigInts, exact like Python's ints. */
export function mainPaths({ years, cites }: Inputs, cap = MAIN_PATHS): PathLink[] {
  const keys = [...years.keys()].sort((a, b) => years.get(a)! - years.get(b)! || cmp(a, b));
  const order = new Map(keys.map((k, i) => [k, i]));
  const seen = new Set<string>();
  const links: [string, string][] = [];
  for (const [a, b] of cites) {
    if (!order.has(a) || !order.has(b) || order.get(b)! >= order.get(a)!) continue;
    const id = `${b}\u0000${a}`;
    if (seen.has(id)) continue;
    seen.add(id);
    links.push([b, a]);
  }
  const ins = new Map<string, string[]>();
  const outs = new Map<string, string[]>();
  for (const [u, v] of links) {
    (outs.get(u) ?? outs.set(u, []).get(u)!).push(v);
    (ins.get(v) ?? ins.set(v, []).get(v)!).push(u);
  }
  const before = new Map<string, bigint>();
  for (const k of keys) {
    const i = ins.get(k);
    before.set(k, i ? i.reduce((s, u) => s + before.get(u)!, 0n) : 1n);
  }
  const after = new Map<string, bigint>();
  for (const k of [...keys].reverse()) {
    const o = outs.get(k);
    after.set(k, o ? o.reduce((s, v) => s + after.get(v)!, 0n) : 1n);
  }
  const ranked = links
    .map(([u, v]) => [before.get(u)! * after.get(v)!, u, v] as const)
    .sort((p, q) => (q[0] > p[0] ? 1 : q[0] < p[0] ? -1 : 0) || cmp(p[1], q[1]) || cmp(p[2], q[2]))
    .slice(0, cap);
  const top = ranked[0]?.[0] ?? 1n;
  return ranked.map(([n, u, v]) => ({ source: u, target: v, weight: share(n, top) }));
}

/** n / top as Python's true division of ints gives it: correctly rounded. The
 * quotient is taken to at least 64 bits with a sticky bit for any remainder, so
 * Number() rounds it once, to nearest even, as Python does. */
export function share(n: bigint, top: bigint): number {
  if (n === 0n) return 0;
  const bits = (v: bigint) => v.toString(2).length;
  const shift = Math.max(0, 64 + bits(top) - bits(n));
  const scaled = n << BigInt(shift);
  let q = scaled / top;
  if (scaled % top !== 0n) q |= 1n;
  let x = Number(q);
  // Dividing by powers of two is exact (until the result underflows).
  for (let left = shift; left > 0; left -= 1000) x /= 2 ** Math.min(left, 1000);
  return x;
}

/** Lineage rows top to bottom: each root (community, then onset), followed by the
 * keywords that grow out of it, depth first, by onset; with each row's depth. */
export function rows(l: Lineage): { kw: LineageKeyword; depth: number }[] {
  const byId = new Map(l.keywords.map((k) => [k.id, k]));
  const kids = new Map<string, LineageKeyword[]>();
  const hasParent = new Set<string>();
  for (const e of l.edges)
    if (e.kind === 'grows_out_of') {
      hasParent.add(e.source);
      (kids.get(e.target) ?? kids.set(e.target, []).get(e.target)!).push(byId.get(e.source)!);
    }
  const byOnset = (a: LineageKeyword, b: LineageKeyword) => a.onset - b.onset || cmp(a.id, b.id);
  const out: { kw: LineageKeyword; depth: number }[] = [];
  const walk = (k: LineageKeyword, depth: number) => {
    out.push({ kw: k, depth });
    for (const c of (kids.get(k.id) ?? []).sort(byOnset)) walk(c, depth + 1);
  };
  const roots = l.keywords
    .filter((k) => !hasParent.has(k.id))
    .sort((a, b) => (a.community ?? Infinity) - (b.community ?? Infinity) || byOnset(a, b));
  for (const r of roots) walk(r, 0);
  return out;
}

/** Keyword ids related to `id` in the lineage: its ancestors, descendants and crossings. */
export function related(l: Lineage, id: string): Set<string> {
  const out = new Set([id]);
  const up = new Map<string, string>();
  const down = new Map<string, string[]>();
  const crosses: string[] = [];
  for (const e of l.edges) {
    if (e.kind === 'grows_out_of') {
      up.set(e.source, e.target);
      (down.get(e.target) ?? down.set(e.target, []).get(e.target)!).push(e.source);
    } else if (e.source === id) crosses.push(e.target);
    else if (e.target === id) crosses.push(e.source);
  }
  // Parents are always older, so neither walk can loop.
  for (let k = up.get(id); k; k = up.get(k)) out.add(k);
  const stack = [...(down.get(id) ?? [])];
  while (stack.length) {
    const k = stack.pop()!;
    out.add(k);
    stack.push(...(down.get(k) ?? []));
  }
  for (const k of crosses) out.add(k);
  return out;
}

/** Each paper's PageRank under the key the run's paper list knows it by: `keyIndex`
 * maps any key a paper went by to that key (a merge can rename it). A paper's own
 * id comes first; its other keys only where no paper's own id took them. */
export function rankByKey(doc: GraphDoc, keyIndex: ReadonlyMap<string, string>): Map<string, number> {
  const m = new Map<string, number>();
  const papers = doc.nodes.filter((n) => n.kind === 'paper');
  for (const n of papers) m.set(keyIndex.get(n.id) ?? n.id, n.metrics.pagerank ?? 0);
  for (const n of papers)
    for (const k of n.paper?.keys ?? []) {
      const key = keyIndex.get(k) ?? k;
      if (!m.has(key)) m.set(key, n.metrics.pagerank ?? 0);
    }
  return m;
}
