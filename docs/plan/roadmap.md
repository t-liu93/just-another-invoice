# Just Another Invoice · v1 主路线图（Master Roadmap）

> **这是什么**：把 `docs/insight/InvoiceShelf-analysis.md` 里已拍板的 v1 范围（P0–P7）翻译成一份**可施工的总览**——按「原子化、前后端可并行、每个里程碑完成即可部署自测」重新切成 **M0–M11**。
>
> **这不是什么**：不是逐行施工图。本文停在「里程碑 + 方法论 + 约束」这一层；每个里程碑的**原子步骤清单**在动手时才落到 `docs/plan/milestones/M<x>.md`（JIT 细化，模板见 `milestones/_TEMPLATE.md`）。
>
> **上游文档**：方向、领域模型、决策记录、荷兰 VAT 模型一律以 `docs/insight/InvoiceShelf-analysis.md` 为准（下称「分析文档」）。本文只做「怎么落地」。
>
> **架构母版**：`~/workspace/trading-journal`（同构、已上线）。骨架、约定、Dockerfile、CI 全部对齐它；差异仅在「换 PostgreSQL + 发票领域」。

---

## 0. agent 开工必读（每次进场先看这 5 条）

1. **先读约束**：本文 [§2 全局约束] 是纪律红线，违反任何一条都要先停下来问。
2. **契约先行**：动手前先定/改 API 契约（Pydantic schema），再分头写后端实现与前端界面。见 [§1.1]。
3. **算钱只在后端**：前端只收原始输入，所有金额由后端 `services/pricing` 权威计算。见 [§2] 第 1 条。
4. **一步 = 一个原子改动 + 过 DoD**：原子、可独立部署、附测试，CI 绿即可合 `main`（单人开发不强制 PR）。模板见 [§5]。
5. **改了契约就重生成 TS 类型**：`npm run codegen`，并确保 CI 的 drift 检测过。见 [§1.1]、[§5]。

---

## 1. 开发方法论（四支柱）

### 1.1 Contract-first（契约先行）→ 前后端可并行

每个功能的**第一个动作**是把 API 契约定下来：在后端写 Pydantic 请求/响应模型 + 路由签名（可先返回桩数据），FastAPI 自动产出 OpenAPI。契约一锁：

- **后端线**：写 `models → schemas → services → api` 的真实实现 + 测试。
- **前端线**：`npm run codegen` 把 OpenAPI 拉成 `frontend/src/api/schema.d.ts`（**已提交进仓库**），前端对着类型写 store / view，先连 mock、最后对接真接口。

两条线并行，靠「类型 + OpenAPI」这一份契约保证不漂。CI 的 `codegen-freshness` 关会强制 `schema.d.ts` 与后端一致。

### 1.2 Walking Skeleton + 纵向薄切片 → 每个里程碑都能部署自测

- **M0 不是"堆基础设施"**，而是打通一条最薄的端到端链路：单容器起来、FastAPI 托管前端、健康检查、一个能打开的页面、Postgres 连上、迁移能跑、CI 绿。**第一天就能"进部署页面点一下"**。
- 之后每个里程碑加**一条纵向可见的功能**（DB → API → UI 一条龙），而不是"先写完所有后端再写前端"。每个里程碑结尾都有明确的 **部署自测点**（见 [§4]）。

### 1.3 原子步骤 + 统一 Definition of Done → 可维护、可 review

每个原子步骤是一组小而自洽的改动，结构永远是母版那套分层（`models / schemas / services / api`，算钱在 `services`），过统一的 DoD（见 [§5]）。**单人开发，不强制走 PR**：自测 + CI 绿即可直接合 `main`（想做人工 review 时仍可开分支/PR，留这个口）。好处：每个步骤都小而同构，哪天要回看也扫一眼就懂。

### 1.4 仓内 Markdown 计划文档 → vibe coding 友好

计划全部以 Markdown 落在 `docs/plan/`，随代码版本化、agent 与人都能读。本文是总览；里程碑细节 JIT 落到 `milestones/M<x>.md`。结构见 [§7]。

