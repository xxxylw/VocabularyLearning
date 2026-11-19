import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { IcpFooter } from './IcpFooter';

describe('IcpFooter', () => {
  it('renders the MIIT ICP filing link pointing at beian.miit.gov.cn', () => {
    render(<IcpFooter />);
    const icp = screen.getByRole('link', { name: '陇ICP备2026010404号' });
    expect(icp).toHaveAttribute('href', 'https://beian.miit.gov.cn');
    expect(icp).toHaveAttribute('target', '_blank');
    expect(icp).toHaveAttribute('rel', 'noopener');
  });

  it('renders the public-security filing link with its record code', () => {
    render(<IcpFooter />);
    const psb = screen.getByRole('link', { name: /甘公网安备62050302000397号/ });
    expect(psb).toHaveAttribute(
      'href',
      'http://www.beian.gov.cn/portal/registerSystemInfo?recordcode=62050302000397'
    );
    expect(psb).toHaveAttribute('target', '_blank');
    expect(psb).toHaveAttribute('rel', 'noopener');
  });

  it('shows the self-hosted police badge inside the public-security link', () => {
    render(<IcpFooter />);
    const psb = screen.getByRole('link', { name: /甘公网安备62050302000397号/ });
    const badge = psb.querySelector('img');
    expect(badge).not.toBeNull();
    expect(badge).toHaveAttribute('src', '/gongan.png');
    // Decorative: the record number text already names the link.
    expect(badge).toHaveAttribute('alt', '');
  });
});
