# 云端部署交付物（C-11）：部署说明与 CSP 配置样例

## 一图看懂 · 云端部署总览

> 部署总览图（飞书画板）见云文档原件：https://flexivrobotics.feishu.cn/docx/ULwad7yYfoIfMHx9vmQcWgaon6f

> 适用版本：v2 云端版（三批已于 2026-09-06 全部上线）。本文是 C-11 遗留挂账的收口交付物，覆盖云端版当前线上形态的部署清单、升级 / 回滚流程，以及可直接套用的 Content-Security-Policy 配置样例。单机版口径见 docs/windows_packaging.md，两者互不替代。
> 
> 依据：cloud 分支 commit `1786b4a`（2026-09-11）源码核对；dist 产物 `index-Dfyyslxr.js` 与当前线上 bundle 一致，证明该分支即线上基线。文中标注「待线上核对」的项（服务器 IP、systemd 单元名等来自运维背景信息，非代码可证）。

## 一、当前架构（文字版）

```text
用户浏览器（React 19 SPA，URL hash 路由）
  │  http://124.223.10.115（腾讯云，HTTP，域名/ICP 未定）
  ▼
nginx（:80，静态托管 + 反向代理）
  ├─ /            → frontend/dist 静态资源（当前 bundle：assets/index-Dfyyslxr.js）
  └─ /api/*       → 127.0.0.1:8000
                      │
                      ▼
FastAPI（systemd vocab.service，单进程 uvicorn）
  ├─ 存储：SQLite 单文件（VOCAB_DB_PATH，默认 ./data/vocabulary.sqlite，WAL 模式）
  ├─ 出站：api.brevo.com          — 邮件发送（验证码 / 重置码，API v3）
  ├─ 出站：en.wiktionary.org      — 发音词条 wikitext
  ├─ 出站：commons.wikimedia.org  — 发音音频元数据（文件 URL / 许可信息）
  └─ 出站：api.mch.weixin.qq.com / openapi.alipay.com — 支付下单、验签、回调

浏览器直连（不经过服务器）：
  · upload.wikimedia.org —— 发音 <audio> 播放地址（Commons 托管 ogg）
  · 支付宝收银台 —— payUrl 以 <a href> 链接跳转（CSP 不拦截普通链接导航）
```

要点说明：

- **前后端同源**：前端所有请求都是相对路径 `/api/*`（frontend/src/api.ts），无跨域 fetch；开发期 Vite proxy 也指向同一后端端口 8000，云端由 nginx `/api` 反代完成同样的同源合并。
- **SPA 路由**：应用按 URL hash 路由；`/login`、`/verify-email` 等 7 个路径形态入口由后端 301 到 `#/` 形态（main.py spa_path_redirect）。云端静态由 nginx 托管时，这 7 个路径需反代到后端才能复用该 301（见 nginx 配置要点）。
- **支付**：微信 Native 返回 code_url，前端用 qrcode 库在本地 canvas 渲染二维码（无外联）；支付宝返回 payUrl，前端渲染为 `<a href>` 跳转（SubscriptionView.tsx）。
- **邮件**：Brevo 仅是服务端发信通道；邮件内容是 6 位数字验证码，**不含任何跳转链接**（emailing.py，2026-09 C-01a 改版），因此前端 CSP 与 Brevo 无关。

## 二、CSP 配置样例

### 2.1 直接可用的策略头

覆盖当前前端实际用到的资源形态（依据 cloud 分支源码，非猜测）：

```text
default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; media-src 'self' https://upload.wikimedia.org; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'
```

### 2.2 挂法一：nginx（推荐，全站生效）