---

## 2. 全局约束（Guardrails · 红线）

> 源自分析文档 §7.3 避坑清单 + 决策记录。**每个 agent 开工必读，违反先停。**

1. **算钱在后端**：前端只传 `{item_id?, name, description, quantity, unit_price, discount, tax_category_id, ...}` 等原始输入；后端 `services/pricing` 负责 行小计→折扣→计税(含税/不含税/复合/定额)→单据合计→base 换算，统一产出并落盘。金额一律 **`Decimal`**（DB `NUMERIC`，scale≈3），**定死舍入规则与位置**（逐行 vs 合计舍入口径固定）。
2. **多租户用 Postgres RLS，别手动 scope**：即便 v1 单租户，也把数据访问收敛、核心表预留 `company_id`，别散落 `where company=`。详见 [§3.3]。
3. **别手写级联删除**：用 DB 外键 + ORM cascade（SQLAlchemy `relationship(cascade=...)` / `ondelete="CASCADE"`），杜绝孤儿数据。
4. **编号并发安全**：别 `max+1`；用 DB 序列 / 唯一约束 + 重试；且支持**自定义起始与跳号**（迁移旧系统衔接）。
5. **设置别 stringly-typed**：三层设置类型化访问（Pydantic/枚举）+ 缓存，别满地 `'YES'/'NO'`。
6. **税表结构规范化**：别用一张宽表挂一堆可空 FK；用规范多态或「单据级税表 / 行级税表」分表。
7. **渲染用户输入要清洗**：进 PDF/HTML 前过滤（XSS/SSRF），沿用 InvoiceShelf 近期的 sanitizer 思路。
8. **汇率锁快照**：外币按**开票日**锁 EUR 税基（VAT 合规），收款日另算现金/汇兑；历史不漂移（分析文档 §7.4.5）。
9. **不做应用内自更新**：升级走重新部署容器镜像，不给网络留控制容器的入口。
10. **`description` 用 `text`**：别再犯 255 上限的错。
11. **OpenAPI→TS 类型生成**：沿用母版做法，前后端类型一致、契约不漂；CI 强制 drift 检测。
12. **VAT 数据驱动、不写死枚举**：税率与 VAT 处理类别是**用户可增删改的记录**（NL 默认 21/9/0 仅作种子）；「类别→申报格子」映射是国别特定的，与税率表解耦（分析文档 §7.4.2）。

---

## 3. 技术栈与骨架（对齐母版）

### 3.1 技术栈

| 层 | 选型 |
| --- | --- |
| 后端 | **FastAPI** + **SQLAlchemy 2.0 (async)** + **Alembic** + **fastapi-users** + **pydantic-settings**；包管理 **uv**；Python 3.12 |
| 数据库 | **PostgreSQL**（开发期即用，不用 SQLite；asyncpg 驱动）+ **行级安全 RLS** |
| 质量门 | **ruff**（line 100；E/F/I/B/UP/ASYNC）+ **mypy --strict** + **pytest**（asyncio auto） |
| 前端 | **Vue 3 + TypeScript** + Pinia + Vue Router + Vite + **Naive UI** + **ECharts**；**openapi-typescript** 生成 `schema.d.ts` |
| 部署 | **单容器**：三阶段 Dockerfile（前端 `vite build` → 后端 `uv` 装依赖 → runtime 托管 `static/` + uvicorn）；entrypoint 跑 `alembic upgrade head` |
| CI | GitHub Actions：backend-quality / codegen-freshness / frontend-build / docker-build；tag 触发多架构镜像发布 |

> **与母版唯一的实质差异**：母版用 `sqlite+aiosqlite`，本项目从 M0 起就用 `postgresql+asyncpg`（为 RLS 铺路）。其余骨架、Dockerfile 分层、CI 四关、codegen 流程**照搬**。

### 3.2 目录骨架（Python 包 = `jai`）

