// The map's search (#55).
import { describe, expect, it } from 'vitest';
import { findNext } from './find';

const nodes: [string, { label?: string | null }][] = [
  ['p1', { label: 'Gust forecasts' }],
  ['kw:wind', { label: 'wind' }],
  ['p2', { label: null }],
];
const titles: Record<string, string> = {
  p1: 'Probabilistic wind gust forecasts',
  p2: 'Wind speed ensembles',
};
const title = (k: string) => titles[k];

describe('findNext', () => {
  it('finds by title or label, ignoring case and spaces around', () => {
    expect(findNext(nodes, title, '  ENSEMBLES ', null)).toBe('p2');
    expect(findNext(nodes, title, 'nothing', null)).toBeNull();
    expect(findNext(nodes, title, '   ', null)).toBeNull();
  });
  it('moves on to the next match each time, round to the first', () => {
    expect(findNext(nodes, title, 'wind', null)).toBe('p1');
    expect(findNext(nodes, title, 'wind', 'p1')).toBe('kw:wind');
    expect(findNext(nodes, title, 'wind', 'kw:wind')).toBe('p2');
    expect(findNext(nodes, title, 'wind', 'p2')).toBe('p1');
    // A selection that is not a match starts from the first.
    expect(findNext(nodes, title, 'ensembles', 'p1')).toBe('p2');
  });
});
