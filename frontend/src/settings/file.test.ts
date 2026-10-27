// The settings file (#114): the same reading of examples/settings.example.json
// as tests/py/core/test_settings_file.py pins for `isbe settings import`.
import { describe, expect, it } from 'vitest';
import { apply, exported, FileError, parse } from './file';
import EXAMPLE from '../../../examples/settings.example.json?raw';
import type { Snapshot } from './model';

const NAMES = new Set([
  'ESSAIES_URL',
  'ISBE_FULLTEXT_EXTRACTOR',
  'ISBE_LLM_BASE_URL',
  'ISBE_LLM_EXTRA_BODY',
  'ISBE_LLM_MODEL_FAST',
  'ISBE_LLM_MODEL_SMART',
  'ISBE_LLM_NO_AUTH',
  'ISBE_LLM_PROVIDER',
  'ISBE_LLM_TIMEOUT_S',
  'ISBE_PAPER_SOURCE',
  'OPENALEX_MAILTO',
  'OPENAI_API_KEY',
  'GITHUB_TOKEN',
  'SEARXNG_URL',
  'SMTP_PORT',
]);

const snapshot = (over: Partial<Snapshot> = {}): Snapshot => ({
  values: { ISBE_LLM_PROVIDER: 'anthropic', ESSAIES_URL: '', SEARXNG_URL: 'http://old.invalid' },
  configured: { OPENAI_API_KEY: false, GITHUB_TOKEN: true },
  sources: { SEARXNG_URL: 'file', GITHUB_TOKEN: 'file' },
  provider_defaults: {},
  ...over,
});

