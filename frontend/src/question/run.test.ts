import { describe, expect, it } from 'vitest';
import { recordedLog } from '../test/recorded';
import { actions, apply, counts, currentStage, emptyRun, inReview, screening, type RunState } from './run';
import type { QuestionEvent } from './types';

let seq = 0;
function ev<T extends QuestionEvent['type']>(
  type: T,
  data: Extract<QuestionEvent, { type: T }>['data'],
): QuestionEvent {
  seq += 1;
  return {
    schema: 'question_event@1',
    run_id: 'r1',
    seq,
    at: '2026-10-23T10:00:00.000Z',
    type,
    data,
  } as QuestionEvent;
}
const paper = (key: string, keys = [key]) => ({ key, keys, title: `Paper ${key}`, cited_by_count: 3 });
const fold = (events: QuestionEvent[]): RunState => apply(emptyRun(), events);

describe('run state from events', () => {
  it('rebuilds the recorded run: one star per new hit, minus the dedup folds', () => {
    const log = recordedLog();
    const s = fold(log);
    const hits = log.filter((e) => e.type === 'hit');
    const merged = log.flatMap((e) => (e.type === 'dedup' ? e.data.merged : []));
    expect(s.stars.size).toBe(hits.filter((e) => e.data.new).length - merged.length);
    expect(counts(s).hits).toBe(hits.length);
    expect(s.status).toBe('done');
    expect(s.done).toBe('completed');
    expect(s.stages).toEqual({
      plan: 'end',
      search: 'end',
      screen: 'end',
      expand: 'skipped',
      graph: 'skipped',
    });
    const c = counts(s);
    expect(c.included + c.excluded + c.unscreened).toBe(s.stars.size);
    expect(s.plan?.concepts?.length).toBeGreaterThan(0);
  });

  it('gives the same state whether the log arrives at once, in pieces, or with repeats', () => {
    const log = recordedLog();
    const whole = fold(log);
    let pieces = emptyRun();
    for (let i = 0; i < log.length; i += 37) pieces = apply(pieces, log.slice(Math.max(0, i - 10), i + 37));
    expect(pieces.stars.size).toBe(whole.stars.size);
    expect(counts(pieces)).toEqual(counts(whole));
    expect(pieces.lastSeq).toBe(whole.lastSeq);
    // A reconnect that resends what was seen changes nothing.
    expect(apply(whole, log.slice(-50))).toBe(whole);
  });

  it('counts papers the citation expansion added apart from the search hits', () => {
    seq = 0;
    const s = fold([
      ev('hit', { query_id: 'q1', rank: 1, paper: paper('arxiv:1'), new: true }),
      ev('hit', { query_id: 'q2', rank: 3, paper: paper('arxiv:1'), new: false }),
      ev('hit', { query_id: 'q2', rank: 4, paper: paper('arxiv:2'), new: true }),
      ev('expand', { from_key: 'arxiv:1', via: 'cites', paper: paper('arxiv:9'), new: true }),
    ]);
    expect(counts(s)).toMatchObject({ candidates: 3, hits: 3, searched: 2, expanded: 1 });
  });

  it('follows the run states and the stage in progress', () => {
    seq = 0;
    let s = fold([ev('state', { state: 'queued' })]);
    expect(s.status).toBe('queued');
    expect(actions(s)).toMatchObject({ cancel: true, pause: false });
    s = apply(s, [ev('state', { state: 'running' }), ev('stage', { stage: 'plan', state: 'begin' })]);
    expect(currentStage(s)).toBe('plan');
    const plan = recordedLog().find((e) => e.type === 'plan')!;
    s = apply(s, [{ ...plan, seq: ++seq }, ev('stage', { stage: 'plan', state: 'end' })]);
    expect(inReview(s)).toBe(true);
    expect(actions(s).rerun).toBe(false);
    s = apply(s, [ev('stage', { stage: 'search', state: 'begin' })]);
    expect(inReview(s)).toBe(false);
    expect(currentStage(s)).toBe('search');
    s = apply(s, [ev('state', { state: 'paused' })]);
    expect(actions(s)).toMatchObject({ pause: false, resume: true, cancel: true, rerun: true });
    s = apply(s, [ev('error', { stage: 'search', message: 'q2 failed', fatal: false, query_id: 'q2' })]);
    expect(s.problems).toHaveLength(1);
    s = apply(s, [ev('error', { stage: 'search', message: 'essaies is down', fatal: true })]);
    s = apply(s, [ev('state', { state: 'failed' }), ev('done', { status: 'failed' })]);
    expect(s.fatal?.message).toBe('essaies is down');
    expect(actions(s)).toMatchObject({ pause: false, resume: false, cancel: false });
  });

  it('brightens a repeat, folds duplicates and applies verdicts and overrides', () => {
    seq = 0;
    let s = fold([
      ev('hit', {
        query_id: 'q1',
        rank: 1,
        paper: paper('openalex:W1', ['openalex:W1', 'doi:10.1/a']),
        new: true,
      }),
      ev('hit', { query_id: 'q2', rank: 4, paper: paper('openalex:W1'), new: false }),
      ev('hit', { query_id: 'q2', rank: 5, paper: paper('arxiv:2101.1'), new: true }),
    ]);
    expect(s.stars.size).toBe(2);
    expect(s.stars.get('openalex:W1')!.repeats).toBe(1);
    s = apply(s, [ev('dedup', { kept: 'openalex:W1', merged: ['arxiv:2101.1'] })]);
    expect(s.stars.size).toBe(1);
    const star = s.stars.get('openalex:W1')!;
    expect(star.merged).toEqual(['arxiv:2101.1']);
    expect(star.found).toHaveLength(3);
    expect(screening(star)).toBe('candidate');
    const verdict = {
      key: 'openalex:W1',
      decision: 'exclude' as const,
      reason: 'off topic',
      model: 'm',
      created_at: '',
    };
    s = apply(s, [ev('verdict', { verdict })]);
    expect(screening(s.stars.get('openalex:W1')!)).toBe('exclude');
    const o1 = {
      override_id: 'o1',
      key: 'arxiv:2101.1',
      decision: 'include' as const,
      actor: 'zh',
      created_at: '',
    };
    s = apply(s, [ev('override', { override: o1 })]);
    expect(screening(s.stars.get('openalex:W1')!)).toBe('include');
    // A later model verdict does not beat the person.
    s = apply(s, [ev('verdict', { verdict: { ...verdict, decision: 'exclude' } })]);
    expect(screening(s.stars.get('openalex:W1')!)).toBe('include');
    const revert = {
      override_id: 'o2',
      key: 'openalex:W1',
      decision: null,
      revokes: 'o1',
      actor: 'zh',
      created_at: '',
    };
    s = apply(s, [ev('override', { override: revert })]);
    expect(screening(s.stars.get('openalex:W1')!)).toBe('exclude');
  });

  it('places an expanded paper next to its parent', () => {
    seq = 0;
    const s = fold([
      ev('hit', { query_id: 'q1', rank: 1, paper: paper('openalex:W1'), new: true }),
      ev('expand', { from_key: 'openalex:W1', via: 'cited_by', paper: paper('openalex:W9'), new: true }),
    ]);
    expect(s.stars.get('openalex:W9')!.found).toEqual([{ from_key: 'openalex:W1', via: 'cited_by' }]);
  });
});
