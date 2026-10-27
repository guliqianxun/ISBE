// The first-run guide, shared by Home and Help.
import { useT } from '../i18n';
import { useSignedIn } from '../session';
import type { HomeState } from './useHome';

/** What ISBE does, what a run needs, and where to start: Home's first-run
 * guide, and the opening of Help (`on`, for the wording that points at Home). */
export function Welcome({ home, on = 'home' }: { home: HomeState; on?: 'home' | 'help' }) {
  const t = useT();
  const session = useSignedIn();
  const admin = session.user.role === 'admin';
  // A member's view leaves the paper source out: unknown, not missing. An
  // administrator whose view could not be read is told so rather than left
  // without the row.
  const papers =
    home.state === 'ready'
      ? (home.home.services.papers ?? undefined)
      : home.state === 'failed' && admin
        ? null
        : undefined;
  const ready = (on: boolean | null | undefined) =>
    on === undefined ? null : on === null ? (
      <span className="g-ready">{t('welcomeUnchecked')}</span>
    ) : (
      <span className="g-ready" data-on={on || undefined}>
        {t(on ? 'welcomeReady' : 'welcomeMissing')}
      </span>
    );
  return (
    <section className="g-welcome" aria-labelledby="welcome-h">
      <h2 id="welcome-h">{t('welcomeTitle')}</h2>
      <p>{t('welcomeLead')}</p>
      <p>{t(on === 'home' ? 'welcomeGalaxy' : 'welcomeGalaxyHelp')}</p>
      <dl className="g-welcome-facts">
        <div>
          <dt>{t('welcomeSearched')}</dt>
          <dd>{t('welcomeSearchedText')}</dd>
        </div>
        <div>
          <dt>{t('welcomeTime')}</dt>
          <dd>{t('welcomeTimeText')}</dd>
        </div>
        <div>
          <dt>{t('welcomeModel')}</dt>
          <dd>{ready(session.llm?.ready)}</dd>
        </div>
        {papers !== undefined && (
          <div>
            <dt>
              {t(
                papers?.source === 'essaies'
                  ? 'welcomeEssaies'
                  : papers
                    ? 'welcomeOpenalex'
                    : 'welcomePapers',
              )}
            </dt>
            <dd>{ready(papers && papers.ready)}</dd>
          </div>
        )}
      </dl>
      <ol className="g-welcome-steps">
        <li>
          {t(admin ? 'welcomeStep1' : 'welcomeStep1Member')}{' '}
          {admin && <a href="/settings">{t('welcomeStep1Act')}</a>}
        </li>
        <li>{t(on === 'home' ? 'welcomeStep2' : 'welcomeStep2Help')}</li>
        <li>
          {t('welcomeStep3')} <a href="/topics">{t('welcomeStep3Act')}</a>
        </li>
      </ol>
      <p className="g-welcome-note">{t('welcomeNote')}</p>
    </section>
  );
}
