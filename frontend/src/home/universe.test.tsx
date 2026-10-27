// The universe's interaction and the data hooks behind Home: the canvas gets
// a stand-in 2D context so pointers, keys and the camera run as in a browser.
import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { setCsrfToken } from '../api/client';
import { PrefsProvider, STORAGE_KEY } from '../prefs';
import type { Run } from '../question/types';
import { fakeServer } from '../test/fakeServer';
import { RIM, layoutField } from './field';
import { focusMap, type FocusMap } from './focus';
import small from './fixtures/field-121.json';
import { layout, type Galaxy } from './galaxies';
import { aim, fitAll, RING, toScreen } from './sky';
import { inView, stepFrom, Universe } from './Universe';
import { forgetFields, nextLoad, REFRESH_MS, RETRY_MS, useFields } from './useFields';
import { POLL_MS, useRuns } from './useRuns';

const W = 800;
const H = 600;

function run(id: string, over: Partial<Run> = {}, counts: Partial<Run['counts']> = {}): Run {
  return {
    run_id: id,
    question: `Question ${id}?`,
    lang: 'en',
    status: 'done',
    stage: null,
    degraded: false,
    problems: 0,
    plan_version: 1,
    review: { on: false, active: false, ends_at: null },
    options: { year_from: null, year_to: null, engine: 'arxiv' },
    pausing: false,
    cancelling: false,
    counts: { hits: 0, candidates: 100, included: 20, excluded: 70, unscreened: 0, overridden: 0, ...counts },
    last_seq: 1,
    created_at: `2026-10-0${id.length}T00:00:00Z`,
    updated_at: '2026-10-01T00:00:00Z',
    ...over,
  };
}

