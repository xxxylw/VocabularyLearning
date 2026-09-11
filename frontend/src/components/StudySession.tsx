import { useCallback, useEffect, useRef, useState } from 'react';
import type { StudyCard, DefinitionSource } from '../api';
import type { OxfordLookupResult } from '../api';
import type { ReviewRating } from '../api';
import type { Pronunciation } from '../api';
import { PronunciationPanel } from './PronunciationPanel';
import { WordHeadline } from './WordHeadline';

// 当日重复池（task 7684082688076025051，规格规则 2 / D2 拍板）：评 New
// 的卡从当前位置后移 3 张重新出现（剩余不足 3 张时落队尾）。
const REPEAT_INSERT_INTERVAL = 3;

type StudySessionProps = {
  cards: StudyCard[];
  onReview: (card: StudyCard, rating: ReviewRating) => Promise<unknown> | unknown;
  onExit: () => void;
  onLookupWord?: (word: string) => Promise<OxfordLookupResult>;
  onLookupPronunciation?: (word: string) => Promise<Pronunciation>;
  onComplete?: (cards: StudyCard[]) => void;
  onPracticeSpelling?: (cards: StudyCard[]) => void;
  onReviewDueWords?: (cards: StudyCard[]) => void;
  // PRD ch.8: day-level progress anchors. totalCards is the day queue's
  // size (denominator); reviewedCards counts queue entries already
  // reviewed before this session started (numerator offset). Both fall
  // back to session-local values when absent (e.g. ad-hoc re-review).
  totalCards?: number;
  reviewedCards?: number;
};

const ratingLabels: Array<{ rating: ReviewRating; label: string; shortcut: string }> = [
  { rating: 'known', label: 'Got it', shortcut: '1' },
  { rating: 'uncertain', label: 'Maybe', shortcut: '2' },
  { rating: 'unknown', label: 'New', shortcut: '3' }
];

const VISIBLE_SENSE_COUNT = 3;
const DEGRADED_SOURCES: ReadonlySet<DefinitionSource> = new Set(['fallback']);

function isDistinctSenseLabel(senseLabel: string, definition: string): boolean {
  return senseLabel.trim().toLowerCase() !== definition.trim().toLowerCase();
}

function isDegradedSource(source: DefinitionSource | undefined): boolean {
  return source !== undefined && DEGRADED_SOURCES.has(source);
}

type LookupState =
  | { status: 'idle' }
  | { status: 'loading'; word: string }
  | { status: 'ready'; result: OxfordLookupResult }
  | { status: 'error'; word: string; message: string };

