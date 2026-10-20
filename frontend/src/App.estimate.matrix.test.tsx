import { render, screen } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { localDateString, type CheckInRecord } from './checkins';

// P1 复现矩阵：账号有无打卡数据 × 今日未完成/已完成，
// 断言 Today 封面卡主位预估（DP-A3）四态都有值。

function jsonResponse(payload: unknown) {
  return { ok: true, text: () => Promise.resolve(JSON.stringify(payload)) };
}

function daySummary(dayCompleted: boolean) {
  return jsonResponse({
    studyDate: localDateString(new Date()),
    totalCards: 32,
    reviewedCards: dayCompleted ? 32 : 12,
    dayCompleted,
    completedCards: dayCompleted
      ? [{ word: 'apple' }, { word: 'banana' }]
      : []
  });
}

function serverCheckIn(dayOffset: number, completed: number, newWords: number): CheckInRecord {
  const date = new Date();
  date.setDate(date.getDate() + dayOffset);
  return {
    date: localDateString(date),
    completedCards: completed,
    newCards: newWords,
    reviewCards: completed - newWords,
    completedAt: `${localDateString(date)}T21:00:00.000Z`
  };
}

function setupFetch(opts: {
  checkIns: CheckInRecord[];
  dayCompleted: boolean;
  failCurrentBook?: boolean;
}) {
  return vi.fn((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.startsWith('/api/books/current')) {
      if (opts.failCurrentBook) {
        // P1 2026-09-09 线上走查根因：/api/books/current 瞬时失败
        // （超时/5xx）时旧实现静默吞错，封面卡整体不渲染。
        return Promise.reject(new Error('books/current unavailable'));
      }
      // 与真实后端 BookSummaryResponse 同形：totalWords + learnedWords 都有。
      return Promise.resolve(
        jsonResponse({
          id: 'book-default',
          title: '雅思词汇真经',
          description: null,
          source: 'book_words.csv',
          createdAt: '2026-07-01T00:00:00Z',
          updatedAt: '2026-07-01T00:00:00Z',
          totalWords: 3383,
          learnedWords: 21,
          masteredWords: 0,
          fallbackNotice: null
        })
      );
    }
    if (url.startsWith('/api/books')) {
      // 书架列表端点：QA 走查时书架辅位正常，说明该端点可用，
      // 是修复后的回退数据源。列表项与 current 同形（含
      // totalWords/learnedWords）+ isCurrent 标记。
      return Promise.resolve(
        jsonResponse({
          books: [
            {
              id: 'book-default',
              title: '雅思词汇真经',
              description: null,
              source: 'book_words.csv',
              createdAt: '2026-07-01T00:00:00Z',
              updatedAt: '2026-07-01T00:00:00Z',
              totalWords: 3383,
              learnedWords: 21,
              masteredWords: 0,
              fallbackNotice: null,
              isCurrent: true
            }
          ]
        })
      );
    }
    if (url.startsWith('/api/study/today/summary')) {
      return Promise.resolve(daySummary(opts.dayCompleted));
    }
    if (url.startsWith('/api/check-ins')) {
      return Promise.resolve(jsonResponse({ checkIns: opts.checkIns }));
    }
    return Promise.resolve(jsonResponse({}));
  });
}

describe('Today 封面卡主位预估 · 四态复现矩阵', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('无打卡数据 × 今日未完成 → 主位有预估', async () => {
    vi.stubGlobal('fetch', setupFetch({ checkIns: [], dayCompleted: false }));
    render(<App />);
    expect(await screen.findByTestId('book-cover-estimate')).toBeInTheDocument();
  });

  it('无打卡数据 × 今日已完成 → 主位有预估', async () => {
    vi.stubGlobal('fetch', setupFetch({ checkIns: [], dayCompleted: true }));
    render(<App />);
    expect(await screen.findByTestId('book-cover-estimate')).toBeInTheDocument();
  });

  it('有服务端打卡数据 × 今日未完成 → 主位有预估', async () => {
    vi.stubGlobal(
      'fetch',
      setupFetch({
        checkIns: [
          serverCheckIn(-3, 12, 8),
          serverCheckIn(-2, 32, 20),
          serverCheckIn(-1, 5, 5),
          serverCheckIn(0, 21, 20)
        ],
        dayCompleted: false
      })
    );
    render(<App />);
    expect(await screen.findByTestId('book-cover-estimate')).toBeInTheDocument();
  });

  it('有服务端打卡数据 × 今日已完成 → 主位有预估', async () => {
    vi.stubGlobal(
      'fetch',
      setupFetch({
        checkIns: [
          serverCheckIn(-3, 12, 8),
          serverCheckIn(-2, 32, 20),
          serverCheckIn(-1, 5, 5),
          serverCheckIn(0, 21, 20)
        ],
        dayCompleted: true
      })
    );
    render(<App />);
    expect(await screen.findByTestId('book-cover-estimate')).toBeInTheDocument();
  });
});

describe('Today 封面卡主位预估 · books/current 失败回退', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  // P1 2026-09-09 线上（cloud@d3d2da8）走查症状：书架辅位正常、
  // Today 封面卡主位缺失。根因：/api/books/current 瞬时失败被
  // 静默吞掉，bookTitle/totalWords/learnedWords 保持 null，封面卡
  // 整体不渲染。修复：失败时回退到 /api/books 列表的 isCurrent 项。
  it.each([
    { dayCompleted: false, label: '今日未完成' },
    { dayCompleted: true, label: '今日已完成' }
  ])('books/current 失败 × $label → 仍用书架列表回退渲染主位预估', async ({ dayCompleted }) => {
    vi.stubGlobal(
      'fetch',
      setupFetch({
        checkIns: [
          serverCheckIn(-2, 32, 20),
          serverCheckIn(-1, 5, 5),
          serverCheckIn(0, 21, 20)
        ],
        dayCompleted,
        failCurrentBook: true
      })
    );
    render(<App />);
    expect(await screen.findByTestId('book-cover-estimate')).toBeInTheDocument();
    expect(await screen.findByTestId('book-cover-card')).toBeInTheDocument();
  });

  it('books/current 与 books 列表都失败 → 不抛错、页面保持可用（预估不渲染但不崩溃）', async () => {
    const fetchMock = vi.fn((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.startsWith('/api/books')) {
        return Promise.reject(new Error('backend unavailable'));
      }
      return setupFetch({ checkIns: [], dayCompleted: false })(input);
    });
    vi.stubGlobal('fetch', fetchMock);
    render(<App />);
    // Today 页主内容仍可渲染（desk-panel 存在），封面卡优雅缺失。
    expect(
      await screen.findByText('Ready for today', { exact: false })
    ).toBeInTheDocument();
  });
});
