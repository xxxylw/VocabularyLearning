# Windows 本地单机版：构建与发布指南（P0-5）

面向维护者。对应 PRD 第七章「P0-5 Windows 本地单机版产品化（第一版）」。

## 产品形态（一屏速览）

- 分发形态：绿色 zip 压缩包（无 installer），GitHub Release v1.0.0 托管。
- 运行形态：单进程——托盘启动器内嵌 FastAPI/uvicorn 后端线程，同时托管前端静态产物（同源 `/` + `/api`），浏览器访问 `http://127.0.0.1:<port>/`。
- 数据形态：解压目录只读（程序 + `builtin/vocabulary.sqlite` + `static/`）；全部可变数据在 `%APPDATA%\VocabularyLearning\`。

## 包内布局（构建产物）

```
VocabularyLearning/
├── VocabularyLearning.exe      # 托盘启动器（PyInstaller onedir，--noconsole）
├── _internal/                  # PyInstaller 运行时（后端 + launcher 全部依赖）
├── builtin/
│   └── vocabulary.sqlite       # 只读内置词库（净化后：无用户进度）
├── static/                     # 前端 vite 构建产物（index.html 等）
└── README.txt                  # 用户说明（安装/退出/SmartScreen/数据目录）
```

关键环境变量（由启动器在拉起后端前设置，业务代码不感知打包细节）：

- `VOCAB_DB_PATH` — 指向 `%APPDATA%\VocabularyLearning\vocabulary.sqlite`（工作库）
- `VOCAB_STATIC_DIR` — 指向包内 `static/`（FastAPI 同源托管前端，`app/main.py: static_dir()`）
- `VOCAB_ENRICHMENT_SOURCE` — 默认 `fallback`（离线优先）；用户在 `%APPDATA%\VocabularyLearning\settings.json` 写 `{"online_enrichment": true}` 可启用 `oxford`

## 构建步骤（Windows 机器）

前置：Python 3.11+、Node.js、pnpm、GitHub CLI（发布时）。

```powershell
git clone https://github.com/xxxylw/VocabularyLearning.git
cd VocabularyLearning
.\scripts\build_windows.ps1            # 默认 -Version 1.0.0，含测试
```

脚本依次执行：

1. 后端 pytest + launcher pytest（`-SkipTests` 跳过）；
2. `pnpm install --frozen-lockfile && pnpm build` 构建前端；
3. `scripts/make_builtin_db.py` 由本机 `backend/data/vocabulary.sqlite` 生成净化内置库（剥离 reviews / prepare_jobs / settings，卡片重置为初始态）并按阈值核验计数（词数=3383、例句≥9000、可用音标≥3000 等，不达标即失败）；
4. PyInstaller 打包启动器（`launcher/__main__.py`，`--paths backend` 让 `app` 包可导入）；
5. 组装包目录、生成 `README.txt`、打 zip、计算 SHA256 写 `dist/checksums.txt`。

产物：`dist/VocabularyLearning-v1.0.0-win64.zip`、`dist/checksums.txt`、`dist/release_notes_v1.0.0.md`。

## 发布 GitHub Release v1.0.0

```powershell
gh release create v1.0.0 `
  dist/VocabularyLearning-v1.0.0-win64.zip `
  dist/checksums.txt `
  --title "v1.0.0" `
  --notes-file docs/release_notes_v1.0.0.md
```

（或在 GitHub 网页上传 zip 与 checksums.txt，粘贴 release notes。）

## 发布前验收清单（对应 PRD 验收标准 1-8）

在干净 Windows 环境（建议无 Python/Node 的账户或虚拟机）逐条核验：

1. 解压 zip 双击 exe → 浏览器自动进入学习界面，全程零命令；
2. 默认书显示「雅思词汇真经」，单词/释义/例句/卡片齐全（构建日志中的 DB 计数即实测核验）；
3. Today study、复习调度、拼写练习可用；断网复测仍可用；
4. 学习若干卡片 → 托盘 Exit → 重新启动，进度保留（`%APPDATA%\VocabularyLearning\vocabulary.sqlite` 有 reviews 记录）；
5. Exit 后任务管理器无 `VocabularyLearning.exe` 残留；学习中直接杀进程再启动，已保存数据完好；
6. 两个 Windows 账户分别启动，各自 `%APPDATA%` 下独立工作库，进度互不影响；
7. 占住 8000 端口（如 `python -m http.server 8000`）后启动应用 → 自动换端口且浏览器打开正确地址；
8. Release 资产齐全：zip + SHA256 checksums + release notes。

## 维护要点

- **升级不覆盖用户数据**：`launcher/core.py: ensure_working_db()` 只在工作库不存在时复制内置库；新版本解压覆盖程序目录不影响 `%APPDATA%`。
- **内置库数据源**：`scripts/make_builtin_db.py` 以构建机 `backend/data/vocabulary.sqlite` 为源。该库必须先完成全量 enrichment（含 Wiktionary 音标缓存），否则计数核验会主动失败。
- **端口策略**：首选 8000，占用则扫 8000–8199（`launcher/core.py: pick_free_port()`），全部不可用弹窗报错（不静默失败）。
- **单实例**：`%APPDATA%\VocabularyLearning\instance.lock`（含 PID，死进程锁会被接管）；二次启动读 `runtime.json` 里的 URL 直接开浏览器。
- **托盘依赖**：pystray + pillow（`launcher/requirements.txt`）。沙箱/无 GUI 环境自动降级为无托盘常驻。
