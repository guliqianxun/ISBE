import { usePrefs } from '../prefs';

export function LanguageSwitch() {
  const { prefs, update } = usePrefs();
  return (
    <div className="seg" role="group" aria-label="Language">
      <button type="button" aria-pressed={prefs.lang === 'zh'} onClick={() => update({ lang: 'zh' })}>
        中文
      </button>
      <button type="button" aria-pressed={prefs.lang === 'en'} onClick={() => update({ lang: 'en' })}>
        EN
      </button>
    </div>
  );
}
