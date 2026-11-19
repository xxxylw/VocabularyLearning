import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import { StudySession } from './StudySession';
import type { StudyCard } from '../api';

// 当日重复池（task 7684082688076025051；2026-10-29 规格扩展）前端
// 行为：非 Got it 评级（Maybe / New）的卡（new 卡与复习卡都算）间隔
// 3 张以 isRepeat 副本重新出现、Got it 一次才移除、进度条停滞不回退
// （规格规则 2 / 3 / 6；2 评级 × 2 卡型矩阵）。

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

// 读取两段式进度条的宽度：浅绿段（见过未掌握，占总进度的宽度）
// 与深绿段（Got it 完成）。返回形如 '20%' 的字符串。
function getFillWidths(container: HTMLElement) {
  const seen = container.querySelector<HTMLElement>('.progress-fill-seen');
  const done = container.querySelector<HTMLElement>('.progress-fill-done');
  return {
    seen: seen?.style.width ?? null,
    done: done?.style.width ?? null
  };
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
    const { container } = render(
      <StudySession
        cards={cards}
        totalCards={5}
        onReview={onReview}
        onExit={vi.fn()}
      />
    );

    await revealAndRate(user, /^new$/i); // alpha：浅绿 +1，深绿不计入
    expect(screen.getByRole('progressbar', { name: /Today completed words/i })).toHaveAttribute(
      'aria-valuenow',
      '0'
    );
    expect(getFillWidths(container)).toEqual({ seen: '20%', done: '0%' });

    await revealAndRate(user, /got it/i); // bravo
    await revealAndRate(user, /got it/i); // charlie
    await revealAndRate(user, /got it/i); // delta
    expect(screen.getByRole('progressbar', { name: /Today completed words/i })).toHaveAttribute(
      'aria-valuenow',
      '3'
    );
    expect(getFillWidths(container)).toEqual({ seen: '80%', done: '60%' });

    await revealAndRate(user, /got it/i); // alpha 重复副本 Got it：浅绿转深绿
    expect(screen.getByRole('progressbar', { name: /Today completed words/i })).toHaveAttribute(
      'aria-valuenow',
      '4'
    );
    expect(getFillWidths(container)).toEqual({ seen: '80%', done: '80%' });

    await revealAndRate(user, /got it/i); // echo：队列清空，会话完成
    expect(await screen.findByText('Checked in for today.')).toBeInTheDocument();
  });

  it('drops a repeat card for good when the pool reports it capped', async () => {
    const user = userEvent.setup();
    const alpha = { ...makeCard('card-1', 'alpha'), isRepeat: true };
    const cards = [alpha, makeCard('card-2', 'bravo'), makeCard('card-3', 'charlie')];
    const onReview = vi.fn().mockImplementation((card: StudyCard, rating: string) =>
      Promise.resolve(
        card.isRepeat
          ? { status: rating === 'known' ? 'cleared' : 'capped' }
          : undefined
      )
    );
    const { container } = render(<StudySession cards={cards} totalCards={3} onReview={onReview} onExit={vi.fn()} />);

    await revealAndRate(user, /maybe/i); // 重复卡评 Maybe → 服务端 capped
    expect(onReview.mock.calls[0][0].isRepeat).toBe(true);

    // capped：没 Got it 就不算完成（深绿不变），副本留在浅绿段，
    // 且不再重插。旧口径把 capped 计入 completed——2026-10-30 起废除。
    expect(screen.getByRole('progressbar', { name: /Today completed words/i })).toHaveAttribute(
      'aria-valuenow',
      '0'
    );
    expect(getFillWidths(container)).toEqual({ seen: '0%', done: '0%' });

    await revealAndRate(user, /got it/i); // bravo
    expect(screen.getByRole('progressbar', { name: /Today completed words/i })).toHaveAttribute(
      'aria-valuenow',
      '1'
    );
    expect(getFillWidths(container)).toEqual({ seen: '33%', done: '33%' });

    await revealAndRate(user, /got it/i); // charlie：队列清空，会话完成
    expect(await screen.findByText('Checked in for today.')).toBeInTheDocument();
    expect(onReview).toHaveBeenCalledTimes(3);
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

  it('reinserts a new card rated Maybe three cards later (uncertain × new)', async () => {
    const user = userEvent.setup();
    const cards = makeCards(['alpha', 'bravo', 'charlie', 'delta', 'echo']);
    const onReview = vi.fn().mockImplementation((card: StudyCard, rating: string) =>
      card.isRepeat
        ? Promise.resolve({ status: rating === 'known' ? 'cleared' : 'pending' })
        : Promise.resolve(undefined)
    );
    render(<StudySession cards={cards} onReview={onReview} onExit={vi.fn()} />);

    await revealAndRate(user, /maybe/i); // alpha 评 Maybe：同样重插
    await revealAndRate(user, /got it/i); // bravo
    await revealAndRate(user, /got it/i); // charlie
    await revealAndRate(user, /got it/i); // delta

    // 第 5 次展示：alpha 的重复副本（间隔 3 张）先于 echo 出现。
    await revealAndRate(user, /got it/i);
    expect(onReview).toHaveBeenCalledTimes(5);
    expect(onReview.mock.calls[4][0].word).toBe('alpha');
    expect(onReview.mock.calls[4][0].isRepeat).toBe(true);

    await revealAndRate(user, /got it/i); // echo：队列清空，会话完成
    expect(await screen.findByText('Checked in for today.')).toBeInTheDocument();
  });

  it('reinserts a review card rated New and keeps the review label (unknown × review)', async () => {
    const user = userEvent.setup();
    const cards = makeCards(['alpha', 'bravo', 'charlie', 'delta', 'echo'], 'review');
    const onReview = vi.fn().mockImplementation((card: StudyCard, rating: string) =>
      card.isRepeat
        ? Promise.resolve({ status: rating === 'known' ? 'cleared' : 'pending' })
        : Promise.resolve(undefined)
    );
    render(<StudySession cards={cards} totalCards={5} onReview={onReview} onExit={vi.fn()} />);

    await revealAndRate(user, /^new$/i); // alpha（复习卡）评 New：重插、进度停滞
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

    // 第 5 次展示：alpha 的重复副本，仍标 review（queueType 沿用原卡）。
    await revealAndRate(user, /got it/i);
    expect(onReview).toHaveBeenCalledTimes(5);
    expect(onReview.mock.calls[4][0].word).toBe('alpha');
    expect(onReview.mock.calls[4][0].isRepeat).toBe(true);
    expect(onReview.mock.calls[4][0].queueType).toBe('review');
    expect(screen.getByRole('progressbar', { name: /Today completed words/i })).toHaveAttribute(
      'aria-valuenow',
      '4'
    );

    await revealAndRate(user, /got it/i); // echo：完成
    expect(await screen.findByText('Checked in for today.')).toBeInTheDocument();
  });

  it('reinserts a review card rated Maybe and stalls progress until cleared (uncertain × review)', async () => {
    const user = userEvent.setup();
    const cards = makeCards(['alpha', 'bravo'], 'review');
    const onReview = vi.fn().mockImplementation((card: StudyCard, rating: string) =>
      card.isRepeat
        ? Promise.resolve({ status: rating === 'known' ? 'cleared' : 'pending' })
        : Promise.resolve(undefined)
    );
    render(<StudySession cards={cards} totalCards={2} onReview={onReview} onExit={vi.fn()} />);

    await revealAndRate(user, /maybe/i); // alpha（复习卡）评 Maybe：重插、不计入
    expect(screen.getByRole('progressbar', { name: /Today completed words/i })).toHaveAttribute(
      'aria-valuenow',
      '0'
    );

    await revealAndRate(user, /got it/i); // bravo：+1
    expect(screen.getByRole('progressbar', { name: /Today completed words/i })).toHaveAttribute(
      'aria-valuenow',
      '1'
    );

    await revealAndRate(user, /got it/i); // alpha 副本（不足 3 张落队尾）：清空 +1
    expect(onReview).toHaveBeenCalledTimes(3);
    expect(onReview.mock.calls[2][0].word).toBe('alpha');
    expect(onReview.mock.calls[2][0].isRepeat).toBe(true);
    expect(onReview.mock.calls[2][0].queueType).toBe('review');
    expect(await screen.findByText('Checked in for today.')).toBeInTheDocument();
  });

  it('falls back to reviewedCards and keeps both segments within the denominator across repeat re-shows', async () => {
    const user = userEvent.setup();
    // 旧后端未返回 gotItCards / seenUnmasteredCards 时前端回退：
    // 深绿基数 = reviewedCards。当日队列 8 张、已完成 3 张，剩余
    // alpha～echo 5 张。alpha 评 New → 副本 3 张后插入（delta 与
    // echo 之间）；副本 Got it 清空时 echo 还在队列，能观察到浅绿
    // 转深绿的稳定中间态。两段恒 ≤ 分母，数字 = completed。
    const cards = [
      { ...makeCard('card-1', 'alpha'), queuePosition: 4 },
      { ...makeCard('card-2', 'bravo'), queuePosition: 5 },
      { ...makeCard('card-3', 'charlie'), queuePosition: 6 },
      { ...makeCard('card-4', 'delta'), queuePosition: 7 },
      { ...makeCard('card-5', 'echo'), queuePosition: 8 }
    ];
    const onReview = vi.fn().mockImplementation((card: StudyCard, rating: string) =>
      card.isRepeat
        ? Promise.resolve({ status: rating === 'known' ? 'cleared' : 'pending' })
        : Promise.resolve(undefined)
    );
    const { container } = render(
      <StudySession
        cards={cards}
        totalCards={8}
        reviewedCards={3}
        onReview={onReview}
        onExit={vi.fn()}
      />
    );

    expect(screen.getByText('3 / 8')).toBeInTheDocument();
    expect(getFillWidths(container)).toEqual({ seen: '38%', done: '38%' });

    await revealAndRate(user, /^new$/i); // alpha 评 New：浅绿 +1，深绿不动
    expect(screen.getByText('3 / 8')).toBeInTheDocument();
    expect(getFillWidths(container)).toEqual({ seen: '50%', done: '38%' });

    await revealAndRate(user, /got it/i); // bravo：深绿 +1
    expect(screen.getByText('4 / 8')).toBeInTheDocument();
    expect(getFillWidths(container)).toEqual({ seen: '63%', done: '50%' });

    await revealAndRate(user, /got it/i); // charlie：深绿 +1
    expect(screen.getByText('5 / 8')).toBeInTheDocument();
    expect(getFillWidths(container)).toEqual({ seen: '75%', done: '63%' });

    await revealAndRate(user, /got it/i); // delta：深绿 +1，下一张是 alpha 副本
    expect(screen.getByText('6 / 8')).toBeInTheDocument();
    expect(getFillWidths(container)).toEqual({ seen: '88%', done: '75%' });

    await revealAndRate(user, /got it/i); // 副本清空：浅绿 1 → 0、深绿 +1
    expect(onReview.mock.calls[4][0].word).toBe('alpha');
    expect(onReview.mock.calls[4][0].isRepeat).toBe(true);
    expect(screen.getByText('7 / 8')).toBeInTheDocument();
    expect(getFillWidths(container)).toEqual({ seen: '88%', done: '88%' });

    await revealAndRate(user, /got it/i); // echo：队列清空，会话完成
    expect(await screen.findByText('Checked in for today.')).toBeInTheDocument();
  });

  it('shows the server completed count for a server-injected repeat copy (no zero display)', async () => {
    const user = userEvent.setup();
    // 重进 Today 的服务端流：当日 5 张，服务端已计 gotItCards=1、
    // seenUnmasteredCards=1（alpha 评过 Maybe 在池内），队首直接是
    // alpha 的重复副本。数字 = completed（1 / 5），副本在浅绿段里，
    // 绝不因队首是副本而归零；副本 cleared 后浅绿转深绿。
    const cards = [
      { ...makeCard('card-1', 'alpha'), isRepeat: true },
      { ...makeCard('card-2', 'bravo'), queuePosition: 3 },
      { ...makeCard('card-3', 'charlie'), queuePosition: 4 }
    ];
    const onReview = vi.fn().mockResolvedValue({ status: 'cleared' });
    const { container } = render(
      <StudySession
        cards={cards}
        totalCards={5}
        gotItCards={1}
        seenUnmasteredCards={1}
        onReview={onReview}
        onExit={vi.fn()}
      />
    );

    // 副本在队首：数字显示服务端 completed 基数，浅绿段含池内 alpha。
    expect(screen.getByText('1 / 5')).toBeInTheDocument();
    expect(getFillWidths(container)).toEqual({ seen: '40%', done: '20%' });

    await revealAndRate(user, /got it/i); // 副本 cleared：浅绿转深绿
    expect(screen.getByText('2 / 5')).toBeInTheDocument();
    expect(getFillWidths(container)).toEqual({ seen: '40%', done: '40%' });
    await revealAndRate(user, /got it/i); // bravo
    expect(screen.getByText('3 / 5')).toBeInTheDocument();
    // charlie 是最后一张：评完队列清空直接切完成页，5/5 不再可见。
    await revealAndRate(user, /got it/i);
    expect(await screen.findByText('Checked in for today.')).toBeInTheDocument();
  });

  it('anchors the completed count to server gotItCards after re-entry (no regression)', async () => {
    const user = userEvent.setup();
    // 2026-10-30 场景：alpha 评 New 入池、bravo Got it 后离开 ——
    // 重进时服务端 gotItCards=1（bravo）、seenUnmasteredCards=1
    // （alpha 在池内），两段之和 = positionOffset=2。服务端重进流：
    // charlie、delta、alpha 副本、echo。数字以服务端 completed 为
    // 基数单调不回退；副本 cleared 时浅绿转深绿。
    const cards = [
      { ...makeCard('card-1', 'charlie'), queuePosition: 3 },
      { ...makeCard('card-2', 'delta'), queuePosition: 4 },
      { ...makeCard('card-3', 'alpha'), isRepeat: true },
      { ...makeCard('card-4', 'echo'), queuePosition: 5 }
    ];
    const onReview = vi.fn().mockImplementation((card: StudyCard, rating: string) =>
      card.isRepeat
        ? Promise.resolve({ status: rating === 'known' ? 'cleared' : 'pending' })
        : Promise.resolve(undefined)
    );
    const { container } = render(
      <StudySession
        cards={cards}
        totalCards={5}
        gotItCards={1}
        seenUnmasteredCards={1}
        onReview={onReview}
        onExit={vi.fn()}
      />
    );

    expect(screen.getByText('1 / 5')).toBeInTheDocument();
    expect(getFillWidths(container)).toEqual({ seen: '40%', done: '20%' });
    await revealAndRate(user, /got it/i); // charlie
    expect(screen.getByText('2 / 5')).toBeInTheDocument();
    await revealAndRate(user, /got it/i); // delta
    expect(screen.getByText('3 / 5')).toBeInTheDocument();

    // alpha 副本 cleared：浅绿 1 → 0、深绿 +1，总进度不回退。
    await revealAndRate(user, /got it/i);
    expect(screen.getByText('4 / 5')).toBeInTheDocument();
    expect(getFillWidths(container)).toEqual({ seen: '80%', done: '80%' });

    await revealAndRate(user, /got it/i); // echo：最后一张，队列清空
    expect(await screen.findByText('Checked in for today.')).toBeInTheDocument();
  });
});