```
backend/src/jai/
  main.py            # FastAPI app 装配、路由挂载、静态托管 + SPA fallback
  config.py          # pydantic-settings：DATABASE_URL / SMTP / STATIC_DIR / ...
  db.py              # async engine / session / Base；RLS 会话上下文（后续）
  auth/              # fastapi-users：users / backend / deps
  models/            # SQLAlchemy ORM（含 _enums.py、Money/Decimal 约定）
  schemas/           # Pydantic 请求/响应（与 models 分离；不进算钱）
  services/          # 业务逻辑：pricing / numbering / fx / reports ...（算钱在这）
  api/               # 路由（/api/v1/*）：薄控制器，调 services
backend/alembic/     # 迁移
frontend/src/        # api(schema.d.ts) / stores / views / components / router / ...
Dockerfile           # 三阶段单容器
.github/workflows/   # ci.yml / release.yml
docs/                # insight(分析) + plan(本路线图 + 里程碑)
```

### 3.3 「多租户友好 schema + 单租户简单逻辑」落地基线

- v1 **单公司单用户**实现，但 schema 不写死成单例：把「公司/业务主体」建成一张表（v1 仅一行），核心业务表预留归属关系。
- `company_id` 现在就挂还是日后 Alembic 补列，**留到各里程碑建表时定**（默认倾向：核心业务表从 M2 起就挂 `company_id`，给 RLS 留位）。
- RLS / 多用户 RBAC / 多公司切换 UI 等**应用层复杂度 v1 直接不做**，只在 schema 留口。

---

## 4. 里程碑地图 M0–M11

> 依赖：**M0 → M1 → M2 → {M3, M4}**，然后分两条可并行的线 ——
> ① **单据线**：M5 → M6 → M7；② **开支线**：M8（只依赖 M2 本位币 + M4 字典，可选挂 M3 客户，**不依赖发票/报价/收款**）。两线汇合于 **M9 → M10 → M11**。
> 可并行：M3‖M4；单据线‖开支线（即 M8 可与 M5/M6/M7 同时进行）。每格结尾的「🟢 部署自测点」是该里程碑的验收信号。

### M0 · 地基骨架（walking skeleton）｜对应 P0
- **目标**：单容器跑通的最薄端到端链路。
- **关键内容**：仿母版搭 `jai` 后端骨架 + Vue 前端骨架；接入 **PostgreSQL + asyncpg**；Alembic 基线迁移；三阶段 Dockerfile + entrypoint；CI 四关；`/api/health`；**Money/Decimal 货币基础类型**（`NUMERIC` 约定 + 舍入工具）；i18n 脚手架（EN/ZH 骨架）；`openapi-typescript` codegen 接好。
- **两种运行方式（M0 都要打通，基础 `docker-compose.yml` 含 `app` + `postgres`，DB 不发布宿主机端口；app 只发布到 `127.0.0.1:${APP_HOST_PORT:-8000}` 给本机反代，容器内固定 8000；本地开发叠加 `docker-compose.dev.yml` 只绑定 Postgres 到 `127.0.0.1`，端口可由 dev-only `POSTGRES_DEV_PORT` 调整）**：
  - **开发态**：前端 `npm run dev`（Vite）｜后端 `uv run uvicorn jai.main:app --reload`｜数据库 **`docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres`（只起 Postgres 这一个 service）**。
  - **部署态**：`docker compose up -d`（起 **单容器 app = 前端构建产物 + 后端**，外加 **Postgres**；生产从 GHCR 拉镜像，本地集成用 dev override `up --build`），migration service 自动 `alembic upgrade head`。
- **建表/预留**：Alembic 起始；约定 RLS 会话钩子留空实现位。
- **🟢 部署自测点**：本地集成 `docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build` 起 app + Postgres，生产 `docker compose up -d` 从 GHCR 拉镜像；浏览器打开 `http://localhost:${APP_HOST_PORT:-8000}` 看到（空）占位页 + `/api/health` 返回 ok；CI 全绿。

