import { useCallback, useEffect, useRef, useState } from 'react';
import type { CSSProperties } from 'react';
import QRCode from 'qrcode';
import {
  cancelOrder,
  createOrder,
  fetchLatestOrder,
  fetchSubscriptionMe,
  fetchSubscriptionPlans,
  setRenewReminder
} from '../../api';
import type {
  PaymentChannel,
  PaymentOrder,
  SubscriptionPlans,
  SubscriptionStatus,
  SubscriptionTier
} from '../../api';
import { formatPrice } from '../../api';
import { navigate } from '../../router';
import { Spinner, Toast, useFlash } from './shared';

// v3 P0 订阅页（V3-01/V3-02/V3-03）。整页数据驱动：
// - 四档价格全部来自 GET /api/subscription/plans（配置化，改价不发版），
//   通过 formatPrice() 渲染，文案不出现任何硬编码金额；
// - 状态卡按 trialing / active / expired 三态展示（PM 附则 2026-09-06；
//   expired 主行文案按设计定稿 2026-09-07 P2 #1 修订）：
//   trialing 显「试用剩余 X 天」；active 显「有效期至 X」+（续费窗口内）
//   2.99 优惠倒计时只读展示；expired 显「已于 X 到期 · 书架、进度与
//   统计仍可浏览」（过去时；expires_at 不可得或未来日期时降级为
//   「订阅已到期」不展示日期）+ 续费 CTA；
// - 收银台：选档 → 选渠道（微信扫码 / 支付宝跳转官方收银台）→ 下单 →
//   微信展示本地渲染的二维码（code_url → SVG）或支付宝跳转链接 +
//   15 分钟倒计时 + 「取消支付」（订单级），
//   3 秒轮询最新订单状态，支付成功即刷新状态卡；
// - 手动续费模式：无「取消订阅 / 恢复订阅」语义，管理侧唯一开关是
//   「续费提醒」toggle（默认开）；
// - 支付未配置（双通道密钥均未就绪）：显式提示 + 按钮置灰，其余可浏览；
//   单渠道未配置：渠道按钮置灰标注「暂未开通」。
// 全站文案硬约束：不出现「自动续费 / 连续包月 / 自动扣款」。

type SubscriptionViewProps = {
  onSubscriptionChange?: (status: SubscriptionStatus) => void;
};

type LoadState =
  | { phase: 'loading' }
  | { phase: 'error' }
  | { phase: 'ready'; plans: SubscriptionPlans; status: SubscriptionStatus };

type CheckoutState =
  | { phase: 'idle' }
  | { phase: 'selecting'; plan: string }
  | { phase: 'creating'; plan: string; channel: PaymentChannel }
  | { phase: 'paying'; order: PaymentOrder };

// CHANNELS drives the checkout channel picker: 渠道可用性来自 plans.channels
// （后端按 env/密钥逐渠道判定），未配置渠道置灰并说明原因。
const CHANNELS: Array<{
  id: PaymentChannel;
  name: string;
  hint: string;
}> = [
  { id: 'wechat', name: 'WeChat Pay', hint: 'Scan QR to pay' },
  { id: 'alipay', name: 'Alipay', hint: 'Opens the official checkout page' }
];

function channelLabel(channel: string): string {
  return channel === 'wechat' ? 'WeChat Pay' : channel === 'alipay' ? 'Alipay' : channel;
}

const POLL_INTERVAL_MS = 3000;

const BENEFITS = [
  'Sync your study progress across devices seamlessly',
  'Study data is stored in the cloud — never lost when switching devices',
  'Support ongoing development and get new features first'
];

// 四档标签（V3-02）。label 仅做档位命名，价格一律后端下发。
// 折扣标签（2026-09-10 拍板）：精确折扣、不带「约」——半年卡 21/30=7折、
// 年卡 30/60=5折；单月与 2.99 续费档不打折。
const TIER_META: Record<string, { name: string; note: string | null; primary: boolean }> = {
  monthly: { name: 'Monthly', note: null, primary: false },
  renew: { name: 'Renewal Offer', note: 'Within 7 days before or after expiry', primary: false },
  halfyear: { name: '6-Month', note: '30% off', primary: false },
  yearly: { name: '12-Month', note: '50% off', primary: true }
};

