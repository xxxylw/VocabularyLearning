import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { describe, expect, it, vi } from 'vitest';
import type { StudyCard } from '../api';
import { SpellingSession } from './SpellingSession';

const spellingCards: StudyCard[] = [
  {
    cardId: 'card-1',
    cardIds: ['card-1'],
    word: 'El Nino',
    partOfSpeech: 'noun',
    senseLabel: 'weather pattern',
    definition: 'a weather pattern that warms the eastern Pacific Ocean',
    definitionSource: 'oxford_api',
    examples: [],
    chineseNote: 'El Nino phenomenon',
    senses: [
      {
        cardId: 'card-1',
        partOfSpeech: 'noun',
        senseLabel: 'weather pattern',
        definition: 'a weather pattern that warms the eastern Pacific Ocean',
        definitionSource: 'oxford_api',
        examples: [],
        chineseNote: 'El Nino phenomenon'
      }
    ],
    queueType: 'new',
    degraded: false
  },
  {
    cardId: 'card-2',
    cardIds: ['card-2'],
    word: 'carbon dioxide',
    partOfSpeech: 'noun',
    senseLabel: 'gas',
    definition: 'a gas produced when carbon burns',
    definitionSource: 'oxford_api',
    examples: [],
    chineseNote: null,
    senses: [],
    queueType: 'review',
    degraded: false
  }
];

const hydrogenCard: StudyCard = {
  cardId: 'card-h',
  cardIds: ['card-h'],
  word: 'hydrogen',
  partOfSpeech: 'noun',
  senseLabel: 'chemical element',
  definition: 'a chemical element. Hydrogen is a gas that is the lightest of all the elements.',
  definitionSource: 'oxford_api',
  examples: [],
  chineseNote: null,
  senses: [],
  queueType: 'new',
  degraded: false
};

