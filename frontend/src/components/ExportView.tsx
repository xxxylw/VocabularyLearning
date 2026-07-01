import { useState } from 'react';
import type { ExportResult } from '../api';

type ExportViewProps = {
  onExport: () => Promise<ExportResult>;
};

export function ExportView({ onExport }: ExportViewProps) {
  const [result, setResult] = useState<ExportResult | null>(null);
  const [isLoading, setIsLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleExport() {
    setIsLoading(true);
    setError(null);

    try {
      setResult(await onExport());
    } catch {
      setError('The export could not be prepared. Please try again.');
    } finally {
      setIsLoading(false);
    }
  }

  return (
    <section className="export-panel" aria-labelledby="export-title">
      <div>
        <p className="eyebrow">Anki</p>
        <h2 id="export-title">Full-book export</h2>
        <p>Prepare a deck with the English card content and the quieter Chinese note.</p>
      </div>
      <button className="secondary-action" type="button" onClick={() => void handleExport()} disabled={isLoading}>
        {isLoading ? 'Preparing export' : 'Export full book'}
      </button>
      {result?.status === 'ready' ? (
        <p className="export-result">
          Ready: <a href={result.downloadUrl}>{result.cardCount} cards</a>
        </p>
      ) : null}
      {result?.status === 'missing' ? (
        <p className="export-result">
          {result.preparedWords} of {result.totalWords} words are ready. {result.missingWords} still need card
          preparation.
        </p>
      ) : null}
      {error ? <p className="inline-error">{error}</p> : null}
    </section>
  );
}
