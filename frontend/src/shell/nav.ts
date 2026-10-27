// The console's pages. The sidebar holds the places a person goes to (Home,
// Galaxies, Reports, Topics, Settings), System, which lists the working
// pages, and Help; the classic console draws the same sidebar from its own copy of
// `NAV` (`NAV` in web/app.js, which a Vitest test compares with this one).
// `react` pages are routed in this app; the others load as whole pages. Keep
// the React paths in step with `REACT_PATHS` in isbe/web/routes.py, which
// decides which app the server answers with.
import type { Session } from '../session';
import type { MessageKey } from '../i18n';
import type { ICONS } from './icons';

export interface NavItem {
  href: string;
  label: MessageKey;
  icon: keyof typeof ICONS;
  react?: boolean;
  admin?: boolean;
  sharedOnly?: boolean;
  /** Shown but not yet a page. */
  soon?: MessageKey;
  /** Also current on these paths. */
  also?: string[];
}

export const NAV: NavItem[] = [
  { href: '/', label: 'home', icon: 'overview', react: true },
  { href: '/galaxies', label: 'galaxies', icon: 'question', react: true, also: ['/question'] },
  { href: '/reports', label: 'reports', icon: 'reports' },
  { href: '/topics', label: 'topics', icon: 'topicSettings' },
  { href: '/settings', label: 'settings', icon: 'settings', react: true, admin: true },
  {
    href: '/system',
    label: 'system',
    icon: 'system',
    react: true,
    also: ['/overview', '/tasks', '/triage', '/runs', '/sources', '/users'],
  },
  { href: '/help', label: 'help', icon: 'help', react: true },
];

/** What System lists, with a line on each: the pages that are not in the sidebar. */
export interface SystemItem extends NavItem {
  about: MessageKey;
}

export const SYSTEM: SystemItem[] = [
  { href: '/question', label: 'question', icon: 'question', react: true, about: 'sysQuestion' },
  { href: '/overview', label: 'overview', icon: 'overview', about: 'sysOverview' },
  { href: '/tasks', label: 'tasks', icon: 'reports', about: 'sysTasks' },
  { href: '/triage', label: 'triage', icon: 'triage', admin: true, about: 'sysTriage' },
  { href: '/review', label: 'review', icon: 'review', admin: true, soon: 'reviewSoon', about: 'sysReview' },
  { href: '/runs', label: 'runs', icon: 'runs', admin: true, about: 'sysRuns' },
  { href: '/sources', label: 'sources', icon: 'sources', admin: true, about: 'sysSources' },
  { href: '/users', label: 'users', icon: 'reports', admin: true, sharedOnly: true, about: 'sysUsers' },
];

const allowed = (session: Session) => (item: NavItem) =>
  (!item.admin || session.user?.role === 'admin') && (!item.sharedOnly || session.mode === 'shared');

export const visibleNav = (session: Session): NavItem[] => NAV.filter(allowed(session));
export const visibleSystem = (session: Session): SystemItem[] => SYSTEM.filter(allowed(session));
