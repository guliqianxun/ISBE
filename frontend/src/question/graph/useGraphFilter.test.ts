// The run page's one graph filter (#55): a rebuilt graph shows every community again.
import { act, renderHook } from '@testing-library/react';
import { expect, it } from 'vitest';
import { NO_FILTER } from './model';
import { useGraphFilter } from './useGraphFilter';

it('clears hidden communities when the fingerprint changes, and keeps the rest', () => {
  const { result, rerender } = renderHook(({ fp }) => useGraphFilter(fp), { initialProps: { fp: 'a' } });
  const exclude = [{ id: 'ai', label: 'AI', words: ['learning'] }];
  act(() => result.current[1]({ ...NO_FILTER, hidden: [1, 2], exclude, from: 2000 }));
  rerender({ fp: 'a' });
  expect(result.current[0].hidden).toEqual([1, 2]);
  rerender({ fp: 'b' });
  expect(result.current[0]).toEqual({ ...NO_FILTER, hidden: [], exclude, from: 2000 });
});
