// Galaxies (/galaxies): every galaxy as a table row, newest first. The saved
// views (graph, board, timeline, cards) come later; this is the plain list.
import { useEffect, useMemo } from 'react';
import { useNavigate } from 'react-router';
import { Table } from '../components/Table';
import { useT } from '../i18n';
import { PROBLEM_KEYS } from '../question/Reason';
import { runStatusKey } from '../question/RunHeader';
import { layout, type Galaxy } from './galaxies';
import { color } from './sky';
import { useRuns } from './useRuns';

export function GalaxiesPage() {
  const t = useT();
  const navigate = useNavigate();
  const { runs, failed } = useRuns();
  const rows = useMemo(() => layout(runs ?? []).reverse(), [runs]);

  useEffect(() => {
    document.title = `ISBE · ${t('galaxies')}`;
  });

  return (
    <>
      <header className="head">
        <h1 className="title g-title">{t('galaxies')}</h1>
      </header>
      <section className="card">
        {runs === null ? (
          <p className="muted">{t('loading')}</p>
        ) : failed ? (
          <p className="error" role="alert">
            {t('gLoadFailed')}
          </p>
        ) : (
          <Table<Galaxy>
            caption={t('galaxies')}
            rows={rows}
            rowKey={(g) => g.id}
            empty={t('gEmpty')}
            onOpen={(g) => navigate(`/?g=${encodeURIComponent(g.id)}`)}
            columns={[
              {
                key: 'name',
                header: t('gGalaxy'),
                cell: (g) => (
                  <span className="g-row-name">
                    <i className="g-dot" style={{ background: color(g, 70) }} />
                    {g.name}
                  </span>
                ),
              },
              { key: 'papers', header: t('gPapers'), align: 'end', cell: (g) => g.papers.toLocaleString() },
              {
                key: 'included',
                header: t('qIncluded'),
                align: 'end',
                cell: (g) => g.included.toLocaleString(),
              },
              {
                key: 'unjudged',
                header: t('gUnjudged'),
                align: 'end',
                cell: (g) => g.run.counts.unscreened.toLocaleString(),
              },
              {
                key: 'status',
                header: t('gStatus'),
                cell: (g) => {
                  if (g.run.status === 'done' && g.run.degraded)
                    return t('qDoneDegraded', { n: g.run.problems });
                  const code = g.run.status === 'failed' ? g.run.failure?.code : undefined;
                  const what =
                    code && PROBLEM_KEYS[code]
                      ? ` · ${t(PROBLEM_KEYS[code].what).replace(/[。.]$/, '')}`
                      : '';
                  return t(runStatusKey(g.run)) + what;
                },
              },
              { key: 'asked', header: t('gAsked'), cell: (g) => g.run.created_at.slice(0, 10) },
            ]}
          />
        )}
      </section>
    </>
  );
}
