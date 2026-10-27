// System (/system): the console's working pages, most of them still the
// classic console's, until they move into Home, Galaxies and Reports.
import { useEffect } from 'react';
import { Link } from 'react-router';
import { useT } from '../i18n';
import { useSignedIn } from '../session';
import { ICONS } from './icons';
import { visibleSystem } from './nav';

export function SystemPage() {
  const t = useT();
  const session = useSignedIn();

  useEffect(() => {
    document.title = `ISBE · ${t('system')}`;
  });

  return (
    <>
      <header className="head">
        <h1 className="title g-title">{t('system')}</h1>
      </header>
      <ul className="g-tiles">
        {visibleSystem(session).map((item) => {
          const body = (
            <>
              <span className="g-tile-head">
                {ICONS[item.icon]}
                {t(item.label)}
              </span>
              <span className="muted">{t(item.soon ?? item.about)}</span>
            </>
          );
          return (
            <li key={item.href}>
              {item.soon ? (
                <span className="g-tile" aria-disabled="true">
                  {body}
                </span>
              ) : item.react ? (
                <Link className="g-tile" to={item.href}>
                  {body}
                </Link>
              ) : (
                <a className="g-tile" href={item.href}>
                  {body}
                </a>
              )}
            </li>
          );
        })}
      </ul>
    </>
  );
}
