import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { ExternalLink, safeUrl } from './safe';

describe('outside links', () => {
  it('accepts only http(s)', () => {
    expect(safeUrl('https://arxiv.org/abs/1')).toBe('https://arxiv.org/abs/1');
    expect(safeUrl(' HTTP://x.org ')).toBe('HTTP://x.org');
    for (const bad of ['javascript:alert(1)', 'data:text/html,x', '//x.org', '/local', null, 3]) {
      expect(safeUrl(bad)).toBe('');
    }
  });

  it('renders text instead of an unsafe link, and escapes markup', () => {
    render(
      <>
        <ExternalLink href="javascript:alert(1)">bad</ExternalLink>
        <ExternalLink href="https://x.org">{'<b>good</b>'}</ExternalLink>
      </>,
    );
    expect(screen.queryByRole('link', { name: 'bad' })).toBeNull();
    const link = screen.getByRole('link', { name: '<b>good</b>' });
    expect(link).toHaveAttribute('href', 'https://x.org');
    expect(link).toHaveAttribute('rel', 'noreferrer noopener');
    expect(link.querySelector('b')).toBeNull();
  });
});
