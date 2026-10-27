// Per-browser preferences: theme preset or custom hue, light/dark/device mode,
// reading surface and language. Stored under the classic console's key
// (`isbe.ui`) in the same shape, so settings carry over in both directions.
import {
  createContext,
  useCallback,
  useContext,
  useLayoutEffect,
  useMemo,
  useState,
  type ReactNode,
} from 'react';

export const PRESETS = {
  signal: [175, 1],
  glacier: [235, 1],
  iris: [290, 1],
  coral: [22, 1],
  ember: [62, 1],
  moss: [145, 1],
  graphite: [250, 0],
} as const satisfies Record<string, readonly [number, number]>;

export type Preset = keyof typeof PRESETS;
export type Theme = Preset | 'custom';
export type Mode = 'light' | 'dark' | 'device';
export type Paper = 'paper' | 'sepia' | 'night';
export type Lang = 'zh' | 'en';

export interface Prefs {
  theme: Theme;
  hue: number;
  mode: Mode;
  paper: Paper;
  lang: Lang;
}

export const STORAGE_KEY = 'isbe.ui';

export const defaults = (): Prefs => ({
  theme: 'signal',
  hue: 200,
  mode: 'dark',
  paper: 'paper',
  lang: (navigator.language || '').startsWith('zh') ? 'zh' : 'en',
});

const pick = <T extends string>(value: unknown, allowed: readonly T[], fallback: T): T =>
  allowed.includes(value as T) ? (value as T) : fallback;

/** Saved preferences over the defaults; unknown or malformed values fall back. */
export function loadPrefs(): Prefs {
  const base = defaults();
  let saved: Record<string, unknown> = {};
  try {
    const parsed: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    if (parsed && typeof parsed === 'object') saved = parsed as Record<string, unknown>;
  } catch {
    // Unreadable or private mode: defaults.
  }
  const hue = Number(saved.hue);
  return {
    theme: pick(saved.theme, [...(Object.keys(PRESETS) as Preset[]), 'custom'], base.theme),
    hue: Number.isFinite(hue) && hue >= 0 && hue <= 360 ? hue : base.hue,
    mode: pick(saved.mode, ['light', 'dark', 'device'], base.mode),
    paper: pick(saved.paper, ['paper', 'sepia', 'night'], base.paper),
    lang: pick(saved.lang, ['zh', 'en'], base.lang),
  };
}

export function savePrefs(prefs: Prefs): void {
  try {
    // Keep keys this version does not know, should the other console add any.
    const current: unknown = JSON.parse(localStorage.getItem(STORAGE_KEY) || '{}');
    const merged = current && typeof current === 'object' ? { ...current, ...prefs } : prefs;
    localStorage.setItem(STORAGE_KEY, JSON.stringify(merged));
  } catch {
    // Private mode: preferences last for this page only.
  }
}

/** The seed hue and chroma switch every color token derives from. */
export function hueAndChroma(prefs: Prefs): readonly [number, number] {
  return prefs.theme === 'custom' ? [prefs.hue, 1] : PRESETS[prefs.theme];
}

interface PrefsContext {
  prefs: Prefs;
  update: (change: Partial<Prefs>) => void;
  reset: () => void;
}

const Context = createContext<PrefsContext | null>(null);

export function PrefsProvider({ children }: { children: ReactNode }) {
  const [prefs, setPrefs] = useState(loadPrefs);
  const update = useCallback((change: Partial<Prefs>) => {
    setPrefs((current) => {
      const next = { ...current, ...change };
      savePrefs(next);
      return next;
    });
  }, []);
  // Reset keeps the language, as in the classic console.
  const reset = useCallback(() => update({ ...defaults(), lang: prefs.lang }), [update, prefs.lang]);
  useLayoutEffect(() => {
    document.documentElement.lang = prefs.lang === 'zh' ? 'zh-CN' : 'en';
  }, [prefs.lang]);
  const value = useMemo(() => ({ prefs, update, reset }), [prefs, update, reset]);
  return <Context.Provider value={value}>{children}</Context.Provider>;
}

export function usePrefs(): PrefsContext {
  const value = useContext(Context);
  if (!value) throw new Error('usePrefs outside PrefsProvider');
  return value;
}
