// The map once the graph is built (#55): kept papers move to the community layout,
// hued by community and sized by PageRank; the filter and the edge kinds prune it.
import { describe, expect, it } from 'vitest';
import { recordedLog } from '../../test/recorded';
import { NO_FILTER, type GraphDoc, type GraphEdge, type GraphNode } from '../graph/model';
import { colour } from '../graph/palette';
import { apply, emptyRun, screening } from '../run';
import { DEFAULT_KINDS, graphScene, litFor, mapViewOf } from './graphScene';
import { layoutGraph, LAYOUT_RADIUS } from './layout';
import { flatten, SKY } from './sky';

const state = apply(emptyRun(), recordedLog());
const kept = [...state.stars.values()].filter((s) => screening(s) === 'include');
const ids = kept.map((s) => s.key);
const papers: GraphNode[] = kept.map((s, i) => ({
  id: s.key,
  kind: 'paper',
  label: s.paper.title,
  community: i % 3,
  metrics: { pagerank: i === 0 ? 0.5 : 0.5 / kept.length, bridge: i === 1 },
  decision: 'include',
}));
const keyword: GraphNode = { id: 'kw:wind', kind: 'keyword', label: 'wind', community: 0, metrics: {} };
const edges: GraphEdge[] = [
  { source: ids[1]!, target: ids[0]!, kind: 'cites', weight: 1 },
  { source: ids[2]!, target: ids[0]!, kind: 'cites', weight: 1 },
  { source: ids[0]!, target: 'kw:wind', kind: 'has_keyword', weight: 1 },
  { source: ids[3]!, target: 'kw:wind', kind: 'has_keyword', weight: 1 },
  { source: ids[1]!, target: ids[2]!, kind: 'coupling', weight: 3 },
  { source: ids[3]!, target: ids[0]!, kind: 'cites', weight: 1 },
  { source: ids[4]!, target: ids[0]!, kind: 'cites', weight: 1 },
];
const doc: GraphDoc = {
  schema: 'question_graph@1',
  run_id: 'r',
  fingerprint: 'f',
  nodes: [...papers, keyword],
  edges,
  communities: [0, 1, 2].map((id) => ({
    id,
    label: `topic ${id}`,
    size: papers.filter((p) => p.community === id).length,
    top_keywords: [],
  })),
};
const layout = layoutGraph(doc);
const view = { kinds: new Set<string>(DEFAULT_KINDS), keep: null };

