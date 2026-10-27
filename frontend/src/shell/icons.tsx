// Navigation icons, drawn as in the classic console.
import type { ReactNode } from 'react';

const Svg = ({ children, size = 18 }: { children: ReactNode; size?: number }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" aria-hidden="true">
    {children}
  </svg>
);

export const ICONS = {
  overview: (
    <Svg>
      <circle cx="12" cy="12" r="9" />
      <circle cx="12" cy="12" r="4" />
      <path d="M12 12 L18.5 5.5" />
    </Svg>
  ),
  reports: (
    <Svg>
      <path d="M6 3h9l4 4v14H6z" />
      <path d="M9 11h7M9 15h7M9 7h3" />
    </Svg>
  ),
  triage: (
    <Svg>
      <path d="M3 4h18l-7 8v6l-4 2v-8z" />
    </Svg>
  ),
  review: (
    <Svg>
      <path d="M4 5a2 2 0 0 1 2-2h12v16H6a2 2 0 0 0-2 2z" />
      <path d="M4 19V5" />
      <path d="M9 8h6" />
    </Svg>
  ),
  runs: (
    <Svg>
      <path d="M3 12h4l3-8 4 16 3-8h4" />
    </Svg>
  ),
  settings: (
    <Svg>
      <path d="M4 7h16M4 17h16" />
      <circle cx="9" cy="7" r="3" style={{ fill: 'var(--nav)' }} />
      <circle cx="15" cy="17" r="3" style={{ fill: 'var(--nav)' }} />
    </Svg>
  ),
  topicSettings: (
    <Svg>
      <path d="M4 4h6v6H4zM14 4h6v6h-6zM4 14h6v6H4zM14 14h6v6h-6z" />
    </Svg>
  ),
  sources: (
    <Svg>
      <path d="M4 5h16v14H4zM4 10h16M9 10v9" />
    </Svg>
  ),
  system: (
    <Svg>
      <rect x="4" y="4" width="16" height="6" rx="1" />
      <rect x="4" y="14" width="16" height="6" rx="1" />
      <path d="M8 7h2M8 17h2" />
    </Svg>
  ),
  help: (
    <Svg>
      <circle cx="12" cy="12" r="9" />
      <path d="M9.5 9.5a2.5 2.5 0 1 1 3.5 2.3c-.6.3-1 .9-1 1.5v.7M12 17h.01" />
    </Svg>
  ),
  question: (
    <Svg>
      <circle cx="6" cy="17" r="2" />
      <circle cx="12" cy="6" r="2" />
      <circle cx="18" cy="15" r="2" />
      <path d="M7 15.2 11 7.8M13.1 7.7l3.9 5.6M8 17h8" />
    </Svg>
  ),
};

export const Brush = () => (
  <svg
    width="16"
    height="16"
    viewBox="0 0 24 24"
    fill="none"
    strokeWidth="1.8"
    strokeLinecap="round"
    strokeLinejoin="round"
    aria-hidden="true"
    style={{ stroke: 'var(--accent)' }}
  >
    <path d="M4 20h4L19 9l-4-4L4 16z" />
    <path d="M13.5 6.5l4 4" />
  </svg>
);

export const Close = () => (
  <svg
    width="16"
    height="16"
    viewBox="0 0 24 24"
    fill="none"
    stroke="currentColor"
    strokeWidth="2"
    strokeLinecap="round"
    aria-hidden="true"
  >
    <path d="M6 6l12 12M18 6L6 18" />
  </svg>
);
