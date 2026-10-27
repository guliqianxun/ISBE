// Service settings as the server reports them (`GET /api/settings`) and the
// edits a person has made on the page but not saved. Pure functions, so the
// rules for defaults, secrets and environment locks are tested without a DOM.

export type Source = 'environment' | 'file' | 'default';

export interface Snapshot {
  /** Every setting that is not a secret, with its effective value. */
  values: Record<string, string>;
  /** For secrets: whether one is set. Their values never reach the page. */
  configured: Record<string, boolean>;
  sources: Record<string, Source>;
  /** Base URL and model names each provider uses when they are not set. */
  provider_defaults: Record<string, Record<string, string>>;
  llm?: { ready: boolean; error?: string | null };
  mail?: { configured: boolean; sender?: string; message?: string };
}

/** A new value for a setting, or `null` to clear it (remove a saved secret). */
export type Edits = Record<string, string | null>;

export const PROVIDERS = ['anthropic', 'deepseek', 'openai'] as const;
export type Provider = (typeof PROVIDERS)[number];

export const PROVIDER_KEYS: Record<Provider, string> = {
  anthropic: 'ANTHROPIC_API_KEY',
  deepseek: 'DEEPSEEK_API_KEY',
  openai: 'OPENAI_API_KEY',
};

export const SECRETS = new Set([
  ...Object.values(PROVIDER_KEYS),
  'GITHUB_TOKEN',
  'OPENALEX_API_KEY',
  'ZEPTOMAIL_TOKEN',
  'SMTP_PASSWORD',
]);

/** Fields whose default is chosen for the person (by the provider or by
 * whether metrail has a URL): picking the default on purpose keeps it. */
const INFERRED = new Set([
  'ISBE_FULLTEXT_EXTRACTOR',
  'ISBE_PAPER_SOURCE',
  'ISBE_LLM_BASE_URL',
  'ISBE_LLM_MODEL_FAST',
  'ISBE_LLM_MODEL_SMART',
]);

export const sourceOf = (s: Snapshot, key: string): Source => s.sources[key] ?? 'default';

export const isProvider = (v: string): v is Provider => (PROVIDERS as readonly string[]).includes(v);

/** The provider shown: the edited one, else the saved one. */
export function provider(s: Snapshot, edits: Edits): Provider {
  const p = edits.ISBE_LLM_PROVIDER || s.values.ISBE_LLM_PROVIDER || 'anthropic';
  return isProvider(p) ? p : 'anthropic';
}

/** What a field shows. A field left at its default follows the chosen
 * provider's default; an edited one shows the edit, and a cleared one stays
 * empty until saved (the server then fills in the default); secrets show
 * only an edit. */
export function shown(s: Snapshot, edits: Edits, key: string): string {
  if (key in edits) return edits[key] ?? '';
  if (SECRETS.has(key)) return '';
  const defaults = s.provider_defaults[provider(s, edits)];
  if (sourceOf(s, key) === 'default' && defaults && key in defaults) return defaults[key]!;
  return s.values[key] ?? '';
}

/** `edits` after the person typed or chose `value` for `key`. */
export function edit(s: Snapshot, edits: Edits, key: string, value: string): Edits {
  if (sourceOf(s, key) === 'environment') return edits;
  const next = { ...edits };
  // A default chosen on purpose must stay fixed when what infers it changes.
  const pinned = sourceOf(s, key) === 'default' && INFERRED.has(key);
  const unchanged = SECRETS.has(key) ? value === '' : !pinned && value === (s.values[key] ?? '');
  if (unchanged) delete next[key];
  else next[key] = value || null;
  return next;
}

/** `edits` with a saved secret marked for removal, or the removal undone. */
export function toggleRemove(edits: Edits, key: string): Edits {
  const next = { ...edits };
  if (next[key] === null) delete next[key];
  else next[key] = null;
  return next;
}

/** Whether the extra request body edit, if any, is a JSON object. */
export function extraBodyValid(edits: Edits): boolean {
  const extra = edits.ISBE_LLM_EXTRA_BODY;
  if (!extra) return true;
  try {
    const v: unknown = JSON.parse(extra);
    return !!v && typeof v === 'object' && !Array.isArray(v);
  } catch {
    return false;
  }
}

/** Recipients as typed, split on commas, semicolons and spaces, without
 * duplicates (case-insensitively; the last spelling wins). */
export function recipients(text: string): string {
  const unique = new Map(
    text
      .split(/[,;\s]+/)
      .filter(Boolean)
      .map((v) => [v.toLowerCase(), v] as const),
  );
  return [...unique.values()].join(', ');
}
