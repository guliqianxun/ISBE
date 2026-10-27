// The selected galaxy's map (#115): sectors, growth rings, main paths and labels.
import { renderHook, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeServer } from '../test/fakeServer';
import type { GraphDoc, GraphNode } from '../question/graph/model';
import {
  CORE,
  MAX_SECTORS,
  OUTER,
  focusMap,
  fromWire,
  rings,
  starLabel,
  yearRadius,
  type MapWire,
} from './focus';
import { layout } from './galaxies';
import { FOCUS_PX, focusFrame, labelled, pickStar } from './sky';
import type { Run } from '../question/types';
import { forgetFocus, useFocus } from './useFocus';

function paper(id: string, community: number | null, year: number | null, pagerank: number): GraphNode {
  return {
    id,
    kind: 'paper',
    label: id,
    community,
    decision: 'include',
    metrics: { pagerank },
    paper: { key: id, title: `Paper ${id} on wind ensembles`, year, cited_by_count: 3 },
  };
}

/** Three communities of different sizes, papers 1990–2024, a few main paths. */
function doc(): GraphDoc {
  const nodes: GraphNode[] = [];
  const sizes: [number | null, number][] = [
    [0, 30],
    [1, 12],
    [2, 4],
    [null, 2],
  ];
  for (const [c, n] of sizes)
    for (let i = 0; i < n; i++) nodes.push(paper(`p${c}-${i}`, c, 1990 + ((i * 7) % 35), 1 / (i + 1)));
  nodes.push({ ...paper('gone', 0, 2000, 9), decision: 'exclude' });
  nodes.push({ id: 'kw:wind', kind: 'keyword', label: 'wind', community: 0, metrics: {} });
  return {
    schema: 'question_graph@1',
    run_id: 'r',
    fingerprint: 'f',
    nodes,
    edges: [],
    communities: [
      { id: 0, label: 'ensemble spread', size: 30, top_keywords: ['spread', 'calibration'] },
      { id: 1, label: 'windstorms', size: 12, top_keywords: ['storm'] },
      { id: 2, label: null, size: 4, top_keywords: ['gusts'] },
    ],
    main_paths: [
      { source: 'p0-1', target: 'p1-2', weight: 1 },
      { source: 'p0-2', target: 'p0-3', weight: 0.4 },
      { source: 'gone', target: 'p0-3', weight: 0.2 },
    ],
  };
}

/** Clockwise angle from the top, 0–2π. */
const turn = (x: number, y: number) => (Math.atan2(y, x) + Math.PI / 2 + 4 * Math.PI) % (2 * Math.PI);