### M1 · 认证 + 邮件底座｜对应 P1
- **目标**：能注册/登录的私有应用，且邮件能发出去。
- **关键内容**：fastapi-users 用户名+密码（**Argon2**）；**MFA(TOTP)，记账软件必须有**；登录/登出 UI；受保护路由 + 空壳 dashboard。
- **邮件底座（本里程碑一并做掉，后续 M9 直接复用）**：用 Python 现成包实现 **SMTP 发送**；**密码重置邮件**；**设置表基础**（即三层设置的单表 + `level`，先用来存 SMTP 配置）+ **SMTP 配置设置页**（前端填写并保存）。
- **建表/预留**：`user` 绑 `company_id` + `role` 字段建表即留（RBAC 后补，v1 owner 全权）；**设置表（key-value + level）在此落地**，M2 在其上扩公司/用户层。
- **🟢 部署自测点**：注册 → 登录 → 绑定 TOTP → 进到空 dashboard；在设置页填 SMTP → 触发密码重置 → 收到邮件；登出后受保护页跳登录。

### M2 · 公司档案 + 三层设置（补全）｜对应 P1
- **目标**：能配置「你的业务主体」。
- **关键内容**：单例**业务档案**（name/logo/VAT 号/地址/本位币/编号规则）；在 **M1 已落地的设置表**上补全 **三层语义**（global/company/user，scope id，类型化访问 + 缓存，按 user→company→global 回退）+ 公司/用户级设置 UI 页。
- **建表/预留**：`company` 表落地（v1 一行）；设置表的 `level`/scope 设计为多公司友好。
- **🟢 部署自测点**：编辑公司抬头/logo/本位币并持久化；改一个用户级偏好看回退生效。

### M3 · 客户｜对应 P2（可与 M4 并行）
- **目标**：能管理客户档案。
- **关键内容**：Customer CRUD + 列表；账单/收货**地址**；每客户默认币种（有交易后锁定）；**国家 + VAT 号**（为 M10 的 ICP / 反向征收判定铺路）；长尾字段用 **JSONB** 承载。
- **建表/预留**：`customer.company_id`；地址用 `type`(BILLING/SHIPPING)。
- **🟢 部署自测点**：新建/编辑/删除客户，填地址与 VAT 号，列表搜索。

### M4 · 字典 / 主数据｜对应 P2（可与 M3 并行）
- **目标**：单据要用的字典就绪。
- **关键内容**：**税种/VAT 处理类别**（数据驱动，NL 21/9/0/免税/反向/EU-B2B/出口作种子，见分析文档 §7.4.2）；收款方式；开支分类（对齐 NL/EU 口径）；币种 + 汇率基础（手填，provider 接口留空）。
- **建表/预留**：税类别表与「申报格子映射」**解耦**；汇率 provider 抽象接口。
- **🟢 部署自测点**：维护税率/收款方式/开支分类，种子数据可见可改。

### M5 · 定价引擎 + 发票核心｜对应 P3（v1 的"心脏"）
- **目标**：能开出一张金额由后端算准的发票。
- **关键内容**：**`services/pricing` 权威计算**（行小计→行/单据折扣→按单/按行计税→含税/不含税→合计→base 换算，定死舍入）；`POST /invoices/calculate` 预览端点；发票 CRUD + 列表；**编号**（模板化 + 可自定义起始 + 跳号 + 并发安全）；**双状态**（生命周期 + 收款状态）；行项目（自由填写 `description` 用 `text`、数量必填、单位可选、可选目录项）。
- **建表/预留**：税表规范化（单据级/行级分表或正规多态）；`unique_hash` 字段留位（公开链接 v1 不启用）。
- **🟢 部署自测点**：建发票、加多行、切按单/按行计税、看后端算出的小计/税/合计；改编号起始号生效。

