// The settings file people import and export on this page (#114). The same
// format as isbe/core/settings_file.py reads for `isbe settings import`:
// `{"format": "isbe-settings", "version": 1, "settings": {...}}`, or a bare
// object of names (what <data>/settings.json holds). Pure functions, tested
// without a DOM; both suites read examples/settings.example.json.
import { edit, SECRETS, sourceOf, type Edits, type Snapshot } from './model';

export const FORMAT = 'isbe-settings';
export const VERSION = 1;
/** The largest file read, the same limit as settings.json. */
export const MAX_FILE = 64 * 1024;
/** Names of removed features; dropped without a mention, as the server does. */
const RETIRED = new Set(['BAIDU_SEARCH_API_KEY', 'ISBE_SEARCH_DAILY_LIMIT']);

export interface Parsed {
  /** A value for each known name, or null to clear it. */
  changes: Record<string, string | null>;
  /** Names the file has that are not settings. */
  unknown: string[];
}

/** Why a file could not be read, for the page to word in its language. */
export class FileError extends Error {
  constructor(
    readonly reason: 'json' | 'object' | 'format' | 'version' | 'settings' | 'value' | 'number' | 'size',
    readonly name = '',
  ) {
    super(reason);
  }
}

/** Every setting name the server reported. */
export const known = (s: Snapshot): Set<string> =>
  new Set([...Object.keys(s.sources), ...Object.keys(s.values), ...Object.keys(s.configured)]);

/** The changes the file `text` asks for; throws FileError when it is not a settings file. */
export function parse(text: string, names: Set<string>): Parsed {
  let doc: unknown;
  try {
    doc = JSON.parse(text);
  } catch {
    throw new FileError('json');
  }
  if (!isObject(doc)) throw new FileError('object');
  if ('format' in doc) {
    if (doc.format !== FORMAT) throw new FileError('format');
    if (doc.version !== VERSION) throw new FileError('version');
    doc = doc.settings;
    if (!isObject(doc)) throw new FileError('settings');
  }
  const parsed: Parsed = { changes: {}, unknown: [] };
  for (const name of Object.keys(doc as object).sort()) {
    if (RETIRED.has(name)) continue;
    if (!names.has(name)) parsed.unknown.push(name);
    else parsed.changes[name] = asText(name, (doc as Record<string, unknown>)[name]);
  }
  return parsed;
}

function isObject(v: unknown): v is Record<string, unknown> {
  return !!v && typeof v === 'object' && !Array.isArray(v);
}

function asText(name: string, value: unknown): string | null {
  if (value === null || typeof value === 'string') return value || null;
  if (typeof value === 'boolean') return value ? 'true' : 'false';
  if (typeof value === 'number') {
    // Every numeric setting is a whole number; JSON.parse already reads 900.0 as 900.
    if (!Number.isSafeInteger(value)) throw new FileError('number', name);
    return String(value);
  }
  if (isObject(value) && name === 'ISBE_LLM_EXTRA_BODY') return sorted(value);
  throw new FileError('value', name);
}

/** Compact JSON with sorted keys, as the server writes it. */
function sorted(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(sorted).join(',')}]`;
  if (isObject(value))
    return `{${Object.keys(value)
      .sort()
      .map((k) => `${JSON.stringify(k)}:${sorted(value[k])}`)
      .join(',')}}`;
  return JSON.stringify(value);
}

export interface Imported {
  edits: Edits;
  /** Names whose value the import changed on the page. */
  changed: string[];
  /** Names the file sets to what the page already has. */
  same: string[];
  /** Names the server's environment sets, which the page cannot change. */
  locked: string[];
}

/** `edits` with the file's changes typed in, as if the person had typed them. */
export function apply(s: Snapshot, edits: Edits, parsed: Parsed): Imported {
  let next = edits;
  const changed: string[] = [];
  const same: string[] = [];
  const locked: string[] = [];
  for (const [key, value] of Object.entries(parsed.changes)) {
    if (sourceOf(s, key) === 'environment') {
      locked.push(key);
      continue;
    }
    const before = next;
    if (SECRETS.has(key) && value === null) {
      // Clearing a secret is a removal; with none saved there is nothing to do.
      next = { ...next };
      if (s.configured[key]) next[key] = null;
      else delete next[key];
    } else next = edit(s, next, key, value ?? '');
    const differs = key in before !== key in next || before[key] !== next[key];
    (differs ? changed : same).push(key);
  }
  return { edits: next, changed, same, locked };
}

/** The settings this server runs with (saved and environment values, not
 * defaults) as a version 1 file. Secrets never reach the page, so their
 * names are listed instead. */
export function exported(s: Snapshot): Record<string, unknown> {
  const settings: Record<string, string> = {};
  const omitted: string[] = [];
  for (const key of Object.keys(s.sources).sort()) {
    if (sourceOf(s, key) === 'default') continue;
    if (SECRETS.has(key)) {
      if (s.configured[key]) omitted.push(key);
    } else if (s.values[key] !== undefined) settings[key] = s.values[key];
  }
  return {
    format: FORMAT,
    version: VERSION,
    settings,
    ...(omitted.length ? { secrets_omitted: omitted } : {}),
  };
}
