import { useState } from 'react';
import type { StudyCard } from '../api';
import type { Pronunciation } from '../api';
import { PronunciationPanel } from './PronunciationPanel';
import { buildSpellingHint } from './spellingHint';

type SpellingSessionProps = {
  cards: StudyCard[];
  onExit: () => void;
  onLookupPronunciation?: (word: string) => Promise<Pronunciation>;
};

// Bug 7684167107172388020（2026-09-11 用户反馈「拼写右上角 57/55」）：
// 拼写页进度不再锚定当日队列（dayProgress / PRD ch.8 旧口径）——
// 当日重复池上线后背完 55 卡进拼写，startIndex(55) 起步，进度从
// 56/55 起算、恒 >100%。产品口径修正（用户拍板）：拼写页进度 =
// 拼写会话自身进度（第 X / 共 N，N = 本次拼写卡组数），首轮与
// retry round 统一口径。

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

// 逐词判定结果：correct = 首答答对；second = 重试后答对（均计为正确，
// 不进错词列表，用于完成态 SP-09 补充行）；wrong = 连续答错两次。
type WordOutcome = 'correct' | 'second' | 'wrong';

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
  onLookupPronunciation
}: SpellingSessionProps) {
  // 「错词再来一组」在会话内部重启：sessionCards 仅在首轮取 props.cards，
  // 之后由 retry round 接管。
  const [sessionCards, setSessionCards] = useState<StudyCard[]>(() => cards);
  const [currentIndex, setCurrentIndex] = useState(0);
  const [answer, setAnswer] = useState('');
  const [result, setResult] = useState<ResultState>('idle');
  const [outcomes, setOutcomes] = useState<WordOutcome[]>([]);
  // Apple 风微效果（设计规格 2026-09-11）：shake 仅在首答错误时触发一次，
  // animationend 后移除；entering 在换卡时加 240ms 入场过渡。
  const [shake, setShake] = useState(false);
  const [entering, setEntering] = useState(true);

  const card = sessionCards[currentIndex];
  // 会话内本地计数：X = 已拼到第几张（1 起），N = 本次拼写卡组数；
  // 不再锚定 dayProgress，首轮与 retry round 同一口径。
  const completedCount = Math.min(currentIndex, sessionCards.length);
  const denominator = sessionCards.length;
  const currentPosition = currentIndex + 1;
  const completionPercent = denominator === 0 ? 0 : (completedCount / denominator) * 100;
  const isComplete = sessionCards.length === 0 || currentIndex >= sessionCards.length;

  const correctCount = outcomes.filter((outcome) => outcome === 'correct' || outcome === 'second').length;
  const secondTryCorrectCount = outcomes.filter((outcome) => outcome === 'second').length;
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
      recordOutcome(result === 'retry' ? 'second' : 'correct');
      setResult('correct');
      return;
    }

    if (result === 'idle') {
      // 第一次答错：不揭示答案，进入重试态（保留输入），并触发一次 shake。
      setResult('retry');
      setShake(true);
      window.setTimeout(() => setShake(false), 400);
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
    setEntering(true);
  }

  function handleRetryWrongWords() {
    if (wrongCards.length === 0) {
      return;
    }
    setSessionCards(wrongCards);
    setCurrentIndex(0);
    setAnswer('');
    setResult('idle');
    setOutcomes([]);
    setEntering(true);
  }

  if (isComplete || !card) {
    return (
      <main className="spelling-shell completion-state" aria-label="Spelling complete">
        <section className="completion-panel" aria-labelledby="spelling-complete-title">
          <p className="eyebrow">Spelling complete</p>
          <h1 id="spelling-complete-title">Practice complete</h1>
          <p className="spelling-summary-line" data-testid="spelling-summary">
            {sessionCards.length} words · {correctCount} correct · {wrongOutcomes} missed
          </p>
          {wrongCards.length === 0 ? (
            <p className="spelling-summary-note" data-testid="spelling-summary-note">
              Every word correct on the first or second try.
            </p>
          ) : null}
          {wrongCards.length > 0 && secondTryCorrectCount > 0 ? (
            <p className="spelling-summary-note" data-testid="spelling-summary-note">
              {secondTryCorrectCount} correct on the second try.
            </p>
          ) : null}
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
                Practice missed words
              </button>
            ) : null}
            <button
              className={wrongCards.length > 0 ? 'ghost-button' : 'primary-action'}
              type="button"
              onClick={onExit}
              data-testid="spelling-back-today"
            >
              Back to Today
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

      <section
        className={`spelling-card${entering ? ' entering' : ''}`}
        aria-labelledby="spelling-title"
        onAnimationEnd={() => setEntering(false)}
      >
        <div className="spelling-prompt">
          <p className="eyebrow">Definition</p>
          {/* DP-B1/F-08：主提示为安全首句、h2 级字号，不再用 h1。 */}
          <h2 id="spelling-title">{prompt}</h2>
          <p className="spelling-pos">{card.partOfSpeech}{card.senseLabel ? ` · ${card.senseLabel}` : ''}</p>
        </div>

        <div className="spelling-answer-panel">
          <label htmlFor="spelling-answer">Type the word</label>
          <input
            id="spelling-answer"
            className={`spelling-input${shake ? ' shake' : ''}`}
            onAnimationEnd={() => setShake(false)}
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
              {isResolved ? 'Next word' : 'Check'}
            </button>
          </div>

          {result === 'retry' ? (
            <p className="spelling-feedback incorrect" data-testid="spelling-retry-hint">
              Try again
            </p>
          ) : null}
          {isCorrect ? <p className="spelling-feedback correct">✓ Correct.</p> : null}
          {isRevealed ? (
            <p className="spelling-feedback incorrect">✗ Not quite. The correct answer is below.</p>
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
          <p className="completed-text">{completedCount} / {denominator} completed</p>
        </div>
      </section>
    </main>
  );
}
