import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { StudySession } from './StudySession';
import type { StudyCard } from '../api';

// 当日重复池（task 7684082688076025051）前端行为：评 New 的 new 卡间隔
// 3 张以 isRepeat 副本重新出现、Got it 一次才移除、进度条停滞不回退、
// 复习卡评 New 不重插（规格规则 2 / 3 / 6 / D1）。

function makeCard(id: string, word: string, queueType: 'new' | 'review' = 'new'): StudyCard {
  return {
    cardId: id,
    cardIds: [id],
    word,
    partOfSpeech: 'noun',
    senseLabel: `sense of ${word}`,
    definition: `the definition of ${word}`,
    definitionSource: 'oxford_api',
    examples: [
      {
        exampleId: `${id}-example-1`,
        sentence: `An example sentence for ${word}.`,
        isPrimary: true
      }
    ],
    chineseNote: null,
    senses: [
      {
        cardId: id,
        partOfSpeech: 'noun',
        senseLabel: `sense of ${word}`,
        definition: `the definition of ${word}`,
        definitionSource: 'oxford_api',
        examples: [
          {
            exampleId: `${id}-example-1`,
            sentence: `An example sentence for ${word}.`,
            isPrimary: true
          }
        ],
        chineseNote: null
      }
    ],
    queueType,
    degraded: false
  };
}

function makeCards(words: string[], queueType: 'new' | 'review' = 'new'): StudyCard[] {
  return words.map((word, index) => makeCard(`card-${index + 1}`, word, queueType));
}

async function revealAndRate(
  user: ReturnType<typeof userEvent.setup>,
  rating: RegExp
) {
  await user.click(screen.getByRole('button', { name: /reveal/i }));
  await user.click(screen.getByRole('button', { name: rating }));
}

