// A progress bar; without `value` it shows ongoing work of unknown length.
import { useT } from '../i18n';

export interface ProgressProps {
  value?: number;
  max?: number;
  label?: string;
}

export function Progress({ value, max = 100, label }: ProgressProps) {
  const t = useT();
  const known = value !== undefined && Number.isFinite(value) && max > 0;
  const share = known ? Math.min(1, Math.max(0, value / max)) : 0;
  return (
    <div
      className={`ui-progress ${known ? '' : 'ui-progress-busy'}`}
      role="progressbar"
      aria-label={label ?? t('progress')}
      aria-valuemin={known ? 0 : undefined}
      aria-valuemax={known ? max : undefined}
      aria-valuenow={known ? value : undefined}
    >
      <i style={known ? { width: `${(share * 100).toFixed(1)}%` } : undefined} />
    </div>
  );
}
