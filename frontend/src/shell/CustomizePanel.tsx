// Theme preset or custom hue, light/dark/device mode, and the report sheet's
// reading surface, as in the classic console's panel.
import { useT } from '../i18n';
import { PRESETS, hueAndChroma, usePrefs, type Mode, type Paper, type Preset } from '../prefs';
import { Close } from './icons';

const NAMES: Record<Preset, string> = {
  signal: 'Signal',
  glacier: 'Glacier',
  iris: 'Iris',
  coral: 'Coral',
  ember: 'Ember',
  moss: 'Moss',
  graphite: 'Graphite',
};

const MODE_ICONS: Record<Mode, string> = {
  light:
    'M12 8a4 4 0 1 0 0 8a4 4 0 1 0 0-8M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4',
  dark: 'M20 14.5A8 8 0 0 1 9.5 4a8 8 0 1 0 10.5 10.5z',
  device: 'M3 5h18v11H3zM8 20h8M12 16v4',
};

const RAINBOW =
  'oklch(.75 .14 0),oklch(.75 .14 60),oklch(.75 .14 120),oklch(.75 .14 180),oklch(.75 .14 240),oklch(.75 .14 300),oklch(.75 .14 360)';

export function CustomizePanel({ onClose }: { onClose: () => void }) {
  const t = useT();
  const { prefs, update, reset } = usePrefs();
  const light = prefs.mode === 'light';
  const fill = (h: number, c: number) =>
    light
      ? `linear-gradient(135deg, oklch(.5 ${0.12 * c} ${h}) 0 50%, oklch(.955 ${0.012 * c} ${h}) 50% 100%)`
      : `linear-gradient(135deg, oklch(.8 ${0.135 * c} ${h}) 0 50%, oklch(.205 ${0.016 * c} ${h}) 50% 100%)`;
  const [h, c] = hueAndChroma(prefs);
  const papers: [Paper, string, string, string][] = [
    ['paper', 'oklch(.982 .008 95)', 'oklch(.21 0 0)', 'oklch(.86 .012 85)'],
    ['sepia', 'oklch(.94 .035 85)', 'oklch(.25 .03 60)', 'oklch(.80 .04 80)'],
    ['night', `oklch(.22 ${0.012 * c} ${h})`, 'oklch(.91 .01 90)', `oklch(.36 ${0.015 * c} ${h})`],
  ];
  return (
    <aside className="custom" aria-labelledby="customize-h">
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
        <div style={{ display: 'flex', flexDirection: 'column', gap: 4 }}>
          <h2 id="customize-h">{t('customTitle')}</h2>
          <span className="muted" style={{ fontSize: 13 }}>
            {t('customNote')}
          </span>
        </div>
        <button
          type="button"
          className="btn"
          aria-label={t('close')}
          style={{ width: 44, height: 44, padding: 0 }}
          onClick={onClose}
        >
          <Close />
        </button>
      </div>
      <section>
        <h3>{t('mode')}</h3>
        <div className="opts3">
          {(Object.keys(MODE_ICONS) as Mode[]).map((mode) => (
            <button
              key={mode}
              type="button"
              aria-pressed={prefs.mode === mode}
              onClick={() => update({ mode })}
            >
              <svg
                width="20"
                height="20"
                viewBox="0 0 24 24"
                fill="none"
                stroke="currentColor"
                strokeWidth="1.8"
                strokeLinecap="round"
                strokeLinejoin="round"
                aria-hidden="true"
              >
                <path d={MODE_ICONS[mode]} />
              </svg>
              {t(mode)}
            </button>
          ))}
        </div>
      </section>
      <section>
        <h3>{t('color')}</h3>
        <div className="swatches">
          {(Object.entries(PRESETS) as [Preset, readonly [number, number]][]).map(([key, [ph, pc]]) => (
            <button
              key={key}
              type="button"
              aria-pressed={prefs.theme === key}
              onClick={() => update({ theme: key })}
            >
              <i style={{ background: fill(ph, pc) }} />
              {NAMES[key]}
            </button>
          ))}
          <button
            type="button"
            aria-pressed={prefs.theme === 'custom'}
            onClick={() => update({ theme: 'custom' })}
          >
            <i style={{ background: `conic-gradient(${RAINBOW})` }} />
            {t('custom')}
          </button>
        </div>
        <label className="hue">
          <span style={{ display: 'flex', justifyContent: 'space-between' }}>
            <span>{t('customHue')}</span>
            <span className="mono muted">{prefs.hue}°</span>
          </span>
          <span className="rainbow" aria-hidden="true" />
          <input
            type="range"
            min={0}
            max={360}
            step={1}
            value={prefs.hue}
            onChange={(e) => update({ hue: Number(e.target.value), theme: 'custom' })}
          />
        </label>
      </section>
      <section>
        <h3>{t('surface')}</h3>
        <div className="opts3">
          {papers.map(([key, bg, ink, line]) => (
            <button
              key={key}
              type="button"
              className="paperpick"
              aria-pressed={prefs.paper === key}
              onClick={() => update({ paper: key })}
              style={{ padding: '8px 8px 10px', gap: 8, alignItems: 'stretch' }}
            >
              <i style={{ background: bg }}>
                <b style={{ display: 'block', height: 6, width: '72%', borderRadius: 3, background: ink }} />
                <b style={{ display: 'block', height: 4, width: '92%', borderRadius: 2, background: line }} />
                <b
                  style={{
                    display: 'block',
                    height: 4,
                    width: '50%',
                    borderRadius: 2,
                    background: `oklch(.45 ${0.09 * c} ${h})`,
                  }}
                />
              </i>
              {t(key)}
            </button>
          ))}
        </div>
        <span className="muted" style={{ fontSize: 12.5, lineHeight: 1.5 }}>
          {t('surfaceNote')}
        </span>
      </section>
      <button type="button" className="btn" style={{ alignSelf: 'flex-start' }} onClick={reset}>
        {t('reset')}
      </button>
    </aside>
  );
}
