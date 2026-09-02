import { useEffect, useRef, useState } from 'react';
import type { Pronunciation } from '../api';

type PronunciationPanelProps = {
  word: string;
  onLookupPronunciation: (word: string) => Promise<Pronunciation>;
  /**
   * "live"    — call the pronunciation API and render IPA / audio.
   * "placeholder" — render a static "Pronunciation · coming in v2" notice
   *                 without making any network requests. Used on the main
   *                 study card until the v2 audio pipeline is wired up.
   */
  mode?: 'live' | 'placeholder';
};

export function PronunciationPanel({
  word,
  onLookupPronunciation,
  mode = 'live'
}: PronunciationPanelProps) {
  const [pronunciation, setPronunciation] = useState<Pronunciation | null>(null);
  const [status, setStatus] = useState<'loading' | 'error' | 'ready'>('loading');
  const audioRef = useRef<HTMLAudioElement>(null);

  useEffect(() => {
    if (mode === 'placeholder') {
      return;
    }

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
  }, [word, onLookupPronunciation, mode]);

  if (mode === 'placeholder') {
    return (
      <p className="pronunciation-status pronunciation-placeholder" aria-live="polite">
        Pronunciation · coming in v2
      </p>
    );
  }

  if (status === 'loading') {
    return <p className="pronunciation-status" aria-live="polite">Loading American pronunciation…</p>;
  }

  if (status === 'error' || !pronunciation || pronunciation.status === 'unavailable') {
    return <p className="pronunciation-status" aria-live="polite">American pronunciation unavailable.</p>;
  }

  return (
    <div className="pronunciation-panel">
      {pronunciation.ipa ? <span className="pronunciation-ipa">{pronunciation.ipa}</span> : null}
      {pronunciation.audioUrl ? (
        <>
          <audio ref={audioRef} src={pronunciation.audioUrl} preload="none" />
          <button
            className="pronunciation-play"
            type="button"
            aria-label={`Play ${word} American pronunciation`}
            onClick={() => void audioRef.current?.play()}
          >
            Play
          </button>
        </>
      ) : <span className="pronunciation-status">No American recording.</span>}
      <details className="pronunciation-source">
        <summary>Source</summary>
        <p>
          <a href={pronunciation.sourceUrl} target="_blank" rel="noreferrer">Wiktionary</a>
          {pronunciation.audioSourceUrl ? <> · <a href={pronunciation.audioSourceUrl} target="_blank" rel="noreferrer">Wikimedia Commons</a></> : null}
          {pronunciation.attribution ? ` · ${pronunciation.attribution}` : ''}
          {pronunciation.license && pronunciation.licenseUrl ? <> · <a href={pronunciation.licenseUrl} target="_blank" rel="noreferrer">{pronunciation.license}</a></> : null}
        </p>
      </details>
    </div>
  );
}
