import { describe, expect, it } from 'vitest';
import fixture from '../graph/graph-views.json';
import type { GraphDoc, GraphEdge, GraphNode } from '../graph/model';
import { layoutGraph, LAYOUT_RADIUS } from './layout';

/** A run of the issue's size: `n` papers in 20 communities, `m` edges mostly inside them. */
function big(n: number, m: number): GraphDoc {
  let seed = 1;
  const rnd = () => ((seed = (seed * 16807) % 2147483647) - 1) / 2147483646;
  const nodes: GraphNode[] = Array.from({ length: n }, (_, i) => ({
    id: `p${i}`,
    kind: 'paper',
    label: `P${i}`,
    community: i % 20,
    metrics: { pagerank: 1 / n },
  }));
  const edges: GraphEdge[] = [];
  for (let j = 0; j < m; j++) {
    const a = Math.floor(rnd() * n);
    const same = rnd() < 0.85;
    const b = same ? (a + 20 * (1 + Math.floor(rnd() * 20))) % n : Math.floor(rnd() * n);
    edges.push({ source: `p${a}`, target: `p${b}`, kind: j % 3 ? 'coupling' : 'cites', weight: 1 });
  }
  const communities = Array.from({ length: 20 }, (_, c) => ({
    id: c,
    label: null,
    size: n / 20,
    top_keywords: [],
  }));
  return { schema: 'question_graph@1', run_id: 'r', fingerprint: 'f', nodes, edges, communities };
}

describe('layoutGraph', () => {
  it('places every node inside the map, the same way every time', () => {
    const doc = fixture as unknown as GraphDoc;
    const a = layoutGraph(doc);
    expect(a.size).toBe(doc.nodes.length);
    for (const p of a.values()) {
      expect(Number.isFinite(p.x) && Number.isFinite(p.y)).toBe(true);
      expect(Math.hypot(p.x, p.y)).toBeLessThanOrEqual(LAYOUT_RADIUS * 1.08 + 1e-6);
    }
    expect([...layoutGraph(doc)]).toEqual([...a]);
  });

  it('keeps communities apart: a paper sits nearer its own community than the others', () => {
    const doc = big(400, 2000);
    const at = layoutGraph(doc);
    const mean = (c: number) => {
      const ps = doc.nodes.filter((n) => n.community === c).map((n) => at.get(n.id)!);
      return {
        x: ps.reduce((s, p) => s + p.x, 0) / ps.length,
        y: ps.reduce((s, p) => s + p.y, 0) / ps.length,
      };
    };
    const centres = Array.from({ length: 20 }, (_, c) => mean(c));
    const nearest = doc.nodes.filter((n) => {
      const p = at.get(n.id)!;
      const d = centres.map((c) => Math.hypot(c.x - p.x, c.y - p.y));
      return d.indexOf(Math.min(...d)) === n.community;
    }).length;
    expect(nearest / doc.nodes.length).toBeGreaterThan(0.8);
  });

  it('lays out 2,000 nodes and 10,000 edges within the budget', () => {
    const doc = big(2000, 10000);
    const t = performance.now();
    layoutGraph(doc);
    const ms = performance.now() - t;
    console.log(`layout 2,000 nodes / 10,000 edges: ${ms.toFixed(0)} ms`);
    expect(ms).toBeLessThan(2000);
  });
});
