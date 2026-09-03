import { useLayoutEffect, useRef } from 'react';

// PRD ch.12 (P1) 单词卡词面单行展示: the study-card front word face must
// always render on ONE line — no wrapping, no truncation, no horizontal
// scrolling. The default responsive font size from styles.css stays first
// choice; only when the word's natural single-line width exceeds the card's
// content box do we shrink the font size proportionally so it fits. The fit
// is measured against real rendered widths and re-run on window resize and
// whenever the displayed word changes (PRD interaction rules 2 and 3).
//
// Readability floor decision: no hard minimum is enforced — the single-line
// rule wins down to the narrowest viewport (PRD edge-case rule). In practice
// the longest built-in book word ("industrialization", 17 chars) still lands
// around ~25px at a 320px-wide viewport, which stays comfortably readable.

type WordHeadlineProps = {
  word: string;
};

function roundPx(px: number): number {
  return Math.round(px * 100) / 100;
}

/**
 * Pure scaling math, kept DOM-free so it can be unit-tested directly.
 * Returns the fitted font size in px, or null when the default font size
 * already fits on one line (or the measurements are unusable). The result
 * is always smaller than the default — short words are never enlarged.
 */
export function computeFittedFontSize(params: {
  defaultFontSizePx: number;
  naturalWidthPx: number;
  containerWidthPx: number;
}): number | null {
  const { defaultFontSizePx, naturalWidthPx, containerWidthPx } = params;
  if (
    !Number.isFinite(defaultFontSizePx) ||
    defaultFontSizePx <= 0 ||
    !Number.isFinite(naturalWidthPx) ||
    naturalWidthPx <= 0 ||
    !Number.isFinite(containerWidthPx) ||
    containerWidthPx <= 0
  ) {
    return null;
  }
  if (naturalWidthPx <= containerWidthPx) {
    return null;
  }
  return roundPx((defaultFontSizePx * containerWidthPx) / naturalWidthPx);
}

export function WordHeadline({ word }: WordHeadlineProps) {
  const headlineRef = useRef<HTMLHeadingElement>(null);

  useLayoutEffect(() => {
    const fit = () => {
      const el = headlineRef.current;
      if (!el) {
        return;
      }

      // Drop any previous fit first so the measured default font size and
      // the natural width always reflect the CSS default (clamp(...)),
      // never a previously shrunk inline value.
      el.style.fontSize = '';

      // styles.css keeps the headline on one line (white-space: nowrap), so
      // scrollWidth is the word's natural single-line width at the default
      // size while clientWidth is the available content-box width.
      const fitted = computeFittedFontSize({
        defaultFontSizePx: parseFloat(window.getComputedStyle(el).fontSize),
        naturalWidthPx: el.scrollWidth,
        containerWidthPx: el.clientWidth
      });

      el.style.fontSize = fitted === null ? '' : `${fitted}px`;
    };

    fit();

    // PRD interaction rule 3: keep the word on one line as the window (and
    // therefore the card) resizes.
    window.addEventListener('resize', fit);
    // A late font swap changes text metrics; re-fit once fonts settle.
    document.fonts?.ready?.then(fit).catch(() => undefined);

    return () => {
      window.removeEventListener('resize', fit);
    };
  }, [word]);

  return (
    <h1 id="study-word" className="word-headline" ref={headlineRef}>
      {word}
    </h1>
  );
}
