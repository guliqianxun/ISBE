import { useEffect } from 'react';
import { useT } from '../i18n';

export function NotFound() {
  const t = useT();
  useEffect(() => {
    document.title = `ISBE · ${t('notFoundTitle')}`;
  });
  return (
    <section className="card">
      <h1>{t('notFoundTitle')}</h1>
      <p>{t('notFound')}</p>
      <a className="btn" href="/" style={{ alignSelf: 'flex-start' }}>
        {t('backHome')}
      </a>
    </section>
  );
}
