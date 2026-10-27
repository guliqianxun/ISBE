// Text from sources (feeds, papers, other accounts) is untrusted. React escapes
// it when rendered as text; never pass it to dangerouslySetInnerHTML. Links
// from it become links only when they are http(s).
import type { AnchorHTMLAttributes } from 'react';

export const safeUrl = (url: unknown): string =>
  typeof url === 'string' && /^https?:\/\//i.test(url.trim()) ? url.trim() : '';

type Props = Omit<AnchorHTMLAttributes<HTMLAnchorElement>, 'href' | 'rel' | 'target'> & { href: unknown };

/** A link to an outside page, or plain text when the address is not http(s). */
export function ExternalLink({ href, children, ...rest }: Props) {
  const url = safeUrl(href);
  if (!url) return <span {...rest}>{children}</span>;
  return (
    <a {...rest} href={url} target="_blank" rel="noreferrer noopener">
      {children}
    </a>
  );
}
