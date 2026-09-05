import { useCallback, useEffect, useState } from 'react';
import {
  cancelSubscription,
  createMockOrder,
  fetchSubscriptionMe,
  fetchSubscriptionPlan
} from '../../api';
import type { SubscriptionPlan, SubscriptionStatus } from '../../api';
import { formatPrice } from '../../api';
import { navigate } from '../../router';
import { Spinner, Toast, useFlash } from './shared';

// C-10: the subscription page. The whole card is data-driven — the
// price comes from GET /api/subscription/plan and renders through
// formatPrice(); no amount is ever hardcoded in copy, so a config
// change from 0.1 to 4.99 requires zero visual edits. Subscribed
// visitors see the status card (badge + expiry + mock cancel), and
// expired visitors get the non-modal renewal hint above the price.
// Nothing here gates study features — subscription is display-only
// (2026-09-05 拍板) and sends no email at any point.

type SubscriptionViewProps = {
  onSubscriptionChange?: (status: SubscriptionStatus) => void;
};

type LoadState =
  | { phase: 'loading' }
  | { phase: 'error' }
  | { phase: 'ready'; plan: SubscriptionPlan; status: SubscriptionStatus };

const BENEFITS = [
  '云端同步学习进度，多设备无缝衔接',
  '学习数据云端保存，换设备不丢失',
  '支持项目持续开发，优先获得新功能'
];

function formatExpiryDate(expiresAt: string | null): string {
  if (expiresAt === null) {
    // super 免订阅读路径：合成视图无到期时间。
    return '长期有效';
  }
  const date = new Date(expiresAt);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `有效期至 ${year}-${month}-${day}`;
}

export function SubscriptionView({ onSubscriptionChange }: SubscriptionViewProps) {
  const [load, setLoad] = useState<LoadState>({ phase: 'loading' });
  const [isSubscribing, setIsSubscribing] = useState(false);
  const [isCanceling, setIsCanceling] = useState(false);
  const [toastMessage, showToast] = useFlash();

  const loadAll = useCallback(() => {
    setLoad({ phase: 'loading' });
    Promise.all([fetchSubscriptionPlan(), fetchSubscriptionMe()])
      .then(([plan, status]) => {
        setLoad({ phase: 'ready', plan, status });
      })
      .catch(() => {
        // plan 拉取失败 → 卡片内空态 + 重试；me 失败也按整体失败
        // 处理，重试按钮一次重拉两者。
        setLoad({ phase: 'error' });
      });
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  function applyStatus(next: SubscriptionStatus) {
    setLoad((current) =>
      current.phase === 'ready' ? { ...current, status: next } : current
    );
    onSubscriptionChange?.(next);
  }

  async function subscribe(): Promise<void> {
    if (load.phase !== 'ready' || isSubscribing || load.status.subscribed) {
      return;
    }
    setIsSubscribing(true);
    try {
      const next = await createMockOrder();
      applyStatus(next);
      showToast('订阅成功');
    } catch {
      // mock 下单失败：保持价格卡，toast 提示稍后重试。
      showToast('订阅失败，请稍后重试');
    } finally {
      setIsSubscribing(false);
    }
  }

  async function cancel(): Promise<void> {
    if (load.phase !== 'ready' || isCanceling) {
      return;
    }
    setIsCanceling(true);
    try {
      const next = await cancelSubscription();
      applyStatus(next);
      showToast('已取消订阅（模拟）');
    } catch {
      showToast('取消失败，请稍后重试');
    } finally {
      setIsCanceling(false);
    }
  }

  if (load.phase === 'loading') {
    return (
      <main className="auth-page">
        <section className="auth-card subscription-card" aria-busy="true">
          <p className="eyebrow">SUBSCRIPTION</p>
          <h1 className="auth-title">开通订阅</h1>
          <div className="subscription-price-skeleton" aria-hidden="true" />
          <div className="subscription-benefits-skeleton" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
          <button type="button" className="auth-cta" disabled>
            立即订阅
          </button>
          <p className="subscription-mock-note">模拟订阅，不会产生真实扣款</p>
        </section>
      </main>
    );
  }

  if (load.phase === 'error') {
    return (
      <main className="auth-page">
        <section className="auth-card subscription-card">
          <p className="eyebrow">SUBSCRIPTION</p>
          <h1 className="auth-title">开通订阅</h1>
          <p className="subscription-empty">订阅信息加载失败</p>
          <div className="subscription-empty-actions">
            <button type="button" className="auth-cta subscription-retry" onClick={loadAll}>
              重试
            </button>
          </div>
        </section>
        <Toast message={toastMessage} />
      </main>
    );
  }

  const { plan, status } = load;
  const price = formatPrice(plan);

  return (
    <main className="auth-page">
      <section className="auth-card subscription-card">
        <p className="eyebrow">SUBSCRIPTION</p>
        {status.subscribed ? (
          // 已订阅：整卡切订阅状态卡（badge + 有效期 + ghost 取消）。
          <>
            <h1 className="auth-title">订阅状态</h1>
            <span className="subscription-badge" data-testid="subscription-badge">
              订阅高
            </span>
            <p className="subscription-expiry">{formatExpiryDate(status.expiresAt)}</p>
            <button
              type="button"
              className="auth-ghost-cta"
              disabled={isCanceling}
              onClick={() => {
                void cancel();
              }}
            >
              {isCanceling ? (
                <>
                  <Spinner /> 取消中…
                </>
              ) : (
                '取消订阅（模拟）'
              )}
            </button>
          </>
        ) : (
          // 未订阅 / 已过期：价格卡（价格只在价格面板出现一次）。
          <>
            <h1 className="auth-title">开通订阅</h1>
            {status.status === 'expired' ? (
              <p className="subscription-expired-notice">订阅已过期，续订后恢复云同步</p>
            ) : null}
            <div className="subscription-price">
              <span className="subscription-price-currency">{price.currencySymbol}</span>
              <span className="subscription-price-integer">{price.integer}</span>
              <span className="subscription-price-fraction">{price.fraction}</span>
              <span className="subscription-price-period">{price.periodLabel}</span>
            </div>
            <ul className="subscription-benefits">
              {BENEFITS.map((benefit) => (
                <li key={benefit}>
                  <svg
                    className="subscription-benefit-check"
                    width="18"
                    height="18"
                    viewBox="0 0 18 18"
                    fill="none"
                    aria-hidden="true"
                  >
                    <path
                      d="M3.5 9.5l3.5 3.5 7.5-7.5"
                      stroke="#6f8b79"
                      strokeWidth="2"
                      strokeLinecap="round"
                      strokeLinejoin="round"
                    />
                  </svg>
                  <span>{benefit}</span>
                </li>
              ))}
            </ul>
            <button
              type="button"
              className="auth-cta"
              disabled={isSubscribing}
              onClick={() => {
                void subscribe();
              }}
            >
              {isSubscribing ? (
                <>
                  <Spinner /> 订阅中…
                </>
              ) : (
                '立即订阅'
              )}
            </button>
            <p className="subscription-mock-note">模拟订阅，不会产生真实扣款</p>
          </>
        )}
        <button
          type="button"
          className="auth-text-link subscription-skip-link"
          onClick={() => navigate('/today')}
        >
          暂不订阅，先去背单词
        </button>
      </section>
      <Toast message={toastMessage} />
    </main>
  );
}
