// The map's edges in graph mode (#55), apart from sigma so they can be checked
// without WebGL: keeping the drawn graph's edges in step with the scene, and how an
// edge looks while a node is hovered.
import type Graph from 'graphology';
import type { EdgeAttrs } from './graphScene';

/** Brings the map's edges to `edges`: drops the rest, adds and updates by key.
 * An edge whose end is not on the map (yet) waits for the next call. */
export function syncEdges(g: Graph, edges: EdgeAttrs[]) {
  const want = new Map(edges.map((e) => [e.key, e]));
  g.forEachEdge((key) => {
    if (!want.has(key)) g.dropEdge(key);
  });
  for (const e of edges) {
    if (!g.hasNode(e.source) || !g.hasNode(e.target)) continue;
    const attrs = { color: e.color, size: e.size, type: e.type, kind: e.kind, hot: e.hot };
    if (g.hasEdge(e.key)) g.mergeEdgeAttributes(e.key, attrs);
    else g.addDirectedEdgeWithKey(e.key, e.source, e.target, attrs);
  }
}

/** While `hovered` is set, only its own edges show, in their hot colour, on top. */
export function edgeLook<T extends { color?: string; hot?: string }>(
  data: T,
  ends: [string, string],
  hovered: string | null,
): T & { hidden?: boolean; zIndex?: number } {
  if (!hovered) return data;
  return ends.includes(hovered)
    ? { ...data, color: data.hot ?? data.color, zIndex: 1 }
    : { ...data, hidden: true };
}
