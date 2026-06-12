# AGENTS.md · Just Another Invoice (`jai`)

> 给所有在本仓库工作的 agent（Claude Code / Codex，任意模型）。这份文件只装**不变的规矩 + 路标**；具体的、随里程碑演进的内容在 `docs/plan/`，**别在这里重复，去读源头**。
>
> （`CLAUDE.md` 是指向本文件的软链接，两者内容永远一致。）

## 项目是什么
自托管的**发票 / 个人公司管理系统**，作者本人 + 开源项目自用（荷兰个体户口径）。**FastAPI + Vue 3 SPA，单容器，PostgreSQL。** Python 包名 `jai`。

## 先读哪里（路标，不要在本文件里重复其内容）
- **领域 / 范围 / 决策 / 荷兰 VAT 模型（权威）**：`docs/insight/InvoiceShelf-analysis.md`
- **主路线图 + 全局约束 + 里程碑地图**：`docs/plan/roadmap.md`
- **动手实现前**：读当前里程碑的 `docs/plan/milestones/M<x>.md`。
  **当前活跃的里程碑 = `roadmap.md` 进度表里标 🟡 的那个**（“做到哪了”只在这张表上记，本文件不记）。

## 红线（每个里程碑都成立，违反先停 · 详解见 roadmap §2）
1. **算钱只在后端 `services/`**：前端只收原始输入；金额一律 `Decimal`（DB `NUMERIC`，scale≈3），舍入规则与位置定死。
2. **多租户用 Postgres RLS，不手动 scope**：核心业务表预留 `company_id`，别散落 `where company=`。
3. **不手写级联删除**：用 DB 外键 + ORM cascade。
4. **编号并发安全**：别 `max+1`；用 DB 序列 / 唯一约束 + 重试；支持自定义起始与跳号。
5. **设置类型化**：三层设置用 Pydantic/枚举 + 缓存，别 `'YES'/'NO'` 满地跑。
6. **税表规范化**：别一张宽表挂一堆可空 FK；单据级/行级分表或正规多态。
7. **渲染用户输入先清洗**：进 PDF/HTML 前过滤（XSS/SSRF）。
8. **汇率锁快照**：外币按开票日锁 EUR 税基，历史不漂移。
9. **不做应用内自更新**：升级走重建容器镜像。
10. **`description` 用 `text`**，别再犯 255 上限。
11. **OpenAPI→TS 类型生成**：契约一改就重生成，CI 强制无漂移。
12. **VAT 数据驱动**：税率/类别是用户可增删改的记录，不写死成枚举；“类别→申报格子”映射与税率表解耦。

## 架构与约定
- **目录分层**（后端）：`backend/src/jai/` 下 `models/ schemas/ services/ api/ auth/` + `config.py db.py main.py`。
  - `api/` = 薄路由，只做编排，调 `services/`；
  - **业务/算钱逻辑全在 `services/`**；
  - `schemas/`（Pydantic 请求/响应）与 `models/`（SQLAlchemy ORM）**分离**，schema 层不算钱。
- **前端**：`frontend/src/` 下 `api/ stores/ views/ components/ composables/ router/ ...`。
- **后端栈**：FastAPI · SQLAlchemy 2.0 (async) · Alembic · fastapi-users · pydantic-settings · asyncpg/PostgreSQL · uv · Python 3.12。
- **前端栈**：Vue 3 + TypeScript · Pinia · Vue Router · Vite · Naive UI · ECharts；`openapi-typescript` 生成 `frontend/src/api/schema.d.ts`（**已提交进仓库**）。
- **契约先行**：动手前先定/锁 API schema，前后端各自对着它写；契约一改就 `npm run codegen`。
- **API 前缀**：业务一律 `/api/v1/*`；健康检查 `/api/health`。其余路由由后端托管 SPA。

## 常用命令
> M0 脚手架落地后这些才全部可用；命令本身是稳定约定。

- 后端（`cd backend`）：`uv sync` · `uv run ruff check .` · `uv run mypy --strict src` · `uv run pytest` · `uv run uvicorn jai.main:app --reload` · `uv run alembic upgrade head`
- 前端（`cd frontend`）：`npm install` · `npm run dev` · `npm run build`（`vue-tsc + vite`）· `npm run codegen`
- 开发态全栈：`docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d`（显式加载基础 Compose + dev override）
- 开发态只起 Postgres：`docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres`
- 生产 / 普通部署态：`docker compose up -d`（单容器 app + Postgres）