export function StudySession({
  cards,
  onReview,
  onExit,
  onLookupWord,
  onLookupPronunciation,
  onComplete,
  onPracticeSpelling,
  onReviewDueWords,
  totalCards,
  reviewedCards = 0
}: StudySessionProps) {
  const [isRevealed, setIsRevealed] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lookupState, setLookupState] = useState<LookupState>({ status: 'idle' });
  const [showAllDefinitions, setShowAllDefinitions] = useState(false);
  const submittingRef = useRef(false);
  const completedRef = useRef(false);
  // 当日重复池（task 7684082688076025051）：会话内工作流。queue 是
  // 「剩余待展示卡」—— 普通队列卡消费后 splice 移除、评 unknown 的
  // new 卡 / 仍 pending 的重复卡在 3 张之后以 isRepeat 副本重新插回
  // （规格规则 2：间隔 3 张）。服务端 today_repeat_pool 是持久权威，
  // 这里的本地重插只保证同一会话内即时可见，重进由服务端确定性重算。
  const [queue, setQueue] = useState<StudyCard[]>(cards);
  // 规格规则 6：进度分子只计已完成卡 —— Got it / Maybe 的队列卡、
  // 池内已 Got it（cleared）与达上限移出（capped）的卡；评 New 未清空
  // 的卡不计入（停滞、不回退，清空 / 达上限时 +1）。复习卡评 New 不进
  // 池（D1），照常计入。
  const [repeatCompleted, setRepeatCompleted] = useState(0);
  // 已展示的原始队列卡张数：currentPosition 的兜底计数。重复卡
  // isRepeat 副本不计入——副本是同一张卡的重现，若每次重现都推进
  // 游标，位置显示会在当日重复场景超过 day queue 分母（6/5、7/5）。
  const [shownCount, setShownCount] = useState(0);

  useEffect(() => {
    // cards prop 只在服务端重新拉会话时换引用（重进 Today / 再来一组 /
    // ad-hoc 复习），同步工作流并复位会话内进度。
    setQueue(cards);
    setRepeatCompleted(0);
    setShownCount(0);
    completedRef.current = false;
  }, [cards]);

  const card = queue[0];
  // PRD ch.8: the progress bar is anchored to the day queue, not the
  // in-session list, so it never resets after re-entering Today.
  const denominator = totalCards ?? cards.length;
  const dayCompletedCount = reviewedCards + repeatCompleted;
  const completionPercent =
    denominator === 0 ? 0 : (dayCompletedCount / denominator) * 100;
  // 位置游标口径：重复卡 isRepeat 副本是同一张卡的重现，不推进
  // shownCount（服务端 flow 计算同口径——池卡不算展示消耗）。副本
  // 显示最近一张原始卡的位置（下限 1），原始卡显示下一槽位——位置
  // 恒 ≤ 分母，不再出现 6/5、7/5。
  const fallbackPosition = reviewedCards + shownCount;
  const currentPosition =
    card?.queuePosition ??
    (card?.isRepeat ? Math.max(1, fallbackPosition) : fallbackPosition + 1);
  // 消费即从工作流头移除，队列清空（含重复副本全部解决）才会话完成
  // —— 与服务端「dayCompleted 追加池清空条件」同构（规格规则 5）。
  const isComplete = cards.length > 0 && queue.length === 0;
  const newCardsCompleted = cards.filter((item) => item.queueType === 'new').length;
  const reviewCardsCompleted = cards.filter((item) => item.queueType === 'review').length;
  const reviewCards = cards.filter((item) => item.queueType === 'review');

  const handleRating = useCallback(
    async (rating: ReviewRating) => {
      if (!card || submittingRef.current) {
        return;
      }

      submittingRef.current = true;
      setIsSubmitting(true);
      setError(null);

      try {
        const result = await onReview(card, rating);
        // 池端点返回的词级状态（App.reviewWordCard 聚合）：pending =
        // 仍在池内（重插）；cleared = Got it 移出；capped = 达 3 次
        // 上限自动移出。普通队列卡返回 undefined。
        const poolStatus = card.isRepeat
          ? ((result as { status?: 'pending' | 'cleared' | 'capped' } | undefined)
              ?.status ?? null)
          : null;
        const shouldReinsert = card.isRepeat
          ? poolStatus === 'pending'
          : rating === 'unknown' && card.queueType === 'new';
        setQueue((prev) => {
          const next = prev.slice();
          next.splice(0, 1);
          if (shouldReinsert) {
            const repeatCopy: StudyCard = {
              ...card,
              isRepeat: true,
              queuePosition: null,
              queueType: 'new'
            };
            // 间隔 3 张：副本前有 3 张未学卡时重插（不足 3 张落队尾，
            // 规格规则 2 边界态）。
            const insertAt = Math.min(REPEAT_INSERT_INTERVAL, next.length);
            next.splice(insertAt, 0, repeatCopy);
          }
          return next;
        });
        if (
          (!card.isRepeat && (rating !== 'unknown' || card.queueType !== 'new')) ||
          (card.isRepeat && (poolStatus === 'cleared' || poolStatus === 'capped'))
        ) {
          setRepeatCompleted((count) => count + 1);
        }
        setShownCount((count) => (card.isRepeat ? count : count + 1));
        setIsRevealed(false);
        setLookupState({ status: 'idle' });
        setShowAllDefinitions(false);
      } catch {
        setError('The review did not save. Try that rating again.');
      } finally {
        submittingRef.current = false;
        setIsSubmitting(false);
      }
    },
    [card, onReview]
  );

  useEffect(() => {
    function handleKeyDown(event: KeyboardEvent) {
      if (!card) {
        return;
      }

      if (!isRevealed && (event.key === ' ' || event.code === 'Space' || event.key === 'Enter')) {
        event.preventDefault();
        setIsRevealed(true);
        return;
      }

      if (!isRevealed || submittingRef.current) {
        return;
      }

      const shortcutRatings: Record<string, ReviewRating> = {
        '1': 'known',
        '2': 'uncertain',
        '3': 'unknown'
      };
      const rating = shortcutRatings[event.key];

      if (rating) {
        event.preventDefault();
        void handleRating(rating);
      }
    }

    window.addEventListener('keydown', handleKeyDown);

    return () => {
      window.removeEventListener('keydown', handleKeyDown);
    };
  }, [card, handleRating, isRevealed]);

  useEffect(() => {
    if (!isComplete || completedRef.current) {
      return;
    }

    completedRef.current = true;
    onComplete?.(cards);
  }, [cards, isComplete, onComplete]);

  if (!card) {
    return (
      <main
        className="study-shell completion-state"
        aria-label="Session complete"
        onClick={onExit}
      >
        <div className="completion-panel" aria-labelledby="completion-title">
          <div className="celebration-field" aria-hidden="true">
            {Array.from({ length: 18 }, (_, index) => (
              <span key={index} />
            ))}
          </div>
          <div className="completion-mark" aria-hidden="true">
            <span>OK</span>
          </div>
          <p className="eyebrow">Session complete</p>
          <h1 id="completion-title">Checked in for today.</h1>
          <p>The desk is clear. Tomorrow&apos;s review path is already waiting in the background.</p>
          <div className="completion-stats" aria-label="Completed session summary">
            <div>
              <strong>{cards.length}</strong>
              <span>cards</span>
            </div>
            <div>
              <strong>{newCardsCompleted}</strong>
              <span>new</span>
            </div>
            <div>
              <strong>{reviewCardsCompleted}</strong>
              <span>review</span>
            </div>
          </div>
          <div className="completion-actions">
            {onReviewDueWords && reviewCards.length > 0 ? (
              <button
                className="secondary-action"
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  onReviewDueWords(reviewCards);
                }}
              >
                Review due words
              </button>
            ) : null}
            {onPracticeSpelling ? (
              <button
                className="primary-action"
                type="button"
                onClick={(event) => {
                  event.stopPropagation();
                  onPracticeSpelling(cards);
                }}
              >
                Practice spelling
              </button>
            ) : null}
            <button
              className="ghost-button completion-exit-button"
              type="button"
              onClick={(event) => {
                event.stopPropagation();
                onExit();
              }}
            >
              Back home
            </button>
          </div>
        </div>
      </main>
    );
  }

  // When a card is "degraded" (senses came from the fallback provider) the
  // server still ships the placeholder text, but the frontend must NOT show
  // it as a real definition. We always render a "Definition preparing"
  // notice and only show the first example if it isn't template text.
  const senses = card.senses.length > 0
    ? card.senses
    : [
        {
          cardId: card.cardId,
          partOfSpeech: card.partOfSpeech,
          senseLabel: card.senseLabel,
          definition: card.definition,
          definitionSource: card.definitionSource,
          examples: card.examples,
          chineseNote: card.chineseNote
        }
      ];

  const cardIsDegraded = card.degraded || senses.every((sense) => isDegradedSource(sense.definitionSource));
  const hiddenSenseCount = Math.max(senses.length - VISIBLE_SENSE_COUNT, 0);
  const visibleSenses = showAllDefinitions || hiddenSenseCount === 0
    ? senses
    : senses.slice(0, VISIBLE_SENSE_COUNT);

  async function handleRatingClick(rating: ReviewRating) {
    if (isSubmitting) {
      return;
    }

    await handleRating(rating);
  }

  async function handleLookupSelection() {
    if (!onLookupWord) {
      return;
    }

    const selectedText = window.getSelection()?.toString() ?? '';
    const selectedWord = selectedText.match(/[A-Za-z][A-Za-z'-]*/)?.[0]?.toLowerCase();

    if (!selectedWord) {
      return;
    }

    setLookupState({ status: 'loading', word: selectedWord });

    try {
      const result = await onLookupWord(selectedWord);
      setLookupState({ status: 'ready', result });
    } catch {
      setLookupState({
        status: 'error',
        word: selectedWord,
        message: 'Oxford did not return a definition for this word.'
      });
    }
  }

  return (
    <main className="study-shell" aria-label="Study session">
      <header className="study-topbar">
        <button className="ghost-button" type="button" onClick={onExit}>
          Exit
        </button>
        <div className="study-progress">
          <div className="progress-text" aria-label="Progress">
            {currentPosition} / {denominator}
          </div>
          <div
            className="progress-bar"
            role="progressbar"
            aria-label="Today completed words"
            aria-valuemin={0}
            aria-valuemax={denominator}
            aria-valuenow={dayCompletedCount}
          >
            <div className="progress-bar-fill" style={{ width: `${completionPercent}%` }} />
          </div>
          <div className="completed-text">{dayCompletedCount} / {denominator} completed</div>
        </div>
      </header>

      <section className="study-card" aria-labelledby="study-word">
        <div className="queue-pill">{card.queueType}</div>
        <div className="card-front">
          {/* PRD ch.12 (P1): the word face always renders on one line;
              WordHeadline shrinks the font size only when the default
              size overflows the card width. */}
          <WordHeadline word={card.word} />
          {/* PRD decision 1: render real UK/US IPA when data exists; when the
              panel has no real IPA it renders nothing (no placeholder copy). */}
          {onLookupPronunciation ? (
            <PronunciationPanel
              word={card.word}
              onLookupPronunciation={onLookupPronunciation}
              autoPlay={!card.isRepeat}
            />
          ) : null}
        </div>

        {!isRevealed ? (
          <button className="primary-action reveal-action" type="button" onClick={() => setIsRevealed(true)}>
            Reveal
          </button>
        ) : (
          <div className="card-back">
            {cardIsDegraded ? (
              <div className="definition-preparing" role="status" aria-live="polite">
                <strong>Definition preparing</strong>
                <span>Real Oxford content will replace this entry shortly. Tap Reveal later to check again.</span>
              </div>
            ) : null}

            <div className="sense-list" onDoubleClick={() => void handleLookupSelection()}>
              {visibleSenses.map((sense, index) => {
                // PRD decision 2: show every real example sentence attached
                // to this sense (usually 1-2 from Oxford); senses without
                // examples get no example block at all.
                const senseExamples = sense.examples;
                const shouldShowSenseLabel =
                  sense.senseLabel && isDistinctSenseLabel(sense.senseLabel, sense.definition);
                const isPrimary = index === 0;
                const senseIsDegraded = isDegradedSource(sense.definitionSource);

                return (
                  <section
                    className={`sense-card${isPrimary ? ' is-primary-sense' : ''}`}
                    key={sense.cardId}
                    aria-label={`Sense ${index + 1}`}
                  >
                    <div className="definition-block">
                      {sense.partOfSpeech ? (
                        <span className="pos-badge" aria-label="Part of speech">
                          {sense.partOfSpeech}
                        </span>
                      ) : null}
                      {shouldShowSenseLabel ? <strong className="sense-label">{sense.senseLabel}</strong> : null}
                      {senseIsDegraded ? (
                        <p className="definition-text definition-text-placeholder">Definition preparing</p>
                      ) : (
                        <p className={isPrimary ? 'definition-text definition-text-primary' : 'definition-text'}>
                          {sense.definition}
                        </p>
                      )}
                    </div>

                    {senseExamples.length > 0 && !senseIsDegraded ? (
                      <div className="example-block">
                        {senseExamples.map((example) => (
                          <p key={example.exampleId}>{example.sentence}</p>
                        ))}
                      </div>
                    ) : null}

                    {sense.chineseNote ? <p className="chinese-note">{sense.chineseNote}</p> : null}
                  </section>
                );
              })}

              {!showAllDefinitions && hiddenSenseCount > 0 ? (
                <button
                  type="button"
                  className="show-more-definitions"
                  onClick={() => setShowAllDefinitions(true)}
                  aria-expanded={false}
                >
                  Show more definitions ({hiddenSenseCount})
                </button>
              ) : null}
            </div>

            {lookupState.status !== 'idle' ? (
              <aside className="lookup-popover" role="dialog" aria-label="Oxford lookup" aria-live="polite">
                <div className="lookup-popover-header">
                  <span>Oxford lookup</span>
                  <button
                    className="icon-button"
                    type="button"
                    aria-label="Close lookup"
                    onClick={() => setLookupState({ status: 'idle' })}
                  >
                    x
                  </button>
                </div>

                {lookupState.status === 'loading' ? <p>Looking up {lookupState.word}...</p> : null}

                {lookupState.status === 'error' ? (
                  <p>
                    {lookupState.message} <strong>{lookupState.word}</strong>
                  </p>
                ) : null}

                {lookupState.status === 'ready' ? (
                  <div>
                    <h2>{lookupState.result.word}</h2>
                    <div className="lookup-senses">
                      {lookupState.result.senses.map((sense, index) => (
                        <section className="lookup-sense" key={`${sense.definition}-${index}`}>
                          <span>{sense.partOfSpeech}</span>
                          <p>{sense.definition}</p>
                        </section>
                      ))}
                    </div>
                    <a href={lookupState.result.sourceUrl} target="_blank" rel="noreferrer">
                      Open in Oxford
                    </a>
                  </div>
                ) : null}
              </aside>
            ) : null}

            <div className="rating-row" aria-label="Rate this card">
              {ratingLabels.map((item) => (
                <button
                  className={`rating-button rating-${item.rating}`}
                  type="button"
                  key={item.rating}
                  onClick={() => void handleRatingClick(item.rating)}
                  disabled={isSubmitting}
                  aria-keyshortcuts={item.shortcut}
                >
                  {item.label}
                </button>
              ))}
            </div>
            {error ? <p className="inline-error">{error}</p> : null}
          </div>
        )}
      </section>
    </main>
  );
}
