// A modal on the native <dialog>: focus stays inside, Escape closes it, and
// the page behind is inert. `variant="drawer"` slides it in from the side.
import { useEffect, useId, useRef, type ReactNode } from 'react';
import { useT } from '../i18n';
import { Close } from '../shell/icons';

export interface DialogProps {
  open: boolean;
  title: ReactNode;
  onClose: () => void;
  children: ReactNode;
  /** Buttons along the bottom edge. */
  actions?: ReactNode;
  variant?: 'dialog' | 'drawer';
}

export function Dialog({ open, title, onClose, children, actions, variant = 'dialog' }: DialogProps) {
  const t = useT();
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    if (open && !el.open) {
      if (typeof el.showModal === 'function') el.showModal();
      else el.setAttribute('open', '');
    } else if (!open && el.open) {
      if (typeof el.close === 'function') el.close();
      else el.removeAttribute('open');
    }
  }, [open]);

  return (
    <dialog
      ref={ref}
      className={`ui-dialog ${variant === 'drawer' ? 'ui-drawer' : ''}`}
      aria-labelledby={titleId}
      // Escape: let React own the state instead of the browser closing it.
      onCancel={(e) => {
        e.preventDefault();
        onClose();
      }}
      // A click on the backdrop lands on the <dialog> itself.
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      {open && (
        <div className="ui-dialog-body">
          <div className="ui-dialog-head">
            <h2 id={titleId}>{title}</h2>
            <button type="button" className="btn ui-icon" aria-label={t('closeDialog')} onClick={onClose}>
              <Close />
            </button>
          </div>
          <div className="ui-dialog-content">{children}</div>
          {actions && <div className="settings-actions">{actions}</div>}
        </div>
      )}
    </dialog>
  );
}

export function Drawer(props: Omit<DialogProps, 'variant'>) {
  return <Dialog {...props} variant="drawer" />;
}