describe('focus map', () => {
  it('places years by their share of the papers, oldest in the core, newer outward', () => {
    const at = yearRadius([2000, 2000, 2010, 2020]);
    expect(at(1990)).toBeCloseTo(CORE);
    expect(at(2030)).toBeCloseTo(OUTER);
    expect(at(2000)).toBeLessThan(at(2010));
    expect(at(2010)).toBeLessThan(at(2020));
    expect(at(null)).toBe(1);
    // Equal shares, equal areas: the ring at the median halves the disc's area.
    const half = yearRadius([1, 1, 3, 3])(2);
    expect(half ** 2 - CORE ** 2).toBeCloseTo((OUTER ** 2 - CORE ** 2) / 2);
  });

  it('rings round years, dropping old rings that would print on top of each other', () => {
    const years = [1900, ...Array.from({ length: 60 }, (_, i) => 1990 + (i % 35))];
    const got = rings(years, yearRadius(years));
    expect(got.map((r) => r.year)).toContain(2020);
    expect(got.map((r) => r.year)).toContain(2000);
    // 1900–1980 hold one paper between them: their rings would overlap.
    expect(got.filter((r) => r.year < 1990).length).toBeLessThanOrEqual(1);
    for (let i = 1; i < got.length; i++) expect(got[i]!.r - got[i - 1]!.r).toBeGreaterThanOrEqual(0.05);
  });

  it('gives every community a sector, the largest the widest, and keeps its papers in it', () => {
    const map = focusMap(doc());
    expect(map.sectors.map((s) => s.community)).toEqual([0, 1, 2, null]);
    expect(map.sectors.map((s) => s.label)).toEqual(['ensemble spread', 'windstorms', null, null]);
    expect(map.sectors[2]!.keywords).toEqual(['gusts']);
    const width = map.sectors.map((s) => s.a1 - s.a0);
    expect(width[0]).toBeGreaterThan(width[1]!);
    expect(width[1]).toBeGreaterThan(width[2]!);
    // The sectors do not overlap, and leave the top open for the years.
    for (let i = 1; i < map.sectors.length; i++)
      expect(map.sectors[i]!.a0).toBeGreaterThan(map.sectors[i - 1]!.a1);
    const top = (map.sectors[0]!.a0 + Math.PI / 2 + 2 * Math.PI) % (2 * Math.PI);
    expect(top).toBeGreaterThan(0.1);
    // Excluded papers and keywords are not stars.
    expect(map.stars).toHaveLength(48);
    expect(map.stars.some((s) => s.id === 'gone' || s.id === 'kw:wind')).toBe(false);
    for (const s of map.stars) {
      const sec = map.sectors.find((x) => x.community === s.community)!;
      const a = turn(s.x, s.y);
      expect(a).toBeGreaterThanOrEqual(turn(Math.cos(sec.a0), Math.sin(sec.a0)) - 1e-9);
      expect(a).toBeLessThanOrEqual(turn(Math.cos(sec.a1), Math.sin(sec.a1)) + 1e-9);
      expect(Math.hypot(s.x, s.y)).toBeLessThanOrEqual(1);
    }
  });

  it('ranks by PageRank and keeps only the main paths between its stars', () => {
    const map = focusMap(doc());
    const first = map.stars.find((s) => s.rank === 0)!;
    expect(first.weight).toBe(1);
    expect(map.links).toHaveLength(2);
    const [l] = map.links;
    expect(map.stars[l!.from]!.id).toBe('p0-1');
    expect(map.stars[l!.to]!.id).toBe('p1-2');
  });

  it('folds the smallest of many communities into one "other" sector', () => {
    // 150 communities of two papers each and a few without one, as Louvain leaves small components.
    const nodes: GraphNode[] = [];
    for (let c = 0; c < 150; c++)
      for (let i = 0; i < 2 + (c < 5 ? 10 - c : 0); i++)
        nodes.push(paper(`c${c}-${i}`, c, 1990 + ((c + i) % 30), 1 / (c + i + 1)));
    for (let i = 0; i < 3; i++) nodes.push(paper(`n${i}`, null, null, 0.01));
    const map = focusMap({ ...doc(), nodes, communities: [], main_paths: [] });
    expect(map.sectors).toHaveLength(MAX_SECTORS);
    expect(map.sectors.at(-1)!.community).toBeNull();
    expect(map.sectors.slice(0, 5).map((x) => x.community)).toEqual([0, 1, 2, 3, 4]);
    expect(map.sectors.reduce((n, x) => n + x.papers, 0)).toBe(nodes.length);
    for (let i = 0; i < map.sectors.length; i++) {
      const sec = map.sectors[i]!;
      expect(sec.a1).toBeGreaterThan(sec.a0);
      if (i) expect(sec.a0).toBeGreaterThan(map.sectors[i - 1]!.a1);
    }
    for (const st of map.stars) {
      const sec = map.sectors[st.sector]!;
      const a = turn(st.x, st.y);
      expect(a).toBeGreaterThanOrEqual(turn(Math.cos(sec.a0), Math.sin(sec.a0)) - 1e-9);
      expect(a).toBeLessThanOrEqual(turn(Math.cos(sec.a1), Math.sin(sec.a1)) + 1e-9);
    }
    // Undated papers sit on the rim.
    expect(
      Math.hypot(map.stars.find((x) => x.id === 'n0')!.x, map.stars.find((x) => x.id === 'n0')!.y),
    ).toBeCloseTo(1, 1);
  });

  it('reads the map form the server sends as the graph it was cut from', () => {
    const d = doc();
    const wire: MapWire = {
      schema: 'question_graph_map@1',
      run_id: d.run_id,
      fingerprint: d.fingerprint,
      papers: d.nodes
        .filter((n) => n.kind === 'paper' && n.decision !== 'exclude')
        .map((n) => [n.id, n.community, n.paper!.year!, n.paper!.title, 3, n.metrics.pagerank!]),
      communities: d.communities,
      main_paths: d.main_paths!,
    };
    expect(focusMap(fromWire(wire))).toEqual(focusMap(d));
  });

  it('puts every ring below the papers of its year', () => {
    const map = focusMap(doc());
    for (const ring of map.rings)
      for (const st of map.stars)
        if (st.year !== null && st.year >= ring.year && st.sector < 3)
          expect(Math.hypot(st.x, st.y)).toBeGreaterThan(ring.r - 0.0126);
  });

  it('is the same map for the same graph', () => {
    expect(focusMap(doc())).toEqual(focusMap(doc()));
  });

  it('labels the top papers without overlaps, more as the map grows', () => {
    const map = focusMap(doc());
    const width = (t: string) => t.length * 6;
    const small = labelled(map, 200, width);
    const big = labelled(map, 900, width);
    expect(small.length).toBeGreaterThan(0);
    expect(big.length).toBeGreaterThan(small.length);
    for (const set of [small, big])
      for (let i = 0; i < set.length; i++)
        for (let j = i + 1; j < set.length; j++) {
          const a = set[i]!.box;
          const b = set[j]!.box;
          expect(a.x0 < b.x1 && b.x0 < a.x1 && a.y0 < b.y1 && b.y0 < a.y1).toBe(false);
        }
    expect(labelled(map, 100, width)).toEqual([]);
    // A hover left over from another map lights nothing and breaks nothing.
    expect(labelled(map, 200, width, [], map.stars.length + 5)).toEqual(small);
    // The hovered paper is always labelled.
    const last = map.stars.findIndex((s) => s.rank === map.stars.length - 1);
    expect(labelled(map, 200, width, [], last).some((l) => l.i === last)).toBe(true);
    expect(starLabel({ ...map.stars[0]!, title: 'x'.repeat(50), year: 2001 }, 10)).toBe('xxxxxxxxx… · 2001');
  });
});

