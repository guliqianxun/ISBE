// The graph view's model (#113): the client's lineage and main paths equal the ones
// isbe/question/graph.py stored for the same graph (graph-views.json, written by
// tests/py/question/test_graph.py), and the filter re-prunes both.
import { describe, expect, it } from 'vitest';
import fixture from './graph-views.json';
import {
  decode,
  inputs,
  kept,
  lineage,
  mainPaths,
  matches,
  share,
  NO_FILTER,
  PRESETS,
  rankByKey,
  related,
  rows,
  type Filter,
  type GraphDoc,
  type GraphNode,
  type Term,
} from './model';

const doc = fixture as unknown as GraphDoc;
const views = (f: Filter) => {
  const inp = inputs(doc, kept(doc, f));
  return { lineage: lineage(inp), paths: mainPaths(inp), keep: kept(doc, f) };
};
const ai: Term = { ...PRESETS.ai, label: 'AI' };
const word = (w: string): Term => ({ id: w, label: w, words: [w] });
const titled = (title: string, keywords: string[] = []): GraphNode => ({
  id: 'p',
  kind: 'paper',
  label: title,
  community: 0,
  metrics: {},
  paper: { key: 'p', title, keywords },
});

describe('the views', () => {
  it('are the ones Python computed, with no filter', () => {
    const v = views(NO_FILTER);
    expect(v.lineage).toEqual(doc.lineage);
    expect(v.paths).toEqual(doc.main_paths);
    expect(v.keep.size).toBe(23);
  });

  it('drop the AI papers and their keyword when AI is excluded', () => {
    const v = views({ ...NO_FILTER, exclude: [ai] });
    expect(v.keep.size).toBe(17);
    expect(v.lineage.keywords.map((k) => k.label)).toEqual([
      'ensemble spread',
      'forecast calibration',
      'verification',
    ]);
    const ml = new Set(
      doc.nodes.filter((n) => n.paper?.keywords?.includes('machine learning')).map((n) => n.id),
    );
    expect(v.paths.some((p) => ml.has(p.source) || ml.has(p.target))).toBe(false);
    expect(v.paths[0]?.weight).toBe(1);
  });

  it('are the ones Python computed without the machine-learning papers', () => {
    const v = views({ ...NO_FILTER, exclude: [word('machine learning')] });
    expect(v.lineage).toEqual(fixture.without_machine_learning.lineage);
    expect(v.paths).toEqual(fixture.without_machine_learning.main_paths);
  });

  it('keep only what a focus term matches', () => {
    const v = views({ ...NO_FILTER, focus: [word('verification')] });
    // Four dated verification papers, three that also carry calibration, and paper 22 (no year).
    expect(v.keep.size).toBe(7);
    expect(v.lineage.keywords.map((k) => k.label)).toEqual(['verification']);
    expect(v.lineage.edges).toEqual([]);
  });

  it('leave out hidden communities and papers outside the years', () => {
    const community = (n: GraphNode) => n.community;
    const hidden = doc.nodes.find((n) => n.kind === 'paper')!.community!;
    const v = views({ ...NO_FILTER, hidden: [hidden] });
    expect(v.keep.size).toBeLessThan(23);
    for (const id of v.keep) expect(community(doc.nodes.find((n) => n.id === id)!)).not.toBe(hidden);
    // The six calibration papers and the 2006 verification paper; paper 22 has no year.
    expect(views({ ...NO_FILTER, from: 2005, to: 2010 }).keep.size).toBe(7);
  });
});

describe('matching', () => {
  it('matches at the start of a word, in the title or the keywords', () => {
    expect(matches(titled('Statistical post-processing of wind'), [word('statistic')])).toBe(true);
    expect(matches(titled('Heavy rain over the Alps'), [word('ai')])).toBe(false);
    expect(matches(titled('Wind gusts', ['Deep  Learning']), [ai])).toBe(true);
    expect(matches(titled('A CNN-free model', []), [word('cnn')])).toBe(true);
    expect(matches(titled('Anything'), [])).toBe(false);
  });
});

describe('lineage rows', () => {
  it('put each keyword under the one it grows out of, roots by community then onset', () => {
    expect(rows(doc.lineage!).map((r) => [r.kw.label, r.depth])).toEqual([
      ['ensemble spread', 0],
      ['verification', 1],
      ['forecast calibration', 1],
      ['machine learning', 2],
    ]);
  });

  it('relate a keyword to its ancestors, descendants and crossings', () => {
    expect([...related(doc.lineage!, 'kw:forecast calibration')].sort()).toEqual([
      'kw:ensemble spread',
      'kw:forecast calibration',
      'kw:machine learning',
      'kw:verification',
    ]);
    expect([...related(doc.lineage!, 'kw:machine learning')].sort()).toEqual([
      'kw:ensemble spread',
      'kw:forecast calibration',
      'kw:machine learning',
    ]);
  });
});

