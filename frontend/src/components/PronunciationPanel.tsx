import { useEffect, useRef, useState } from 'react';
import type { Pronunciation } from '../api';

type PronunciationPanelProps = {
  word: string;
  onLookupPronunciation: (word: string) => Promise<Pronunciation>;
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
  onLookupPronunciation
}: PronunciationPanelProps) {
  const [pronunciation, setPronunciation] = useState<Pronunciation | null>(null);
  const [status, setStatus] = useState<'loading' | 'error' | 'ready'>('loading');
  const audioRef = useRef<HTMLAudioElement>(null);

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
            Play
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
