import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { computeFittedFontSize, WordHeadline } from './WordHeadline';

// Read the real stylesheet from disk so the CSS contract test guards the
// actual file the browser will ship, not a vite-transformed copy. Reading
// via `?raw` was unreliable here because the React plugin intercepts .css
// imports before the raw suffix can take effect.
const here = dirname(fileURLToPath(import.meta.url));
const stylesCss = readFileSync(resolve(here, '../styles.css'), 'utf-8');

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

// P0 fix · recvudlLx3voeu: descender clip regression guard.
// jsdom has no layout engine, so the visual clip and overlap that the user
// reported on "hydrogen" / "grammar" can only be verified end-to-end in a
// real browser (see the headless puppeteer repro in the repo's repro/
// folder — it screenshots hydrogen_before.png vs hydrogen_after.png).
// In jsdom we guard the root cause statically: the .word-headline CSS
// must reserve enough room for descenders AND keep the single-line
// measurement (scrollWidth / clientWidth) untouched.
describe('word-headline descender room (CSS contract)', () => {
  // Pull the body of the .word-headline rule from the actual stylesheet.
  // The block is between `^\.word-headline \{` and the next `}` at column 0.
  function wordHeadlineBlock(): string {
    const start = stylesCss.indexOf('.word-headline {');
    expect(start, 'expected a .word-headline rule in styles.css').toBeGreaterThan(-1);
    let i = stylesCss.indexOf('{', start);
    let depth = 1;
    i += 1;
    while (i < stylesCss.length && depth > 0) {
      const ch = stylesCss[i];
      if (ch === '{') depth += 1;
      else if (ch === '}') depth -= 1;
      i += 1;
    }
    const rawBlock = stylesCss.slice(start, i);
    // Strip CSS comments so explanatory prose in the stylesheet (e.g. the
    // P0 fix comment quoting the old `line-height: 1`) does not trip the
    // regression guards below.
    return rawBlock.replace(/\/\*[\s\S]*?\*\//g, '');
  }

  it('reserves enough vertical room for descenders (line-height > 1em)', () => {
    const block = wordHeadlineBlock();
    // line-height: 1 was the root cause: any font whose typographic
    // ascent+descent exceeds 1em (Segoe UI ≈1.33, system-ui on many
    // platforms ≥1.2) gets its descenders cut by `overflow: hidden`.
    // Lifting line-height to 1.2 keeps the line box tall enough that
    // the ink fits inside the padding box.
    expect(block).toMatch(/line-height:\s*1\.2/);
    expect(block).not.toMatch(/line-height:\s*1(?!\.)/);
  });

  it('adds symmetric padding so descender ink does not reach the pronunciation area', () => {
    const block = wordHeadlineBlock();
    // The em-based padding-block scales with the inline-shrunk font size
    // (WordHeadline applies the fit via `el.style.fontSize`), so the
    // descender room shrinks with the word on narrow viewports.
    expect(block).toMatch(/padding-block:\s*0\.1em/);
  });

  it('keeps the single-line guards intact (nowrap + overflow hidden)', () => {
    const block = wordHeadlineBlock();
    expect(block).toMatch(/white-space:\s*nowrap/);
    expect(block).toMatch(/overflow:\s*hidden/);
    // overflow-wrap:normal is part of the same guard (stops long words
    // from breaking arbitrarily when whitespace is the only opportunity).
    expect(block).toMatch(/overflow-wrap:\s*normal/);
  });

  it('vertical padding does not affect horizontal width measurement', () => {
    // This is the core invariant that lets the WordHeadline fitter keep
    // working: clientWidth includes horizontal padding only, scrollWidth
    // is the content's overflow, and neither is influenced by
    // padding-top / padding-bottom. The component tests above already
    // exercise the fitter against stubbed measurements; this assertion
    // documents the invariant in CSS terms so a future refactor that
    // changes the padding to something horizontal (e.g. `padding: 0.1em`)
    // will fail loudly.
    const block = wordHeadlineBlock();
    expect(block).toMatch(/padding-block:/);
    expect(block).not.toMatch(/padding:\s*0/);
    expect(block).not.toMatch(/padding-inline:/);
  });
});