### M6 · 报价 + 转换 + 内容模板｜对应 P3
- **目标**：报价闭环 + 单据内容复用。
- **关键内容**：报价 CRUD（与发票同构）；**简化状态** draft/sent/accepted/rejected/expired（去掉 viewed）；**到期自动置 expired**（后端定时，APScheduler）；**Convert 报价→发票**；**文档内容模板**（常见工种报价/发票一键填充）；**标准内容块**（保修/T&C/银行信息/付款条款，公司级默认、单据可覆盖）；单据 **Notes**（自由备注 + 可复用模板）。
- **🟢 部署自测点**：建报价、套用内容模板、标记 accepted、一键转发票；到期报价被定时置 expired。

### M7 · 收款｜对应 P4（可与 M8 并行）
- **目标**：发票能收款、状态自动流转。
- **关键内容**：Payment 实体（关联发票 / 独立）；**部分/多次付款**（首/中/尾款）；收款方式；**收款时锁汇率**（收款日 EUR 口径）；自动重算 `due_amount` 与 `paid_status`。
- **🟢 部署自测点**：对一张发票分两次收款，看 UNPAID→PARTIALLY_PAID→PAID 自动流转。

### M8 · 开支｜对应 P4（独立线：仅依赖 M2+M4，可与整条单据线 M5/M6/M7 并行）
- **目标**：能记账并智能填单。
- **依赖**：M2（本位币）+ M4（开支分类/收款方式/币种）；可选挂 M3 客户。**不依赖发票/报价/收款**，拿到 M4 即可与单据线并行开工。
- **关键内容**：Expense CRUD + 分类；**收据上传**（图片/PDF，本地存储 + storage 抽象）；**周期性开支**（固定成本按周期自动生成）；**⭐ AI 票据智能填写**（票据图 → 视觉大模型 → 自动填净额/税额/税率/供应商/日期/分类；外部依赖：Claude 等视觉模型 API）；每笔标「是否可抵扣」。
- **🟢 部署自测点**：上传一张票据照片，AI 自动填好开支字段，保存归类；建一条周期性开支看自动生成。

### M9 · 输出：PDF（邮件底座已在 M1）｜对应 P5
- **目标**：能把单据交付给客户。
- **关键内容**：**PDF 生成**（一套模板，Jinja2 + WeasyPrint 候选；用户输入清洗）+ 下载（无公开链接，手动发）；**复用 M1 的 SMTP 底座**，加单据**邮件正文模板/占位符** + **Email log**（无已读回执）+ 把 PDF 作附件发出；收款收据 PDF（低优）。
- **建表/预留**：PDF 模板留 CSS 接口（自定义模板 v1 不做）。
- **🟢 部署自测点**：下载一张发票 PDF（抬头/行项目/税/合计正确）；用 M1 配好的 SMTP 把发票邮件 + PDF 附件发出，并在 Email log 看到记录。

### M10 · 报表 / 仪表盘｜对应 P6
- **目标**：报税与经营看得见。
- **关键内容**：盈亏 **P/L**；**⭐ VAT 申报汇总**（按季度 + VAT 类别聚合进 BTW 格子 1a/1b/1e/2a/3a/3b/4a/4b/5a/5b/5c + 生成 **ICP 清单**，口径见分析文档 §7.4，具体数字口径实现时细化）；开支报表；**Dashboard**（ECharts 图）。
- **🟢 部署自测点**：选一个季度，导出 VAT 汇总与 ICP；Dashboard 显示收入/支出/利润图。

### M11 · 收尾 / GA 前体检｜对应 P7
- **目标**：可长期自托管。
- **关键内容**：**备份脚本**（pg_dump + 卷快照）；i18n **EN/ZH 补全**；安全/性能打磨；文档（部署 README）。
- **🟢 部署自测点**：跑一次备份/恢复演练；切换 EN/ZH UI 完整。

---

## 5. 每个原子步骤的模板 + Definition of Done

> 细化里程碑时，每个原子步骤都按这个模板拆（见 `milestones/_TEMPLATE.md`）。**单人开发不强制 PR**：自测 + CI 绿即可合 `main`；想做人工 review 时再开分支/PR。