```nginx
# http 级或 server 级；always 保证 4xx/5xx 响应也带头
server {
    listen 80;
    server_name 124.223.10.115;

    root /var/www/vocab/dist;          # frontend/dist 部署目标
    index index.html;

    # SPA 静态资源
    location / {
        try_files $uri $uri/ =404;
        add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; media-src 'self' https://upload.wikimedia.org; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'" always;
    }

    # API 反代
    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        add_header Content-Security-Policy "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; font-src 'self'; connect-src 'self'; media-src 'self' https://upload.wikimedia.org; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'" always;
    }

    # 7 个 SPA 路径形态入口复用后端 301 → #/ 形态
    location ~ ^/(login|register|check-email|forgot-password|reset-password|verify-email|subscription)$ {
        proxy_pass http://127.0.0.1:8000;
    }
}
```

（示例只含 CSP 相关与路由要点；gzip、日志、限流等按机器现状保留。）

### 2.3 挂法二：后端中间件（备选）

适用于后端直接托管静态（VOCAB_STATIC_DIR 打包模式）或希望 API 响应也带头的场景。在 `backend/app/main.py` 的 `create_app()` 内加：

```python
CSP_POLICY = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data:; "
    "font-src 'self'; "
    "connect-src 'self'; "
    "media-src 'self' https://upload.wikimedia.org; "
    "object-src 'none'; "
    "base-uri 'none'; "
    "frame-ancestors 'none'; "
    "form-action 'self'"
)

@app.middleware("http")
async def add_csp_header(request: Request, call_next):
    response = await call_next(request)
    response.headers["Content-Security-Policy"] = CSP_POLICY
    return response
```

云端当前架构（nginx 托管静态）下推荐挂法一；两种挂法二选一即可，同时挂也不会冲突（后加的头覆盖），但没必要。

### 2.4 逐条指令的理由

