// The ask bar on every page: a question becomes a new run (a new galaxy) and
// Home opens with it selected. The question's options (years, engine, plan
// review) stay on the question page, linked beside the bar.
import { useState, type FormEvent } from 'react';
import { Link, useNavigate } from 'react-router';
import { ApiError } from '../api/client';
import { useT } from '../i18n';
import { createRun } from '../question/client';
import type { Lang } from '../question/types';
import { mayLeave, stayed } from './leave';

/** A question with Chinese characters in it is a Chinese question. */
export const langOf = (q: string): Lang => (/[㐀-鿿]/.test(q) ? 'zh' : 'en');

export function AskBar() {
  const t = useT();
  const navigate = useNavigate();
  const [question, setQuestion] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    const q = question.trim();
    if (!q || busy || !mayLeave()) return;
    setBusy(true);
    setError('');
    try {
      const id = await createRun({
        question: q,
        lang: langOf(q),
        year_from: null,
        year_to: null,
        review: true,
        request_id: crypto.randomUUID?.(),
      });
      setQuestion('');
      navigate(`/?g=${encodeURIComponent(id)}`);
    } catch (err) {
      stayed();
      setError(err instanceof ApiError ? err.message : t('actionFailed'));
    } finally {
      setBusy(false);
    }
  };

  return (
    <form className="g-ask" role="search" onSubmit={submit}>
      <div className="g-ask-box">
        <svg viewBox="0 0 24 24" aria-hidden="true">
          <circle cx="11" cy="11" r="6.5" />
          <path d="M16 16l4.5 4.5" />
        </svg>
        <input
          type="search"
          value={question}
          onChange={(e) => setQuestion(e.target.value)}
          placeholder={t('askPlaceholder')}
          aria-label={t('askLabel')}
          disabled={busy}
        />
        <button type="submit" className="btn primary g-small" disabled={busy || !question.trim()}>
          {busy ? t('askBusy') : t('ask')}
        </button>
      </div>
      <Link className="g-ask-more" to="/question">
        {t('askOptions')}
      </Link>
      {error && (
        <p className="error g-ask-error" role="alert">
          {error}
        </p>
      )}
    </form>
  );
}
