// The graph view (#113) over the graph-views.json fixture: the presets and typed
// terms re-prune both views, a keyword row focuses on its keyword, and a paper opens.
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ApiError } from '../../api/client';
import { PrefsProvider, STORAGE_KEY } from '../../prefs';
import fixture from './graph-views.json';
import { GraphView } from './GraphView';
import type { GraphDoc } from './model';
import { useGraphFilter } from './useGraphFilter';

/** The view with its filter held as the run page holds it. */
function Held(props: {
  doc: GraphDoc;
  stale: boolean;
  onSelect: (k: string | null) => void;
  onRebuild?: () => Promise<void>;
}) {
  const [filter, setFilter] = useGraphFilter(props.doc.fingerprint);
  return <GraphView {...props} selected={null} filter={filter} onFilter={setFilter} />;
}

const doc = {
  schema: 'question_graph@1',
  run_id: 'run-x',
  fingerprint: 'f'.repeat(16),
  ...fixture,
} as unknown as GraphDoc;

function show(onSelect = vi.fn(), stale = false) {
  render(
    <PrefsProvider>
      <Held doc={doc} stale={stale} onSelect={onSelect} />
    </PrefsProvider>,
  );
  return onSelect;
}

const lineage = () => screen.getByRole('group', { name: 'Topic lineage' });
const rowNames = () =>
  within(lineage())
    .getAllByRole('button')
    .map((b) => b.getAttribute('aria-label')!.split(':')[0]);

beforeEach(() => localStorage.setItem(STORAGE_KEY, JSON.stringify({ lang: 'en' })));

