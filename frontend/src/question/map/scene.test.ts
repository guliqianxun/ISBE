import { describe, expect, it } from 'vitest';
import { apply, emptyRun } from '../run';
import { recordedLog } from '../../test/recorded';
import { scene } from './scene';
import { ARM, between, brightness, CORE, flatten, hue, oklch, onTrack, SKY, tracks } from './sky';

describe('sky encoding', () => {
  it('mixes a translucent color into the sky, since the map draws opaque colors', () => {
    expect(flatten('#ffffff00')).toBe(SKY.bg);
    expect(flatten('#123456')).toBe('#123456');
    expect(flatten('#ffffffff', 0)).toBe(SKY.bg);
    expect(flatten('#ffffff80')).toMatch(/^#[0-9a-f]{6}$/);
  });

  it('converts oklch to sRGB hex', () => {
    expect(oklch(1, 0, 0)).toBe('#ffffff');
    expect(oklch(0, 0, 0)).toBe('#000000');
    expect(SKY.bg).toMatch(/^#[0-9a-f]{6}$/);
    expect(oklch(0.5, 0, 0, 0.5)).toMatch(/^#[0-9a-f]{8}$/);
  });

  it('hands out hues so the first few never neighbour, and shares one after nine', () => {
    const first = Array.from({ length: 9 }, (_, i) => hue(i));
    expect(new Set(first).size).toBe(9);
    expect(hue(9)).toBe(SKY.other);
    expect(hue(12)).toBe(SKY.other);
  });

  it('brightens with citations and repeats, within bounds', () => {
    expect(brightness(0, 100, 0)).toBeCloseTo(0.45);
    expect(brightness(100, 100, 0)).toBeCloseTo(1);
    expect(brightness(0, 100, 2)).toBeCloseTo(0.57);
    expect(brightness(0, 100, 9)).toBeCloseTo(0.63);
  });

  it('gives each query a spiral arm, longest for the highest priority', () => {
    const t = tracks([
      { id: 'q1', query: 'a', layer: 'canonical', priority: 1 },
      { id: 'q2', query: 'b', layer: 'canonical', priority: 1 },
      { id: 'q3', query: 'c', layer: 'emerging', priority: 3 },
    ]);
    expect(t.get('q1')!.outer).toBe(ARM);
    expect(t.get('q2')!.outer).toBe(ARM);
    expect(t.get('q3')!.outer).toBeLessThan(ARM);
    expect(new Set([...t.values()].map((a) => a.angle)).size).toBe(3);
    // Rank 1 sits by the core, the last rank near the tip, the same place every time.
    const a = onTrack(t.get('q1')!, 1, 'openalex:W1');
    expect(onTrack(t.get('q1')!, 1, 'openalex:W1')).toEqual(a);
    expect(Math.hypot(a.x, a.y)).toBeLessThan(CORE + 30);
    const z = onTrack(t.get('q1')!, 100, 'openalex:W1');
    expect(Math.hypot(z.x, z.y)).toBeGreaterThan(ARM - 80);
  });

  it('pulls a paper several queries found towards the core', () => {
    const one = between([{ x: 300, y: 0 }]);
    const two = between([
      { x: 300, y: 0 },
      { x: 0, y: 300 },
    ]);
    expect(one).toEqual({ x: 300, y: 0 });
    expect(Math.hypot(two.x, two.y)).toBeLessThan(Math.hypot(150, 150));
  });
});

describe('map scene', () => {
  it('lights every star of the recorded run; excluded ones are hollow and sink', () => {
    const state = apply(emptyRun(), recordedLog());
    const s = scene(state, null);
    expect(s.nodes.size).toBe(state.stars.size);
    expect(s.tracks).toHaveLength(state.plan!.queries.length);
    const excluded = [...s.nodes.values()].filter((n) => n.state === 'exclude');
    const included = [...s.nodes.values()].filter((n) => n.state === 'include');
    expect(excluded.length).toBeGreaterThan(0);
    expect(excluded.every((n) => n.hollow && n.zIndex === 0 && n.label === null && n.color === SKY.bg)).toBe(
      true,
    );
    expect(included.every((n) => !n.hollow && n.zIndex === 1)).toBe(true);
    // "Show excluded" fills them in again.
    const shown = scene(state, null, true);
    expect(
      [...shown.nodes.values()].filter((n) => n.state === 'exclude').every((n) => n.color !== SKY.bg),
    ).toBe(true);
  });

  it('rings and labels the selected star, even when excluded', () => {
    const state = apply(emptyRun(), recordedLog());
    const key = [...scene(state, null).nodes].find(([, n]) => n.state === 'exclude')![0];
    const node = scene(state, key).nodes.get(key)!;
    expect(node.borderColor).toBe(SKY.mark);
    expect(node.label).not.toBeNull();
    expect(node.forceLabel).toBe(true);
    expect(node.zIndex).toBe(2);
  });

  it('marks the track of a failed query', () => {
    const log = recordedLog().slice(0, 6);
    const state = apply(emptyRun(), [
      ...log,
      {
        schema: 'question_event@1',
        run_id: 'r',
        seq: 99,
        at: '',
        type: 'error',
        data: { stage: 'search', message: 'failed', fatal: false, query_id: 'q2' },
      },
    ]);
    expect([...scene(state, null).failed]).toEqual(['q2']);
  });
});