function formatExpiryDate(expiresAt: string | null): string {
  if (expiresAt === null) {
    // super 免订阅读路径：合成视图无到期时间。
    return 'Lifetime access';
  }
  const date = new Date(expiresAt);
  const year = date.getFullYear();
  const month = String(date.getMonth() + 1).padStart(2, '0');
  const day = String(date.getDate()).padStart(2, '0');
  return `Valid until ${year}-${month}-${day}`;
}

// P2 #1（设计定稿 2026-09-07）：到期只读态主行统一过去时「已于 X 到期」。
// expires_at 不可得（null / 非法日期）或取到未来日期（字段口径异常，如
// v2 mock 清退遗留的行带着未来 expires_at）时降级为不带日期的
// 「订阅已到期」。铁律：已到期状态绝不出现未来日期；日期与状态文案
// 之间必须有分隔（禁止「有效期至 2026-10-06到期」这类无分隔拼接）。
function formatExpiredLine(expiresAt: string | null): string {
  const suffix = ' — bookshelf, progress and stats remain viewable';
  if (expiresAt !== null) {
    const date = new Date(expiresAt);
    if (!Number.isNaN(date.getTime()) && date.getTime() <= Date.now()) {
      const year = date.getFullYear();
      const month = String(date.getMonth() + 1).padStart(2, '0');
      const day = String(date.getDate()).padStart(2, '0');
      return `Expired on ${year}-${month}-${day}${suffix}`;
    }
  }
  return `Your subscription has expired${suffix}`;
}

function formatAmountCents(amountCents: number): string {
  return `¥${(amountCents / 100).toFixed(2)}`;
}

function countdownSeconds(expiresAt: string | null, now: number): number | null {
  if (expiresAt === null) {
    return null;
  }
  const remaining = Math.floor((new Date(expiresAt).getTime() - now) / 1000);
  return remaining > 0 ? remaining : 0;
}

function formatCountdown(seconds: number): string {
  const minutes = Math.floor(seconds / 60);
  const rest = seconds % 60;
  return `${String(minutes).padStart(2, '0')}:${String(rest).padStart(2, '0')}`;
}