describe('graphScene', () => {
  it('moves kept papers to the layout, hued by community and sized by PageRank', () => {
    const s = graphScene(state, doc, layout, view, null);
    const top = s.nodes.get(ids[0]!)!;
    expect({ x: top.x, y: top.y }).toEqual(layout.get(ids[0]!));
    expect(top.hue).toBe(colour(0));
    const small = s.nodes.get(ids[4]!)!;
    expect(top.size).toBeGreaterThan(small.size * 2);
    // The most central paper is a hub (one ring); the bridge carries two.
    expect(top.ring).toBe(1);
    expect(s.nodes.get(ids[1]!)!.ring).toBe(2);
    // Excluded papers are not in the graph: hidden until asked for, then on the rim.
    const excluded = [...state.stars.values()].find((x) => screening(x) === 'exclude')!;
    expect(s.nodes.has(excluded.key)).toBe(false);
    const shown = graphScene(state, doc, layout, view, null, true).nodes.get(excluded.key)!;
    expect(Math.hypot(shown.x, shown.y)).toBeGreaterThan(LAYOUT_RADIUS * 1.1);
    expect(s.tracks).toEqual([]);
  });

  it('draws keyword rings and the chosen edge kinds, citations as arrows', () => {
    const s = graphScene(state, doc, layout, view, null);
    const kw = s.nodes.get('kw:wind')!;
    expect(kw.kind).toBe('keyword');
    expect(kw.color).toBe(SKY.bg);
    expect(s.graph.edges.map((e) => [e.kind, e.type])).toEqual([
      ['cites', 'arrow'],
      ['cites', 'arrow'],
      ['has_keyword', 'line'],
      ['has_keyword', 'line'],
      ['cites', 'arrow'],
      ['cites', 'arrow'],
    ]);
    // A citation takes the citing paper's community colour, mixed into the sky:
    // strong inside a community, a faint thread across, lit on hover either way.
    expect(s.graph.edges[4]!.color).toBe(flatten(`${colour(0)}66`));
    expect(s.graph.edges[0]!.color).toBe(flatten(`${colour(1)}1c`));
    expect(s.graph.edges[0]!.hot).toBe(flatten(`${colour(1)}dd`));
    const all = graphScene(state, doc, layout, { ...view, kinds: new Set(['coupling']) }, null);
    expect(all.graph.edges.map((e) => e.kind)).toEqual(['coupling']);
    // Neighbours follow the drawn edges, for the hover highlight.
    expect([...all.graph.neighbours.get(ids[1]!)!]).toEqual([ids[2]]);
    expect(all.graph.communities.map((c) => c.label)).toEqual(['topic 0', 'topic 1', 'topic 2']);
  });

  it('sums the citations between communities into one flow per ordered pair', () => {
    const s = graphScene(state, doc, layout, view, null);
    // Communities are i % 3: 1 → 0, 2 → 0 and 4 → 0 cross, 3 → 0 stays inside.
    const expected = new Map<string, number>();
    for (const e of edges) {
      if (e.kind !== 'cites') continue;
      const [a, b] = [ids.indexOf(e.source) % 3, ids.indexOf(e.target) % 3];
      if (a !== b) expected.set(`${a}>${b}`, (expected.get(`${a}>${b}`) ?? 0) + 1);
    }
    expect(new Map(s.graph.flows.map((f) => [`${f.from}>${f.to}`, f.count]))).toEqual(expected);
    expect(s.graph.flows[0]!.count).toBe(Math.max(...expected.values()));
    // No citations drawn, no flows.
    const none = graphScene(state, doc, layout, { ...view, kinds: new Set(['has_keyword']) }, null);
    expect(none.graph.flows).toEqual([]);
  });

  it('counts the shown papers that carry a keyword', () => {
    expect(graphScene(state, doc, layout, view, null).nodes.get('kw:wind')!.papers).toBe(2);
    const keep = new Set([ids[0]!]);
    expect(graphScene(state, doc, layout, { ...view, keep }, null).nodes.get('kw:wind')!.papers).toBe(1);
  });

  it('frames the graph, and the rim only when excluded papers are shown', () => {
    const s = graphScene(state, doc, layout, view, null);
    const inGraph = [...s.nodes.values()].filter((n) => n.graphId);
    expect(s.bbox!.x[1]).toBe(Math.max(...inGraph.map((n) => n.x)) + 40);
    const rim = graphScene(state, doc, layout, view, null, true);
    expect(rim.bbox!.x[1] - rim.bbox!.x[0]).toBeGreaterThan(s.bbox!.x[1] - s.bbox!.x[0]);
  });

  it('lights a hovered node and its shown neighbours only', () => {
    const s = graphScene(state, doc, layout, view, null);
    expect(litFor(s.graph, null)).toBeNull();
    expect(litFor(undefined, ids[0]!)).toBeNull();
    expect([...litFor(s.graph, ids[1]!)!].sort()).toEqual([ids[0]!, ids[1]!].sort());
  });

  it('keeps only the papers the shared filter keeps, and the keywords they carry', () => {
    const keep = new Set([ids[1]!, ids[2]!]);
    const s = graphScene(state, doc, layout, { ...view, keep }, null);
    const shown = [...s.nodes.keys()];
    expect(shown.sort()).toEqual([ids[1]!, ids[2]!].sort());
    expect(s.graph.edges).toEqual([]);
    expect(s.graph.communities.map((c) => c.id)).toEqual([1, 2]);
  });
});

describe('mapViewOf', () => {
  const kinds = new Set<string>(DEFAULT_KINDS);
  it('keeps every paper until the shared filter narrows them', () => {
    expect(mapViewOf(doc, NO_FILTER, kinds)).toEqual({ kinds, keep: null });
    expect(mapViewOf(null, { ...NO_FILTER, hidden: [0] }, kinds).keep).toBeNull();
    const keep = mapViewOf(doc, { ...NO_FILTER, hidden: [0] }, kinds).keep!;
    expect([...keep].sort()).toEqual(
      papers
        .filter((p) => p.community !== 0)
        .map((p) => p.id)
        .sort(),
    );
  });
});

describe('community colours on the sky', () => {
  const lum = (hex: string) => {
    const [r, g, b] = [1, 3, 5].map((i) => {
      const v = parseInt(hex.slice(i, i + 2), 16) / 255;
      return v <= 0.03928 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
    });
    return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
  };
  it('keeps every community colour at least 3 : 1 against the night sky', () => {
    for (const c of [null, ...Array.from({ length: 10 }, (_, i) => i)]) {
      const ratio = (lum(colour(c)) + 0.05) / (lum(SKY.bg) + 0.05);
      expect(ratio, `community ${c}`).toBeGreaterThanOrEqual(3);
    }
  });
});
