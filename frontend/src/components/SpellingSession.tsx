import { useState } from 'react';
import type { StudyCard } from '../api';
import type { Pronunciation } from '../api';
import { PronunciationPanel } from './PronunciationPanel';
import { buildSpellingHint } from './spellingHint';

type SpellingSessionProps = {
  cards: StudyCard[];
  onExit: () => void;
  onLookupPronunciation?: (word: string) => Promise<Pronunciation>;
  // PRD ch.8: day-level progress anchors — same queue snapshot as card
  // mode. startIndex offsets by cards already reviewed on the study date;
  // totalCount is the day queue's size. Both fall back to session-local
  // values when absent. 错词再来一组的一轮（retry round）改用轮内本地计数。
  startIndex?: number;
  totalCount?: number;
};

// 拼写界面重设计（2026-09-08 规格需求 B，DP-B2 重试一次口径）：
//   idle（出题）→ checked（correct | incorrect-first）
//   → retry（保留输入、提示「再试一次」，可修改后再提交）
//   → checked（correct | incorrect-final 揭示答案）→ next → idle … → complete
// 状态语义：
//   - idle     出题，等待首次提交
//   - retry    第一次答错：不揭示答案，保留用户输入
//   - correct  答对（含重试后答对，均计为正确）
//   - revealed 连续答错两次：已揭示正确答案 + 完整释义
type ResultState = 'idle' | 'retry' | 'correct' | 'revealed';

// 逐词判定结果：correct = 首答或重试后答对（不进错词列表）；
// wrong = 连续答错两次。
type WordOutcome = 'correct' | 'wrong';

function normalizeAnswer(value: string): string {
  return value
    .replace(/[‘’]/g, "'")
    .trim()
    .toLowerCase()
    .replace(/\s+/g, ' ');
}

