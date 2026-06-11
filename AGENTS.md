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
- **实现简报**：每一轮 implementation 完成后（planning 不算），都要在 `review-notes/` 下用中文写实现简报，内容至少包括：(a) 本轮实现内容；(b) 自动化测试结果；(c) 人工 walkthrough 步骤。
- **人工 walkthrough 默认启动方式**：默认用开发态 Compose（`docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d`）启动，不默认拆成分别手动启动前后端。
- **Review 输入**：作者要求 review 时，优先读取作者指定的实现简报；如果未指定，自动读取 `review-notes/` 下最新的实现简报，再结合增量 diff 和相关设计文档审。
- **Review 输出**：只有发现修改意见 / findings 时，才在 `review-notes/` 下用中文写 review 报告；如果没有修改意见，直接在聊天框说明即可，不额外落文件。

## 提交规范（硬要求）
- 提交信息用**英文 Conventional Commits**：`feat:` / `fix:` / `docs:` / `docs(plan):` / `refactor:` / `chore:` …
- **严禁任何 AI/Claude 署名**：不加 `Co-Authored-By`、不加 “authored by Claude” 之类字样。
- **只在作者明确要求时才 commit / push。**

## 维护本文件
只在**根基**变化时才改本文件：技术栈、上面这些红线/命令/约定、或新增一个 agent 工具。**里程碑的推进不需要动它**——那只更新 `docs/plan/`。