export function SubscriptionView({ onSubscriptionChange }: SubscriptionViewProps) {
  const [load, setLoad] = useState<LoadState>({ phase: 'loading' });
  const [checkout, setCheckout] = useState<CheckoutState>({ phase: 'idle' });
  const [isCancelingOrder, setIsCancelingOrder] = useState(false);
  const [isTogglingReminder, setIsTogglingReminder] = useState(false);
  const [toastMessage, showToast] = useFlash();
  const [nowMs, setNowMs] = useState(() => Date.now());
  // 收银台倒计时 + 轮询共享的 tick；Unmount 时清理。
  const pollTimer = useRef<number | null>(null);
  const mounted = useRef(true);
  // onSubscriptionChange 可能在轮询/重试时才被消费；ref 保证拿到最新回调。
  const onSubscriptionChangeRef = useRef(onSubscriptionChange);
  useEffect(() => {
    onSubscriptionChangeRef.current = onSubscriptionChange;
  });

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (pollTimer.current !== null) {
        window.clearInterval(pollTimer.current);
      }
    };
  }, []);

  const loadAll = useCallback(() => {
    setLoad({ phase: 'loading' });
    Promise.all([fetchSubscriptionPlans(), fetchSubscriptionMe()])
      .then(([plans, status]) => {
        if (mounted.current) {
          setLoad({ phase: 'ready', plans, status });
          onSubscriptionChangeRef.current?.(status);
        }
      })
      .catch(() => {
        // plans / me 任一失败 → 卡片内空态 + 重试；重试按钮一次重拉两者。
        if (mounted.current) {
          setLoad({ phase: 'error' });
        }
      });
  }, []);

  useEffect(() => {
    loadAll();
  }, [loadAll]);

  function applyStatus(next: SubscriptionStatus) {
    setLoad((current) =>
      current.phase === 'ready' ? { ...current, status: next } : current
    );
    onSubscriptionChangeRef.current?.(next);
  }

  // 收银台轮询（V3-03 兜底：回调可能先于用户刷新到达）。
  const pollLatest = useCallback(() => {
    fetchLatestOrder()
      .then(({ order, subscription }) => {
        if (!mounted.current) {
          return;
        }
        if (subscription.subscribed) {
          // 已入账：刷新状态卡，收银台收起。
          applyStatus(subscription);
          setCheckout({ phase: 'idle' });
          showToast('Payment successful. Full study features restored.');
          return;
        }
        setCheckout((current) => {
          if (current.phase !== 'paying') {
            return current;
          }
          if (order === null || order.status === 'closed' || order.status === 'failed') {
            showToast('Order closed. Please place a new order.');
            return { phase: 'idle' };
          }
          return { ...current, order };
        });
      })
      .catch(() => {
        // 单次轮询失败静默忽略，下一轮 tick 重试。
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  useEffect(() => {
    if (checkout.phase !== 'paying') {
      if (pollTimer.current !== null) {
        window.clearInterval(pollTimer.current);
        pollTimer.current = null;
      }
      return;
    }
    pollTimer.current = window.setInterval(() => {
      pollLatest();
      setNowMs(Date.now());
    }, POLL_INTERVAL_MS);
    return () => {
      if (pollTimer.current !== null) {
        window.clearInterval(pollTimer.current);
        pollTimer.current = null;
      }
    };
  }, [checkout.phase, pollLatest]);

  async function startCheckout(plan: string, channel: PaymentChannel): Promise<void> {
    if (load.phase !== 'ready' || checkout.phase === 'creating' || checkout.phase === 'paying') {
      return;
    }
    if (!load.plans.paymentEnabled) {
      showToast('Payment channels are not enabled yet; ordering is temporarily unavailable.');
      return;
    }
    if (!load.plans.channels[channel]) {
      showToast('This payment channel is not available. Please choose another one.');
      return;
    }
    setCheckout({ phase: 'creating', plan, channel });
    try {
      const order = await createOrder(plan, channel);
      if (!mounted.current) {
        return;
      }
      setNowMs(Date.now());
      setCheckout({ phase: 'paying', order });
    } catch (error) {
      if (!mounted.current) {
        return;
      }
      setCheckout({ phase: 'idle' });
      showToast(error instanceof Error ? error.message : 'Failed to place the order. Please try again later.');
    }
  }

  async function cancelCheckout(): Promise<void> {
    if (checkout.phase !== 'paying' || isCancelingOrder) {
      return;
    }
    setIsCancelingOrder(true);
    try {
      await cancelOrder(checkout.order.outTradeNo);
      if (mounted.current) {
        setCheckout({ phase: 'idle' });
      }
    } catch (error) {
      // 关单失败（网络抖动 / 订单已被超时关闭）：订单侧反正会过期，
      // 前端直接收起收银台并刷新一次状态。
      if (mounted.current) {
        setCheckout({ phase: 'idle' });
        pollLatest();
        showToast(error instanceof Error ? error.message : 'Order canceled');
      }
    } finally {
      if (mounted.current) {
        setIsCancelingOrder(false);
      }
    }
  }

  async function toggleReminder(next: boolean): Promise<void> {
    if (load.phase !== 'ready' || isTogglingReminder) {
      return;
    }
    setIsTogglingReminder(true);
    try {
      const status = await setRenewReminder(next);
      if (mounted.current) {
        applyStatus(status);
      }
    } catch {
      if (mounted.current) {
        showToast('Failed to save the setting. Please try again later.');
      }
    } finally {
      if (mounted.current) {
        setIsTogglingReminder(false);
      }
    }
  }

  if (load.phase === 'loading') {
    return (
      <main className="auth-page">
        <section className="auth-card subscription-card" aria-busy="true">
          <p className="eyebrow">SUBSCRIPTION</p>
          <h1 className="auth-title">Subscribe to Fusio</h1>
          <div className="subscription-price-skeleton" aria-hidden="true" />
          <div className="subscription-benefits-skeleton" aria-hidden="true">
            <span />
            <span />
            <span />
          </div>
          <button type="button" className="auth-cta" disabled>
            Pay Now
          </button>
        </section>
      </main>
    );
  }

  if (load.phase === 'error') {
    return (
      <main className="auth-page">
        <section className="auth-card subscription-card">
          <p className="eyebrow">SUBSCRIPTION</p>
          <h1 className="auth-title">Subscribe to Fusio</h1>
          <p className="subscription-empty">Failed to load subscription info.</p>
          <div className="subscription-empty-actions">
            <button type="button" className="auth-cta subscription-retry" onClick={loadAll}>
              Retry
            </button>
          </div>
        </section>
        <Toast message={toastMessage} />
      </main>
    );
  }

  const { plans, status } = load;

  // 未满足优惠资格的用户不展示续费优惠档（V3-02 交互规则 3：
  // 后端判定，不信任前端 — plans.renewEligible 即服务端判定结果）。
  const visibleTiers = plans.plans.filter(
    (tier) => tier.plan !== 'renew' || plans.renewEligible
  );
  // V3-09（2026-09-11 DP-1/DP-2/DP-3 拍板）：已充值 = me 的
  // hasActivePaidSubscription（服务端判定，前端只渲染不推导）。
  // 有效期内标价三档整体灰·禁用（文案「已订阅 · 有效期至 X」），
  // ¥2.99 续费档保留可点；「当前方案」角标仅挂当前持有档位那张卡。
  // 到期后 me 翻 expired → 字段变 False，三档恢复可点（灰态非永久）。
  const hasPaid = status.hasActivePaidSubscription === true;
  const grayLabel = `Subscribed · ${formatExpiryDate(status.expiresAt)}`;

  return (
    <main className="auth-page">
      <section className="auth-card subscription-card">
        <p className="eyebrow">SUBSCRIPTION</p>

        {status.subscribed || status.readOnly ? (
          // 状态卡（PM 附则：trialing / active / expired 三态）。
          <>
            <h1 className="auth-title">Subscription Status</h1>
            {status.status === 'trialing' ? (
              <>
                <span className="subscription-badge subscription-badge-trial" data-testid="subscription-badge">
                  Trial
                </span>
                <p className="subscription-expiry">
                  Trial: {status.trialDaysLeft ?? '—'} days left · {formatExpiryDate(status.expiresAt)}
                </p>
                {status.trialDaysLeft !== null && status.trialDaysLeft <= 3 ? (
                  <p className="subscription-trial-urgent" data-testid="trial-urgent">
                    Your trial is ending soon. Renew to keep all study progress.
                  </p>
                ) : null}
              </>
            ) : status.readOnly ? (
              <>
                <span className="subscription-badge subscription-badge-expired" data-testid="subscription-badge">
                  Expired · Read-only
                </span>
                <p className="subscription-expiry" data-testid="subscription-expiry">
                  {formatExpiredLine(status.expiresAt)}
                </p>
                {status.renewEligible && status.renewDeadline !== null ? (
                  <p className="subscription-renew-window">
                    Renewal price valid for {Math.max(0, Math.ceil((new Date(status.renewDeadline).getTime() - nowMs) / 86400000))} more days (within 7 days after expiry)
                  </p>
                ) : null}
              </>
            ) : status.subscribed ? (
              <>
                <span className="subscription-badge" data-testid="subscription-badge">
                  Subscription Active
                </span>
                <p className="subscription-expiry">{formatExpiryDate(status.expiresAt)}</p>
                {status.renewEligible && status.renewDeadline !== null ? (
                  <p className="subscription-renew-window">
                    Renewal offer ¥2.99: {Math.max(0, Math.ceil((new Date(status.renewDeadline).getTime() - nowMs) / 86400000))} days left
                  </p>
                ) : null}
              </>
            ) : null}
            {/* 续费提醒：管理侧唯一用户自主开关（默认开）。 */}
            <label className="subscription-reminder-toggle">
              <input
                type="checkbox"
                checked={status.renewReminder !== false}
                disabled={isTogglingReminder}
                onChange={(event) => {
                  void toggleReminder(event.target.checked);
                }}
              />
              <span>Remind me to renew before expiry</span>
            </label>
            {plans.paymentEnabled ? (
              <button
                type="button"
                className="auth-cta"
                onClick={() => {
                  setCheckout({ phase: 'idle' });
                  document
                    .getElementById('subscription-tiers')
                    ?.scrollIntoView({ behavior: 'smooth' });
                }}
              >
                Renew
              </button>
            ) : null}
          </>
        ) : null}

        {/* 价格四档（数据驱动，desktop 4 列 / mobile 2 列）。 */}
        <div id="subscription-tiers" className="subscription-tiers">
          <h2 className="subscription-tiers-title">
            {status.subscribed ? 'Choose a renewal plan' : 'Choose a plan'}
          </h2>
          {!plans.paymentEnabled ? (
            <p className="subscription-payment-disabled" data-testid="payment-disabled">
              Payment channels are not enabled yet (no gateway keys configured), so ordering is temporarily unavailable. Your bookshelf and existing progress are not affected.
            </p>
          ) : null}
          {/* 2026-09-10 对齐修复（设计师规格）：列数跟随实际渲染档位数注入
              --tier-count（renewEligible 账号 4 档、普通 3 档），移动端
              2 列布局由 CSS media query 覆盖，不写死。 */}
          <div
            className="subscription-tier-grid"
            data-testid="subscription-tier-grid"
            style={{ '--tier-count': visibleTiers.length } as CSSProperties}
          >
            {visibleTiers.map((tier) => (
              <TierCard
                key={tier.plan}
                tier={tier}
                currency={plans.currency}
                disabled={!plans.paymentEnabled || checkout.phase !== 'idle'}
                isPaying={checkout.phase === 'creating' && checkout.plan === tier.plan}
                grayed={hasPaid && tier.plan !== 'renew'}
                grayLabel={grayLabel}
                currentPlan={hasPaid && status.plan === tier.plan}
                onBuy={() => {
                  setCheckout({ phase: 'selecting', plan: tier.plan });
                }}
              />
            ))}
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
        </div>

        {/* 渠道选择（官方双通道：微信扫码 / 支付宝跳转收银台）。 */}
        {checkout.phase === 'selecting' ? (
          <div className="subscription-checkout" data-testid="subscription-channel-picker">
            <h2 className="subscription-checkout-title">Choose a payment method</h2>
            <div className="subscription-channel-grid">
              {CHANNELS.map((channel) => {
                const available = plans.channels[channel.id] ?? false;
                return (
                  <button
                    key={channel.id}
                    type="button"
                    className="subscription-channel-button"
                    data-testid={`channel-${channel.id}`}
                    disabled={!available}
                    onClick={() => {
                      void startCheckout(checkout.plan, channel.id);
                    }}
                  >
                    <span className="subscription-channel-name">{channel.name}</span>
                    <span className="subscription-channel-hint">
                      {available ? channel.hint : 'Not available yet'}
                    </span>
                  </button>
                );
              })}
            </div>
            <button
              type="button"
              className="auth-text-link"
              onClick={() => {
                setCheckout({ phase: 'idle' });
              }}
            >
              Back
            </button>
          </div>
        ) : null}

        {/* 收银台（订单级 15 分钟倒计时 + 渠道差异化展示 + 取消支付）。 */}
        {checkout.phase === 'paying' ? (
          <div className="subscription-checkout" data-testid="subscription-checkout">
            <h2 className="subscription-checkout-title">
              {checkout.order.channel === 'wechat' ? 'WeChat Pay (scan QR)' : 'Alipay'}
            </h2>
            <p className="subscription-checkout-amount">
              {formatAmountCents(checkout.order.amountCents)}
            </p>
            {checkout.order.channel === 'wechat' ? (
              <WechatQrCode content={checkout.order.payQrUrl} />
            ) : null}
            <p className="subscription-checkout-hint">
              {checkout.order.channel === 'wechat'
                ? 'Use WeChat to scan the QR code above; this page refreshes automatically once payment completes'
                : 'Click the button below to open the Alipay checkout; this page refreshes automatically after payment'}
            </p>
            {countdownSeconds(checkout.order.expiresAt, nowMs) !== null ? (
              <p className="subscription-checkout-countdown" data-testid="checkout-countdown">
                Order held for {formatCountdown(countdownSeconds(checkout.order.expiresAt, nowMs) ?? 0)}
              </p>
            ) : null}
            <div className="subscription-checkout-actions">
              {checkout.order.channel === 'alipay' && checkout.order.payUrl ? (
                <a
                  className="auth-cta"
                  href={checkout.order.payUrl}
                  target="_blank"
                  rel="noreferrer"
                >
                  Open Alipay Checkout
                </a>
              ) : null}
              <button
                type="button"
                className="auth-text-link"
                disabled={isCancelingOrder}
                onClick={() => {
                  void cancelCheckout();
                }}
              >
                {isCancelingOrder ? 'Canceling…' : 'Cancel Payment'}
              </button>
            </div>
            <p className="subscription-checkout-channel">
              Payment method: {channelLabel(checkout.order.channel)}
            </p>
          </div>
        ) : null}

        <button
          type="button"
          className="auth-text-link subscription-skip-link"
          onClick={() => navigate('/today')}
        >
          {status.readOnly ? 'Back to browsing (read-only)' : 'Not now — start studying'}
        </button>
      </section>
      <Toast message={toastMessage} />
    </main>
  );
}

// 微信 Native 支付的 code_url（weixin://wxpay/…）不是图片地址，二维码
// 由前端本地渲染：qrcode 库生成内联 SVG（无 canvas 依赖，Node/jsdom 与
// 浏览器行为一致），密钥/网关内容不经过任何第三方图片服务。
function WechatQrCode({ content }: { content: string | null }) {
  const [svg, setSvg] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    if (content === null || content === '') {
      setSvg(null);
      return () => {
        active = false;
      };
    }
    QRCode.toString(content, {
      type: 'svg',
      margin: 1,
      width: 200,
      errorCorrectionLevel: 'M'
    })
      .then((rendered) => {
        if (active) {
          setSvg(rendered);
        }
      })
      .catch(() => {
        if (active) {
          setSvg(null);
        }
      });
    return () => {
      active = false;
    };
  }, [content]);

  if (content === null || content === '') {
    return null;
  }
  if (svg === null) {
    return (
      <div className="subscription-checkout-qr subscription-checkout-qr-skeleton" aria-hidden="true" />
    );
  }
  return (
    <div
      className="subscription-checkout-qr"
      role="img"
      aria-label="WeChat Pay QR code"
      data-testid="wechat-qr"
      dangerouslySetInnerHTML={{ __html: svg }}
    />
  );
}

function TierCard({
  tier,
  currency,
  disabled,
  isPaying,
  grayed,
  grayLabel,
  currentPlan,
  onBuy
}: {
  tier: SubscriptionTier;
  currency: string;
  disabled: boolean;
  isPaying: boolean;
  grayed: boolean;
  grayLabel: string | null;
  currentPlan: boolean;
  onBuy: () => void;
}) {
  const meta = TIER_META[tier.plan] ?? { name: tier.plan, note: null, primary: false };
  const price = formatPrice(tier.priceCents, currency, tier.durationDays);
  return (
    <div
      className={`subscription-tier${meta.primary ? ' subscription-tier-primary' : ''}${
        grayed ? ' subscription-tier-grayed' : ''
      }`}
      data-testid={`subscription-tier-${tier.plan}`}
    >
      {currentPlan ? <p className="subscription-tier-current">Current plan</p> : null}
      <p className="subscription-tier-name">{meta.name}</p>
      <p className="subscription-price">
        <span className="subscription-price-currency">{price.currencySymbol}</span>
        <span className="subscription-price-integer">{price.integer}</span>
        <span className="subscription-price-fraction">{price.fraction}</span>
        <span className="subscription-price-period">{price.periodLabel}</span>
      </p>
      {meta.note ? <p className="subscription-tier-note">{meta.note}</p> : null}
      <button
        type="button"
        className={meta.primary ? 'auth-cta' : 'auth-ghost-cta'}
        disabled={disabled || isPaying || grayed}
        aria-disabled={grayed ? 'true' : undefined}
        data-testid={`tier-cta-${tier.plan}`}
        onClick={onBuy}
      >
        {grayed ? (
          <>
            {/* V3-09 灰态（DP-2/DP-3 拍板）：✓ 图标 + 「已订阅 · 有效期至 X」，
                不可点、不聚焦、不发下单请求；年卡绿边降灰边由 CSS 处理。 */}
            <svg
              className="subscription-tier-grayed-check"
              width="14"
              height="14"
              viewBox="0 0 14 14"
              fill="none"
              aria-hidden="true"
            >
              <path
                d="M2.5 7.5l3 3 6-6.5"
                stroke="#5b5347"
                strokeWidth="2"
                strokeLinecap="round"
                strokeLinejoin="round"
              />
            </svg>
            {grayLabel}
          </>
        ) : isPaying ? (
          <>
            <Spinner /> Placing order…
          </>
        ) : (
          'Pay Now'
        )}
      </button>
    </div>
  );
}
