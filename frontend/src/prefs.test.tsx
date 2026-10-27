import { act, render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { loadPrefs, PrefsProvider, savePrefs, STORAGE_KEY, usePrefs } from './prefs';

describe('preferences', () => {
  it('reads what the classic console saved', () => {
    localStorage.setItem(
      STORAGE_KEY,
      JSON.stringify({ theme: 'custom', hue: 42, mode: 'light', paper: 'sepia', lang: 'zh' }),
    );
    expect(loadPrefs()).toEqual({ theme: 'custom', hue: 42, mode: 'light', paper: 'sepia', lang: 'zh' });
  });

  it('falls back on unknown or malformed values', () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ theme: 'neon', hue: 999, mode: 'dim', lang: 'fr' }));
    const prefs = loadPrefs();
    expect([prefs.theme, prefs.hue, prefs.mode, prefs.paper]).toEqual(['signal', 200, 'dark', 'paper']);
    localStorage.setItem(STORAGE_KEY, '{not json');
    expect(loadPrefs().theme).toBe('signal');
  });

  it('keeps keys it does not know when saving', () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ future: 1, mode: 'light' }));
    savePrefs({ ...loadPrefs(), mode: 'device' });
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!)).toMatchObject({ future: 1, mode: 'device' });
  });

  it('reset keeps the language and saves', async () => {
    localStorage.setItem(STORAGE_KEY, JSON.stringify({ theme: 'iris', lang: 'zh', mode: 'light' }));
    function Probe() {
      const { prefs, reset } = usePrefs();
      return (
        <button type="button" onClick={reset}>
          {prefs.theme} {prefs.mode} {prefs.lang}
        </button>
      );
    }
    render(
      <PrefsProvider>
        <Probe />
      </PrefsProvider>,
    );
    expect(document.documentElement.lang).toBe('zh-CN');
    await act(() => userEvent.click(screen.getByRole('button')));
    expect(screen.getByRole('button')).toHaveTextContent('signal dark zh');
    expect(JSON.parse(localStorage.getItem(STORAGE_KEY)!)).toMatchObject({ theme: 'signal', lang: 'zh' });
  });
});
