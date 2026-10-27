// Help (/help): what ISBE does and how to start (Home's first-run guide), the
// report guide (`#report-guide`, opened from the classic pages with
// `?topic=`, `?task=` or `?new=1`), and for administrators the setup and
// operations guide served at /help/service (docs/configuration.md and
// docs/deployment.md).
import { useEffect, useLayoutEffect } from 'react';
import { useLocation, useSearchParams } from 'react-router';
import { useT } from '../i18n';
import { useHome } from '../home/useHome';
import { Welcome } from '../home/Welcome';
import { useSignedIn } from '../session';
import { useOwner } from '../workspace';
import { ReportGuide } from './ReportGuide';

// What the person does that ends the landing on the report guide.
const HELD = ['wheel', 'touchstart', 'keydown', 'pointerdown'] as const;

export function HelpPage() {
  const t = useT();
  const session = useSignedIn();
  const admin = session.user.role === 'admin';
  const [params] = useSearchParams();
  const { hash } = useLocation();
  // The guide works in one's own workspace only, as in the classic console.
  const owner = useOwner();
  const home = useHome(null);

  useEffect(() => {
    document.title = `ISBE · ${t('help')}`;
  });

  // A link to `#report-guide` lands on the guide, not the top of the page.
  // The sections load their content after that, which moves or grows the page,
  // so it lands again on each change until the person scrolls or a few
  // seconds pass.
  useLayoutEffect(() => {
    if (hash !== '#report-guide') return;
    const land = () => document.getElementById('report-guide')?.scrollIntoView?.({ block: 'start' });
    land();
    document.getElementById('guide-title')?.focus({ preventScroll: true });
    if (typeof ResizeObserver === 'undefined') return;
    const page = new ResizeObserver(land);
    page.observe(document.body);
    const stop = () => {
      page.disconnect();
      clearTimeout(timer);
      for (const e of HELD) window.removeEventListener(e, stop);
    };
    const timer = setTimeout(stop, 3000);
    for (const e of HELD) window.addEventListener(e, stop, { passive: true });
    return stop;
  }, [hash]);

  return (
    <>
      <header className="head">
        <div className="settings-intro">
          <h1 className="title g-title">{t('help')}</h1>
          <p className="sub">{t('helpLead')}</p>
        </div>
      </header>
      <div className="help-page">
        <Welcome home={home} on="help" />
        {!owner && (
          <ReportGuide
            topic={params.get('topic') ?? ''}
            task={params.get('task') ?? ''}
            fresh={params.get('new') === '1'}
          />
        )}
        {admin && (
          <section className="card help-service" aria-labelledby="help-service-h">
            <h2 id="help-service-h">{t('helpService')}</h2>
            <p>{t('helpServiceText')}</p>
            <a className="btn" href="/help/service">
              {t('helpServiceAct')} →
            </a>
          </section>
        )}
      </div>
    </>
  );
}