**一个原子步骤包含：**
- **目标**：一句话。
- **契约**：本步新增/改动的 API schema（OpenAPI 片段）——**先定**。
- **后端任务** / **前端任务**：两栏，可并行（都对着上面的契约）。
- **迁移**：Alembic（如涉及建表/改列）。
- **测试**：后端 pytest（services 计算逻辑必测）；必要时前端基本校验。

**Definition of Done（每步都要全过）：**
- [ ] `ruff check` + `mypy --strict` 通过
- [ ] `pytest` 通过（pricing/numbering/fx/报表等**算钱逻辑必须有单测**）
- [ ] 若改了契约：`npm run codegen` 重生成 `schema.d.ts`，CI drift 检测过
- [ ] `frontend` 构建通过（`vue-tsc + vite build`）
- [ ] **部署冒烟**：`docker build` 通过；该里程碑的「🟢 部署自测点」能手动点到
- [ ] **CI 四关绿**（绿即可合 `main`，单人开发不强制 PR）
- [ ] 遵守 [§2 全局约束]，无违反项
- [ ] 自查：命名/分层与母版一致，`description` 用 `text`，算钱在 `services`

---

## 6. 部署与自测 loop

- **开发态**：前后端分离——前端 `npm run dev`（Vite，代理 `/api`），后端 `uv run uvicorn jai.main:app --reload`，数据库用 `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres`（只起 Postgres 这一个 service，DB 端口只绑定 `127.0.0.1`，host 端口可由 dev-only `POSTGRES_DEV_PORT` 调整）。
- **部署态 / 里程碑验收**：生产 `docker compose up -d`（**单容器 app + Postgres**，从 GHCR 拉镜像；migration service 自动 `alembic upgrade head`），本地集成用 `docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build` → 浏览器走该里程碑的「🟢 部署自测点」。
- **CI**：每次 push 跑四关；`main`/PR 额外跑 docker-build；打 `v*` tag 发布多架构镜像。绿即可合 `main`（单人开发不强制 PR）。
- 每个里程碑结束在 `docs/plan/milestones/M<x>.md` 记一行验收结论 + 已知遗留。

---

## 7. 计划文档结构 & JIT 细化流程

```
docs/
  insight/InvoiceShelf-analysis.md   # 上游：方向/领域/决策/VAT 模型（权威）
  plan/
    roadmap.md                       # 本文：总览 + 方法论 + 约束 + M0–M11
    milestones/
      _TEMPLATE.md                   # 里程碑细化模板
      M0.md, M1.md, ...              # 动手前 JIT 产出（原子步骤清单）
```

**JIT 细化流程（每进入一个里程碑）**：
1. 对 agent 说：「读 `docs/plan/roadmap.md` 的 §2 约束 + 目标里程碑那一格，再读分析文档对应章节，然后用 `milestones/_TEMPLATE.md` 把 M<x> 拆成原子步骤。」
2. 评审该里程碑的原子步骤清单（必要时回填分析文档里"待细化"的产品决策，如单位是否可选、VAT 数字口径等）。
3. 逐步骤实现，每步过 [§5] DoD。
4. 里程碑收尾：走「🟢 部署自测点」，记录验收结论。

---

## 进度追踪

| 里程碑 | 内容 | 状态 |
| --- | --- | --- |
| M0 | 地基骨架 | 🟢 完成 |
| M1 | 认证 + 邮件底座 | 🟡 进行中 |
| M2 | 公司档案 + 三层设置（补全） | ⬜ |
| M3 | 客户 | ⬜ |
| M4 | 字典 / 主数据 | ⬜ |
| M5 | 定价引擎 + 发票核心 | ⬜ |
| M6 | 报价 + 转换 + 内容模板 | ⬜ |
| M7 | 收款 | ⬜ |
| M8 | 开支（含 AI 填单，可与单据线并行） | ⬜ |
| M9 | PDF（邮件底座在 M1） | ⬜ |
| M10 | 报表 / 仪表盘（含 VAT 申报） | ⬜ |
| M11 | 收尾 / GA 前体检 | ⬜ |

> 图例：⬜ 未开始 ｜ 🟡 进行中 ｜ 🟢 完成（已过部署自测点）