describe('settings file', () => {
  it('reads the example file the way the server does', () => {
    expect(parse(EXAMPLE, NAMES)).toEqual({
      unknown: [],
      changes: {
        ISBE_FULLTEXT_EXTRACTOR: 'llm',
        ISBE_LLM_BASE_URL: 'http://127.0.0.1:8080/v1',
        ISBE_LLM_EXTRA_BODY: '{"chat_template_kwargs":{"enable_thinking":false}}',
        ISBE_LLM_MODEL_FAST: 'example-fast-model',
        ISBE_LLM_MODEL_SMART: 'example-smart-model',
        ISBE_LLM_PROVIDER: 'openai',
        ISBE_LLM_TIMEOUT_S: '900',
        ISBE_PAPER_SOURCE: 'openalex',
        OPENALEX_MAILTO: 'you@example.org',
        SEARXNG_URL: null,
        SMTP_PORT: '465',
      },
    });
  });

  it('reads a bare settings.json, lists unknown names and drops retired ones', () => {
    const parsed = parse(
      '{"ESSAIES_URL":"http://e.invalid","NOPE":"x","BAIDU_SEARCH_API_KEY":"old","ISBE_LLM_NO_AUTH":true}',
      NAMES,
    );
    expect(parsed).toEqual({
      changes: { ESSAIES_URL: 'http://e.invalid', ISBE_LLM_NO_AUTH: 'true' },
      unknown: ['NOPE'],
    });
  });

  it.each([
    ['not json', 'json'],
    ['[1]', 'object'],
    ['{"format":"other","version":1,"settings":{}}', 'format'],
    ['{"format":"isbe-settings","version":2,"settings":{}}', 'version'],
    ['{"format":"isbe-settings","version":1}', 'settings'],
    ['{"ESSAIES_URL":["a"]}', 'value'],
    ['{"ESSAIES_URL":{"a":1}}', 'value'],
  ])('refuses %s', (text, reason) => {
    expect(() => parse(text, NAMES)).toThrow(FileError);
    try {
      parse(text, NAMES);
    } catch (e) {
      expect((e as FileError).reason).toBe(reason);
    }
  });

  it('types the file into the edits, skipping what the environment sets', () => {
    const s = snapshot({
      sources: { SEARXNG_URL: 'file', GITHUB_TOKEN: 'file', ESSAIES_URL: 'environment' },
    });
    const parsed = parse(
      '{"ESSAIES_URL":"http://new.invalid","SEARXNG_URL":null,"GITHUB_TOKEN":null,"OPENAI_API_KEY":"sk-x","ISBE_LLM_PROVIDER":"openai"}',
      NAMES,
    );
    const result = apply(s, { ISBE_LLM_MODEL_FAST: 'typed' }, parsed);
    expect(result.locked).toEqual(['ESSAIES_URL']);
    expect(result.changed).toEqual(['GITHUB_TOKEN', 'ISBE_LLM_PROVIDER', 'OPENAI_API_KEY', 'SEARXNG_URL']);
    expect(result.same).toEqual([]);
    expect(result.edits).toEqual({
      ISBE_LLM_MODEL_FAST: 'typed',
      GITHUB_TOKEN: null,
      ISBE_LLM_PROVIDER: 'openai',
      OPENAI_API_KEY: 'sk-x',
      SEARXNG_URL: null,
    });
  });

  it('clears nothing for a secret that is not saved, and leaves unchanged values out', () => {
    const parsed = parse('{"OPENAI_API_KEY":null,"ISBE_LLM_PROVIDER":"anthropic"}', NAMES);
    const result = apply(snapshot({ sources: { ISBE_LLM_PROVIDER: 'file' } }), {}, parsed);
    expect(result.edits).toEqual({});
    expect(result).toMatchObject({ changed: [], same: ['ISBE_LLM_PROVIDER', 'OPENAI_API_KEY'] });
  });

  // tests/py/core/test_settings_file.py pins the same texts and refusals.
  it.each([
    ['{"ISBE_LLM_TIMEOUT_S": 900.0}', { ISBE_LLM_TIMEOUT_S: '900' }],
    ['{"ISBE_LLM_TIMEOUT_S": -3}', { ISBE_LLM_TIMEOUT_S: '-3' }],
    [
      '{"ISBE_LLM_EXTRA_BODY": {"t": 1.0, "p": 0.5, "a": [2.0, 1e21]}}',
      { ISBE_LLM_EXTRA_BODY: '{"a":[2,1e+21],"p":0.5,"t":1}' },
    ],
  ])('reads numbers in %s as the server does', (text, changes) => {
    expect(parse(text, new Set(['ISBE_LLM_TIMEOUT_S', 'ISBE_LLM_EXTRA_BODY'])).changes).toEqual(changes);
  });

  it.each(['{"ISBE_LLM_TIMEOUT_S": 1.5}', '{"ISBE_LLM_TIMEOUT_S": 1e21}', '{"SMTP_PORT": 9007199254740992}'])(
    'refuses %s, which is not a whole number',
    (text) => {
      expect(() => parse(text, NAMES)).toThrow(expect.objectContaining({ reason: 'number' }));
    },
  );

  it('refuses a version that is not the number 1', () => {
    expect(() => parse('{"format":"isbe-settings","version":true,"settings":{}}', NAMES)).toThrow(
      expect.objectContaining({ reason: 'version' }),
    );
  });

  it('exports saved and environment values, never secrets', () => {
    const s = snapshot({
      values: {
        ESSAIES_URL: 'http://e.invalid',
        ISBE_LLM_MODEL_FAST: 'env-model',
        ARXIV_API_URL: 'https://a',
      },
      configured: { GITHUB_TOKEN: true, OPENAI_API_KEY: false },
      sources: {
        ESSAIES_URL: 'file',
        ISBE_LLM_MODEL_FAST: 'environment',
        ARXIV_API_URL: 'default',
        GITHUB_TOKEN: 'file',
        OPENAI_API_KEY: 'default',
      },
    });
    expect(exported(s)).toEqual({
      format: 'isbe-settings',
      version: 1,
      settings: { ESSAIES_URL: 'http://e.invalid', ISBE_LLM_MODEL_FAST: 'env-model' },
      secrets_omitted: ['GITHUB_TOKEN'],
    });
  });
});