describe('StudySession 当日重复池', () => {
  it('reinserts a new card rated New three cards later with the isRepeat flag', async () => {
    const user = userEvent.setup();
    const cards = makeCards(['alpha', 'bravo', 'charlie', 'delta', 'echo']);
    const onReview = vi.fn().mockResolvedValue(undefined);
    render(<StudySession cards={cards} onReview={onReview} onExit={vi.fn()} />);

    await revealAndRate(user, /^new$/i);
    await revealAndRate(user, /got it/i); // bravo
    await revealAndRate(user, /got it/i); // charlie
    await revealAndRate(user, /got it/i); // delta

    // 第 4 张未学卡（echo）之前不重现 alpha。
    expect(onReview).toHaveBeenCalledTimes(4);

    // 第 5 次展示：不是 echo，而是 alpha 的重复副本（间隔 3 张）。
    await revealAndRate(user, /got it/i);
    expect(onReview).toHaveBeenCalledTimes(5);
    expect(onReview.mock.calls[4][0].word).toBe('alpha');
    expect(onReview.mock.calls[4][0].isRepeat).toBe(true);
  });

  it('keeps the progress bar stalled until the repeat copy is resolved, then counts it', async () => {
    const user = userEvent.setup();
    const cards = makeCards(['alpha', 'bravo', 'charlie', 'delta', 'echo']);
    const onReview = vi.fn().mockImplementation((card: StudyCard, rating: string) =>
      card.isRepeat
        ? Promise.resolve({ status: rating === 'known' ? 'cleared' : 'pending' })
        : Promise.resolve(undefined)
    );
    render(
      <StudySession
        cards={cards}
        totalCards={5}
        onReview={onReview}
        onExit={vi.fn()}
      />
    );

    await revealAndRate(user, /^new$/i); // alpha: 停滞，不计入
    expect(screen.getByRole('progressbar', { name: /Today completed words/i })).toHaveAttribute(
      'aria-valuenow',
      '0'
    );

    await revealAndRate(user, /got it/i); // bravo
    await revealAndRate(user, /got it/i); // charlie
    await revealAndRate(user, /got it/i); // delta
    expect(screen.getByRole('progressbar', { name: /Today completed words/i })).toHaveAttribute(
      'aria-valuenow',
      '3'
    );

    await revealAndRate(user, /got it/i); // alpha 重复副本 Got it：+1
    expect(screen.getByRole('progressbar', { name: /Today completed words/i })).toHaveAttribute(
      'aria-valuenow',
      '4'
    );

    await revealAndRate(user, /got it/i); // echo：队列清空，会话完成
    expect(await screen.findByText('Checked in for today.')).toBeInTheDocument();
  });

  it('drops a repeat card for good when the pool reports it capped', async () => {
    const user = userEvent.setup();
    const alpha = { ...makeCard('card-1', 'alpha'), isRepeat: true };
    const cards = [alpha, makeCard('card-2', 'bravo')];
    const onReview = vi.fn().mockImplementation((card: StudyCard, rating: string) =>
      Promise.resolve(
        card.isRepeat
          ? { status: rating === 'known' ? 'cleared' : 'capped' }
          : undefined
      )
    );
    render(<StudySession cards={cards} totalCards={2} onReview={onReview} onExit={vi.fn()} />);

    await revealAndRate(user, /maybe/i); // 重复卡评 Maybe → 服务端 capped
    expect(onReview.mock.calls[0][0].isRepeat).toBe(true);

    // capped：+1 计入完成，且不再重插。
    expect(screen.getByRole('progressbar', { name: /Today completed words/i })).toHaveAttribute(
      'aria-valuenow',
      '1'
    );

    await revealAndRate(user, /got it/i); // bravo
    expect(await screen.findByText('Checked in for today.')).toBeInTheDocument();
    expect(onReview).toHaveBeenCalledTimes(2);
  });

  it('reinserts a repeat card again when the pool keeps it pending', async () => {
    const user = userEvent.setup();
    const alpha = { ...makeCard('card-1', 'alpha'), isRepeat: true };
    const cards = [
      alpha,
      makeCard('card-2', 'bravo'),
      makeCard('card-3', 'charlie'),
      makeCard('card-4', 'delta')
    ];
    const onReview = vi.fn().mockImplementation((card: StudyCard, rating: string) =>
      card.isRepeat
        ? Promise.resolve({ status: rating === 'known' ? 'cleared' : 'pending' })
        : Promise.resolve(undefined)
    );
    render(<StudySession cards={cards} totalCards={4} onReview={onReview} onExit={vi.fn()} />);

    // 队首就是服务端注入的重复卡；评 Maybe → pending → 3 张后重现。
    await revealAndRate(user, /maybe/i);
    await revealAndRate(user, /got it/i); // bravo
    await revealAndRate(user, /got it/i); // charlie
    await revealAndRate(user, /got it/i); // delta

    await revealAndRate(user, /got it/i); // alpha 副本重现，Got it 清空
    expect(onReview).toHaveBeenCalledTimes(5);
    expect(onReview.mock.calls[4][0].word).toBe('alpha');
    expect(onReview.mock.calls[4][0].isRepeat).toBe(true);
    expect(await screen.findByText('Checked in for today.')).toBeInTheDocument();
  });

  it('inserts at the tail when fewer than three cards remain', async () => {
    const user = userEvent.setup();
    const cards = makeCards(['alpha', 'bravo']);
    const onReview = vi.fn().mockImplementation((card: StudyCard, rating: string) =>
      card.isRepeat
        ? Promise.resolve({ status: rating === 'known' ? 'cleared' : 'pending' })
        : Promise.resolve(undefined)
    );
    render(<StudySession cards={cards} totalCards={2} onReview={onReview} onExit={vi.fn()} />);

    await revealAndRate(user, /^new$/i); // alpha → 剩余不足 3 张，落队尾
    await revealAndRate(user, /got it/i); // bravo

    // 队尾重现 alpha，Got it 后完成。
    await revealAndRate(user, /got it/i);
    expect(onReview).toHaveBeenCalledTimes(3);
    expect(onReview.mock.calls[2][0].word).toBe('alpha');
    expect(onReview.mock.calls[2][0].isRepeat).toBe(true);
    expect(await screen.findByText('Checked in for today.')).toBeInTheDocument();
  });

  it('does not reinsert review cards rated New (D1: only new cards enter the pool)', async () => {
    const user = userEvent.setup();
    const cards = [
      makeCard('card-1', 'alpha', 'review'),
      makeCard('card-2', 'bravo', 'review')
    ];
    const onReview = vi.fn().mockResolvedValue(undefined);
    render(<StudySession cards={cards} totalCards={2} onReview={onReview} onExit={vi.fn()} />);

    await revealAndRate(user, /^new$/i); // 复习卡评 New：计入完成、不重插
    expect(screen.getByRole('progressbar', { name: /Today completed words/i })).toHaveAttribute(
      'aria-valuenow',
      '1'
    );

    await revealAndRate(user, /got it/i);
    expect(await screen.findByText('Checked in for today.')).toBeInTheDocument();
    expect(onReview).toHaveBeenCalledTimes(2);
  });

  it('keeps the position indicator within the day-queue denominator across repeat re-shows', async () => {
    const user = userEvent.setup();
    // 复现 bug 场景：当日队列 5 张、已学 3 张，剩余 alpha（槽 4）、
    // bravo（槽 5）。alpha 评 New 本地重插后，重复副本的 queuePosition
    // 为 null 走兜底计数——修复前依次显示 5/5 → 6/5 → 7/5。
    const cards = [
      { ...makeCard('card-1', 'alpha'), queuePosition: 4 },
      { ...makeCard('card-2', 'bravo'), queuePosition: 5 }
    ];
    const onReview = vi.fn().mockImplementation((card: StudyCard, rating: string) =>
      card.isRepeat
        ? Promise.resolve({ status: rating === 'known' ? 'cleared' : 'pending' })
        : Promise.resolve(undefined)
    );
    render(
      <StudySession
        cards={cards}
        totalCards={5}
        reviewedCards={3}
        onReview={onReview}
        onExit={vi.fn()}
      />
    );

    expect(screen.getByText('4 / 5')).toBeInTheDocument();
    await revealAndRate(user, /^new$/i); // alpha 评 New → 剩余不足 3 张，副本落队尾

    expect(screen.getByText('5 / 5')).toBeInTheDocument(); // bravo（槽 5）
    await revealAndRate(user, /got it/i);

    // alpha 副本重现：位置不推进、不超分母（修复前 6 / 5）。
    expect(screen.getByText('5 / 5')).toBeInTheDocument();
    expect(screen.queryByText('6 / 5')).not.toBeInTheDocument();

    await revealAndRate(user, /maybe/i); // 副本 pending → 再次重插（修复前 7 / 5）
    expect(screen.getByText('5 / 5')).toBeInTheDocument();
    expect(screen.queryByText('7 / 5')).not.toBeInTheDocument();

    await revealAndRate(user, /got it/i); // 副本清空 → 会话完成
    expect(await screen.findByText('Checked in for today.')).toBeInTheDocument();
  });

  it('shows the last original-card position for a server-injected repeat copy (no zero display)', async () => {
    const user = userEvent.setup();
    // 重进 Today 的服务端流：已学 2 张，队首直接是重复副本（queuePosition
    // 为 null）。副本显示最近一张原始卡的位置，且恒 ≤ 分母。
    const cards = [
      { ...makeCard('card-1', 'alpha'), isRepeat: true },
      { ...makeCard('card-2', 'bravo'), queuePosition: 3 },
      { ...makeCard('card-3', 'charlie'), queuePosition: 4 }
    ];
    const onReview = vi.fn().mockResolvedValue({ status: 'cleared' });
    render(
      <StudySession
        cards={cards}
        totalCards={4}
        reviewedCards={2}
        onReview={onReview}
        onExit={vi.fn()}
      />
    );

    // 副本在队首：显示 2 / 4（最近原始卡槽位），绝不 0、不超分母。
    expect(screen.getByText('2 / 4')).toBeInTheDocument();

    await revealAndRate(user, /got it/i); // 副本 cleared
    expect(screen.getByText('3 / 4')).toBeInTheDocument(); // bravo（槽 3）
    await revealAndRate(user, /got it/i);
    expect(screen.getByText('4 / 4')).toBeInTheDocument(); // charlie（槽 4）
    await revealAndRate(user, /got it/i);
    expect(await screen.findByText('Checked in for today.')).toBeInTheDocument();
  });
});