describe('focus on the canvas', () => {
  const run = {
    run_id: 'r',
    question: 'Wind?',
    created_at: '2026-10-01T00:00:00Z',
    status: 'done',
    counts: { candidates: 400, included: 48 },
  } as unknown as Run;
  const [g] = layout([run]);
  const map = focusMap(doc());

  it('draws the map only for the selected galaxy, once it is large enough', () => {
    const base = { selected: g!, focus: map, w: 1000, h: 800 };
    const big = { x: g!.x, y: g!.y, z: (FOCUS_PX + 10) / g!.r };
    expect(focusFrame({ ...base, cam: big })?.R).toBeGreaterThan(FOCUS_PX);
    expect(focusFrame({ ...base, cam: { ...big, z: (FOCUS_PX - 10) / g!.r } })).toBeNull();
    expect(focusFrame({ ...base, focus: null, cam: big })).toBeNull();
    expect(focusFrame({ ...base, selected: null, cam: big })).toBeNull();
  });

  it('finds the paper under the pointer', () => {
    const cam = { x: g!.x, y: g!.y, z: 300 / g!.r };
    const s = { selected: g!, focus: map, cam, w: 1000, h: 800 };
    const star = map.stars[5]!;
    expect(pickStar(s, 500 + star.x * 300 + 2, 400 + star.y * 300 - 2)).toBe(5);
    expect(pickStar(s, 5, 5)).toBeNull();
    // Too small to be drawn as a map: no paper answers.
    const small = { ...s, cam: { ...cam, z: (FOCUS_PX - 5) / g!.r } };
    const p = focusFrame({ ...s, cam: { ...cam, z: (FOCUS_PX + 5) / g!.r } })!;
    expect(pickStar(small, p.cx + star.x * (FOCUS_PX - 5), p.cy + star.y * (FOCUS_PX - 5))).toBeNull();
  });
});

describe('loading the map', () => {
  afterEach(() => {
    forgetFocus();
    vi.unstubAllGlobals();
  });
  const make = (status: string) =>
    layout([
      {
        run_id: 'r',
        question: 'Wind?',
        created_at: '2026-10-01T00:00:00Z',
        status,
        counts: { candidates: 400, included: 48, excluded: 10 },
      } as unknown as Run,
    ])[0]!;
  // As graph_io.map_form writes it.
  const d = doc();
  const wire: MapWire = {
    schema: 'question_graph_map@1',
    run_id: d.run_id,
    fingerprint: d.fingerprint,
    papers: d.nodes
      .filter((n) => n.kind === 'paper' && n.decision !== 'exclude')
      .map((n) => [n.id, n.community, n.paper!.year!, n.paper!.title, 3, n.metrics.pagerank!]),
    communities: d.communities,
    main_paths: d.main_paths!,
  };
  const url = '/api/question/runs/r/graph?form=map';

  it('draws a kept map at once on the next selection, and reads the graph again for a rebuild', async () => {
    let graph = wire;
    const { requests } = fakeServer({ [`GET ${url}`]: () => ({ body: { graph, stale: false } }) });
    const g = make('done');
    const first = renderHook(() => useFocus(g));
    await waitFor(() => expect(first.result.current?.stars).toHaveLength(48));
    first.unmount();
    // Rebuilt after an override: the same counts, one paper fewer in the graph.
    graph = { ...wire, papers: wire.papers.slice(1) };
    const again = renderHook(() => useFocus(g));
    expect(again.result.current?.stars).toHaveLength(48);
    await waitFor(() => expect(again.result.current?.stars).toHaveLength(47));
    expect(requests.filter((r) => r.url === url)).toHaveLength(2);
  });

  it('waits for a running galaxy, and asks again for a graph that was missing', async () => {
    let built = false;
    const { requests } = fakeServer({
      [`GET ${url}`]: () => (built ? { body: { graph: wire, stale: false } } : { status: 404, body: {} }),
    });
    const busy = renderHook(() => useFocus(make('running')));
    expect(busy.result.current).toBeNull();
    expect(requests).toHaveLength(0);
    const none = renderHook(() => useFocus(make('done')));
    await waitFor(() => expect(requests).toHaveLength(1));
    expect(none.result.current).toBeNull();
    none.unmount();
    // Built from the CLI meanwhile: the next selection draws it.
    built = true;
    const later = renderHook(() => useFocus(make('done')));
    await waitFor(() => expect(later.result.current?.stars).toHaveLength(48));
  });
});
