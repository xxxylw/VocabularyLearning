import { useCallback, useEffect, useRef, useState } from 'react';
import type { StudyCard, DefinitionSource } from '../api';
import type { OxfordLookupResult } from '../api';
import type { ReviewRating } from '../api';
import type { Pronunciation } from '../api';
import { PronunciationPanel } from './PronunciationPanel';
import { WordHeadline } from './WordHeadline';

// 当日重复池（task 7684082688076025051；2026-10-29 规格扩展）：非
// Got it 评级（Maybe / New）的卡从当前位置后移 3 张重新出现（剩余
// 不足 3 张时落队尾）—— new 卡与到期复习卡都适用，Got it 才移除。
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
  // size (denominator). 2026-10-30 学习页两段式进度条：
  // - gotItCards = 深绿分子 —— 当日队列里评过 Got it 的卡数（顶部
  //   进度数字同源）。旧调用方只传 reviewedCards 时回退（口径略宽，
  //   含达上限 capped 的卡）；ad-hoc 复习 / 旧后端不传时从 0 起算。
  // - seenUnmasteredCards = 浅绿段基数 —— 见过但还没 Got it 的卡数
  //   （重复池 pending 与达 3 次上限移出的卡都算）。
  totalCards?: number;
  gotItCards?: number;
  reviewedCards?: number;
  seenUnmasteredCards?: number;
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
  gotItCards,
  reviewedCards,
  seenUnmasteredCards
}: StudySessionProps) {
  const [isRevealed, setIsRevealed] = useState(false);
  const [isSubmitting, setIsSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [lookupState, setLookupState] = useState<LookupState>({ status: 'idle' });
  const [showAllDefinitions, setShowAllDefinitions] = useState(false);
  const submittingRef = useRef(false);
  const completedRef = useRef(false);
  // 当日重复池（task 7684082688076025051）：会话内工作流。queue 是
  // 「剩余待展示卡」—— 普通队列卡消费后 splice 移除、评非 Got it
  // （Maybe / New，new 卡与复习卡都算）/ 仍 pending 的重复卡在 3 张
  // 之后以 isRepeat 副本重新插回（规格规则 2：间隔 3 张）。服务端
  // today_repeat_pool 是持久权威，这里的本地重插只保证同一会话内
  // 即时可见，重进由服务端确定性重算。
  const [queue, setQueue] = useState<StudyCard[]>(cards);
  // 规格规则 6 → 2026-10-30 两段式进度条改版：会话内增量分两色
  // 记账 —— Got it 计入深绿（gotItDelta）；评 Maybe / New 入池的卡
  // 计入浅绿（seenDelta），重复副本 Got it 清空时从浅绿转深绿
  // （seen -1、gotIt +1）；达 3 次上限移出（capped）的卡停在浅绿，
  // 不再计入深绿（旧口径曾把 capped 算完成，本版按「只要没 Got it
  // 就算见过未掌握」修正）。深绿只增、浅绿只在转深绿时收缩且
  // 总进度不降 —— 两段合起来单调不回退。
  const [gotItDelta, setGotItDelta] = useState(0);
  const [seenDelta, setSeenDelta] = useState(0);

  useEffect(() => {
    // cards prop 只在服务端重新拉会话时换引用（重进 Today / 再来一组 /
    // ad-hoc 复习），同步工作流并复位会话内进度（两段基数由服务端
    // gotItCards / seenUnmasteredCards 权威重算）。
    setQueue(cards);
    setGotItDelta(0);
    setSeenDelta(0);
    completedRef.current = false;
  }, [cards]);

  const card = queue[0];
  // PRD ch.8: the progress bar is anchored to the day queue, not the
  // in-session list, so it never resets after re-entering Today.
  // 2026-10-30 两段式进度条：深绿 = 评过 Got it 的卡（顶部数字同源
  // ——「数字显示 completed 的数量」）；浅绿 = 见过但还没 Got it
  // 的卡（入池 pending 与达上限 capped 都算）。两段间用同色系深浅
  // 渐变丝滑过渡。旧调用方只传 reviewedCards 时回退为深绿基数
  // （口径略宽），ad-hoc 复习不传时从 0 起算。
  const denominator = totalCards ?? cards.length;
  const dayCompletedCount = (gotItCards ?? reviewedCards ?? 0) + gotItDelta;
  // 浅绿允许 delta 为负：重进场景服务端基数已把池内 pending 卡计入
  // 浅绿，副本 cleared 时要 -1 才能正确「浅绿转深绿」；总量再钳 0。
  const daySeenCount = Math.max(0, (seenUnmasteredCards ?? 0) + seenDelta);
  const clampPercent = (value: number) =>
    denominator === 0
      ? 0
      : Math.min(100, Math.max(0, Math.round((value / denominator) * 100)));
  const completedPercent = clampPercent(dayCompletedCount);
  const seenPercent = clampPercent(dayCompletedCount + daySeenCount);
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
        // 2026-10-29 规格扩展：非 Got it 评级（Maybe / New）一律本地重
        // 插 —— new 卡与到期复习卡都算（服务端 _enter_repeat_pool 同
        // 口径入池），Got it 才真正完成。
        const shouldReinsert = card.isRepeat
          ? poolStatus === 'pending'
          : rating !== 'known';
        setQueue((prev) => {
          const next = prev.slice();
          next.splice(0, 1);
          if (shouldReinsert) {
            const repeatCopy: StudyCard = {
              ...card,
              isRepeat: true,
              queuePosition: null,
              // 沿用原始卡型（复习卡的重复副本仍标 review）。
              queueType: card.queueType
            };
            // 间隔 3 张：副本前有 3 张未学卡时重插（不足 3 张落队尾，
            // 规格规则 2 边界态）。
            const insertAt = Math.min(REPEAT_INSERT_INTERVAL, next.length);
            next.splice(insertAt, 0, repeatCopy);
          }
          return next;
        });
        if (!card.isRepeat && rating === 'known') {
          // 普通队列卡 Got it：深绿 +1（卡此前未见过，浅绿无变化）。
          setGotItDelta((count) => count + 1);
        } else if (!card.isRepeat) {
          // Maybe / New 入池：见过但未掌握，浅绿 +1。
          setSeenDelta((count) => count + 1);
        } else if (poolStatus === 'cleared') {
          // 重复副本 Got it 清空：浅绿转深绿（seen -1、gotIt +1）。
          // 不钳 0 —— 重进场景服务端基数已把池内卡计入浅绿，需要
          // 负 delta 抵消（总量在 daySeenCount 处再钳 0）。
          setSeenDelta((count) => count - 1);
          setGotItDelta((count) => count + 1);
        }
        // 重复副本 pending（再现 Maybe / New）与 capped（达上限移出）
        // 都停在浅绿：已在入池时计入，不重复、不转深绿。
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
          chineseNote: null
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
          {/* 2026-10-30 两段式进度条：数字 = completed（Got it 卡数）；
              深绿段 = Got it，其后浅绿段 = 见过但未掌握（入池 pending
              与达上限 capped 都算），两段以同色系深浅渐变丝滑过渡。
              进度条下方不再单独显示 completed 文案。 */}
          <div className="progress-text" aria-label="Progress">
            {dayCompletedCount} / {denominator}
          </div>
          <div
            className="progress-bar"
            role="progressbar"
            aria-label="Today completed words"
            aria-valuemin={0}
            aria-valuemax={denominator}
            aria-valuenow={dayCompletedCount}
            aria-valuetext={`${dayCompletedCount} of ${denominator} words completed`}
          >
            <div className="progress-fill progress-fill-seen" style={{ width: `${seenPercent}%` }} />
            <div className="progress-fill progress-fill-done" style={{ width: `${completedPercent}%` }} />
          </div>
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