## 工作流与质量门
- **原子改动**：一次只做一件可独立部署、附测试的小事。
- **单人开发，不强制 PR**：自测 + CI 绿即可直接合 `main`（想要人工 review 时再开分支/PR）。
- **Definition of Done**（每步全过）：`ruff` + `mypy --strict` + `pytest` 绿；改了契约则重生成 `schema.d.ts` 且无漂移；前端能 `build`；**算钱/编号/汇率等逻辑必须有单测**；`docker build` 通过；不违反任何红线。

## 实现 / Review 简报
- **实现简报（每步）**：每一轮 implementation 完成后（planning 不算），都要在 `review-notes/` 下用中文写实现简报，内容至少包括：(a) 本轮实现内容；(b) 自动化测试结果；(c) 人工 walkthrough 步骤。orchestrator 模式下命名 `review-notes/M<x>-step<n>-impl.md`。
- **里程碑级实现报告（milestone 末）**：整个 milestone 全部步骤跑完后，额外出一份 `review-notes/M<x>-report.md` —— ① 内容详尽；② 面向作者可读；③ 含**完整的本 milestone 人工 walkthrough 步骤**（把各 `M<x>.md` 的「🟢 部署自测点」整合串讲）。这是作者人工 walkthrough 的输入。
- **人工 walkthrough 时机**：自 M7 起**逐步不再人工走**（逐步门 = 自动化测试绿 + 盲审无 finding）；人工 walkthrough **收敛到 milestone 末一次**，作者对着上面的里程碑级报告走。**默认启动方式**：开发态 Compose（`docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d`），不默认拆成分别手动起前后端。
- **Review 输入**：作者要求 review 时，优先读取作者指定的实现简报；如果未指定，自动读取 `review-notes/` 下最新的实现简报，再结合增量 diff 和相关设计文档审。
- **Review 输出**：只有发现修改意见 / findings 时，才在 `review-notes/` 下用中文写 review 报告；如果没有修改意见，直接在聊天框说明即可，不额外落文件。

## 提交规范（硬要求）
- 提交信息用**英文 Conventional Commits**：`feat:` / `fix:` / `docs:` / `docs(plan):` / `refactor:` / `chore:` …
- **严禁任何 AI/Claude 署名**：不加 `Co-Authored-By`、不加 “authored by Claude” 之类字样。
- **只在作者明确要求时才 commit / push。**

## 每轮开发的 commit 节奏（实现 / 返工 / 收尾）
> 作者用这三个关键词驱动一个 feature 的提交节奏；**关键词本身即“明确要求 commit”的授权**（细化上面“只在作者明确要求时才 commit”的笼统说法，不冲突）。三步都遵守上面的「提交规范」（英文 Conventional Commits、严禁 AI 署名）。
> **Orchestrator 模式下这三步按步自动发生**，且 autosquash 是**逐步**（每个原子步骤各压成一个 commit）而非逐 feature——见「Agent orchestration」节。

1. **实现**：作者说“实现”时，做完即为该 feature 定好 Conventional Commits message 并 `git commit` 落一轮。
2. **返工**：作者说“返工”时，**不新开独立 commit**，而是针对被返工的那个实现 commit 做 fixup：`git commit --fixup=<目标实现 commit 的 sha>`。
3. **收尾**：作者说该 feature “彻底结束 / 收尾”时，用 auto-squash 把这一串实现 commit + 所有 fixup commit 压成**一个** commit。
   - 命令：`GIT_SEQUENCE_EDITOR=: git rebase --autosquash <feature 起点的前一个 commit>`（本环境不支持交互式 `-i`，用 `GIT_SEQUENCE_EDITOR=:` 跑非交互 autosquash）。
   - autosquash 只把各 fixup 折叠回其目标实现 commit；若本 feature 产生了**多个**实现 commit，在同一次 rebase 里把它们也一并 squash，最终该 feature 只留一个 commit。

