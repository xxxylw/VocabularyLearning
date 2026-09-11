import { useEffect, useRef, useState } from 'react';
import type { Pronunciation } from '../api';

type PronunciationPanelProps = {
  word: string;
  onLookupPronunciation: (word: string) => Promise<Pronunciation>;
  /**
   * P1 auto-play (task 7684163198957702092): when true, automatically play
   * the dictionary audio once as the card's front becomes ready. Callers
   * opt in per card (e.g. StudySession passes `!card.isRepeat`); the
   * spelling flow keeps the default so its prompt stage stays silent.
   */
  autoPlay?: boolean;
};

/**
 * Renders the real UK / US IPA for a word, e.g.
 *   /rɪˈzɪliənt/ UK · /rɪˈzɪlyənt/ US
 * When no real IPA data is available the panel renders nothing at all —
 * no placeholder text, no "coming soon" copy (PRD decision 1).
 */
function buildIpaFragments(pronunciation: Pronunciation): Array<{ ipa: string; label: string }> {
  const fragments: Array<{ ipa: string; label: string }> = [];
  if (pronunciation.ipaUk) {
    fragments.push({ ipa: pronunciation.ipaUk, label: 'UK' });
  }
  if (pronunciation.ipaUs) {
    fragments.push({ ipa: pronunciation.ipaUs, label: 'US' });
  }
  if (fragments.length === 0 && pronunciation.ipa) {
    // Wiktionary-backed rows only carry the American variant.
    fragments.push({ ipa: pronunciation.ipa, label: 'US' });
  }
  return fragments;
}

export function PronunciationPanel({
  word,
  onLookupPronunciation,
  autoPlay = false
}: PronunciationPanelProps) {
  const [pronunciation, setPronunciation] = useState<Pronunciation | null>(null);
  const [status, setStatus] = useState<'loading' | 'error' | 'ready'>('loading');
  const audioRef = useRef<HTMLAudioElement>(null);
  // Per-word guard: each word auto-plays at most once per panel instance, so
  // re-renders (Reveal / rating / Show more) never retrigger playback.
  const autoPlayedWordRef = useRef<string | null>(null);

  useEffect(() => {
    let isCurrent = true;
    setStatus('loading');
    setPronunciation(null);

    void onLookupPronunciation(word)
      .then((result) => {
        if (!isCurrent) return;
        setPronunciation(result);
        setStatus('ready');
      })
      .catch(() => {
        if (isCurrent) setStatus('error');
      });

    return () => {
      isCurrent = false;
      audioRef.current?.pause();
    };
  }, [word, onLookupPronunciation]);

  useEffect(() => {
    // P1 auto-play: fires once per word, only after the <audio> element has
    // actually rendered (i.e. data ready with an audioUrl). If the lookup
    // resolved late and the user already moved on, the isCurrent guard in
    // the lookup effect above kept `pronunciation` null, so this never
    // plays the wrong word. Any failure (no audio, autoplay blocked by the
    // browser, network) is silently skipped — no retry, no UI notice.
    if (!autoPlay || !pronunciation?.audioUrl) {
      return;
    }
    if (autoPlayedWordRef.current === word) {
      return;
    }
    autoPlayedWordRef.current = word;
    const playResult = audioRef.current?.play();
    if (playResult && typeof playResult.catch === 'function') {
      playResult.catch(() => {});
    }
  }, [autoPlay, pronunciation, word]);

  if (status !== 'ready' || !pronunciation || pronunciation.status === 'unavailable') {
    // Loading / error / no data: render nothing (no placeholder slot).
    return null;
  }

  const ipaFragments = buildIpaFragments(pronunciation);
  if (ipaFragments.length === 0) {
    return null;
  }

  return (
    <div className="pronunciation-panel" aria-label={`Pronunciation of ${word}`}>
      <span className="pronunciation-ipa">
        {ipaFragments.map((fragment, index) => (
          <span key={fragment.label} className="pronunciation-ipa-item">
            {index > 0 ? <span className="pronunciation-ipa-separator"> · </span> : null}
            {fragment.ipa} {fragment.label}
          </span>
        ))}
      </span>
      {pronunciation.audioUrl ? (
        <>
          <audio ref={audioRef} src={pronunciation.audioUrl} preload="none" />
          <button
            className="pronunciation-play"
            type="button"
            aria-label={`Play ${word} pronunciation`}
            onClick={() => void audioRef.current?.play()}
          >
            {/*
             * P1 (recvuarDc80JCa) · ghost speaker icon: the word stays the
             * card's only visual anchor. Icon color follows `currentColor`,
             * so the CSS rules in styles.css drive static / hover colors.
             */}
            <svg
              viewBox="0 0 24 24"
              width="13"
              height="13"
              aria-hidden="true"
              focusable="false"
              fill="currentColor"
            >
              <path d="M13.5 4.19a.75.75 0 0 0-1.22-.59L6.99 8.02a.5.5 0 0 1-.32.11H3.5a.5.5 0 0 0-.5.5v6.74a.5.5 0 0 0 .5.5h3.17a.5.5 0 0 1 .32.11l5.29 4.42a.75.75 0 0 0 1.22-.59V4.19Z" />
              <path d="M16.65 7.72a.75.75 0 0 1 1.06.03 6.98 6.98 0 0 1 0 8.5.75.75 0 1 1-1.15-.96 5.48 5.48 0 0 0 0-6.58.75.75 0 0 1 .09-1.06Z" />
              <path d="M19.4 5.03a.75.75 0 0 1 1.06.06 10.97 10.97 0 0 1 0 13.82.75.75 0 1 1-1.14-.97 9.47 9.47 0 0 0 0-11.88.75.75 0 0 1 .08-1.03Z" />
            </svg>
          </button>
        </>
      ) : null}
      <details className="pronunciation-source">
        <summary>Source</summary>
        <p>
          <a href={pronunciation.sourceUrl} target="_blank" rel="noreferrer">
            {pronunciation.sourceUrl.includes('oxfordlearnersdictionaries.com')
              ? 'Oxford Learner\u2019s Dictionaries'
              : 'Wiktionary'}
          </a>
          {pronunciation.audioSourceUrl ? (
            <>
              {' · '}
              <a href={pronunciation.audioSourceUrl} target="_blank" rel="noreferrer">
                Wikimedia Commons
              </a>
            </>
          ) : null}
          {pronunciation.attribution ? ` · ${pronunciation.attribution}` : ''}
          {pronunciation.license && pronunciation.licenseUrl ? (
            <>
              {' · '}
              <a href={pronunciation.licenseUrl} target="_blank" rel="noreferrer">
                {pronunciation.license}
              </a>
            </>
          ) : null}
        </p>
      </details>
    </div>
  );
}