describe('GraphView', () => {
  it('draws both views of every paper, with the coverage note', () => {
    show();
    expect(screen.getByText('23 of 23 papers shown')).toBeTruthy();
    expect(rowNames()).toEqual([
      'ensemble spread',
      'verification',
      'forecast calibration',
      'machine learning',
    ]);
    expect(screen.getByRole('group', { name: 'Citation main paths' })).toBeTruthy();
    expect(screen.getByText(/Full citations were looked up for 0 of 23 papers/)).toBeTruthy();
    expect(screen.queryByText(/This graph predates/)).toBeNull();
  });

  it('excludes AI with the preset and brings it back', async () => {
    const user = userEvent.setup();
    show();
    await user.click(screen.getByRole('button', { name: '+ AI / machine learning' }));
    expect(screen.getByText('17 of 23 papers shown')).toBeTruthy();
    expect(rowNames()).not.toContain('machine learning');
    await user.click(screen.getByRole('button', { name: 'Remove AI / machine learning' }));
    expect(screen.getByText('23 of 23 papers shown')).toBeTruthy();
  });

  it('focuses on a typed word, and clears every filter at once', async () => {
    const user = userEvent.setup();
    show();
    await user.type(screen.getByRole('textbox', { name: /^Focus on/ }), 'verification{Enter}');
    expect(screen.getByText('7 of 23 papers shown')).toBeTruthy();
    expect(rowNames()).toEqual(['verification']);
    await user.click(screen.getByRole('button', { name: 'Clear filters' }));
    expect(screen.getByText('23 of 23 papers shown')).toBeTruthy();
  });

  it('focuses on a keyword when its row is clicked', async () => {
    const user = userEvent.setup();
    show();
    await user.click(within(lineage()).getByRole('button', { name: /^machine learning:/ }));
    expect(screen.getByText('6 of 23 papers shown')).toBeTruthy();
    expect(screen.getByRole('button', { name: 'Remove machine learning' })).toBeTruthy();
  });

  it('opens a paper on the main paths', async () => {
    const user = userEvent.setup();
    const onSelect = show();
    const paths = screen.getByRole('group', { name: 'Citation main paths' });
    const [first] = within(paths).getAllByRole('button');
    await user.click(first!);
    expect(onSelect).toHaveBeenCalledWith(expect.stringMatching(/^arxiv:/));
    onSelect.mockClear();
    first!.focus();
    await user.keyboard(' ');
    expect(onSelect).toHaveBeenCalledTimes(1);
    await user.keyboard('{Enter}');
    expect(onSelect).toHaveBeenCalledTimes(2);
    // Only papers on the paths are buttons; the rest are hidden from the a11y tree.
    const circles = paths.querySelectorAll('circle');
    expect(within(paths).getAllByRole('button').length).toBeLessThan(circles.length);
  });

  it('draws a graph stored before the views existed, without a coverage note', () => {
    const old: GraphDoc = { ...doc, lineage: undefined, main_paths: undefined, coverage: undefined };
    render(
      <PrefsProvider>
        <Held doc={old} stale onSelect={vi.fn()} />
      </PrefsProvider>,
    );
    expect(rowNames()).toHaveLength(4);
    expect(screen.queryByText(/Full citations were looked up/)).toBeNull();
  });

  it('takes a four-digit year and puts anything else back', async () => {
    const user = userEvent.setup();
    show();
    const from = screen.getByRole('textbox', { name: 'From year' });
    await user.type(from, '20x{Enter}');
    expect(from).toHaveValue('');
    expect(screen.getByText('23 of 23 papers shown')).toBeTruthy();
    await user.type(from, '2005{Enter}');
    // 6 calibration, 6 machine learning, 2006 verification, the 2015 loner.
    expect(screen.getByText('14 of 23 papers shown')).toBeTruthy();
  });

  it('says when the graph is stale', () => {
    show(vi.fn(), true);
    expect(screen.getByRole('status').textContent).toMatch(/This graph predates/);
  });

  it('says why a rebuild failed, in the console language, and lets it be tried again', async () => {
    const user = userEvent.setup();
    const onRebuild = vi
      .fn<() => Promise<void>>()
      .mockRejectedValueOnce(new ApiError(409, 'The graph is built when the run ends'))
      .mockRejectedValueOnce(new ApiError(500, 'boom'))
      .mockResolvedValueOnce(undefined);
    render(
      <PrefsProvider>
        <Held doc={doc} stale onSelect={vi.fn()} onRebuild={onRebuild} />
      </PrefsProvider>,
    );
    const button = screen.getByRole('button', { name: 'Rebuild graph' });
    await user.click(button);
    expect(await screen.findByRole('alert')).toHaveTextContent('The run has not ended');
    expect(button).toBeEnabled();
    await user.click(button);
    expect(await screen.findByRole('alert')).toHaveTextContent('The rebuild failed; try again in a moment.');
    expect(screen.getByRole('alert').textContent).not.toMatch(/boom/);
    // Success clears the error; the page then reads the new graph (not stale) and
    // the keyboard lands on the card's heading.
    await user.click(button);
    await waitFor(() => expect(screen.queryByRole('alert')).toBeNull());
    expect(document.activeElement).toBe(screen.getByRole('heading', { name: 'Knowledge graph' }));
    expect(onRebuild).toHaveBeenCalledTimes(3);
  });

  it('keeps its place across a rebuild but shows every community of the new graph', async () => {
    const user = userEvent.setup();
    const at = (d: GraphDoc) => (
      <PrefsProvider>
        <Held doc={d} stale={false} onSelect={vi.fn()} />
      </PrefsProvider>
    );
    const { rerender } = render(at(doc));
    const chips = () => [...document.querySelectorAll('.q-graph-community')];
    const first = chips()[0] as HTMLElement;
    await user.click(first);
    expect(first).toHaveAttribute('aria-pressed', 'false');
    const heading = screen.getByRole('heading', { name: 'Knowledge graph' });
    heading.focus();
    rerender(at({ ...doc, fingerprint: 'e'.repeat(16) }));
    // The same heading element, still focused; the hidden community is shown again.
    expect(screen.getByRole('heading', { name: 'Knowledge graph' })).toBe(heading);
    expect(document.activeElement).toBe(heading);
    expect(chips().map((c) => c.getAttribute('aria-pressed'))).toEqual(['true', 'true', 'true', 'true']);
  });
});
