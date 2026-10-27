// The paper list's PageRank sort (#57): once the graph is read, papers rank by it,
// under any key the stream knows them by; papers outside the graph come last.
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, expect, it } from 'vitest';
import { PrefsProvider, STORAGE_KEY } from '../prefs';
import { recordedLog } from '../test/recorded';
import { apply, emptyRun } from './run';
import { SidePanel } from './SidePanel';

beforeEach(() => localStorage.setItem(STORAGE_KEY, JSON.stringify({ lang: 'en' })));

it('sorts by PageRank once there is a graph, unranked papers last', async () => {
  const state = apply(emptyRun(), recordedLog());
  const stars = [...state.stars.values()].sort(
    (a, b) => (a.paper.cited_by_count ?? 0) - (b.paper.cited_by_count ?? 0),
  );
  // The two least cited papers rank highest; nothing else is in the graph.
  const rank = new Map([
    [stars[0]!.key, 0.6],
    [stars[1]!.key, 0.3],
  ]);
  render(
    <PrefsProvider>
      <SidePanel state={state} selected={null} onSelect={() => {}} rank={rank} />
    </PrefsProvider>,
  );
  await userEvent.selectOptions(screen.getByLabelText('Sort'), 'pagerank');
  const titles = within(screen.getByRole('list'))
    .getAllByRole('button')
    .map((b) => b.querySelector('.q-paper-title')!.textContent);
  expect(titles.slice(0, 2)).toEqual([stars[0]!.paper.title, stars[1]!.paper.title]);
});

it('offers no PageRank sort before the graph is read', () => {
  const state = apply(emptyRun(), recordedLog());
  render(
    <PrefsProvider>
      <SidePanel state={state} selected={null} onSelect={() => {}} />
    </PrefsProvider>,
  );
  expect(within(screen.getByLabelText('Sort')).queryByRole('option', { name: 'By PageRank' })).toBeNull();
});
