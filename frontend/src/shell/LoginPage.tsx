import { useEffect, useState, type FormEvent } from 'react';
import { useT, type MessageKey } from '../i18n';
import { usePrefs } from '../prefs';
import { useSession } from '../session';

export function LoginPage({ notice }: { notice: MessageKey | '' }) {
  const t = useT();
  const { update } = usePrefs();
  const { signIn } = useSession();
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<MessageKey | ''>(notice);

  useEffect(() => {
    document.title = `ISBE · ${t('login')}`;
  });

  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    if (busy) return;
    const form = new FormData(e.currentTarget);
    setBusy(true);
    setError('');
    const ok = await signIn(String(form.get('username') ?? ''), String(form.get('password') ?? ''));
    // On success this page is gone; only a failure needs the form back.
    if (!ok) {
      setBusy(false);
      setError('loginFailed');
    }
  }

  return (
    <main className="main login-page">
      <form className="card settings-form" aria-labelledby="login-title" onSubmit={submit}>
        <h1 id="login-title">ISBE · {t('login')}</h1>
        <p>{t('loginNote')}</p>
        <fieldset disabled={busy}>
          <div className="setting-field">
            <label htmlFor="login-username">{t('username')}</label>
            <input id="login-username" name="username" autoComplete="username" required maxLength={64} />
          </div>
          <div className="setting-field">
            <label htmlFor="login-password">{t('password')}</label>
            <input
              id="login-password"
              name="password"
              type="password"
              autoComplete="current-password"
              required
            />
          </div>
          <button className="btn primary" type="submit">
            {t('login')}
          </button>
        </fieldset>
        <p role="status" aria-live="polite" className="error">
          {error ? t(error) : ''}
        </p>
        <div className="settings-actions">
          <button className="btn" type="button" onClick={() => update({ lang: 'zh' })}>
            中文
          </button>
          <button className="btn" type="button" onClick={() => update({ lang: 'en' })}>
            EN
          </button>
        </div>
      </form>
    </main>
  );
}
