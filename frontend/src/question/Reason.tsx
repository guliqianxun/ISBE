// What failed, what to do and the server's reason, for a problem with a code
// (docs/question/api.md#problem-codes): the advice is the console's, in the
// reader's language, so the reason is the upstream's own `detail` when there is
// one. Without a code (an older run) the server's message is all there is: it
// is shown as the reason under the console's own words for a failed stage.
// Upstreams answer in English, so the reason is marked `lang="en"`.
import { useT, type MessageKey } from '../i18n';
import { usePrefs } from '../prefs';
import type { ProblemCode } from './types';

/** The text of each code: a missing translation is a type error. */
export const PROBLEM_KEYS: Record<ProblemCode, { what: MessageKey; fix: MessageKey }> = {
  essaies_unreachable: { what: 'problemEssaiesUnreachable', fix: 'problemEssaiesUnreachableFix' },
  essaies_down: { what: 'problemEssaiesDown', fix: 'problemEssaiesDownFix' },
  essaies_not_set: { what: 'problemEssaiesNotSet', fix: 'problemEssaiesNotSetFix' },
  openalex_quota: { what: 'problemOpenalexQuota', fix: 'problemOpenalexQuotaFix' },
  query_failed: { what: 'problemQueryFailed', fix: 'problemQueryFailedFix' },
  search_failed: { what: 'problemSearchFailed', fix: 'problemSearchFailedFix' },
  citations_failed: { what: 'problemCitationsFailed', fix: 'problemCitationsFailedFix' },
  essaies_old: { what: 'problemEssaiesOld', fix: 'problemEssaiesOldFix' },
  openalex_unreachable: { what: 'problemOpenalexUnreachable', fix: 'problemOpenalexUnreachableFix' },
  openalex_down: { what: 'problemOpenalexDown', fix: 'problemOpenalexDownFix' },
  openalex_rejected: { what: 'problemOpenalexRejected', fix: 'problemOpenalexRejectedFix' },
  model_unreachable: { what: 'problemModelUnreachable', fix: 'problemModelUnreachableFix' },
  model_not_configured: { what: 'problemModelNotConfigured', fix: 'problemModelNotConfiguredFix' },
  model_rejected: { what: 'problemModelRejected', fix: 'problemModelRejectedFix' },
  model_failed: { what: 'problemModelFailed', fix: 'problemModelFailedFix' },
  plan_fallback: { what: 'problemPlanFallback', fix: 'problemPlanFallbackFix' },
  stage_failed: { what: 'problemStageFailed', fix: 'problemStageFailedFix' },
};

export function Reason({
  code,
  message,
  detail,
}: {
  code?: ProblemCode | null;
  message: string;
  detail?: string | null;
}) {
  const t = useT();
  const { prefs } = usePrefs();
  const keys = code ? PROBLEM_KEYS[code] : undefined;
  if (!keys)
    return (
      <span className="q-reason">
        <strong>{t('problemStageFailed')}</strong>
        <UpstreamReason text={message} />
      </span>
    );
  return (
    <span className="q-reason">
      <strong>{t(keys.what)}</strong>
      {prefs.lang === 'zh' ? '' : ' '}
      {t(keys.fix)}
      <UpstreamReason text={detail || message} />
    </span>
  );
}

/** "Reason: …" with the upstream's own English words. */
export function UpstreamReason({ text }: { text: string }) {
  const t = useT();
  return (
    <small>
      {t('problemReason')}
      <span lang="en">{text}</span>
    </small>
  );
}
