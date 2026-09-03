import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { computeFittedFontSize, WordHeadline } from './WordHeadline';

// jsdom has no layout engine, so the component tests stub the three inputs
// the fitter reads: the computed default font size, the word's natural
// single-line width (scrollWidth), and the available content-box width
// (clientWidth). Real-browser behavior is covered by these stubs mirroring
// the exact values the DOM would report.
function stubMeasurement({
  fontSize = '48px',
  scrollWidth = 0,
  clientWidth = 0
}: {
  fontSize?: string;
  scrollWidth?: number;
  clientWidth?: number;
}) {
  // A real CSSStyleDeclaration keeps methods (getPropertyValue, ...) that
  // user-event and other libraries call on computed styles.
  const computedStyle = document.createElement('div').style;
  computedStyle.fontSize = fontSize;
  vi.spyOn(window, 'getComputedStyle').mockReturnValue(computedStyle);
  const scrollWidthSpy = vi
    .spyOn(Element.prototype, 'scrollWidth', 'get')
    .mockReturnValue(scrollWidth);
  vi.spyOn(Element.prototype, 'clientWidth', 'get').mockReturnValue(clientWidth);
  return { scrollWidthSpy };
}

describe('computeFittedFontSize', () => {
  it('keeps the default font size when the word already fits on one line', () => {
    expect(
      computeFittedFontSize({ defaultFontSizePx: 48, naturalWidthPx: 400, containerWidthPx: 600 })
    ).toBeNull();
  });

  it('shrinks the font proportionally when the word is wider than the card', () => {
    // 48px * 300 / 900 = 16px
    expect(
      computeFittedFontSize({ defaultFontSizePx: 48, naturalWidthPx: 900, containerWidthPx: 300 })
    ).toBe(16);
  });

  it('never enlarges: the fitted size is always below the default', () => {
    const fitted = computeFittedFontSize({
      defaultFontSizePx: 48,
      naturalWidthPx: 901,
      containerWidthPx: 900
    });
    expect(fitted).not.toBeNull();
    expect(fitted as number).toBeLessThan(48);
  });

  it('returns null for unusable measurements', () => {
    expect(
      computeFittedFontSize({ defaultFontSizePx: Number.NaN, naturalWidthPx: 900, containerWidthPx: 300 })
    ).toBeNull();
    expect(
      computeFittedFontSize({ defaultFontSizePx: 48, naturalWidthPx: 0, containerWidthPx: 300 })
    ).toBeNull();
    expect(
      computeFittedFontSize({ defaultFontSizePx: 48, naturalWidthPx: 900, containerWidthPx: 0 })
    ).toBeNull();
  });
});

describe('WordHeadline', () => {
  afterEach(() => {
    vi.restoreAllMocks();
  });

  it('keeps the CSS default font size when the word fits on one line', () => {
    stubMeasurement({ fontSize: '48px', scrollWidth: 400, clientWidth: 600 });
    render(<WordHeadline word="cat" />);

    expect(screen.getByRole('heading', { level: 1, name: 'cat' }).style.fontSize).toBe('');
  });

  it('shrinks an over-wide word onto one line', () => {
    stubMeasurement({ fontSize: '48px', scrollWidth: 900, clientWidth: 300 });
    render(<WordHeadline word="industrialization" />);

    expect(screen.getByRole('heading', { level: 1, name: 'industrialization' }).style.fontSize).toBe('16px');
  });

  it('leaves the headline unstyled when layout measurement is unavailable (jsdom default)', () => {
    // No stubs: jsdom reports no computed px font size, so the fitter must
    // bail out instead of applying a broken size.
    render(<WordHeadline word="cat" />);

    expect(screen.getByRole('heading', { level: 1, name: 'cat' }).style.fontSize).toBe('');
  });

  it('re-fits when the window resizes', () => {
    const { scrollWidthSpy } = stubMeasurement({ fontSize: '48px', scrollWidth: 900, clientWidth: 300 });
    render(<WordHeadline word="industrialization" />);
    const headline = screen.getByRole('heading', { level: 1, name: 'industrialization' });
    expect(headline.style.fontSize).toBe('16px');

    // The card got wider (or the word shorter): natural width now 600px.
    scrollWidthSpy.mockReturnValue(600);
    window.dispatchEvent(new Event('resize'));

    // 48px * 300 / 600 = 24px
    expect(headline.style.fontSize).toBe('24px');
  });

  it('re-fits when the displayed word changes', () => {
    const { scrollWidthSpy } = stubMeasurement({ fontSize: '48px', scrollWidth: 900, clientWidth: 300 });
    const { rerender } = render(<WordHeadline word="industrialization" />);
    expect(screen.getByRole('heading', { level: 1, name: 'industrialization' }).style.fontSize).toBe('16px');

    scrollWidthSpy.mockReturnValue(300);
    rerender(<WordHeadline word="cat" />);

    // The short word fits at the default size, so the inline fit clears.
    expect(screen.getByRole('heading', { level: 1, name: 'cat' }).style.fontSize).toBe('');
  });
});
