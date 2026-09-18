import { navigate } from '../router';

// v3 open-dictionary switch (2026-09-18): attribution page for the
// open-licensed dictionary stack (task compliance requirement, ships
// with the dictionary cutover). Public — reachable by guests — and
// all copy in English per the user's standing rule. The exact source
// list mirrors the `sources` rows the migration wrote into the
// database (English Wiktionary via kaikki.org/wiktextract, Open
// English WordNet 2024, ECDICT, CMU Pronouncing Dictionary, plus
// Wiktionary/Commons recordings and edge-tts synthesized audio).

type AboutViewProps = {
  // Where the "back" link goes: /today when signed in, /login for guests.
  backPath: string;
  backLabel: string;
};

type DataSource = {
  name: string;
  role: string;
  projectLabel: string;
  projectUrl: string;
  license: string;
  licenseUrl: string;
};

const SOURCES: DataSource[] = [
  {
    name: 'English Wiktionary',
    role: 'Definitions, example sentences, and IPA transcriptions (data extracted via kaikki.org / wiktextract).',
    projectLabel: 'en.wiktionary.org',
    projectUrl: 'https://en.wiktionary.org/',
    license: 'CC BY-SA 4.0',
    licenseUrl: 'https://creativecommons.org/licenses/by-sa/4.0/'
  },
  {
    name: 'Open English WordNet 2024',
    role: 'Definitions and example sentences.',
    projectLabel: 'Global WordNet — English WordNet',
    projectUrl: 'https://github.com/globalwordnet/english-wordnet',
    license: 'CC BY 4.0',
    licenseUrl: 'https://creativecommons.org/licenses/by/4.0/'
  },
  {
    name: 'ECDICT (skywind3000)',
    role: 'Chinese translations and word metadata.',
    projectLabel: 'github.com/skywind3000/ECDICT',
    projectUrl: 'https://github.com/skywind3000/ECDICT',
    license: 'MIT License',
    licenseUrl: 'https://opensource.org/licenses/MIT'
  },
  {
    name: 'CMU Pronouncing Dictionary',
    role: 'Pronunciation data.',
    projectLabel: 'speech.cs.cmu.edu/cmudict',
    projectUrl: 'http://www.speech.cs.cmu.edu/cgi-bin/cmudict',
    license: 'BSD-style license',
    licenseUrl: 'http://www.speech.cs.cmu.edu/cgi-bin/cmudict'
  }
];

export function AboutView({ backPath, backLabel }: AboutViewProps) {
  return (
    <main className="auth-page" data-testid="about-page">
      <section className="auth-card about-card">
        <p className="eyebrow">VOCABULARYLEARNING</p>
        <h1 className="auth-title">About &amp; Data Sources</h1>
        <p className="auth-subtitle">
          The dictionary in this app is built from open-licensed sources. This page credits each
          one and links its license.
        </p>
        <ul className="about-sources">
          {SOURCES.map((source) => (
            <li className="about-source" key={source.name}>
              <p className="about-source-name">{source.name}</p>
              <p className="about-source-role">{source.role}</p>
              <p className="about-source-license">
                <a href={source.projectUrl} target="_blank" rel="noreferrer">
                  {source.projectLabel}
                </a>
                <span aria-hidden="true"> · </span>
                <a href={source.licenseUrl} target="_blank" rel="noreferrer">
                  {source.license}
                </a>
              </p>
            </li>
          ))}
        </ul>
        <p className="about-note">
          Pronunciation audio includes human recordings from Wiktionary / Wikimedia Commons and,
          for words without a recording, synthesized speech (Microsoft Edge TTS, voice
          en-US-AriaNeural).
        </p>
        <p className="about-note">
          Licensed content remains under its respective license — see each license link above
          for the exact terms. Reuse of the Wiktionary-derived content must follow the Creative
          Commons Attribution-ShareAlike 4.0 license.
        </p>
        <button type="button" className="auth-text-link about-back" onClick={() => navigate(backPath)}>
          {backLabel}
        </button>
      </section>
    </main>
  );
}