describe('main paths', () => {
  it('count paths exactly on deep citation chains', () => {
    // 3,000 papers, each citing five of the eight before it: the counts pass 1e300,
    // where doubles turn into Infinity and NaN.
    const years = new Map(
      Array.from({ length: 3000 }, (_, i) => [`p${String(i).padStart(4, '0')}`, 1000 + i]),
    );
    const keys = [...years.keys()];
    const cites: [string, string][] = keys.flatMap((k, i) =>
      [1, 2, 4, 6, 8].filter((d) => i >= d).map((d): [string, string] => [k, keys[i - d]!]),
    );
    const paths = mainPaths({ years, cites, carried: new Map(), community: new Map() });
    expect(paths).toHaveLength(90);
    expect(paths[0]!.weight).toBe(1);
    for (const p of paths) expect(p.weight).toBeGreaterThan(0);
    // What graph._main_paths gives for the same chain (computed 2026-10-26).
    expect(paths.slice(0, 3)).toEqual([
      { source: 'p0000', target: 'p0001', weight: 1 },
      { source: 'p2998', target: 'p2999', weight: 1 },
      { source: 'p0002', target: 'p0003', weight: 0.6186987482296435 },
    ]);
    expect(paths[89]).toEqual({ source: 'p2914', target: 'p2915', weight: 0.5529245477586368 });
  });

  it('divide counts as Python does', () => {
    expect(share(1n, 128n)).toBe(1 / 128);
    expect(share(3n, 7n)).toBe(3 / 7);
    expect(share(10n ** 400n, 3n * 10n ** 400n)).toBe(1 / 3);
    expect(share(1n, 10n ** 400n)).toBe(0);
  });

  it('rank the links by search path count and leave out links forward in time', () => {
    const years = new Map([
      ['p0', 2000],
      ['p1', 2000],
      ['p2', 2001],
      ['p3', 2002],
      ['p4', 2003],
    ]);
    const cites: [string, string][] = [
      ['p2', 'p0'],
      ['p2', 'p1'],
      ['p3', 'p2'],
      ['p4', 'p1'],
      ['p0', 'p3'],
    ];
    const paths = mainPaths({ years, cites, carried: new Map(), community: new Map() });
    expect(paths[0]).toEqual({ source: 'p2', target: 'p3', weight: 1 });
    expect(paths.slice(1).map((p) => [p.source, p.target, p.weight])).toEqual([
      ['p0', 'p2', 0.5],
      ['p1', 'p2', 0.5],
      ['p1', 'p4', 0.5],
    ]);
  });
});

describe('decode', () => {
  it('spells out the edges of the compact graph GET /graph?form=compact answers', () => {
    const wire = {
      schema: 'question_graph@1',
      run_id: 'r',
      fingerprint: 'f',
      communities: [],
      nodes: [
        { id: 'a', kind: 'paper' as const, label: 'A', community: 0, metrics: {} },
        { id: 'kw:x', kind: 'keyword' as const, label: 'x', community: 0, metrics: {} },
      ],
      edge_kinds: ['cites', 'has_keyword'],
      edge_rows: [
        [1, 0, 1, 1],
        [0, 0, 0, 2.5],
      ] as [number, number, number, number][],
    };
    const doc = decode(wire);
    expect(doc.edges).toEqual([
      { source: 'a', target: 'kw:x', kind: 'has_keyword', weight: 1 },
      { source: 'a', target: 'a', kind: 'cites', weight: 2.5 },
    ]);
    expect(doc).not.toHaveProperty('edge_rows');
    expect(() => decode({ ...wire, edge_rows: [[0, 0, 5, 1]] })).toThrow(/points past the graph/);
  });
});

describe('rankByKey', () => {
  const paper = (id: string, pagerank: number, keys: string[]): GraphNode =>
    ({
      id,
      kind: 'paper',
      label: id,
      community: 0,
      metrics: { pagerank },
      paper: { keys },
    }) as unknown as GraphNode;
  const doc = (nodes: GraphNode[]) => ({ nodes, edges: [], communities: [] }) as unknown as GraphDoc;

  it('ranks a merged paper under the key the list knows it by', () => {
    const m = rankByKey(doc([paper('doi:a', 0.3, ['doi:a', 'arxiv:1'])]), new Map([['doi:a', 'arxiv:1']]));
    expect(m.get('arxiv:1')).toBe(0.3);
  });

  it("never lets another paper's alias take a paper's own rank", () => {
    // B claimed doi:x first in the stream; A also lists doi:x among its keys.
    const keyIndex = new Map([
      ['doi:x', 'B'],
      ['A', 'A'],
      ['B', 'B'],
    ]);
    const m = rankByKey(doc([paper('B', 0.1, ['doi:x']), paper('A', 0.9, ['doi:x'])]), keyIndex);
    expect(m.get('B')).toBe(0.1);
    expect(m.get('A')).toBe(0.9);
    const reversed = rankByKey(doc([paper('A', 0.9, ['doi:x']), paper('B', 0.1, ['doi:x'])]), keyIndex);
    expect(reversed.get('B')).toBe(0.1);
  });
});
