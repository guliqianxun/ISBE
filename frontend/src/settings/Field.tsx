// One Settings field: its label, where its value comes from (a chip), an
// environment lock, the secret placeholder and remove/undo. Split from
// SettingsPage.tsx (#79).
import { useT, type MessageKey } from '../i18n';
import { SECRETS, shown, sourceOf, type Edits, type Snapshot } from './model';

/** The choices of each select, as message keys (or names shown as they are). */
const OPTIONS: Record<string, [string, MessageKey | null, string?][]> = {
  ISBE_LLM_PROVIDER: [
    ['anthropic', null, 'Anthropic'],
    ['deepseek', null, 'DeepSeek'],
    ['openai', null, 'OpenAI / compatible'],
  ],
  ISBE_LLM_NO_AUTH: [
    ['false', 'sAuthRequired'],
    ['true', 'sAuthNone'],
  ],
  ISBE_FULLTEXT_EXTRACTOR: [
    ['llm', 'sExtractorLlm'],
    ['metrail', 'sExtractorMetrail'],
    ['off', 'sExtractorOff'],
  ],
  ISBE_PAPER_SOURCE: [
    ['openalex', 'sPaperSourceOpenalex'],
    ['essaies', 'sPaperSourceEssaies'],
  ],
  ISBE_ARTICLE_DNS: [
    ['system', 'sArticleDnsSystem'],
    ['cloudflare', 'sArticleDnsCloudflare'],
  ],
  EMAIL_TRANSPORT: [
    ['smtp', 'sSmtp'],
    ['zeptomail_api', 'sZeptomailApi'],
  ],
  SMTP_SECURITY: [
    ['tls', 'sTls'],
    ['starttls', 'sStarttls'],
  ],
};

const SOURCE_KEYS = {
  environment: 'sSourceEnvironment',
  file: 'sSourceFile',
  default: 'sSourceDefault',
} as const;

interface FieldProps {
  name: string;
  label: string;
  type: string;
  snapshot: Snapshot;
  edits: Edits;
  onChange: (key: string, value: string) => void;
  onRemove: (key: string) => void;
}

/** One setting: its label, where its value comes from, the control and a hint. */
export function Field({ name, label, type, snapshot: s, edits, onChange, onRemove }: FieldProps) {
  const t = useT();
  const source = sourceOf(s, name);
  const secret = SECRETS.has(name);
  const removing = secret && edits[name] === null;
  const locked = source === 'environment';
  const set = !!s.configured[name];
  const value = shown(s, edits, name);
  const id = `setting-${name}`;
  const common = {
    id,
    name,
    'aria-describedby': `hint-${name}`,
    disabled: locked || removing,
  };
  const control =
    type === 'select' ? (
      <select {...common} value={value} onChange={(e) => onChange(name, e.target.value)}>
        {OPTIONS[name]!.map(([v, key, text]) => (
          <option key={v} value={v}>
            {key ? t(key) : text}
          </option>
        ))}
      </select>
    ) : type === 'textarea' ? (
      <textarea
        {...common}
        rows={4}
        spellCheck={false}
        placeholder="{}"
        value={value}
        onChange={(e) => onChange(name, e.target.value)}
      />
    ) : (
      <input
        {...common}
        type={secret ? 'password' : type}
        value={value}
        min={type === 'number' ? 1 : undefined}
        step={type === 'number' ? 1 : undefined}
        autoComplete={secret ? 'new-password' : 'off'}
        placeholder={secret ? t(set ? 'sKeepSecret' : 'sNewSecret') : undefined}
        spellCheck={false}
        onChange={(e) => onChange(name, e.target.value)}
      />
    );
  const hint = [
    locked
      ? t('sEnvironmentLock')
      : removing
        ? t('sRemovingSecret')
        : secret
          ? t(set ? 'sKeepSecret' : 'sNewSecret')
          : '',
    name === 'METRAIL_API_URL'
      ? t('sMetrailRequired')
      : name === 'ISBE_ARTICLE_DNS'
        ? t('sArticleDnsNote')
        : name === 'OPENALEX_MAILTO'
          ? t('sOpenalexMailtoNote')
          : '',
  ]
    .filter(Boolean)
    .join(' ');
  return (
    <div className={`setting-field${type === 'textarea' ? ' wide' : ''}`}>
      <div className="setting-label">
        <label htmlFor={id}>{label}</label>
        <span className="chip plain">
          {t(SOURCE_KEYS[source])}
          {secret ? ` · ${t(set ? 'sOnline' : 'sOff')}` : ''}
        </span>
      </div>
      {control}
      <div className="setting-hint" id={`hint-${name}`}>
        {hint}
      </div>
      {secret && !locked && (set || removing) && (
        <button type="button" className="setting-remove" onClick={() => onRemove(name)}>
          {t(removing ? 'sUndoRemove' : 'sRemoveSecret')}
        </button>
      )}
    </div>
  );
}