## Agent orchestration（自 M7 起的实现执行模型）
> 自 M7 起，里程碑的实现支持两种执行方式。**默认是人工模式**；只有作者**明确点名 orchestrator 模式 / 直接生成**时，才跑下面的全自动循环。设计文档（`docs/plan/milestones/M<x>.md`）已把每个原子步骤写成**自包含 + 带盲审要点**，两种方式都能挂。

### 两种模式
- **人工模式（默认）**：作者只让你实现某一步并输出实现简报 = 人工模式。**不自动 spawn 子 agent、不自动跑 review/fix 循环、不自动 commit**（commit 仍按「commit 节奏」的关键词授权）。**没有作者明确点名 orchestrator 模式，一律按此。**
- **Orchestrator 模式（全自动）**：作者新开一个 Opus（Extra High Reasoning）对话，**你就是 orchestrator**，按下方循环自动驱动子 agent 跑完指定步骤 / 里程碑。**作者点名 orchestrator 模式本身 = 对本轮 commit（impl / fixup / per-step autosquash）的明确授权。**

### 三类子 agent（模型默认值，提示词可覆盖）
- **implementer / fixer**：同一类、逻辑一致；默认 **Sonnet + high reasoning**。
- **reviewer**：默认 **Opus + extra high reasoning**。
- 作者在提示词里显式指定别的模型 / reasoning 等级时，**以提示词为准**。

### 逐步循环（orchestrator 模式 · 作者要求“一步一步实现”时）
**做哪一步由 orchestrator 决定并逐步推进**（步骤 1 → 2 → …，一步一个 iteration）。每个原子步骤跑完整一轮再进下一步：

1. **实现（implementer）**：spawn 一个干净 implementer，指令必须含：
   - 只实现**当前指定这一步**，不自由发挥（不顺手做别的步骤 / 不夹带重构）。
   - **测试完备**：Happy Flow + Corner Cases 都要覆盖。
   - **不污染本机**：实现期间如需临时验证，临时文件用完清理干净，**不动本机生产环境**（DB / 容器 / 文件）。
   - 完成后按「实现 / Review 简报」写**该步中文实现简报**。
   - 落一个 **implementation commit**（= 该步 feature commit）。
2. **盲审（reviewer）**：spawn 一个**全新** reviewer，**只给**：(a) 该 milestone 设计文档（`M<x>.md` + roadmap）；(b) 刚生成的实现简报；(c) 该步 diff。**不接触 implementer 的对话 / 思路**（黑盒盲审）。重点：① 是否**完全按设计文档**；② 有无**对设计文档的偏移**；③ 代码 **bug + 潜在风险**。
   - 有 finding → 写一份**中文 review 简报**进 `review-notes/`（作者可能会看）。
   - 无 finding → 该步结束。
3. **返工（fixer）**：有 finding → spawn fixer，输入 = **设计文档 + 该份 review 简报**；改完落一个 **`--fixup` commit**（指向该步 implementation commit，见「commit 节奏」）。
4. **复审**：返工后**再 spawn reviewer 复审**；只要还有**新 finding** 就继续返工 → 复审，直到**无 finding** 为止。**返工上限 = 5 轮**；满 5 轮仍有 finding，**停下来升级给作者人工介入**。
5. **收口该步**：该步 impl + 所有 fixup 落定后，orchestrator 做**一次 per-step autosquash**，把该步 implementation commit + 它的 fixup 压成**该步单一 commit**（命令同「commit 节奏」，base = 该步实现 commit 的前一个 commit）。⇒ milestone 完成时**每步各留一个 commit**。
6. **进下一步**：重复 1–5，直到该 milestone 全部步骤完成。

### 里程碑收尾
- 全部步骤跑完 → 出一份**里程碑级实现报告**（`review-notes/M<x>-report.md`，要求见「实现 / Review 简报」）。
- 作者对着它**人工 walkthrough**；walkthrough 中的修改意见走**人工对话**修改（不再自动循环）。

## 维护本文件
只在**根基**变化时才改本文件：技术栈、上面这些红线/命令/约定、执行模型（Agent orchestration）、或新增一个 agent 工具。**里程碑的推进不需要动它**——那只更新 `docs/plan/`。
