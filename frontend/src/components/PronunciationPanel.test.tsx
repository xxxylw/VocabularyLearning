import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi, afterEach } from 'vitest';
import { readFileSync } from 'node:fs';
import { PronunciationPanel } from './PronunciationPanel';

let cssCache: string | null = null;

/** Source text of the app stylesheet — the design tokens under test live here.
 * Read lazily and through a variable because Vite statically rewrites
 * `new URL('<literal>', import.meta.url)` into an asset URL import, which
 * would not be a readable file:// path at test time. */
function stylesheet(): string {
  if (cssCache === null) {
    const rel = '../styles.css';
    cssCache = readFileSync(new URL(rel, import.meta.url), 'utf-8');
  }
  return cssCache;
}

function readyPronunciation(overrides: Record<string, unknown> = {}) {
  return {
    word: 'atmosphere',
    ipa: null,
    ipaUk: '/ˈætməsfɪə(r)/',
    ipaUs: '/ˈætməsfɪr/',
    audioUrl: 'https://upload.wikimedia.org/atmosphere.ogg',
    sourceUrl: 'https://en.wiktionary.org/wiki/atmosphere',
    audioSourceUrl: null,
    attribution: null,
    license: null,
    licenseUrl: null,
    status: 'ready',
    ...overrides
  };
}

/** Collapses a CSS rule into one flat declaration string, e.g.
 *  `.pronunciation-play { a: 1; b: 2 }` → `"a: 1; b: 2"`. */
function ruleBody(selector: string): string {
  const escaped = selector.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const match = stylesheet().replace(/\s+/g, ' ').match(new RegExp(`${escaped}\\s*\\{([^}]*)\\}`));
  if (!match) {
    throw new Error(`CSS rule not found: ${selector}`);
  }
  return match[1].trim();
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('PronunciationPanel play button (P1 ghost speaker icon)', () => {
  it('renders the ghost button with the accessible name and an SVG speaker icon instead of the Play text', async () => {
    render(
      <PronunciationPanel word="atmosphere" onLookupPronunciation={vi.fn().mockResolvedValue(readyPronunciation())} />
    );

    // aria-label semantics survive the visual downgrade.
    const playButton = await screen.findByRole('button', { name: 'Play atmosphere pronunciation' });
    // The button body is now the inline speaker SVG only — no visible Play text.
    expect(playButton.textContent).toBe('');
    expect(playButton.querySelector('svg')).toBeInTheDocument();
  });

  it('plays the pronunciation audio when the ghost button is clicked', async () => {
    const play = vi.fn().mockResolvedValue(undefined);
    vi.spyOn(HTMLMediaElement.prototype, 'play').mockImplementation(play);
    const user = userEvent.setup();

    render(
      <PronunciationPanel word="atmosphere" onLookupPronunciation={vi.fn().mockResolvedValue(readyPronunciation())} />
    );

    await user.click(await screen.findByRole('button', { name: 'Play atmosphere pronunciation' }));

    await waitFor(() => expect(play).toHaveBeenCalledTimes(1));
  });

  it('renders no play button at all when there is no audio URL', async () => {
    const lookup = vi.fn().mockResolvedValue(readyPronunciation({ audioUrl: null }));

    render(<PronunciationPanel word="atmosphere" onLookupPronunciation={lookup} />);

    await waitFor(() => expect(lookup).toHaveBeenCalledTimes(1));
    expect(await screen.findByText('/ˈætməsfɪə(r)/ UK')).toBeInTheDocument();
    expect(screen.queryByRole('button', { name: /play/i })).not.toBeInTheDocument();
  });

  it('styles the static state as a 28px outlined circle with a quiet gray-blue icon (design spec A)', () => {
    const rule = ruleBody('.pronunciation-play');

    expect(rule).toContain('width: 28px');
    expect(rule).toContain('min-height: 28px');
    expect(rule).toContain('border-radius: 50%');
    expect(rule).toContain('background: transparent');
    expect(rule).toContain('rgba(72, 111, 131, 0.30)');
    expect(rule).toContain('color: #6b7d86');
    // The solid blue pill is gone.
    expect(rule).not.toContain('#486f83;');
  });

  it('recovers discoverability on hover with a soft tint, deeper outline and blue icon (140ms)', () => {
    const rule = ruleBody('.pronunciation-play:hover');

    expect(rule).toContain('background: rgba(72, 111, 131, 0.08)');
    expect(rule).toContain('rgba(72, 111, 131, 0.55)');
    expect(rule).toContain('color: #486f83');
    expect(ruleBody('.pronunciation-play')).toContain('transition: background 140ms ease, border-color 140ms ease, color 140ms ease');
  });

  it('keeps a clearly visible keyboard focus ring on :focus-visible', () => {
    const rule = ruleBody('.pronunciation-play:focus-visible');

    expect(rule).toContain('outline: 2px solid #486f83');
    expect(rule).toContain('outline-offset: 2px');
  });
});