export function SpellingSession({
  cards,
  onExit,
  onLookupPronunciation,
  startIndex = 0,
  totalCount
}: SpellingSessionProps) {
  // 「错词再来一组」在会话内部重启：sessionCards 仅在首轮取 props.cards，
  // 之后由 retry round 接管。
  const [sessionCards, setSessionCards] = useState<StudyCard[]>(() => cards);
  const [isRetryRound, setIsRetryRound] = useState(false);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [answer, setAnswer] = useState('');
  const [result, setResult] = useState<ResultState>('idle');
  const [outcomes, setOutcomes] = useState<WordOutcome[]>([]);

  const card = sessionCards[currentIndex];
  const completedCount = Math.min(currentIndex, sessionCards.length);
  const denominator = isRetryRound ? sessionCards.length : (totalCount ?? sessionCards.length);
  // 首轮沿用当日队列锚点（PRD ch.8）；错词一轮用轮内本地计数。
  const dayCompletedCount = isRetryRound ? completedCount : startIndex + completedCount;
  const currentPosition = isRetryRound ? currentIndex + 1 : startIndex + currentIndex + 1;
  const completionPercent = denominator === 0 ? 0 : (dayCompletedCount / denominator) * 100;
  const isComplete = sessionCards.length === 0 || currentIndex >= sessionCards.length;

  const correctCount = outcomes.filter((outcome) => outcome === 'correct').length;
  const wrongOutcomes = outcomes.filter((outcome) => outcome === 'wrong').length;
  const wrongCards = sessionCards.filter((_, index) => outcomes[index] === 'wrong');

  function recordOutcome(outcome: WordOutcome) {
    setOutcomes((previous) => {
      const next = [...previous];
      next[currentIndex] = outcome;
      return next;
    });
  }

  function handleCheck() {
    if (!card) {
      return;
    }

    const isNowCorrect = normalizeAnswer(answer) === normalizeAnswer(card.word);

    if (isNowCorrect) {
      // 重试后答对同样计为正确（DP-B2），不进错词列表。
      recordOutcome('correct');
      setResult('correct');
      return;
    }

    if (result === 'idle') {
      // 第一次答错：不揭示答案，进入重试态（保留输入）。
      setResult('retry');
      return;
    }

    // 第二次仍错：揭示正确答案 + 完整释义。
    recordOutcome('wrong');
    setResult('revealed');
  }

  function handleNext() {
    setCurrentIndex((index) => index + 1);
    setAnswer('');
    setResult('idle');
  }

  function handleRetryWrongWords() {
    if (wrongCards.length === 0) {
      return;
    }
    setSessionCards(wrongCards);
    setIsRetryRound(true);
    setCurrentIndex(0);
    setAnswer('');
    setResult('idle');
    setOutcomes([]);
  }

  if (isComplete || !card) {
    return (
      <main className="spelling-shell completion-state" aria-label="Spelling complete">
        <section className="completion-panel" aria-labelledby="spelling-complete-title">
          <p className="eyebrow">Spelling complete</p>
          <h1 id="spelling-complete-title">本组拼写完成</h1>
          <p className="spelling-summary-line" data-testid="spelling-summary">
            {sessionCards.length} 词 · 对 {correctCount} · 错 {wrongOutcomes}
          </p>
          {wrongCards.length > 0 ? (
            <ul className="spelling-wrong-list" data-testid="spelling-wrong-list">
              {wrongCards.map((wrongCard) => (
                <li key={wrongCard.cardId}>
                  <strong>{wrongCard.word}</strong>
                  <span> — {buildSpellingHint(wrongCard)}</span>
                </li>
              ))}
            </ul>
          ) : null}
          <div className="completion-actions">
            {wrongCards.length > 0 ? (
              <button
                className="primary-action"
                type="button"
                onClick={handleRetryWrongWords}
                data-testid="spelling-retry-wrong"
              >
                错词再来一组
              </button>
            ) : null}
            <button
              className={wrongCards.length > 0 ? 'ghost-button' : 'primary-action'}
              type="button"
              onClick={onExit}
              data-testid="spelling-back-today"
            >
              返回 Today
            </button>
          </div>
        </section>
      </main>
    );
  }

  const prompt = buildSpellingHint(card);
  const isCorrect = result === 'correct';
  const isRevealed = result === 'revealed';
  const isResolved = isCorrect || isRevealed;
  const fullDefinition =
    [card.definition, card.chineseNote].filter((part) => typeof part === 'string' && part.length > 0).join(' · ') ||
    '';

  return (
    <main className="spelling-shell" aria-label="Spelling practice">
      <header className="study-topbar">
        <button className="ghost-button" type="button" onClick={onExit}>
          Exit
        </button>
        <div className="study-progress">
          <div className="progress-text">
            {currentPosition} / {denominator}
          </div>
          <div className="progress-bar" role="presentation">
            <div className="progress-bar-fill" style={{ width: `${completionPercent}%` }} />
          </div>
        </div>
      </header>

      <section className="spelling-card" aria-labelledby="spelling-title">
        <div className="spelling-prompt">
          <p className="eyebrow">Definition prompt</p>
          {/* DP-B1/F-08：主提示为安全首句、h2 级字号，不再用 h1。 */}
          <h2 id="spelling-title">{prompt}</h2>
          <p className="spelling-pos">{card.partOfSpeech}{card.senseLabel ? ` · ${card.senseLabel}` : ''}</p>
        </div>

        <div className="spelling-answer-panel">
          <label htmlFor="spelling-answer">Type the English word</label>
          <input
            id="spelling-answer"
            className="spelling-input"
            value={answer}
            onChange={(event) => {
              setAnswer(event.target.value);
              // 重试态下编辑输入不重置状态——否则编辑即可绕过「重试一次」。
            }}
            onKeyDown={(event) => {
              if (event.key === 'Enter') {
                event.preventDefault();
                if (isResolved) {
                  handleNext();
                } else {
                  handleCheck();
                }
              }
            }}
            autoFocus
          />

          <div className="spelling-actions">
            <button
              className="primary-action"
              type="button"
              onClick={isResolved ? handleNext : handleCheck}
            >
              {isResolved ? '下一词' : 'Check'}
            </button>
          </div>

          {result === 'retry' ? (
            <p className="spelling-feedback incorrect" data-testid="spelling-retry-hint">
              再试一次
            </p>
          ) : null}
          {isCorrect ? <p className="spelling-feedback correct">✓ Correct.</p> : null}
          {isRevealed ? (
            <p className="spelling-feedback incorrect">✗ 正确答案见下方</p>
          ) : null}

          {isCorrect ? <p className="spelling-answer" data-testid="spelling-word">{card.word}</p> : null}
          {isRevealed ? (
            <p className="spelling-answer" data-testid="spelling-word">Answer: {card.word}</p>
          ) : null}

          {isResolved && fullDefinition ? (
            <p className="spelling-full-definition" data-testid="spelling-full-definition">
              {fullDefinition}
            </p>
          ) : null}

          {isResolved && onLookupPronunciation ? (
            <PronunciationPanel word={card.word} onLookupPronunciation={onLookupPronunciation} />
          ) : null}
          <p className="completed-text">{dayCompletedCount} / {denominator} completed</p>
        </div>
      </section>
    </main>
  );
}
