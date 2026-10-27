// The plan card: the model's restatement, concepts, queries and criteria. While
// the plan is under review the search waits until the person starts it or runs
// an edited plan; nothing starts by itself. Once searching has begun it
// collapses, and the plan can be edited into a new run instead.
import { useState } from 'react';
import { useT, type MessageKey } from '../i18n';
import { textLang } from './lang';
import { hue, tracks } from './map/sky';
import type { Concept, ConceptRole, Plan, PlanChanges } from './types';

const ROLES: ConceptRole[] = ['subject', 'method', 'context', 'outcome', 'landmark'];
const ROLE_KEYS: Record<ConceptRole, MessageKey> = {
  subject: 'qRoleSubject',
  method: 'qRoleMethod',
  context: 'qRoleContext',
  outcome: 'qRoleOutcome',
  landmark: 'qRoleLandmark',
};

export interface PlanCardProps {
  plan: Plan;
  reviewing: boolean;
  onStart(): Promise<void>;
  /** Save the edited fields of `version`: in review they continue this run, later they start a new one. */
  onSave(version: number, changes: PlanChanges): Promise<void>;
}

export function PlanCard({ plan, reviewing, onStart, onSave }: PlanCardProps) {
  const t = useT();
  const [open, setOpen] = useState(reviewing);
  const [wasReviewing, setWasReviewing] = useState(reviewing);
  const [draft, setDraft] = useState<Plan | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState(false);

  // The card opens for review and folds away once searching starts.
  if (wasReviewing !== reviewing) {
    setWasReviewing(reviewing);
    setOpen(reviewing);
  }

  const run = async (what: () => Promise<void>) => {
    setBusy(true);
    setError('');
    try {
      await what();
    } catch {
      setError(t('actionFailed'));
    } finally {
      setBusy(false);
    }
  };
  const edit = () => {
    setDraft(structuredClone(plan));
    setOpen(true);
  };
  const save = () =>
    draft &&
    void run(async () => {
      await onSave(plan.version, changes(plan, draft));
      setDraft(null);
    });

  const byTrack = tracks(plan.queries);
  const shown = draft ?? plan;

  return (
    <section className="card q-plan" aria-label={t('qPlan')}>
      <div className="card-head">
        <h2>
          <button type="button" className="q-disclose" aria-expanded={open} onClick={() => setOpen(!open)}>
            {t('qPlan')}
          </button>
        </h2>
        <span>
          {t('qPlanVersion', { v: plan.version })} ·{' '}
          {plan.source === 'human'
            ? t('qByYou')
            : plan.source === 'fallback'
              ? t('qByFallback')
              : (plan.llm?.model ?? plan.source)}
        </span>
      </div>
      {reviewing && !draft && (
        <div className="q-review">
          <span>{t('qReviewWaits')}</span>
          <button type="button" className="btn primary" disabled={busy} onClick={() => void run(onStart)}>
            {t('qStartNow')}
          </button>
          <button type="button" className="btn" disabled={busy} onClick={edit}>
            {t('qEditPlan')}
          </button>
        </div>
      )}
      {open && (
        <>
          <p className="q-reformulation" lang={textLang(shown.reformulation)}>
            {shown.reformulation}
          </p>
          <h3>{t('qConcepts')}</h3>
          {draft ? (
            <ConceptEditor
              concepts={draft.concepts ?? []}
              onChange={(concepts) => setDraft({ ...draft, concepts })}
            />
          ) : (shown.concepts ?? []).length ? (
            <ul className="q-concepts">
              {(shown.concepts ?? []).map((c, i) => (
                <li key={i}>
                  <span className="chip plain">{t(ROLE_KEYS[c.role])}</span>
                  <span lang={textLang(c.terms.join(' '))}>{c.terms.join(' · ')}</span>
                </li>
              ))}
            </ul>
          ) : (
            <p className="muted">{t('qNoConcepts')}</p>
          )}
          <h3>{t('qQueries')}</h3>
          <ol className="q-queries">
            {shown.queries.map((q, i) => (
              <li key={q.id}>
                <i
                  className="q-swatch"
                  style={{ background: hue(byTrack.get(q.id)?.index ?? i) }}
                  aria-hidden="true"
                />
                {draft ? (
                  <input
                    aria-label={t('qQueryN', { n: i + 1 })}
                    lang={textLang(q.query)}
                    value={q.query}
                    onChange={(e) =>
                      setDraft({
                        ...draft,
                        queries: draft.queries.map((x) =>
                          x.id === q.id ? { ...x, query: e.target.value } : x,
                        ),
                      })
                    }
                  />
                ) : (
                  <code lang={textLang(q.query)}>{q.query}</code>
                )}
                <span className="muted">{t('qPriority', { n: q.priority })}</span>
              </li>
            ))}
          </ol>
          <h3>{t('qCriteria')}</h3>
          {draft ? (
            <div className="q-criteria-edit">
              <label className="q-field">
                <span>{t('qInclude')}</span>
                <textarea
                  rows={3}
                  lang={textLang(draft.criteria.include.join(' '))}
                  value={draft.criteria.include.join('\n')}
                  onChange={(e) =>
                    setDraft({ ...draft, criteria: { ...draft.criteria, include: lines(e.target.value) } })
                  }
                />
              </label>
              <label className="q-field">
                <span>{t('qExclude')}</span>
                <textarea
                  rows={3}
                  lang={textLang(draft.criteria.exclude.join(' '))}
                  value={draft.criteria.exclude.join('\n')}
                  onChange={(e) =>
                    setDraft({ ...draft, criteria: { ...draft.criteria, exclude: lines(e.target.value) } })
                  }
                />
              </label>
            </div>
          ) : (
            <dl className="q-criteria">
              <dt>{t('qInclude')}</dt>
              {shown.criteria.include.map((c, i) => (
                <dd key={`i${i}`} lang={textLang(c)}>
                  {c}
                </dd>
              ))}
              {shown.criteria.exclude.length > 0 && <dt>{t('qExclude')}</dt>}
              {shown.criteria.exclude.map((c, i) => (
                <dd key={`x${i}`} lang={textLang(c)}>
                  {c}
                </dd>
              ))}
            </dl>
          )}
          {(shown.year_from || shown.year_to) && !draft && (
            <p className="muted">
              {t('qYearRange')}: {shown.year_from ?? '…'}–{shown.year_to ?? '…'}
            </p>
          )}
          {draft ? (
            <div className="q-review">
              <button type="button" className="btn primary" disabled={busy} onClick={save}>
                {reviewing ? t('qRunEdited') : t('qRerun')}
              </button>
              <button type="button" className="btn" disabled={busy} onClick={() => setDraft(null)}>
                {t('qDiscardEdits')}
              </button>
            </div>
          ) : (
            !reviewing && (
              <div className="q-review">
                <button type="button" className="btn" onClick={edit}>
                  {t('qRerun')}
                </button>
              </div>
            )
          )}
        </>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </section>
  );
}

function ConceptEditor({ concepts, onChange }: { concepts: Concept[]; onChange(c: Concept[]): void }) {
  const t = useT();
  return (
    <ul className="q-concepts">
      {concepts.map((c, i) => (
        <li key={i}>
          <select
            aria-label={t('qConceptRole', { n: i + 1 })}
            value={c.role}
            onChange={(e) =>
              onChange(concepts.map((x, j) => (j === i ? { ...x, role: e.target.value as ConceptRole } : x)))
            }
          >
            {ROLES.map((r) => (
              <option key={r} value={r}>
                {t(ROLE_KEYS[r])}
              </option>
            ))}
          </select>
          <input
            aria-label={t('qConceptTerms', { n: i + 1 })}
            lang={textLang(c.terms.join(' '))}
            defaultValue={c.terms.join(', ')}
            onChange={(e) =>
              onChange(
                concepts.map((x, j) =>
                  j === i
                    ? {
                        ...x,
                        terms: e.target.value
                          .split(',')
                          .map((s) => s.trim())
                          .filter(Boolean),
                      }
                    : x,
                ),
              )
            }
          />
        </li>
      ))}
    </ul>
  );
}

const lines = (text: string) =>
  text
    .split('\n')
    .map((s) => s.trim())
    .filter(Boolean);

const EDITABLE = ['concepts', 'queries', 'criteria', 'year_from', 'year_to'] as const;

/** The editable fields `draft` changed from `plan`. */
export function changes(plan: Plan, draft: Plan): PlanChanges {
  const out: Record<string, unknown> = {};
  for (const k of EDITABLE) if (JSON.stringify(plan[k]) !== JSON.stringify(draft[k])) out[k] = draft[k];
  return out as PlanChanges;
}