beforeEach(() => {
  localStorage.setItem(STORAGE_KEY, JSON.stringify({ lang: 'en' }));
  setCsrfToken('tok');
  forgetFields();
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

/** A 2D context that draws nothing, and an 800×600 canvas. */
function fakeCanvas() {
  const ctx = new Proxy(
    {},
    {
      get: (_, k) =>
        k === 'createRadialGradient'
          ? () => ({ addColorStop() {} })
          : k === 'measureText'
            ? (t: string) => ({ width: t.length * 6 })
            : () => {},
      set: () => true,
    },
  );
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue(
    ctx as unknown as CanvasRenderingContext2D,
  );
  vi.spyOn(HTMLCanvasElement.prototype, 'getBoundingClientRect').mockReturnValue({
    x: 0,
    y: 0,
    left: 0,
    top: 0,
    width: W,
    height: H,
    right: W,
    bottom: H,
    toJSON: () => ({}),
  });
}

function sky(galaxies: Galaxy[], selected: string | null = null, focus: FocusMap | null = null) {
  const onSelect = vi.fn();
  const onOpen = vi.fn();
  const onView = vi.fn();
  const view = render(
    <PrefsProvider>
      <Universe
        galaxies={galaxies}
        flagged={new Set()}
        fields={new Map()}
        selected={selected}
        focus={focus}
        onSelect={onSelect}
        onOpen={onOpen}
        onView={onView}
      />
    </PrefsProvider>,
  );
  return { onSelect, onOpen, onView, canvas: screen.getByLabelText('Universe of galaxies'), ...view };
}

const galaxies = layout([run('a'), run('bb'), run('ccc')]);
/** Where a galaxy is on screen in the first, fit-everything view. */
const onScreen = (g: Galaxy) => toScreen(fitAll(galaxies, W, H), W, H, g.x, g.y);
const down = (el: Element, id: number, x: number, y: number) =>
  fireEvent.pointerDown(el, { pointerId: id, clientX: x, clientY: y, pointerType: 'touch' });
const move = (el: Element, id: number, x: number, y: number) =>
  fireEvent.pointerMove(el, { pointerId: id, clientX: x, clientY: y, pointerType: 'touch' });
const up = (el: Element, id: number, x: number, y: number) =>
  fireEvent.pointerUp(el, { pointerId: id, clientX: x, clientY: y, pointerType: 'touch' });

describe('universe', () => {
  it('steps from nothing to the first or the last galaxy, and around', () => {
    const ids = ['a', 'b', 'c'];
    expect(stepFrom(ids, null, 1)).toBe('a');
    expect(stepFrom(ids, null, -1)).toBe('c');
    expect(stepFrom(ids, 'a', -1)).toBe('c');
    expect(stepFrom(ids, 'c', 1)).toBe('a');
    expect(stepFrom([], null, 1)).toBeUndefined();
  });

  it('answers the keys', () => {
    fakeCanvas();
    const { canvas, onSelect, onOpen, rerender } = sky(galaxies);
    fireEvent.keyDown(canvas, { key: 'ArrowLeft' });
    expect(onSelect).toHaveBeenLastCalledWith('ccc');
    fireEvent.keyDown(canvas, { key: 'ArrowRight' });
    expect(onSelect).toHaveBeenLastCalledWith('a');
    rerender(
      <PrefsProvider>
        <Universe
          galaxies={galaxies}
          flagged={new Set()}
          fields={new Map()}
          selected="bb"
          onSelect={onSelect}
          onOpen={onOpen}
        />
      </PrefsProvider>,
    );
    fireEvent.keyDown(canvas, { key: 'Enter' });
    expect(onOpen).toHaveBeenCalledWith('bb');
    fireEvent.keyDown(canvas, { key: 'Escape' });
    expect(onSelect).toHaveBeenLastCalledWith(null);
  });

  it('selects on a tap, not on a drag, a cancelled gesture or a pinch', () => {
    fakeCanvas();
    const { canvas, onSelect } = sky(galaxies);
    const p = onScreen(galaxies[1]!);
    down(canvas, 1, p.x, p.y);
    up(canvas, 1, p.x, p.y);
    expect(onSelect).toHaveBeenLastCalledWith('bb');

    // A drag pans.
    onSelect.mockClear();
    down(canvas, 1, p.x, p.y);
    move(canvas, 1, p.x + 40, p.y);
    up(canvas, 1, p.x + 40, p.y);
    expect(onSelect).not.toHaveBeenCalled();

    // The browser takes the gesture over: the release that follows is not a tap,
    // and the next move does not pan.
    down(canvas, 1, p.x, p.y);
    fireEvent.pointerCancel(canvas, { pointerId: 1, pointerType: 'touch' });
    up(canvas, 1, p.x, p.y);
    expect(onSelect).not.toHaveBeenCalled();

    // Two fingers zoom; lifting them never selects.
    down(canvas, 1, p.x - 10, p.y);
    down(canvas, 2, p.x + 10, p.y);
    move(canvas, 2, p.x + 60, p.y);
    up(canvas, 2, p.x + 60, p.y);
    up(canvas, 1, p.x - 10, p.y);
    expect(onSelect).not.toHaveBeenCalled();
  });

  it('reports the galaxies large enough to show their stars once the camera rests', async () => {
    fakeCanvas();
    const { onView } = sky(galaxies);
    await waitFor(() => expect(onView).toHaveBeenCalled());
    const cam = fitAll(galaxies, W, H);
    expect(onView).toHaveBeenLastCalledWith(inView(galaxies, cam, W, H));
    expect(inView(galaxies, cam, W, H)).toHaveLength(3);
    // Far out, they are too small to bother.
    expect(inView(galaxies, { ...cam, z: 0.05 }, W, H)).toEqual([]);
  });

  it('zooms with two fingers, and a poll does not undo the zoom', async () => {
    fakeCanvas();
    const { canvas, onView, onSelect, onOpen, rerender } = sky(galaxies);
    await waitFor(() => expect(onView).toHaveBeenLastCalledWith(expect.arrayContaining(['a', 'bb', 'ccc'])));
    const p = onScreen(galaxies[0]!);
    down(canvas, 1, p.x - 5, p.y);
    down(canvas, 2, p.x + 5, p.y);
    move(canvas, 2, p.x + 200, p.y);
    up(canvas, 2, p.x + 200, p.y);
    up(canvas, 1, p.x - 5, p.y);
    // Closer in, some galaxies leave the screen.
    await waitFor(() => expect(onView.mock.lastCall?.[0].length).toBeLessThan(3));
    const zoomed = onView.mock.calls.length;
    // The runs are read again (same galaxies, new objects): the camera stays.
    rerender(
      <PrefsProvider>
        <Universe
          galaxies={layout([run('a'), run('bb'), run('ccc')])}
          flagged={new Set()}
          fields={new Map()}
          selected={null}
          onSelect={onSelect}
          onOpen={onOpen}
          onView={onView}
        />
      </PrefsProvider>,
    );
    // Long enough for a glide back out to settle and be reported.
    await act(() => new Promise((r) => setTimeout(r, 1500)));
    expect(onView.mock.calls.length).toBe(zoomed);
  });

  it('frames a selection, ring and name, in the sky the preview card leaves open', () => {
    const g = galaxies[0]!;
    const card = { x: 18, y: 380, w: 390, h: 200 };
    for (const [w, h, cover] of [
      [1200, 800, card],
      [390, 520, { x: 18, y: 300, w: 354, h: 200 }],
    ] as const) {
      const cam = aim(galaxies, g.id, w, h, cover);
      const c = toScreen(cam, w, h, g.x, g.y);
      const R = g.r * cam.z * RING;
      const left = c.x - R >= cover.x + cover.w;
      const above = c.y + R <= cover.y;
      expect(left || above).toBe(true);
      expect(c.x - R).toBeGreaterThanOrEqual(0);
      expect(c.x + R).toBeLessThanOrEqual(w);
      expect(c.y - R).toBeGreaterThanOrEqual(0);
    }
  });

  it('draws a selected galaxy as its map, says how to read it and lists its papers', async () => {
    fakeCanvas();
    const nodes = [0, 1, 2, 3].map((i) => ({
      id: `p${i}`,
      kind: 'paper' as const,
      label: `p${i}`,
      community: i % 2,
      decision: 'include' as const,
      metrics: { pagerank: 1 / (i + 1) },
      paper: { key: `p${i}`, title: i === 1 ? '大风集合预报的校准' : `Wind paper ${i}`, year: 2000 + i },
    }));
    const map = focusMap({
      schema: 'question_graph@1',
      run_id: 'bb',
      fingerprint: 'f',
      nodes,
      edges: [],
      communities: [
        { id: 0, label: 'ensemble spread', size: 2, top_keywords: [] },
        { id: 1, label: 'windstorms', size: 2, top_keywords: [] },
      ],
      main_paths: [{ source: 'p0', target: 'p2', weight: 1 }],
    });
    const { canvas, onSelect, rerender } = sky(galaxies, 'bb', map);
    // Once the camera has glided in far enough to draw the map.
    expect(await screen.findByText(/rings: year published/)).toHaveClass('g-key');
    const list = screen.getByRole('list', { name: 'Papers on the galaxy map' });
    expect(list).toHaveTextContent('ensemble spread · 2 papers');
    // A keyboard reaching a paper shows it, its title marked with its language.
    fireEvent.focus(screen.getByRole('button', { name: /大风集合预报的校准/ }));
    const tip = document.querySelector('.g-star-tip')!;
    expect(tip).toHaveTextContent('2001 · windstorms · cited 0 times (all sources)');
    expect(tip.querySelector('b')).toHaveAttribute('lang', 'zh');
    fireEvent.blur(screen.getByRole('button', { name: /大风集合预报的校准/ }));
    expect(document.querySelector('.g-star-tip')).toBeNull();
    // The paper's citations are in its button for those who cannot see the tip.
    expect(
      screen.getByRole('button', { name: /大风集合预报的校准.*2001.*cited 0 times/ }),
    ).toBeInTheDocument();
    // A tap on a paper shows it and keeps the galaxy selected.
    const g = galaxies.find((x) => x.id === 'bb')!;
    const star = map.stars.find((x) => x.id === 'p0')!;
    await waitFor(() => {
      onSelect.mockClear();
      const cam = aim(galaxies, 'bb', W, H, null);
      const R = g.r * cam.z;
      const c = toScreen(cam, W, H, g.x, g.y);
      down(canvas, 9, c.x + star.x * R, c.y + star.y * R);
      up(canvas, 9, c.x + star.x * R, c.y + star.y * R);
      expect(document.querySelector('.g-star-tip')).toHaveTextContent('Wind paper 0');
    });
    expect(onSelect).not.toHaveBeenCalled();
    // Another selection lets the paper go.
    rerender(
      <PrefsProvider>
        <Universe
          galaxies={galaxies}
          flagged={new Set()}
          fields={new Map()}
          selected="a"
          focus={null}
          onSelect={onSelect}
          onOpen={vi.fn()}
        />
      </PrefsProvider>,
    );
    expect(document.querySelector('.g-star-tip')).toBeNull();
  });

  it('keeps the plain sky without a map', () => {
    fakeCanvas();
    sky(galaxies, 'bb');
    expect(screen.queryByText(/rings: year published/)).toBeNull();
  });

  it('shows the galaxy list when a keyboard reaches it and says what is selected', () => {
    fakeCanvas();
    sky(galaxies, 'bb');
    expect(screen.getByRole('list', { name: 'All galaxies' })).toHaveClass('g-list');
    // The whole question, not the shortened name.
    expect(screen.getByText('Selected: Question bb?')).toBeInTheDocument();
  });
});

describe('field shape', () => {
  it('puts the citation core in the middle and every star inside the ring', () => {
    const paper = (key: string, queries: string[]) => ({ key, queries, cited: 0, included: false });
    // Many uncited papers from four searches, and a cited hub with its citers from one of them.
    const papers = [
      ...Array.from({ length: 240 }, (_, i) => paper(`u${i}`, [`q${i % 4}`])),
      paper('hub', ['q0']),
      ...Array.from({ length: 30 }, (_, i) => paper(`c${i}`, ['q0'])),
    ];
    const links = Array.from({ length: 30 }, (_, i): [string, string] => [`c${i}`, 'hub']);
    const f = layoutField(papers, links);
    const hub = f.stars.find((s) => s.key === 'hub')!;
    expect(Math.hypot(hub.x, hub.y)).toBeLessThan(0.1);
    for (const s of f.stars) expect(Math.hypot(s.x, s.y)).toBeLessThanOrEqual(1 + RIM + 1e-9);
    expect(1 + RIM).toBeLessThan(RING);
  });

  it('centres a small citation web too (a real 121-paper run)', () => {
    const f = layoutField(small.papers, small.citations as [string, string][]);
    const linked = new Set(f.edges.flatMap((e) => [e.a, e.b]));
    const web = f.stars.filter((_, i) => linked.has(i));
    const mx = web.reduce((t, s) => t + s.x, 0) / web.length;
    const my = web.reduce((t, s) => t + s.y, 0) / web.length;
    expect(Math.hypot(mx, my)).toBeLessThan(0.1);
  });
});

function Runs() {
  const { runs, failed } = useRuns();
  return <p>{failed ? 'failed' : runs === null ? 'loading' : `${runs.length} runs`}</p>;
}

describe('useRuns', () => {
  it('keeps trying after a failed read, and polls while a run works', async () => {
    vi.useFakeTimers();
    let calls = 0;
    fakeServer({
      'GET /api/question/runs': () => {
        calls += 1;
        return calls === 1
          ? { status: 500, body: { error: 'boom' } }
          : { body: { runs: [run('a', { status: calls < 3 ? 'running' : 'done' })] } };
      },
    });
    render(<Runs />);
    await act(() => vi.advanceTimersByTimeAsync(0));
    expect(screen.getByText('failed')).toBeInTheDocument();
    await act(() => vi.advanceTimersByTimeAsync(POLL_MS));
    expect(screen.getByText('1 runs')).toBeInTheDocument();
    expect(calls).toBe(2);
    // Running: read again; done: stop.
    await act(() => vi.advanceTimersByTimeAsync(POLL_MS));
    expect(calls).toBe(3);
    await act(() => vi.advanceTimersByTimeAsync(POLL_MS * 5));
    expect(calls).toBe(3);
  });
});

/** Each loaded field as `id:stars:edges`. */
function Shown({ fields }: { fields: Map<string, { stars: unknown[]; edges: unknown[] }> }) {
  return (
    <p data-testid="fields">
      {[...fields].map(([id, f]) => `${id}:${f.stars.length}:${f.edges.length}`).join(' ')}
    </p>
  );
}
const shown = () => screen.getAllByTestId('fields').at(-1)!.textContent;

describe('useFields', () => {
  const fieldBody = (citations: [string, string][] = []) => ({
    papers: [
      { key: 'p1', decision: 'include', cited: 3, queries: ['q1'] },
      { key: 'p2', decision: 'exclude', cited: 0, queries: ['q1'] },
    ],
    citations,
    fetched_at: null,
  });

  it('loads only what is wanted, keeps a load that the counts outran, and remembers it', async () => {
    let reads = 0;
    fakeServer({
      'GET /api/question/runs/a/field': () => {
        reads += 1;
        return { body: fieldBody([['p2', 'p1']]) };
      },
    });
    function Fields({ gs, wanted }: { gs: Galaxy[]; wanted: string[] }) {
      return <Shown fields={useFields(gs, wanted)} />;
    }
    const at = (n: number) => layout([run('a', { status: 'running' }, { candidates: n })]);
    const { rerender } = render(<Fields gs={at(2)} wanted={[]} />);
    await act(() => new Promise((r) => setTimeout(r, 20)));
    expect(reads).toBe(0);
    rerender(<Fields gs={at(2)} wanted={['a']} />);
    // The counts move on while it loads; the load still lands.
    rerender(<Fields gs={at(3)} wanted={['a']} />);
    rerender(<Fields gs={at(4)} wanted={['a']} />);
    await waitFor(() => expect(shown()).toBe('a:2:1'));
    expect(reads).toBe(1);
    // Kept for the session: a new Home mounts with it.
    function Again() {
      return <Shown fields={useFields(at(2), [])} />;
    }
    render(<Again />);
    expect(shown()).toBe('a:2:1');
  });

  it('settles a running galaxy again as papers arrive, but only every so often', async () => {
    vi.useFakeTimers();
    let reads = 0;
    fakeServer({
      'GET /api/question/runs/a/field': () => {
        reads += 1;
        return { body: fieldBody() };
      },
    });
    const at = (n: number) => layout([run('a', { status: 'running' }, { candidates: n })]);
    function Fields({ gs }: { gs: Galaxy[] }) {
      useFields(gs, ['a']);
      return null;
    }
    const { rerender } = render(<Fields gs={at(2)} />);
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(reads).toBe(1);
    rerender(<Fields gs={at(3)} />);
    await act(() => vi.advanceTimersByTimeAsync(POLL_MS));
    expect(reads).toBe(1);
    await act(() => vi.advanceTimersByTimeAsync(REFRESH_MS));
    expect(reads).toBe(2);
    expect(nextLoad(at(3), [], 0)).toBeNull();
  });

  it('does not load twice when Home is left and opened again mid-load', async () => {
    let reads = 0;
    fakeServer({
      'GET /api/question/runs/a/field': () => {
        reads += 1;
        return { body: fieldBody() };
      },
    });
    const gs = layout([run('a', {}, { candidates: 2 })]);
    function Fields() {
      return <Shown fields={useFields(gs, ['a'])} />;
    }
    render(<Fields />).unmount();
    render(<Fields />);
    await waitFor(() => expect(shown()).toBe('a:2:0'));
    expect(reads).toBe(1);
  });

  it('tries a failed load again after a wait', async () => {
    vi.useFakeTimers();
    let reads = 0;
    fakeServer({
      'GET /api/question/runs/a/field': () => {
        reads += 1;
        return reads === 1 ? { status: 500, body: { error: 'boom' } } : { body: fieldBody() };
      },
    });
    const gs = layout([run('a', {}, { candidates: 2 })]);
    function Fields() {
      return <Shown fields={useFields(gs, ['a'])} />;
    }
    render(<Fields />);
    await act(() => vi.advanceTimersByTimeAsync(10));
    expect(reads).toBe(1);
    expect(shown()).toBe('');
    await act(() => vi.advanceTimersByTimeAsync(RETRY_MS));
    expect(reads).toBe(2);
    expect(shown()).toBe('a:2:0');
  });
});
