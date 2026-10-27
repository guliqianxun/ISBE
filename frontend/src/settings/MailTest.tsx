// "Send test email" on Settings: reads the mail setup and default recipients
// (`GET /api/mail`), asks before sending, then reports what the provider said.
// A send whose result is unknown is never retried for the person.
import { useEffect, useRef, useState } from 'react';
import { api, ApiError } from '../api/client';
import { useT } from '../i18n';
import { recipients as unique } from './model';

const SUBJECT = 'ISBE · 邮件配置测试 / Email test';

interface MailSetup {
  mail?: { configured?: boolean; sender?: string; message?: string };
  recipients?: string;
}

type Result = { state: 'accepted' | 'failed' | 'unknown'; message?: string; request_id?: string };

const RESULT_KEYS = { accepted: 'sMailAccepted', failed: 'sMailFailed', unknown: 'sMailUnknown' } as const;

/** `unsaved`: the page has edits; the test would use the saved setup, so it waits. */
export function MailTest({
  onClose,
  onBusy,
  unsaved,
}: {
  onClose: () => void;
  onBusy: (busy: boolean) => void;
  unsaved: boolean;
}) {
  const t = useT();
  const [loading, setLoading] = useState(true);
  const [sending, setSending] = useState(false);
  const [configured, setConfigured] = useState(false);
  const [sender, setSender] = useState('');
  const [problem, setProblem] = useState('');
  const [to, setTo] = useState('');
  const [error, setError] = useState('');
  const [result, setResult] = useState<Result | null>(null);
  const input = useRef<HTMLInputElement>(null);
  const status = useRef<HTMLParagraphElement>(null);
  const busy = loading || sending;
  const finished = result?.state === 'accepted' || result?.state === 'unknown';

  useEffect(() => onBusy(busy), [busy, onBusy]);
  useEffect(() => () => onBusy(false), [onBusy]);

  useEffect(() => {
    let live = true;
    api.get<MailSetup>('/api/mail').then(
      (data) => {
        if (!live) return;
        setConfigured(!!data.mail?.configured);
        setSender(data.mail?.sender ?? '');
        setProblem(data.mail?.message ?? '');
        setTo(data.recipients ?? '');
        setLoading(false);
        requestAnimationFrame(() => input.current?.focus());
      },
      (e: unknown) => {
        if (!live) return;
        setError(e instanceof Error ? e.message : t('sMailFailed'));
        setLoading(false);
      },
    );
    return () => {
      live = false;
    };
    // Read once when the form opens.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  async function send(e: React.FormEvent) {
    e.preventDefault();
    if (busy || !configured || finished || unsaved) return;
    const list = unique(to);
    if (!list) {
      setError(t('sMailRequired'));
      return;
    }
    setTo(list);
    if (
      !window.confirm(
        `${t('sMailConfirm')}\n${t('sMailSubject')}: ${SUBJECT}\n${t('sMailRecipients')}: ${list}`,
      )
    )
      return;
    setSending(true);
    setError('');
    setResult(null);
    try {
      const answer = await api.post<Partial<Result>>('/api/mail/test', { recipients: list });
      const state = answer.state;
      setResult(
        state === 'accepted' || state === 'failed' || state === 'unknown'
          ? (answer as Result)
          : { state: 'unknown' },
      );
    } catch (err) {
      // A server error may come after the provider took the email: unknown, not failed.
      setResult(
        err instanceof ApiError && err.status < 500
          ? { state: 'failed', message: err.message }
          : { state: 'unknown' },
      );
    } finally {
      setSending(false);
      requestAnimationFrame(() => status.current?.focus());
    }
  }

  const message = error
    ? error
    : sending || loading
      ? t('sMailSending')
      : result
        ? [
            t(RESULT_KEYS[result.state]),
            result.state === 'failed' ? result.message : '',
            result.request_id ? `${t('sMailRequest')}: ${result.request_id}` : '',
          ]
            .filter(Boolean)
            .join(' ')
        : '';

  return (
    <form
      id="mail-form"
      className="card settings-form no-print"
      aria-labelledby="mail-heading"
      onSubmit={(e) => void send(e)}
    >
      <fieldset disabled={busy}>
        <h2 id="mail-heading">{t('sMailTest')}</h2>
        <p>
          {t('sMailSubject')}: <b>{SUBJECT}</b>
        </p>
        <p>
          {t('sMailSender')}: {sender || t('sOff')}
        </p>
        <div className="setting-field">
          <label htmlFor="mail-recipients">{t('sMailRecipients')}</label>
          <input
            id="mail-recipients"
            ref={input}
            type="text"
            value={to}
            maxLength={4096}
            autoComplete="off"
            aria-describedby="mail-hint"
            disabled={finished}
            onChange={(e) => {
              setTo(e.target.value);
              setError('');
            }}
          />
          <p id="mail-hint" className="setting-hint">
            {t('sMailRecipientsHint')} {t('sMailNote')}
          </p>
        </div>
        {!loading && !configured && <p className="error">{problem || t('sOff')}</p>}
        {unsaved && (
          <p id="mail-send-saved" className="setting-hint">
            {t('sMailSaveFirst')}
          </p>
        )}
        <div className="settings-actions">
          <button
            type="submit"
            id="mail-send"
            className="btn primary"
            disabled={!configured || finished || unsaved}
            aria-describedby={unsaved ? 'mail-send-saved' : undefined}
          >
            {t(sending ? 'sMailSending' : 'sMailSend')}
          </button>
          <button type="button" className="btn" onClick={onClose}>
            {t('sMailCancel')}
          </button>
        </div>
        <p
          id="mail-status"
          role="status"
          aria-live="polite"
          tabIndex={-1}
          ref={status}
          className={error || result?.state === 'failed' ? 'error' : undefined}
        >
          {message}
        </p>
      </fieldset>
    </form>
  );
}
