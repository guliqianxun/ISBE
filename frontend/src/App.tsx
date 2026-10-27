import { useLayoutEffect, useRef, useState, type CSSProperties, type ReactNode } from 'react';
import { createBrowserRouter, Route, RouterProvider, Routes } from 'react-router';
import { HelpPage } from './help/HelpPage';
import { GalaxiesPage } from './home/GalaxiesPage';
import { HomePage } from './home/HomePage';
import { useT } from './i18n';
import { NotFound } from './pages/NotFound';
import { QuestionPage } from './question/QuestionPage';
import { RunPage } from './question/RunPage';
import { hueAndChroma, PrefsProvider, usePrefs } from './prefs';
import { SessionProvider, useSession } from './session';
import { SettingsPage } from './settings/SettingsPage';
import { LoginPage } from './shell/LoginPage';
import { Shell } from './shell/Shell';
import { SystemPage } from './shell/SystemPage';

/** The themed root: the color tokens in web/app.css read these. */
function Themed({ children }: { children: ReactNode }) {
  const { prefs } = usePrefs();
  const root = useRef<HTMLDivElement>(null);
  const [h, c] = hueAndChroma(prefs);
  useLayoutEffect(() => {
    // Overscroll and the area outside the app match the page.
    if (root.current) document.body.style.background = getComputedStyle(root.current).backgroundColor;
  }, [h, c, prefs.mode]);
  const style = { '--h': h, '--c': c } as CSSProperties;
  return (
    <div ref={root} className="isbe" data-mode={prefs.mode} data-paper={prefs.paper} style={style}>
      {children}
    </div>
  );
}

export function Pages() {
  return (
    <Routes>
      <Route path="/" element={<HomePage />} />
      <Route path="/galaxies" element={<GalaxiesPage />} />
      <Route path="/settings" element={<SettingsPage />} />
      <Route path="/system" element={<SystemPage />} />
      <Route path="/help" element={<HelpPage />} />
      <Route path="/question" element={<QuestionPage />} />
      <Route path="/question/runs/:id" element={<RunPage />} />
      <Route path="*" element={<NotFound />} />
    </Routes>
  );
}

function Gate() {
  const t = useT();
  const { state, reload } = useSession();
  if (state.status === 'loading') {
    return (
      <main className="main">
        <p className="muted" role="status">
          {t('loading')}
        </p>
      </main>
    );
  }
  if (state.status === 'unavailable') {
    return (
      <main className="main">
        <div className="card error" role="alert">
          {t('sessionUnavailable')}
          <button type="button" className="btn" onClick={() => void reload()}>
            {t('retry')}
          </button>
        </div>
      </main>
    );
  }
  if (!state.session.user) return <LoginPage notice={state.notice} />;
  return (
    <Shell>
      <Pages />
    </Shell>
  );
}

export function App() {
  // A data router, so a page can hold the person with useBlocker (Settings).
  const [router] = useState(() => createBrowserRouter([{ path: '*', element: <Gate /> }]));
  return (
    <PrefsProvider>
      <Themed>
        <SessionProvider>
          <RouterProvider router={router} />
        </SessionProvider>
      </Themed>
    </PrefsProvider>
  );
}
