import { render, screen, waitFor } from '@testing-library/react';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { App } from './App';
import { localDateString, mergedCheckInsFor, type CheckInRecord } from './checkins';

// P1 2026-09-08 打卡热点图服务端化（task 7683154325467565322）：
// 修复「手机背完 → 记录只在手机浏览器 → 电脑热点图看不到今天」。
// 覆盖三条主路径：
// 1. mount 时 GET /api/check-ins → 服务端派生的打卡渲染进热点图；
// 2. localStorage 有历史且未上报过 → 一次性 POST /api/check-ins/merge，
//    成功后写按账号的 merge 标记，不再重复上报；
// 3. 服务端不可用 → 回退 localStorage 快照，页面照常可用。

function currentBookResponse() {
  return {
    ok: true,
    text: () =>
      Promise.resolve(
        JSON.stringify({
          id: 'book-default',
          title: '雅思词汇真经',
          description: null,
          source: 'book_words.csv',
          createdAt: '2026-07-01T00:00:00Z',
          updatedAt: '2026-07-01T00:00:00Z',
          totalWords: 3383
        })
      )
  };
}

function emptyDaySummaryResponse() {
  return {
    ok: true,
    text: () =>
      Promise.resolve(
        JSON.stringify({
          studyDate: '2026-09-08',
          totalCards: 0,
          reviewedCards: 0,
          dayCompleted: false,
          completedCards: []
        })
      )
  };
}

function checkInsResponse(records: unknown[]) {
  return {
    ok: true,
    text: () => Promise.resolve(JSON.stringify({ checkIns: records }))
  };
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

function localCheckIn(dayOffset: number, completed: number): CheckInRecord {
  const date = new Date();
  date.setDate(date.getDate() + dayOffset);
  return {
    date: localDateString(date),
    completedCards: completed,
    newCards: completed,
    reviewCards: 0,
    completedAt: `${localDateString(date)}T21:00:00.000Z`
  };
}

function setLocalStorageRecords(records: CheckInRecord[]) {
  window.localStorage.setItem('vocabulary-learning-check-ins', JSON.stringify(records));
}

describe('App check-in server hydration', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    window.localStorage.clear();
  });

  it('renders server-derived check-ins on mount (cross-device sync)', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(currentBookResponse())
      .mockResolvedValueOnce(emptyDaySummaryResponse())
      .mockResolvedValueOnce(
        checkInsResponse([
          serverCheckIn(-2, 12, 8),
          serverCheckIn(-1, 5, 5),
          serverCheckIn(0, 21, 20)
        ])
      );
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    // 服务端返回 3 天打卡 → 热点图按服务端数据渲染。
    expect(await screen.findByText('3 checked-in days')).toBeInTheDocument();

    const getCalls = fetchMock.mock.calls.filter(
      (call) => typeof call[0] === 'string' && call[0] === '/api/check-ins'
    );
    expect(getCalls).toHaveLength(1);
  });

  it('uploads local storage history once via POST /api/check-ins/merge', async () => {
    setLocalStorageRecords([localCheckIn(-30, 6), localCheckIn(-29, 4)]);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(currentBookResponse())
      .mockResolvedValueOnce(emptyDaySummaryResponse())
      // mount：本地有历史且未上报 → merge POST，返回合并后列表。
      .mockResolvedValueOnce(
        checkInsResponse([serverCheckIn(-30, 6, 6), serverCheckIn(0, 3, 3)])
      );
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    expect(await screen.findByText('2 checked-in days')).toBeInTheDocument();

    await waitFor(() => {
      const mergeCall = fetchMock.mock.calls.find(
        (call) =>
          typeof call[0] === 'string' &&
          call[0] === '/api/check-ins/merge' &&
          typeof call[1] === 'object' &&
          (call[1] as RequestInit).method === 'POST'
      );
      expect(mergeCall).toBeDefined();
      expect(JSON.parse(String((mergeCall?.[1] as RequestInit).body))).toEqual({
        checkIns: [localCheckIn(-30, 6), localCheckIn(-29, 4)]
      });
    });

    // 上报成功后写入按账号的 merge 标记（App 未传 userEmail → 匿名域）。
    expect(mergedCheckInsFor('anonymous')).toBe(true);
    // 没有再发 GET（merge 的返回值直接整表替换）。
    const getCalls = fetchMock.mock.calls.filter(
      (call) => typeof call[0] === 'string' && call[0] === '/api/check-ins'
    );
    expect(getCalls).toHaveLength(0);
  });

  it('re-uploads for a different account after switching (merge flag is per-account)', async () => {
    setLocalStorageRecords([localCheckIn(-30, 6)]);
    // 上一个账号已上报过 —— 换账号后必须重新触发 merge。
    window.localStorage.setItem(
      'vocabulary-learning-check-ins-merged-for',
      'previous@example.com'
    );
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(currentBookResponse())
      .mockResolvedValueOnce(emptyDaySummaryResponse())
      .mockResolvedValueOnce(checkInsResponse([serverCheckIn(0, 2, 2)]));
    vi.stubGlobal('fetch', fetchMock);

    render(<App userEmail="next@example.com" />);

    expect(await screen.findByText('1 checked-in days')).toBeInTheDocument();
    const mergeCalls = fetchMock.mock.calls.filter(
      (call) => typeof call[0] === 'string' && call[0] === '/api/check-ins/merge'
    );
    expect(mergeCalls).toHaveLength(1);
  });

  it('falls back to the localStorage snapshot when the server is unreachable', async () => {
    setLocalStorageRecords([localCheckIn(-1, 4), localCheckIn(0, 9)]);
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(currentBookResponse())
      .mockResolvedValueOnce(emptyDaySummaryResponse())
      // GET /api/check-ins 失败（服务端不可用）。
      .mockRejectedValueOnce(new Error('network down'));
    vi.stubGlobal('fetch', fetchMock);

    render(<App />);

    // 离线回退：localStorage 快照照常渲染。
    expect(await screen.findByText('2 checked-in days')).toBeInTheDocument();
    // merge 标记未写入（失败不吞掉重试机会）。
    expect(mergedCheckInsFor('anonymous')).toBe(false);
  });
});
