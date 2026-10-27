// Settings (/settings): the language model, full-text extraction, paper
// sources and email, for administrators. Moved from the classic console
// (#79); it keeps that page's fields, notes and rules. Edits stay on the page
// until saved; leaving with unsaved edits asks first. A JSON file can fill
// the form in, and the running settings download as one (#114, file.ts).
import { useCallback, useEffect, useRef, useState, type ReactNode } from 'react';
import { Navigate, useBlocker } from 'react-router';
import { api, ApiError } from '../api/client';
import { useT, type MessageKey } from '../i18n';
import { useSignedIn } from '../session';
import { holdLeave } from '../shell/leave';
import { Field } from './Field';
import { apply, exported, FileError, known, MAX_FILE, parse } from './file';
import { MailTest } from './MailTest';
import {
  edit,
  extraBodyValid,
  provider,
  PROVIDER_KEYS,
  shown,
  sourceOf,
  toggleRemove,
  type Edits,
  type Snapshot,
} from './model';

type Load =
  { state: 'loading' } | { state: 'failed'; message: string } | { state: 'ready'; snapshot: Snapshot };

const FILE_ERRORS: Record<FileError['reason'], MessageKey> = {
  json: 'sFileJson',
  object: 'sFileObject',
  format: 'sFileFormat',
  version: 'sFileVersion',
  settings: 'sFileSettings',
  value: 'sFileValue',
  number: 'sFileNumber',
  size: 'sFileSize',
};

/** The fields under the language model's Advanced. */
const ADVANCED = new Set([
  'ISBE_LLM_MODEL_FAST',
  'ISBE_LLM_MODEL_SMART',
  'ISBE_LLM_MAX_TOKENS',
  'ISBE_LLM_TIMEOUT_S',
  'ISBE_LLM_EXTRA_BODY',
]);

const EXTRACTOR_NOTES: Record<string, MessageKey> = {
  llm: 'sExtractorLlmNote',
  metrail: 'sExtractorMetrailNote',
  off: 'sExtractorOffNote',
};

export function SettingsPage() {
  const session = useSignedIn();
  if (session.user.role !== 'admin') return <Navigate to="/" replace />;
  return <Settings />;
}

