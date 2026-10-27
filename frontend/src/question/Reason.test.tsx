import { render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it } from 'vitest';
import { PrefsProvider, STORAGE_KEY } from '../prefs';
import { Reason } from './Reason';
import { grouped } from './RunHeader';
import type { ProblemCode } from './types';

function show(props: Parameters<typeof Reason>[0]) {
  return render(
    <PrefsProvider>
      <p data-testid="r">
        <Reason {...props} />
      </p>
    </PrefsProvider>,
  ).getByTestId('r');
}

beforeEach(() => localStorage.setItem(STORAGE_KEY, JSON.stringify({ lang: 'en' })));

describe('Reason', () => {
  it('speaks first for a code it does not know; the server message is the reason, marked English', () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ lang: 'zh' }));
    const r = show({ code: 'from_a_newer_server' as ProblemCode, message: 'Something new broke' });
    expect(r).toHaveTextContent(/^这一阶段出错了。原因：Something new broke$/);
    expect(screen.getByText('Something new broke')).toHaveAttribute('lang', 'en');
  });

  it('gives the message as the reason when there is no detail', () => {
    const r = show({ code: 'stage_failed', message: 'The graph stage failed: KeyError: x', detail: null });
    expect(r).toHaveTextContent('A stage failed. Run it again');
    expect(screen.getByText('The graph stage failed: KeyError: x')).toHaveAttribute('lang', 'en');
  });
});

describe('grouped problems', () => {
  it('shows a coded problem that repeats in its stage once, with how often and its latest words', () => {
    const p = (
      seq: number,
      stage: 'screen' | 'search',
      code: 'model_failed' | undefined,
      message: string,
    ) => ({
      seq,
      stage,
      code,
      message,
    });
    const out = grouped([
      p(1, 'screen', 'model_failed', 'batch 1 failed'),
      p(2, 'search', undefined, 'old A'),
      p(3, 'screen', 'model_failed', 'batch 2 failed'),
      p(4, 'search', undefined, 'old B'),
      p(5, 'screen', 'model_failed', 'Screening stopped'),
    ]);
    expect(out.map((g) => [g.problem.seq, g.problem.message, g.times])).toEqual([
      [1, 'Screening stopped', 3],
      [2, 'old A', 1],
      [4, 'old B', 1],
    ]);
  });

  it('keeps each failed query apart, so a person sees which to rewrite', () => {
    const q = (seq: number, query_id: string) => ({
      seq,
      stage: 'search' as const,
      code: 'query_failed' as const,
      message: `query ${query_id} failed`,
      query_id,
    });
    const out = grouped([q(1, 'q1'), q(2, 'q2'), q(3, 'q1')]);
    expect(out.map((g) => [g.problem.query_id, g.times])).toEqual([
      ['q1', 2],
      ['q2', 1],
    ]);
  });
});
