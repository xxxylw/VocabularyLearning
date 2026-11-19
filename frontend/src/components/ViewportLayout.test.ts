import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';
import { describe, expect, it } from 'vitest';

// 2026-11-12 移动端体验改造（任务 7707179893435468795）：学习/复习页
// 评分按钮（Got it / Maybe / New）与拼写页提交/下一词按钮必须恒定
// 可达 —— 单屏布局：视口高度固定（100dvh）、页面不滚动、卡片内容在
// 内框滚动、按钮区钉在视口底部。jsdom 没有布局引擎，按仓库既有先例
// （WordHeadline.test.tsx 的 CSS 契约测试）直接读线上会真实发布的
// styles.css / index.html，守护这些布局契约不被回退。
const here = dirname(fileURLToPath(import.meta.url));
const stylesCss = readFileSync(resolve(here, '../styles.css'), 'utf-8');
const indexHtml = readFileSync(resolve(here, '../../index.html'), 'utf-8');

function escapeRegExp(value: string): string {
  return value.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

/** All declaration blocks ever written for `selector` (base rules first,
 * media-query overrides afterwards, in file order). Also matches the first
 * selector of a comma group (`sel, other { ... }`) — the optional group
 * continuation is bounded by `[^{};]+` so it can never cross into another
 * rule's block. */
function ruleBlocks(selector: string): string[] {
  const pattern = new RegExp(
    `(^|[\\n,}\\s])${escapeRegExp(selector)}(\\s*,\\s*[^{};]+)?\\s*\\{([^}]*)\\}`,
    'g',
  );
  const blocks: string[] = [];
  let match: RegExpExecArray | null;
  while ((match = pattern.exec(stylesCss)) !== null) {
    blocks.push(match[3]);
  }
  return blocks;
}

/** First (base) declaration block for `selector`. */
function baseRule(selector: string): string {
  const blocks = ruleBlocks(selector);
  if (blocks.length === 0) {
    throw new Error(`Selector ${selector} not found in styles.css`);
  }
  return blocks[0];
}

describe('viewport-pinned study layout (CSS contract)', () => {
  it('locks the study shell to the dynamic viewport height so the page itself never scrolls', () => {
    const shell = baseRule('.study-shell');
    expect(shell).toContain('height: 100vh');
    expect(shell).toContain('height: 100dvh');
    expect(shell).toContain('overflow: hidden');
    expect(shell).toContain('minmax(0, 1fr)');
  });

  // 2026-11-12 续补：shell 与 ICP 备案页脚是 #root 下的兄弟节点，若只
  // 锁 shell 高度，页脚会把 scrollingElement 顶出 ~42px（手机上仍能整
  // 页滚动）。#root 必须在 study/spelling 屏变成 100dvh flex 列，页脚
  // 收进视口、shell 吃剩余高度。
  it('pulls the ICP footer into the locked viewport via a #root flex column on study screens', () => {
    const root = baseRule('#root:has(> .study-shell)');
    expect(root).toContain('display: flex');
    expect(root).toContain('flex-direction: column');
    expect(root).toContain('height: 100dvh');
    expect(root).toContain('overflow: hidden');

    const spellingRoot = baseRule('#root:has(> .spelling-shell)');
    expect(spellingRoot).toContain('height: 100dvh');

    const shellInFlex = baseRule('#root:has(> .study-shell) > .study-shell');
    expect(shellInFlex).toContain('flex: 1 1 auto');
    expect(shellInFlex).toContain('min-height: 0');
    expect(shellInFlex).toContain('height: auto');

    const footerInFlex = baseRule('#root:has(> .study-shell) > .icp-footer');
    expect(footerInFlex).toContain('flex: 0 0 auto');
  });

  it('reserves the iOS safe-area inset on the study shell bottom padding', () => {
    expect(baseRule('.study-shell')).toContain('env(safe-area-inset-bottom');
    expect(baseRule('.spelling-shell')).toContain('env(safe-area-inset-bottom');
  });

  it('stretches the study card to fill the shell so the rating row can sit at the viewport bottom', () => {
    const card = baseRule('.study-card');
    expect(card).toContain('align-self: stretch');
    expect(card).toContain('flex-direction: column');
  });

  it('scrolls long sense lists inside the card instead of the page', () => {
    const list = baseRule('.sense-list');
    expect(list).toContain('overflow-y: auto');
    expect(list).toContain('min-height: 0');
  });

  it('keeps the rating row out of the inner scroll and pinned at the card bottom', () => {
    const row = baseRule('.rating-row');
    expect(row).toContain('flex: 0 0 auto');
  });

  it('never stacks the three rating buttons into one column on phones (they stay in one bottom row)', () => {
    for (const block of ruleBlocks('.rating-row')) {
      expect(block).not.toContain('grid-template-columns: 1fr');
    }
  });

  it('locks the spelling shell to the dynamic viewport height as well', () => {
    const shell = baseRule('.spelling-shell');
    expect(shell).toContain('height: 100dvh');
    expect(shell).toContain('overflow: hidden');
  });

  it('pins the spelling action row (Check / Next word) to the answer-panel bottom', () => {
    expect(baseRule('.spelling-actions')).toContain('margin-top: auto');
    expect(baseRule('.spelling-answer-panel')).toContain('overflow-y: auto');
  });

  it('lets long spelling prompts scroll in place instead of pushing the input below the fold', () => {
    const prompt = baseRule('.spelling-prompt');
    expect(prompt).toContain('overflow-y: auto');
    expect(prompt).toContain('min-height: 0');
  });

  it('enables the safe-area inset via viewport-fit=cover in the HTML viewport meta', () => {
    expect(indexHtml).toMatch(/<meta\s+name="viewport"[^>]*viewport-fit=cover/);
  });
});