function Settings() {
  const t = useT();
  const [load, setLoad] = useState<Load>({ state: 'loading' });
  const [edits, setEdits] = useState<Edits>({});
  const [saving, setSaving] = useState(false);
  const [notice, setNotice] = useState<MessageKey | ''>('');
  const [error, setError] = useState('');
  const [advanced, setAdvanced] = useState(false);
  // What the last import did, or why it failed; shown beside its button.
  const [fileNote, setFileNote] = useState<{ text: string; error: boolean } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const [mailOpen, setMailOpen] = useState(false);
  const [mailBusy, setMailBusy] = useState(false);
  const mailButton = useRef<HTMLButtonElement>(null);
  const closeMail = useCallback(() => {
    setMailOpen(false);
    requestAnimationFrame(() => mailButton.current?.focus());
  }, []);
  const status = useRef<HTMLParagraphElement>(null);
  const dirty = Object.keys(edits).length > 0;

  useEffect(() => {
    document.title = `ISBE · ${t('settings')}`;
  });

  const read = useCallback(() => {
    api.get<Snapshot>('/api/settings').then(
      (snapshot) => setLoad({ state: 'ready', snapshot }),
      (e: unknown) =>
        setLoad({ state: 'failed', message: e instanceof ApiError ? e.message : t('unreachable') }),
    );
    // Read once; the message is in the language of the moment it failed.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(read, [read]);
  // After Retry, focus lands on the page's heading rather than the body.
  const retried = useRef(false);
  const heading = useRef<HTMLHeadingElement>(null);

  // Links such as /settings#sources-settings-h open at that section.
  const ready = load.state === 'ready';
  useEffect(() => {
    if (ready && retried.current) heading.current?.focus();
    if (ready && location.hash)
      document.getElementById(decodeURIComponent(location.hash.slice(1)))?.scrollIntoView?.();
  }, [ready]);

  // Unsaved edits, a save or an email in flight: ask before leaving the page,
  // by a link, Back, signing out or the ask bar (shell/leave.ts), and by
  // closing or reloading the tab. Asked once: after a yes nothing asks again,
  // until the person turns out to still be here (a failed sign-out or run,
  // via shell/leave.ts) or edits again.
  const busy = dirty || saving || mailBusy;
  const released = useRef(false);
  const ask = useCallback(() => {
    if (released.current) return true;
    const leave = !saving && !mailBusy && window.confirm(t('sLeaveSettings'));
    released.current = leave;
    return leave;
  }, [saving, mailBusy, t]);
  const stay = useCallback(() => {
    released.current = false;
  }, []);
  useEffect(stay, [edits, stay]);
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      busy && !released.current && currentLocation.pathname !== nextLocation.pathname,
  );
  useEffect(() => {
    if (blocker.state !== 'blocked') return;
    if (ask()) blocker.proceed();
    else blocker.reset();
  }, [blocker, ask]);
  useEffect(() => {
    if (!busy) return;
    released.current = false;
    const unload = (e: BeforeUnloadEvent) => {
      if (!released.current) e.preventDefault();
    };
    // A plain link to a classic page is a whole-page load the router never sees.
    const click = (e: MouseEvent) => {
      const a = e.target instanceof Element ? e.target.closest('a[href]') : null;
      if (!a || a.getAttribute('target') || e.defaultPrevented) return;
      // A new tab or window (modifier or middle click) does not leave this page.
      if (e.button !== 0 || e.ctrlKey || e.metaKey || e.shiftKey || e.altKey) return;
      if (new URL((a as HTMLAnchorElement).href, window.location.href).pathname === window.location.pathname)
        return;
      if (!ask()) {
        e.preventDefault();
        e.stopPropagation();
      }
    };
    const release = holdLeave(ask, stay);
    window.addEventListener('beforeunload', unload);
    document.addEventListener('click', click, true);
    return () => {
      release();
      window.removeEventListener('beforeunload', unload);
      document.removeEventListener('click', click, true);
    };
  }, [busy, ask, stay]);

  if (load.state === 'loading') {
    return (
      <p className="muted" role="status">
        {t('loading')}
      </p>
    );
  }
  if (load.state === 'failed') {
    return (
      <div className="card error" role="alert">
        {t('sSettingsUnavailable')} {load.message}
        <button
          type="button"
          className="btn"
          onClick={() => {
            retried.current = true;
            setLoad({ state: 'loading' });
            read();
          }}
        >
          {t('retry')}
        </button>
      </div>
    );
  }
  const s = load.snapshot;

  const change = (key: string, value: string) => {
    if (saving) return;
    setEdits((e) => {
      const next = edit(s, e, key, value);
      // An essaies URL typed while the inferred source is OpenAlex would make
      // the server infer essaies on save: keep the source the person sees. (A
      // file with only ESSAIES_URL still means essaies, as on the CLI.)
      if (key !== 'ESSAIES_URL' || sourceOf(s, 'ISBE_PAPER_SOURCE') !== 'default') return next;
      if (!('ESSAIES_URL' in next) && e.ISBE_PAPER_SOURCE === 'openalex') {
        // The URL edit undone: so is the pin it brought.
        const rest = { ...next };
        delete rest.ISBE_PAPER_SOURCE;
        return rest;
      }
      if (!('ISBE_PAPER_SOURCE' in e) && shown(s, e, 'ISBE_PAPER_SOURCE') !== 'essaies')
        return { ...next, ISBE_PAPER_SOURCE: 'openalex' };
      return next;
    });
    setNotice('');
    setError('');
  };
  async function importFile(file: File) {
    if (saving) return;
    const failed = (reason: string) =>
      setFileNote({ text: t('sImportFailed', { file: file.name, reason }), error: true });
    if (file.size > MAX_FILE) return failed(t('sFileSize'));
    try {
      const parsed = parse(await file.text(), known(s));
      const result = apply(s, edits, parsed);
      setEdits(result.edits);
      setNotice('');
      setError('');
      // Named, not counted: a key for another provider has no field on the page.
      const lines = [
        result.changed.length
          ? t('sImported', { file: file.name, names: result.changed.join(', ') })
          : t('sImportedNothing', { file: file.name }),
      ];
      if (result.same.length) lines.push(t('sImportSame', { names: result.same.join(', ') }));
      if (result.locked.length) lines.push(t('sImportLocked', { names: result.locked.join(', ') }));
      if (parsed.unknown.length) lines.push(t('sImportUnknown', { names: parsed.unknown.join(', ') }));
      setFileNote({ text: lines.join(' '), error: false });
      // Imported advanced values are where the person checks them.
      if (result.changed.some((k) => ADVANCED.has(k))) setAdvanced(true);
    } catch (e) {
      if (!(e instanceof FileError)) return failed(t('actionFailed'));
      failed(t(FILE_ERRORS[e.reason], { name: e.name }));
    }
  }

  function exportFile() {
    const body = JSON.stringify(exported(s), null, 2) + '\n';
    const url = URL.createObjectURL(new Blob([body], { type: 'application/json' }));
    const a = document.createElement('a');
    a.href = url;
    a.download = 'isbe-settings.json';
    a.click();
    setTimeout(() => URL.revokeObjectURL(url), 0);
  }

  const remove = (key: string) => {
    if (saving) return;
    setEdits((e) => toggleRemove(e, key));
    setNotice('');
    setError('');
  };

  async function save(e: React.FormEvent) {
    e.preventDefault();
    if (saving || !dirty) return;
    if (!extraBodyValid(edits)) {
      setError(t('sInvalidExtraBody'));
      setAdvanced(true);
      requestAnimationFrame(() => document.getElementById('setting-ISBE_LLM_EXTRA_BODY')?.focus());
      return;
    }
    setSaving(true);
    setError('');
    try {
      const snapshot = await api.put<Snapshot>('/api/settings', edits);
      setLoad({ state: 'ready', snapshot });
      setEdits({});
      setFileNote(null);
      setNotice('sSavedSettings');
    } catch (err) {
      setError(err instanceof Error ? err.message : t('actionFailed'));
    } finally {
      setSaving(false);
      status.current?.scrollIntoView?.({ block: 'nearest' });
    }
  }

  const field = (key: string, label: MessageKey, type = 'text') => (
    <Field
      key={key}
      name={key}
      label={t(label)}
      type={type}
      snapshot={s}
      edits={edits}
      onChange={change}
      onRemove={remove}
    />
  );

  const llmProvider = provider(s, edits);
  const extractor = shown(s, edits, 'ISBE_FULLTEXT_EXTRACTOR');
  const paperSource = shown(s, edits, 'ISBE_PAPER_SOURCE') === 'essaies' ? 'essaies' : 'openalex';
  const transport = shown(s, edits, 'EMAIL_TRANSPORT') || 'smtp';
  const extractorDefault =
    sourceOf(s, 'ISBE_FULLTEXT_EXTRACTOR') === 'default' && !('ISBE_FULLTEXT_EXTRACTOR' in edits);
  const readiness = dirty ? t('sLlmUnsaved') : t(s.llm?.ready ? 'sLlmReady' : 'sLlmIncomplete');

  return (
    <>
      <header className="head">
        <div className="settings-intro">
          <div className="eyebrow">ISBE · {t('settings')}</div>
          <h1 className="title" ref={heading} tabIndex={-1}>
            {t('settings')}
          </h1>
          <p className="sub">{t('sSettingsNote')}</p>
        </div>
        <div className="settings-actions settings-file">
          <button
            type="button"
            className="btn"
            id="settings-import"
            disabled={saving}
            aria-describedby="settings-file-hint settings-file-note"
            onClick={() => fileInput.current?.click()}
          >
            {t('sImport')}
          </button>
          <input
            ref={fileInput}
            type="file"
            accept="application/json,.json"
            hidden
            onChange={(e) => {
              const file = e.target.files?.[0];
              e.target.value = '';
              if (file) void importFile(file);
            }}
          />
          <button
            type="button"
            className="btn"
            id="settings-export"
            disabled={dirty}
            aria-describedby="settings-file-hint"
            onClick={exportFile}
          >
            {t('sExport')}
          </button>
          <a className="btn" href="/topics">
            {t('topicSettings')} →
          </a>
          <p id="settings-file-hint" className="settings-file-hint">
            {t(dirty ? 'sExportSaveFirst' : 'sFileNote')}
          </p>
          <p id="settings-file-note" role="status" className={fileNote?.error ? 'error' : undefined}>
            {fileNote?.text}
          </p>
        </div>
      </header>
      <form id="settings-form" className="settings-form" onSubmit={(e) => void save(e)}>
        <fieldset disabled={saving}>
          <Section index="01" id="llm-settings-h" title={t('sLlmSettings')} note={t('sLlmSettingsNote')}>
            <div className="settings-grid">
              {field('ISBE_LLM_PROVIDER', 'sProvider', 'select')}
              {field('ISBE_LLM_BASE_URL', 'sBaseUrl', 'url')}
              {field(PROVIDER_KEYS[llmProvider], 'sApiKey')}
            </div>
            {llmProvider === 'openai' && (
              <>
                <div className="settings-grid">{field('ISBE_LLM_NO_AUTH', 'sNoAuth', 'select')}</div>
                <p className="settings-storage">{t('sNoAuthNote')}</p>
              </>
            )}
            <p className="settings-storage">{t('sProviderDefaultsNote')}</p>
            <p id="llm-readiness" role="status">
              {readiness}
              {!dirty && s.llm?.error ? `: ${s.llm.error}` : ''}
            </p>
            <details
              id="settings-advanced"
              open={advanced}
              onToggle={(e) => setAdvanced((e.target as HTMLDetailsElement).open)}
            >
              <summary>{t('sAdvanced')}</summary>
              <div className="settings-grid">
                {field('ISBE_LLM_MODEL_FAST', 'sFastModel')}
                {field('ISBE_LLM_MODEL_SMART', 'sSmartModel')}
                {field('ISBE_LLM_MAX_TOKENS', 'sMaxTokens', 'number')}
                {field('ISBE_LLM_TIMEOUT_S', 'sTimeout', 'number')}
                {field('ISBE_LLM_EXTRA_BODY', 'sExtraBody', 'textarea')}
              </div>
            </details>
          </Section>
          <Section index="02" id="fulltext-settings-h" title={t('sFulltextSettings')}>
            <div className="settings-grid">
              {field('ISBE_FULLTEXT_EXTRACTOR', 'sExtractor', 'select')}
              {extractor === 'metrail' && field('METRAIL_API_URL', 'sMetrailUrl', 'url')}
            </div>
            <p className="settings-storage">{t(EXTRACTOR_NOTES[extractor] ?? 'sExtractorLlmNote')}</p>
            {extractor === 'llm' && <p className="settings-storage">{t('sExtractorLlmFigures')}</p>}
            {extractorDefault && <p className="settings-storage">{t('sExtractorDefaultNote')}</p>}
          </Section>
          <Section
            index="03"
            id="sources-settings-h"
            title={t('sSourceSettings')}
            note={t('sSourceSettingsNote')}
          >
            <div className="settings-grid">
              {field('ISBE_PAPER_SOURCE', 'sPaperSource', 'select')}
              {paperSource === 'essaies'
                ? field('ESSAIES_URL', 'sEssaiesUrl', 'url')
                : field('OPENALEX_API_KEY', 'sOpenalexKey')}
              {paperSource === 'openalex' && field('OPENALEX_MAILTO', 'sOpenalexMailto', 'email')}
              {field('GITHUB_TOKEN', 'sGithubToken')}
            </div>
            <p className="settings-storage">
              {t(paperSource === 'essaies' ? 'sPaperSourceEssaiesNote' : 'sPaperSourceOpenalexNote')}
            </p>
            <details id="settings-sources-advanced">
              <summary>{t('sSourceAdvanced')}</summary>
              <div className="settings-grid">
                {paperSource === 'openalex' && field('OPENALEX_API_URL', 'sOpenalexUrl', 'url')}
                {paperSource === 'openalex' && field('ESSAIES_URL', 'sEssaiesUrl', 'url')}
                {field('ARXIV_API_URL', 'sArxivUrl', 'url')}
                {field('GITHUB_API_URL', 'sGithubUrl', 'url')}
              </div>
            </details>
            <h3>{t('sWebSearch')}</h3>
            <p className="settings-storage">{t('sWebSearchSourceNote')}</p>
            <div className="settings-grid">
              {field('SEARXNG_URL', 'sWebSearchSearxng', 'url')}
              {field('ISBE_ARTICLE_DNS', 'sArticleDns', 'select')}
            </div>
          </Section>
          <section className="card settings-section" aria-labelledby="mail-settings-h">
            <h2 id="mail-settings-h">{t('sMailSettings')}</h2>
            <p>{t('sMailNote')}</p>
            <p>
              {t(s.mail?.configured ? 'sOnline' : 'sOff')}
              {s.mail?.sender ? ` · ${s.mail.sender}` : ''}
            </p>
            {s.mail?.message && <p className="error">{s.mail.message}</p>}
            <div className="settings-grid">
              {field('EMAIL_TRANSPORT', 'sMailTransport', 'select')}
              {transport === 'smtp' ? (
                <>
                  {field('SMTP_HOST', 'sMailHost')}
                  {field('SMTP_PORT', 'sMailPort', 'number')}
                  {field('SMTP_SECURITY', 'sMailSecurity', 'select')}
                  {field('SMTP_USER', 'sMailUser')}
                  {field('SMTP_PASSWORD', 'sMailPassword')}
                </>
              ) : (
                <>
                  {field('ZEPTOMAIL_API_URL', 'sMailEndpoint', 'url')}
                  {field('ZEPTOMAIL_TOKEN', 'sMailToken')}
                </>
              )}
              {field('SMTP_FROM', 'sMailSender', 'email')}
              {field('SMTP_FROM_NAME', 'sMailSenderName')}
              {field('ISBE_EMAIL_DEFAULT_RECIPIENTS', 'sMailDefault')}
            </div>
            {transport === 'smtp' && <p className="settings-storage">{t('sMailSmtpNote')}</p>}
            <div className="mail-test-open">
              <button
                type="button"
                className="btn"
                ref={mailButton}
                disabled={mailBusy || dirty}
                aria-expanded={mailOpen}
                aria-controls={mailOpen ? 'mail-form' : undefined}
                aria-describedby={dirty ? 'mail-save-first' : undefined}
                onClick={() => setMailOpen(true)}
              >
                {t('sMailTest')}
              </button>
              {dirty && (
                <p id="mail-save-first" className="setting-hint">
                  {t('sMailSaveFirst')}
                </p>
              )}
            </div>
          </section>
          <p className="settings-storage">{t('sSettingsStorage')}</p>
          <div className="settings-actions">
            <button type="submit" className="btn primary" id="settings-save" disabled={!dirty}>
              {t(saving ? 'sSavingSettings' : 'sSaveSettings')}
            </button>
            <button
              type="button"
              className="btn"
              id="settings-discard"
              disabled={!dirty}
              onClick={() => {
                setEdits({});
                setFileNote(null);
                setNotice('');
                setError('');
              }}
            >
              {t('sDiscardSettings')}
            </button>
            <p
              id="settings-status"
              role="status"
              aria-live="polite"
              className={error ? 'error' : undefined}
              ref={status}
            >
              {error ||
                (saving ? t('sSavingSettings') : notice ? t(notice) : dirty ? t('sUnsavedSettings') : '')}
            </p>
          </div>
        </fieldset>
      </form>
      {mailOpen && <MailTest onClose={closeMail} onBusy={setMailBusy} unsaved={dirty} />}
    </>
  );
}

function Section(props: { index: string; id: string; title: string; note?: string; children: ReactNode }) {
  return (
    <section className="card settings-section" aria-labelledby={props.id}>
      <div className="settings-section-head">
        <span className="settings-index mono" aria-hidden="true">
          {props.index}
        </span>
        <div>
          <h2 id={props.id}>{props.title}</h2>
          {props.note && <p>{props.note}</p>}
        </div>
      </div>
      {props.children}
    </section>
  );
}
