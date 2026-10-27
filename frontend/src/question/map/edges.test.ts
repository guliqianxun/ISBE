// The map's edges apart from sigma (#55): kept in step with the scene, and lit
// for a hovered node.
import Graph from 'graphology';
import { describe, expect, it } from 'vitest';
import { edgeLook, syncEdges } from './edges';
import type { EdgeAttrs } from './graphScene';

const edge = (source: string, target: string, kind: EdgeAttrs['kind'] = 'cites'): EdgeAttrs => ({
  key: `${kind}:${source}>${target}`,
  source,
  target,
  kind,
  color: '#111111',
  size: 1,
  type: kind === 'cites' ? 'arrow' : 'line',
  hot: '#eeeeee',
});

describe('syncEdges', () => {
  it('adds, updates and drops edges by key, a citation and a coupling side by side', () => {
    const g = new Graph({ type: 'directed', multi: true });
    for (const n of ['a', 'b', 'c']) g.addNode(n);
    syncEdges(g, [edge('a', 'b'), edge('a', 'b', 'coupling'), edge('b', 'c')]);
    expect(g.edges().sort()).toEqual(['cites:a>b', 'cites:b>c', 'coupling:a>b']);
    syncEdges(g, [{ ...edge('a', 'b'), color: '#222222' }]);
    expect(g.edges()).toEqual(['cites:a>b']);
    expect(g.getEdgeAttribute('cites:a>b', 'color')).toBe('#222222');
  });

  it('waits for an edge whose end is not on the map yet', () => {
    const g = new Graph({ type: 'directed', multi: true });
    g.addNode('a');
    syncEdges(g, [edge('a', 'z')]);
    expect(g.size).toBe(0);
    g.addNode('z');
    syncEdges(g, [edge('a', 'z')]);
    expect(g.edges()).toEqual(['cites:a>z']);
  });
});

describe('edgeLook', () => {
  const data = { color: '#111111', hot: '#eeeeee' };
  it('leaves edges alone when nothing is hovered', () => {
    expect(edgeLook(data, ['a', 'b'], null)).toBe(data);
  });
  it("shows only the hovered node's edges, lit and on top", () => {
    expect(edgeLook(data, ['a', 'b'], 'b')).toEqual({ color: '#eeeeee', hot: '#eeeeee', zIndex: 1 });
    expect(edgeLook(data, ['a', 'b'], 'c')).toEqual({ ...data, hidden: true });
  });
});