| 指令 | 取值 | 理由（代码实据） |
|-|-|-|
| default-src | 'self' | 兜底收紧；站点无任何第三方页面资源依赖 |
| script-src | 'self' | index.html 只引 `/src/main.tsx`，构建后为 `/assets/index-*.js` 自托管 bundle；无 CDN、无内联脚本 |
| style-src | 'self' 'unsafe-inline' | 组件用 style 属性动态设置进度条宽度（StudySession.tsx / SpellingSession.tsx 的 progress-bar-fill），必须放行内联 style；无外部样式表 |
| img-src | 'self' data: | 站内无外部图片；data: 为 qrcode 等本地渲染兜底 |
| font-src | 'self' | styles.css 无 @font-face，无 webfont；保留指令防止未来误引外部字体 |
| connect-src | 'self' | 全部 fetch 均为相对路径 `/api/*`（api.ts），同源；无第三方 XHR |
| media-src | 'self' [https://upload.wikimedia.org](https://upload.wikimedia.org) | 发音 `<audio src>` 指向 Wikimedia Commons 托管的 ogg（pronunciation.py 从 Commons imageinfo 取 audioUrl，前端 PronunciationPanel.tsx 直接播放） |
| object-src | 'none' | 无插件内容；封堵 object/embed 注入面 |
| base-uri | 'none' | 无 `<base>` 标签；防 base 劫持 |
| frame-ancestors | 'none' | 站点无需被任何页面嵌入 |
| form-action | 'self' | 站内无表单；防表单注入向第三方提交 |

**不需要列的外部域（核实结论）**：

- Brevo：仅服务端调 api.brevo.com 发信，且邮件是 6 位验证码、无跳转链接——前端与 CSP 均无 Brevo 依赖。
- 支付宝 openapi.alipay.com：payUrl 是 `<a href>` 跳转，普通链接导航不受 CSP 限制，无需 frame-src / form-action。
- 微信 code_url：qrcode 库本地 canvas 渲染，无网络请求。

### 2.5 上线前核对清单

1. **暂不加 upgrade-insecure-requests / block-all-mixed-content**：站点本身仍是纯 HTTP（IP 直连，域名 / ICP 未定），这些指令对纯 HTTP 站点无收益、只添心智负担；待 TLS 上线后再启用，并同步核对全站资源均为 https（发音音频本身已是 [https://upload.wikimedia.org](https://upload.wikimedia.org)）。
2. 上线后冒烟（浏览器控制台零 CSP violation 才算过）：注册 / 登录、Today 拉卡、发音播放（点一次喇叭）、拼写练习、订阅页出码。
3. 确认 nginx 侧 `add_header ... always`：不加 always 时 404/500 等响应不带 CSP 头。
4. 若未来微信扫码改 iframe 嵌网关页、或支付宝改表单提交跳转，需回来补 frame-src / form-action（当前形态都不需要）。
5. 后端挂法只对后端发出的响应生效；nginx 托管静态的架构下后端中间件覆盖不到静态页，别误以为挂了后端就完事。
6. bundle 更名后（hash 变化）CSP 不需要改——script-src 是 'self' 不含资源指纹。

## 三、部署清单

### 3.1 systemd 单元（vocab.service）

```ini
[Unit]
Description=VocabularyLearning FastAPI backend
After=network.target

[Service]
Type=simple
User=vocab                          # 专用非 root 账户
WorkingDirectory=/opt/vocab/backend  # 相对路径默认 ./data/vocabulary.sqlite 在此目录下
EnvironmentFile=/etc/vocab/vocab.env
ExecStart=/opt/vocab/venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8000
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
```

- 监听 `127.0.0.1`（只给 nginx 反代，不直接暴露后端端口）。
- 启动失败会显式退出（main.py：关键迁移 / 发信配置失败时 fail fast，让 systemd 状态一目了然，不带病 502）。

### 3.2 nginx 配置要点

- root 指向 dist 部署目录，`location /api/` 反代 `127.0.0.1:8000`（完整样例见 2.2）。
- 7 个 SPA 路径入口（login / register / check-email / forgot-password / reset-password / verify-email / subscription）反代后端，复用其后端 301 → hash 路由。
- 按需补 gzip（assets 已是 hash 文件名的静态资源）与访问日志。
- 上线 CSP 头（2.2 样例）。

### 3.3 环境变量 / 密钥位置

统一放 `/etc/vocab/vocab.env`（systemd EnvironmentFile，权限 root:vocab 0640），代码实据：

| 变量 | 用途 | 代码默认（未配置时） |
|-|-|-|
| VOCAB_DB_PATH | SQLite 路径 | `./data/vocabulary.sqlite`（相对 WorkingDirectory） |
| VOCAB_PUBLIC_BASE_URL | 站点对外基址 | `http://127.0.0.1:8000`（云端应设为实际访问地址） |
| VOCAB_STATIC_DIR | 后端托管静态目录 | 未设置（云端由 nginx 托管，留空） |
| BREVO_API_KEY | Brevo 发信 | 未配置则邮件降级（不影响登录后学习功能） |
| WECHAT_APPID / WECHAT_MCHID / WECHAT_APIV3_KEY / WECHAT_MCH_PRIVATE_KEY_PATH / WECHAT_MCH_CERT_SERIAL | 微信 APIv3 | 未配置则该渠道下单 503 payment_not_configured |
| ALIPAY_APPID / ALIPAY_PRIVATE_KEY_PATH / ALIPAY_PUBLIC_KEY_PATH / ALIPAY_GATEWAY | 支付宝 | 同上 |
| PAYMENT_NOTIFY_URL（或分渠道 WECHAT\_/ALIPAY_NOTIFY_URL） | 支付回调地址 | 未配置则降级 |
| PAYMENT_ORDER_TTL_MINUTES / PAYMENT_ORDER_TITLE | 订单参数 | 15 / 默认标题 |

### 3.4 数据备份

- 备份对象：SQLite 单文件 `data/vocabulary.sqlite`（含 WAL 模式，同目录可能有 `-wal` / `-shm` 伴生文件）。
- **不要直接 cp 运行中的库文件**（WAL 模式下拷贝可能得到不一致快照）。用 `sqlite3 vocabulary.sqlite ".backup /backup/vocab-$(date +%F).sqlite"` 或 `VACUUM INTO`，建议 crontab 每日一次并异地（对象存储）留档。
- 其余无状态：dist 可由构建重现，后端代码在 Git。

## 四、升级流程（拉最新 cloud → 构建 → md5 核验 → 重启）

1. `git fetch && git checkout cloud && git pull --ff-only`
2. `cd frontend && pnpm install --frozen-lockfile && pnpm build` → 产出 `dist/`（bundle 名带内容 hash，如本次基线的 `index-Dfyyslxr.js`）
3. md5 核验：构建机 `md5sum dist/assets/*`，与部署到服务器后的 `md5sum` 逐一比对；bundle 文件名本身就是内容指纹——名字变了内容必变，名字没变内容必没变。建议把每次发布的 bundle 名记进发布日志（如本次：`index-Dfyyslxr.js`）。
4. 部署静态：上传到新版本目录后原子切换（如 `current` 软链指向新 dist 目录），避免半新半旧。
5. 后端有变更时 `systemctl restart vocab`；仅前端变更可不重启。
6. 冒烟：`curl -s http://127.0.0.1:8000/api/health`（匿名健康检查），再浏览器过一遍登录 + Today。

## 五、回滚要点

- **前端回滚秒级**：发布目录按版本留存，把 `current` 软链切回上一版即可，无需重启任何服务。
- **后端回滚**：`git checkout <上一提交> && systemctl restart vocab`。
- **数据库**：迁移全部是 ADD COLUMN + 回填的幂等设计（book_words.layer、email_tokens.attempts、subscriptions.order_no、reviews.study_date 等），**回滚代码不需要回滚库**——旧代码遇新增列按旧 schema 读写不受影响。禁止手工 DROP COLUMN 回滚。
- 顺序原则：先回前端（用户可见问题多数是前端）；后端进程级问题先 `systemctl restart`，再考虑代码回退。

## 六、建议入库路径（供协调员统一推送，本单未写仓库）

- `docs/cloud_deployment.md` —— 本文档正文（与 docs/windows_packaging.md 并列，云端口径）。

## 数据风险与置信度说明

| 场景/主张 | 事实状态 | 数据风险与限制 | 置信度 | 核验方式 |
|-|-|-|-|-|
| 前端资源形态（同源 /api、内联 style、audio 指向 upload.wikimedia.org、无外部字体图片） | 已核验事实 | 基于 cloud 分支 commit 1786b4a，后续前端改动需同步更新 CSP | 高 | GitHub cloud 分支源码逐文件核对（api.ts / pronunciation.py / PronunciationPanel.tsx / styles.css / index.html / vite.config.ts） |
| dist bundle index-Dfyyslxr.js 与线上一致 | 已核验事实 | 仅比对文件名（内容 hash），未逐字节比对线上文件 | 高 | 分支 frontend/dist/assets 目录与运维提供的线上 bundle 名比对 |
| 服务器 124.223.10.115、nginx、systemd vocab.service、腾讯云 | 待核验事实 | 来自任务背景的运维口径，非代码可证；单元名/路径以线上实际为准 | 中 | 服务器上 `systemctl cat vocab` 与 nginx 配置比对 |
| systemd / nginx 配置样例 | 理论推演 | 参数（路径、用户名、端口 8000）以模板形式给出，落地需按机器实际调整；端口 8000 与代码默认一致 | 中 | 部署时按 2.2 / 3.1 样例适配后 `nginx -t` + `systemctl restart` 验证 |
| 迁移为 ADD COLUMN 幂等、回滚不动库 | 已核验事实 | 核对的是 db.py 现有迁移；未来新增迁移若改变此模式需重新评估 | 高 | db.py 中 5 处 ADD COLUMN 与幂等分支逻辑核对 |
| 邮件无跳转链接（Brevo 与 CSP 无关） | 已核验事实 | 若未来邮件恢复链接形态，需把目标域列入 connect-src 无关但需评 mail 内链 | 高 | emailing.py：6 位验证码，public_base_url 仅作注释性基址 |