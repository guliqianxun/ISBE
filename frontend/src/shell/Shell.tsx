// The page frame: side navigation, the signed-in account, appearance and
// language, and the ask bar over the routed page.
import { useState, type ReactNode } from 'react';
import { Link, useLocation } from 'react-router';
import { useT } from '../i18n';
import { useSession, useSignedIn } from '../session';
import { useOwner } from '../workspace';
import { mayLeave, stayed } from './leave';
import { AskBar } from './AskBar';
import { CustomizePanel } from './CustomizePanel';
import { Brush, ICONS } from './icons';
import { LanguageSwitch } from './LanguageSwitch';
import { visibleNav, type NavItem } from './nav';

const under = (path: string, href: string) =>
  href === '/' ? path === '/' : path === href || path.startsWith(`${href}/`);

/** The sidebar place a path belongs to. */
export const isCurrent = (path: string, item: NavItem) =>
  under(path, item.href) || !!item.also?.some((p) => under(path, p));

function Brand() {
  const t = useT();
  return (
    <a className="brand" href="/">
      <svg width="36" height="36" viewBox="0 0 36 36" aria-hidden="true">
        <circle cx="18" cy="18" r="16" fill="none" strokeWidth="1.5" style={{ stroke: 'var(--line)' }} />
        <circle cx="18" cy="18" r="9" fill="none" strokeWidth="1.5" style={{ stroke: 'var(--line)' }} />
        <path d="M18 18 L18 2 A16 16 0 0 1 31.86 10 Z" style={{ fill: 'var(--accent)', fillOpacity: 0.32 }} />
        <circle cx="24.5" cy="11" r="2.4" style={{ fill: 'var(--accent)' }} />
      </svg>
      <span>
        <b>ISBE</b>
        <small>{t('radar')}</small>
      </span>
    </a>
  );
}

export function Shell({ children }: { children: ReactNode }) {
  const t = useT();
  const session = useSignedIn();
  const { signOut } = useSession();
  const owner = useOwner();
  const { pathname } = useLocation();
  const [panel, setPanel] = useState(false);
  const [signingOut, setSigningOut] = useState(false);
  const [error, setError] = useState(false);

  async function logout() {
    if (signingOut || !mayLeave()) return;
    setSigningOut(true);
    setError(false);
    const ok = await signOut();
    setSigningOut(false);
    setError(!ok);
    if (!ok) stayed();
  }

  return (
    <>
      <nav className="side" aria-label="Main">
        <Brand />
        <div className="navs">
          {visibleNav(session).map((item) => {
            const body = (
              <>
                {ICONS[item.icon]}
                <span style={{ flex: 1 }}>{t(item.label)}</span>
              </>
            );
            if (item.soon) {
              return (
                <span key={item.href} className="nav" aria-disabled="true" title={t(item.soon)}>
                  {body}
                </span>
              );
            }
            const current = isCurrent(pathname, item) ? 'page' : undefined;
            if (item.react) {
              return (
                <Link key={item.href} className="nav" to={item.href} aria-current={current}>
                  {body}
                </Link>
              );
            }
            // A classic page: a whole-page load.
            return (
              <a key={item.href} className="nav" href={item.href} aria-current={current}>
                {body}
              </a>
            );
          })}
        </div>
        <div className="side-foot">
          {session.mode === 'shared' && (
            <>
              <span className="setting-hint">{session.user.username}</span>
              <button type="button" className="btn" onClick={logout} disabled={signingOut}>
                {t('logout')}
              </button>
              <span role="status" className="error">
                {error ? t('actionFailed') : ''}
              </span>
            </>
          )}
          <button type="button" className="btn" aria-expanded={panel} onClick={() => setPanel(!panel)}>
            <Brush />
            {t('customize')}
          </button>
          <LanguageSwitch />
        </div>
      </nav>
      <main className="main" data-page={pathname === '/' ? 'home' : undefined}>
        <AskBar />
        {owner && (
          <p className="chip warn" role="note" style={{ alignSelf: 'flex-start' }}>
            {t('viewingWorkspace', { owner })}
          </p>
        )}
        {children}
      </main>
      {panel && <CustomizePanel onClose={() => setPanel(false)} />}
    </>
  );
}
