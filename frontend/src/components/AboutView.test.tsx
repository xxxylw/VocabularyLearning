import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it } from 'vitest';
import { AboutView } from './AboutView';
import { isPublicRoute } from '../router';

// v3 open-dictionary switch (2026-09-18): the About & Data Sources page is
// the compliance attribution surface — it must credit every open-licensed
// source with its exact license link, disclose the audio provenance
// (Wiktionary/Commons recordings + synthesized speech), stay public for
// guests, and route back through the hash router.

describe('AboutView', () => {
  it('credits every open-licensed data source with project and license links', () => {
    render(<AboutView backPath="/today" backLabel="Back to Today" />);

    for (const name of [
      'English Wiktionary',
      'Open English WordNet 2024',
      'ECDICT (skywind3000)',
      'CMU Pronouncing Dictionary'
    ]) {
      expect(screen.getByText(name)).toBeTruthy();
    }

    expect(screen.getByText('CC BY-SA 4.0').getAttribute('href')).toBe(
      'https://creativecommons.org/licenses/by-sa/4.0/'
    );
    expect(screen.getByText('CC BY 4.0').getAttribute('href')).toBe(
      'https://creativecommons.org/licenses/by/4.0/'
    );
    expect(screen.getByText('MIT License').getAttribute('href')).toBe(
      'https://opensource.org/licenses/MIT'
    );
    expect(screen.getByText('BSD-style license').getAttribute('href')).toBe(
      'http://www.speech.cs.cmu.edu/cgi-bin/cmudict'
    );
  });

  it('discloses the pronunciation-audio provenance', () => {
    render(<AboutView backPath="/today" backLabel="Back to Today" />);

    expect(screen.getByText(/Wikimedia Commons/)).toBeTruthy();
    expect(screen.getByText(/synthesized speech \(Microsoft Edge TTS/)).toBeTruthy();
  });

  it('navigates back through the hash router', async () => {
    window.location.hash = '#/about';
    render(<AboutView backPath="/today" backLabel="Back to Today" />);

    await userEvent.click(screen.getByRole('button', { name: 'Back to Today' }));

    expect(window.location.hash).toBe('#/today');
  });

  it('keeps /about public in the route predicate', () => {
    expect(isPublicRoute('/about')).toBe(true);
    expect(isPublicRoute('/login')).toBe(true);
    expect(isPublicRoute('/today')).toBe(false);
  });
});