describe('SpellingSession', () => {
  it('never leaks the answer through the prompt and checks spelling with normalized spacing and case', async () => {
    const user = userEvent.setup();
    render(<SpellingSession cards={spellingCards} onExit={vi.fn()} />);

    // F-01: the chinese note ("El Nino phenomenon") contains the answer, so
    // the prompt must fall through to the safe definition sentence instead.
    expect(screen.queryByText('El Nino phenomenon')).not.toBeInTheDocument();
    expect(screen.getByText('a weather pattern that warms the eastern Pacific Ocean')).toBeInTheDocument();

    await user.type(screen.getByRole('textbox', { name: /type the word/i }), '  el   nino ');
    await user.click(screen.getByRole('button', { name: /check/i }));

    // 2026-09-08 重设计：首答对直接进入「correct」态，主按钮切换为「下一词」。
    expect(screen.getByText(/correct/i)).toBeInTheDocument();
    expect(screen.getByText('El Nino')).toBeInTheDocument();

    await user.click(screen.getByRole('button', { name: /next word/i }));

    // F-01: every sentence of the "carbon dioxide" definition leaks a
    // component of the answer, so the prompt falls back to a structured
    // hint instead of revealing "carbon".
    expect(screen.getByText('2 words · 13 letters · starts with "c" · ends with "e"')).toBeInTheDocument();
    expect(screen.queryByText(/a gas produced when carbon burns/i)).not.toBeInTheDocument();
  });

  it('drops the leaking sentence and shows only the safe fragment (hydrogen)', () => {
    render(<SpellingSession cards={[hydrogenCard]} onExit={vi.fn()} />);

    // DP-B1: 主提示为 h2 级，不再用 h1。
    expect(screen.getByRole('heading', { level: 2, name: /a chemical element/i })).toBeInTheDocument();
    // The whole document must not contain the answer before it is revealed.
    expect(screen.queryByText(/hydrogen/i)).not.toBeInTheDocument();
  });

  it('matches the answer case-insensitively when building the prompt', () => {
    render(
      <SpellingSession
        cards={[{ ...hydrogenCard, definition: 'HYDROGEN is the lightest gas. A chemical element.' }]}
        onExit={vi.fn()}
      />
    );

    expect(screen.getByRole('heading', { level: 2, name: /a chemical element/i })).toBeInTheDocument();
    expect(screen.queryByText(/hydrogen/i)).not.toBeInTheDocument();
  });

  it('allows a single retry on the first wrong answer (DP-B2 重试一次)', async () => {
    const user = userEvent.setup();
    render(<SpellingSession cards={spellingCards} onExit={vi.fn()} />);

    await user.type(screen.getByRole('textbox', { name: /type the word/i }), 'El Nnio');
    await user.click(screen.getByRole('button', { name: /check/i }));

    // 第一次答错：进入重试态，提示「再试一次」并保留输入，不揭示答案。
    expect(screen.getByTestId('spelling-retry-hint')).toHaveTextContent('Try again');
    expect(screen.queryByText(/Answer: El Nino/i)).not.toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: /type the word/i })).toHaveValue('El Nnio');

    // 重试答对：计为正确（不进错词列表）、不揭示完整释义，揭示词面。
    await user.clear(screen.getByRole('textbox', { name: /type the word/i }));
    await user.type(screen.getByRole('textbox', { name: /type the word/i }), 'El Nino');
    await user.click(screen.getByRole('button', { name: /check/i }));

    expect(screen.getByText(/correct/i)).toBeInTheDocument();
    expect(screen.getByText('El Nino')).toBeInTheDocument();
    // 重试后答对：进入「correct」态，主按钮变为「下一词」。
    expect(screen.getByRole('button', { name: /next word/i })).toBeInTheDocument();
  });

  it('reveals the answer only after two consecutive wrong attempts (DP-B2)', async () => {
    const user = userEvent.setup();
    render(<SpellingSession cards={spellingCards} onExit={vi.fn()} />);

    await user.type(screen.getByRole('textbox', { name: /type the word/i }), 'El Nnio');
    await user.click(screen.getByRole('button', { name: /check/i }));
    expect(screen.getByTestId('spelling-retry-hint')).toBeInTheDocument();

    // 第二次仍错：揭示正确答案 + 完整释义。
    await user.click(screen.getByRole('button', { name: /check/i }));

    expect(screen.getByText(/^Answer: El Nino$/)).toBeInTheDocument();
    expect(screen.getByTestId('spelling-full-definition')).toHaveTextContent(
      'a weather pattern that warms the eastern Pacific Ocean'
    );
    // 进入 revealed 态：主按钮「下一词」。
    expect(screen.getByRole('button', { name: /next word/i })).toBeInTheDocument();
  });

  it('only shows pronunciation after the spelling answer is revealed', async () => {
    const user = userEvent.setup();
    const onLookupPronunciation = vi.fn().mockResolvedValue({
      word: 'El Nino',
      ipa: '/ɛl ˈninjoʊ/',
      audioUrl: 'https://upload.wikimedia.org/el-nino.ogg',
      sourceUrl: 'https://en.wiktionary.org/wiki/El_Nino#English',
      audioSourceUrl: 'https://commons.wikimedia.org/wiki/File:el-nino.ogg',
      attribution: 'Wikimedia Commons contributor',
      license: 'CC BY-SA 4.0',
      licenseUrl: 'https://creativecommons.org/licenses/by-sa/4.0/',
      status: 'ready'
    });

    render(
      <SpellingSession cards={spellingCards} onExit={vi.fn()} onLookupPronunciation={onLookupPronunciation} />
    );

    expect(onLookupPronunciation).not.toHaveBeenCalled();
    await user.type(screen.getByRole('textbox', { name: /type the word/i }), 'El Nnio');
    await user.click(screen.getByRole('button', { name: /check/i }));
    expect(onLookupPronunciation).not.toHaveBeenCalled();
    // 第二次仍错：揭示后查发音。
    await user.click(screen.getByRole('button', { name: /check/i }));

    expect(await screen.findByText('/ɛl ˈninjoʊ/ US')).toBeInTheDocument();
    expect(onLookupPronunciation).toHaveBeenCalled();
  });

  it('returns to Today from the completion screen via 返回 Today (onExit)', async () => {
    const user = userEvent.setup();
    const onExit = vi.fn();
    render(<SpellingSession cards={[spellingCards[0]]} onExit={onExit} />);

    await user.type(screen.getByRole('textbox', { name: /type the word/i }), 'El Nino');
    await user.click(screen.getByRole('button', { name: /check/i }));
    await user.click(screen.getByRole('button', { name: /next word/i }));
    await user.click(screen.getByTestId('spelling-back-today'));

    expect(onExit).toHaveBeenCalledTimes(1);
  });

  it('counts spelling progress session-locally (X / N), never anchoring the day queue', async () => {
    // Bug 7684167107172388020：背完当日 55 卡进拼写时，旧锚定口径从
    // 56/55 起算（用户实测 57/55）。修正后拼写进度 = 会话自身进度，
    // 与当日队列 / dayProgress 无关，恒 ≤ N。
    const user = userEvent.setup();
    render(<SpellingSession cards={spellingCards} onExit={vi.fn()} />);

    expect(screen.getByText('1 / 2')).toBeInTheDocument();
    expect(screen.getByText('0 / 2 completed')).toBeInTheDocument();

    await user.type(screen.getByRole('textbox', { name: /type the word/i }), 'El Nino');
    await user.click(screen.getByRole('button', { name: /check/i }));
    await user.click(screen.getByRole('button', { name: /next word/i }));

    expect(screen.getByText('2 / 2')).toBeInTheDocument();
    expect(screen.getByText('1 / 2 completed')).toBeInTheDocument();
  });

  it('completes with stats + wrong-word list and offers 错词再来一组 to retry only wrong cards', async () => {
    const user = userEvent.setup();
    const onExit = vi.fn();
    render(<SpellingSession cards={spellingCards} onExit={onExit} />);

    // 第一词：两次都答错 → 计入错词。
    await user.type(screen.getByRole('textbox', { name: /type the word/i }), 'wrong');
    await user.click(screen.getByRole('button', { name: /check/i }));
    await user.click(screen.getByRole('button', { name: /check/i }));
    await user.click(screen.getByRole('button', { name: /next word/i }));

    // 第二词：首答对 → 计为正确，再点「下一词」进入完成态。
    await user.type(screen.getByRole('textbox', { name: /type the word/i }), 'carbon dioxide');
    await user.click(screen.getByRole('button', { name: /check/i }));
    await user.click(screen.getByRole('button', { name: /next word/i }));

    // 完成态：2 词 · 对 1 · 错 1，错误词列表含「El Nino」+ 释义首句。
    const summary = screen.getByTestId('spelling-summary');
    expect(summary).toHaveTextContent('2 words');
    expect(summary).toHaveTextContent('1 correct');
    expect(summary).toHaveTextContent('1 missed');
    const wrongList = screen.getByTestId('spelling-wrong-list');
    expect(within(wrongList).getByText('El Nino')).toBeInTheDocument();
    expect(within(wrongList).getByText((content) => content.includes('a weather pattern that warms the eastern Pacific Ocean'))).toBeInTheDocument();

    // 「错词再来一组」重启错词一轮（仅 1 词）。
    await user.click(screen.getByTestId('spelling-retry-wrong'));
    expect(screen.getByText('1 / 1')).toBeInTheDocument();
    expect(screen.getByRole('textbox', { name: /type the word/i })).toHaveValue('');
  });

  it('does not collapse the prompt to the single-line font fit (DP-B1 / F-08)', () => {
    const computedStyle = document.createElement('div').style;
    computedStyle.fontSize = '48px';
    vi.spyOn(window, 'getComputedStyle').mockReturnValue(computedStyle);
    vi.spyOn(Element.prototype, 'scrollWidth', 'get').mockReturnValue(900);
    vi.spyOn(Element.prototype, 'clientWidth', 'get').mockReturnValue(300);

    render(<SpellingSession cards={spellingCards} onExit={vi.fn()} />);

    // 重设计：主提示改为 h2，不再是 word-headline 单行适配。
    const prompt = screen.getByRole('heading', { level: 2, name: /a weather pattern that warms/i });
    expect(prompt.className).not.toContain('word-headline');
    expect(prompt.style.fontSize).toBe('');

    vi.restoreAllMocks();
  });
});
