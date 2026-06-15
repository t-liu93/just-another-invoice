# M7 · 收款（Payments）

> 🌐 [English](M7.md) · **中文**

> 进入本里程碑前 JIT 产出。先读 `docs/plan/roadmap.md` §2 全局约束 + M7 那一格；分析文档权威章节：§2.3 状态机（双状态线）、§2.4.2 多币种、§2.5 金额精度、§7.4.5 汇率与 VAT。
> **M5 已完成**：`invoice` 表已含 `paid_status`（UNPAID/PARTIALLY_PAID/PAID）、`due_amount`/`base_due_amount`（M5 初始化为全额，`services/invoice.py` 注释明写「M5: full amount owed until M7」）、`status`（DRAFT/SENT/COMPLETED/CANCELLED）。`services/invoice._ALLOWED_TRANSITIONS` 把 `SENT`→∅、`COMPLETED`→∅ 留空，`transition_status` 显式 422 拒绝手动设 `COMPLETED`，注释写「COMPLETED is driven by payments (M7)」——**M7 来接这个**：收满由收款引擎驱动 `→ COMPLETED`。
> **M4 已完成**：`payment_method` 字典（`models/dictionary.py::PaymentMethod`，company 内、`active` 标记）。
> **本里程碑取向**：给发票挂一条**收款子记录线**；一个**与税无关、与编号无关**的 `services/payment` 重算引擎，按收款累计权威重算 `due_amount` / `base_due_amount` / `paid_status` / 生命周期 `status`；收款记录可增改删、即时重算。**单一本位币**（外币顺延，同 M5/M6）。

## 执行模型（两种实现方式 · 协议固化在 CLAUDE.md，本文不重复）
> 自 M7 起本项目支持两种实现方式；**跨里程碑通用的执行协议在 CLAUDE.md（本轮结束后补写）**，这里只放指针 + 本里程碑特有的「每步审查要点」。
> 1. **Agent orchestration**：Opus orchestrator 逐步 spawn 干净 Sonnet implementer（空对话上下文）→ 出中文实现简报 → 干净 Opus reviewer **盲审**（对照本文该步「审查要点」+ diff + 简报）→ 有 finding 则 Sonnet fixer 返工，循环到无新 finding → 下一步；**整个 milestone 完成后**才输出一份 milestone 级实现报告供作者人工 walkthrough 审计。
> 2. **人工实现 + agent review**：作者人工实现每一步（或手工调一个空上下文 Sonnet agent 实现），再调 agent 对照该步「审查要点」review。
> **本文对两种方式的保证**：
> - **每个原子步骤自包含**——干净 agent 只靠「本文档 + 仓库（CLAUDE.md / 记忆 / 既有代码）+ 作者口头几句」即可落地，**不依赖任何对话历史**；每步给清**目标 / 契约 / 要镜像的既有实现文件 / 不变量 / 必覆盖测试 / 盲审要点**。
> - **没有逐步人工 walkthrough**——逐步安全网 = 自动化测试（每步「必覆盖测试」是主要门）+ 盲审；人工 walkthrough 收敛到**里程碑末一次**（见文末「🟢 部署自测点」）。

## 目标与范围
- **目标**：对一张已发出的发票录入一笔或多笔收款（首/中/尾款），后端权威重算欠款与收款状态，收满时自动把生命周期推进到 `COMPLETED`；收款记录可编辑/删除，删改即时回算（可退回 `PARTIALLY_PAID` 并把 `COMPLETED` 退回 `SENT`）。**全程算钱在后端、单一本位币**。
- **纳入（IN）**：
  - **Payment 实体（发票子记录）**：`payment`（挂 `company_id` + `invoice_id`，cascade 于发票）。记录 `payment_date` / `amount` / 收款方式快照 / 备注。
  - **`services/payment` 重算引擎（与税无关、纯状态/金额）**：`recompute_invoice_payment_state(invoice)`——按该发票所有 payment 的 `amount` 累计，重算 `due_amount`/`base_due_amount`/`paid_status`/`status`；`Decimal`，**由已舍入的逐笔 amount 求和、不二次舍入**（同 M5/M6.5 口径）。**算钱/状态逻辑必须单测**（红线 1）。
  - **生命周期联动（决策 D3，作者已定）**：`paid_status == PAID` ⇒ `status = COMPLETED`；收款被改小/删除导致未收满 ⇒ `COMPLETED` 退回 `SENT`。**只由引擎驱动**，不走手动 `transition_status`（后者仍 422 拒绝手动 COMPLETED）。
  - **收款可变性（决策 D4，作者已定）**：payment 可 `PUT`/`DELETE`，每次都重算。无红冲/反向分录。
  - **守卫**：① 前置——录/改款要求发票 `status ∈ {SENT, COMPLETED}`（已发出），对 `DRAFT`/`CANCELLED` 录款 → `422`。② 超额——一笔录/改后会让累计收款 > 发票含税合计 → `422`（`due_amount` 不允许变负；信用/退款留后续）。
  - **收款方式**：`payment_method_id` 选 M4 字典；落库时**快照** `payment_method_name`（删字典项不破坏历史，FK `SET NULL`）。
  - **概览**：`GET /payments` 全局列表（按客户 / 方式 / 日期范围过滤 + 分页），给一个「收款」概览页用。
  - **前端**：发票详情页**收款面板**（列出该发票收款 + 增/改/删 + 实时 `due_amount`/`paid_status` badge）；顶层「收款」概览页（复用上面的全局列表）。
- **不纳入（OUT / 留到后续）**：
  - **独立 / 未分配收款（standalone，不挂发票）** → 不做。`invoice_id` 在 v1 **NOT NULL**（每笔收款必属一张发票）。roadmap 提到「关联发票 / 独立」，独立那支留作后续 additive（加 nullable 列即可，旧数据不动）。
  - **超额 / 信用余额 / 退款（负数收款）** → 不做（超额直接 `422`）。
  - **正式收款编号（receipt number）** → 顺延 M9（收款收据 PDF 才需要顺序号）；M7 的 payment 用 `id` + 日期标识，不占编号序列。
  - **外币收款 / 收款日汇率 / 汇兑损益（§7.4.5 现金口径）** → 顺延（同 M5/M6 单一本位币）。`payment.currency = 本位币`、`exchange_rate = 1`、`base_amount = amount`；非本位币金额后端拒绝。
  - **在线支付 / 银行流水自动对账** → vNext（见 roadmap §4.x）。
  - **收款收据 PDF / 邮件** → M9。
- **对应文档**：roadmap M7 / §2（红线 **1 / 2 / 3 / 5 / 10**）；分析文档 §2.3 / §2.4.2 / §2.5 / §7.4.5；M5 发票实现（`paid_status`/`due_amount`/`status`/`_ALLOWED_TRANSITIONS`）。

## 本轮拟定的产品与技术决策（动手前已与作者共定 · 2026-06-12）
- [x] **D1 · 范围**：M7 拆透；同轮另出 M8/M9 骨架（见 `M8.md`/`M9.md`，标注未冻结）。
- [x] **D2 · 单一本位币**：payment 不带币种/汇率选择列；`currency = company.base_currency`、`exchange_rate = 1`、`base_amount = amount`；非本位币拒绝。FX 与「收款日 EUR 快照」留后续 additive。
- [x] **D3 · 收满自动 → COMPLETED**：抄 InvoiceShelf 双状态；`paid_status=PAID` ⇒ `status=COMPLETED`；退回未收满 ⇒ `COMPLETED→SENT`。仅引擎驱动。
- [x] **D4 · 收款可编辑/删除、即时重算**：无不可变约束、无反向分录（v1 自用从简）。
- [x] **D5 · `invoice_id` NOT NULL**：v1 仅做「挂发票」的收款；独立收款顺延。
- [x] **D6 · 超额拒绝（`422`）**：`due_amount` 恒 ≥ 0；不建信用余额。
- [x] **D7 · 录款前置 = 发票已发出**：`status ∈ {SENT, COMPLETED}`，否则 `422`。
- [x] **D8 · 收款方式 FK `SET NULL` + name 快照**：与 `product`/`unit` 同范式（删字典不破坏历史）；`vat_rate` 那种 `RESTRICT` 不用在这里。
- [x] **D9 · 舍入口径同 M5**：逐笔 `amount` 各自 3 位（`quantize_money`，`ROUND_HALF_UP`）；`paid_total`/`due_amount` = 已舍入逐笔值求和差，**不二次舍入**。
- [x] **D10 · 金额一律 `Decimal`/`NUMERIC(18,3)`**（红线 1）：`amount`/`base_amount` `NUMERIC(18,3)`；`exchange_rate` `NUMERIC(18,8)`，对齐 `invoice`。
- [x] **D11 · 删发票级联删收款**（红线 3）：`payment.invoice_id` FK `ON DELETE CASCADE`；但发票删除仍只允许 `DRAFT`（M5 既有约束），而 `DRAFT` 不可能有收款（D7），所以 cascade 是兜底而非常态。

## 契约（先行 · 前后端各自对着写）
> 业务端点一律 `/api/v1/*`。改契约就 `npm run codegen` 重生成 `schema.d.ts`，CI drift 关强制无漂移（红线 11）。沿用 M1 cookie 会话 + `current_mfa_user`；owner-only 复用 `api/invoices.py::_owner_only` 同款。所有写端点 `company_id` 由 service 注入。

**录入 / 列出（步骤 1）**
- `POST /api/v1/invoices/{invoice_id}/payments` body `PaymentInput` → `201 InvoicePaymentsResponse`（录一笔 + 重算；前置/超额不满足 → `422`；跨公司发票 → `404`）。
- `GET /api/v1/invoices/{invoice_id}/payments` → `200 InvoicePaymentsResponse`（该发票全部收款 + 当前重算口径）。
- `GET /api/v1/payments/{id}` → `200 PaymentRead`；跨公司 → `404`。

**编辑 / 删除（步骤 2）**
- `PUT /api/v1/payments/{id}` body `PaymentInput` → `200 InvoicePaymentsResponse`（改一笔 + 重算；超额 → `422`）。
- `DELETE /api/v1/payments/{id}` → `200 InvoicePaymentsResponse`（删一笔 + 重算）。
  - **注意**：删除返回 `200 + 聚合体`（而非惯常 `204`），让前端面板一次拿到回算后的 `due_amount`/`paid_status`/`status`，省一次 refetch。这是本里程碑有意的小约定。

**概览（步骤 3）**
- `GET /api/v1/payments` query `{q?, customer_id?, payment_method_id?, date_from?, date_to?, limit?=50, offset?=0, sort_by?: "payment_date"|"created_at"="payment_date"}` → `200 PaymentListResponse {items, total}`（`q` 搜发票号/客户名 JOIN）。

**核心 schema 形状**（落 `schemas/payment.py`）
- `PaymentInput { payment_date: date, amount: Decimal, payment_method_id?: uuid, reference?: text, note?: text }`
  - 校验：`amount > 0`；`payment_date` 必填；`reference`/`note` 是 `text`（红线 10）。**只收原始输入，不收任何 base_*/状态**（算钱在后端，红线 1）。
- `PaymentRead { id, invoice_id, invoice_number, payment_date, amount, base_amount, currency, payment_method_id?, payment_method_name?, reference?, note?, created_at, updated_at }`。
- `InvoicePaymentsResponse { invoice_id, invoice_number, total_incl_vat, base_total_incl_vat, paid_total, base_paid_total, due_amount, base_due_amount, paid_status, status, items: PaymentRead[] }`（`items` 按 `payment_date, created_at` 升序）。
- `PaymentListItem { id, invoice_id, invoice_number, customer_id, customer_name, payment_date, amount, payment_method_name?, created_at }`。
- `PaymentListResponse { items: PaymentListItem[], total }`。

## 算钱与状态规则（M7 钉死 · `services/payment`）
> 与税完全无关；schema 只校验形状，所有金额/状态由 service 算。引擎是**纯函数 + 一个落库 wrapper**：先实现可单测的纯重算，再由 service 把结果写回 `invoice`。

- **累计**：`paid_total = Σ payment.amount`；`base_paid_total = Σ payment.base_amount`（逐笔已是 3 位，求和不二次舍入，D9）。
- **欠款**：`due_amount = invoice.total_incl_vat − paid_total`；`base_due_amount = invoice.base_total_incl_vat − base_paid_total`。由超额守卫保证 `≥ 0`。
- **收款状态**：`due_amount == 0` → `PAID`；`paid_total == 0` → `UNPAID`；否则 → `PARTIALLY_PAID`。
- **生命周期（仅引擎驱动，D3）**：
  - `paid_status == PAID` 且 `status == SENT` ⇒ `status = COMPLETED`；
  - `paid_status != PAID` 且 `status == COMPLETED` ⇒ `status = SENT`（退回）；
  - **绝不触碰** `DRAFT` / `CANCELLED`（它们按 D7 不可能进入收款流）。
- **超额守卫（录/改时，D6）**：若该操作后 `paid_total > invoice.total_incl_vat` → `422`（文案：欠款不足以容纳本次收款）。
- **前置守卫（录/改时，D7）**：`invoice.status ∈ {SENT, COMPLETED}`，否则 `422`（文案：请先发出发票再登记收款）。
- **单一本位币（D2）**：`base_amount = amount`、`currency = 发票 currency = 公司本位币`、`exchange_rate = 1`。

## 数据模型 / 迁移
> UUID PK；根挂 `company_id`（红线 2）；子表 FK cascade（红线 3）；金额列 `NUMERIC`；文本列 `text`（红线 10）。**一条 additive 迁移**：建 1 张表 + 索引；**不动 `invoice` 任何列**（`due_amount`/`paid_status`/`status` 已就位）。无新枚举（复用 `InvoicePaidStatus`/`InvoiceStatus`）。

- **`payment`**：`id`；`company_id` FK→`company.id`(`RESTRICT`) index；`invoice_id` FK→`invoice.id`(`CASCADE`) NOT NULL index；`payment_date` Date NOT NULL index；`amount` `NUMERIC(18,3)` NOT NULL；`base_amount` `NUMERIC(18,3)` NOT NULL；`currency` `String(3)` NOT NULL；`exchange_rate` `NUMERIC(18,8)` NOT NULL server_default `1`；`payment_method_id` FK→`payment_method.id`(`SET NULL`) nullable；`payment_method_name` text nullable（快照）；`reference` text nullable；`note` text nullable；`creator_id` FK→`user.id`(`SET NULL`) nullable；timestamps。索引 `ix_payment_company_id` / `ix_payment_invoice_id` / `ix_payment_payment_date`。
- **删除安全**：删 `invoice` → DB cascade 删其 `payment`（红线 3）；删 `payment_method` → 引用它的 payment `SET NULL`（保 `payment_method_name` 快照）；删 `user` → `creator_id` `SET NULL`。
- **RLS**：继续留口不开；服务层集中用当前用户 `company_id`（红线 2）。
- **镜像参照**：建表/relationship cascade 对齐 `models/invoice.py`（同款 `_MONEY = Numeric(18,3)`、`company_id` RESTRICT、子表 CASCADE）；迁移写法对齐 `backend/alembic/versions/` 里 M6.5 那条 estimate 三表迁移。

---

## 原子步骤清单
> 每步 = 一个原子改动（CI 绿即可合 `main`），过 roadmap §5 DoD。**每步自包含**：给清要镜像的既有文件 + 不变量 + 必覆盖测试 + 盲审要点。**`services/payment` 重算逻辑必须有单测**。

### 步骤 1 · `payment` 表 + 迁移 + `services/payment` 重算引擎 + 录入/列出/读取
- **目标**：能对一张已发出发票录入一笔收款并看到欠款/状态被后端权威回算。
- **契约**：`PaymentInput` / `PaymentRead` / `InvoicePaymentsResponse`；`POST/GET /invoices/{id}/payments`、`GET /payments/{id}`。
- **实现任务**：
  - **后端**：
    - `models/payment.py`：`Payment` ORM（按上「数据模型」；relationship cascade 与 DB cascade 对齐，**镜像 `models/invoice.py`**）；在 `models/__init__.py` 注册。
    - `schemas/payment.py`：上述 schema（**镜像 `schemas/invoice.py`** 的 Decimal/校验风格）。
    - `services/payment.py`：① 纯函数 `recompute_payment_state(total_incl_vat, base_total_incl_vat, payments) -> {paid_total, base_paid_total, due_amount, base_due_amount, paid_status, new_status(curr_status)}`（**可独立单测，不碰 DB**）；② `record_payment(session, invoice_id, company_id, body, creator_id)`：跨公司取发票（`404`）、前置守卫（D7→`422`）、超额守卫（D6→`422`）、落 payment（快照收款方式 name + base 镜像）、调纯函数、写回 `invoice.{due_amount,base_due_amount,paid_status,status}`、返回聚合体；③ `list_invoice_payments` / `get_payment`。
    - `api/payments.py`：薄路由（owner-only、`company_id` 注入），挂进 `api/__init__.py`。
  - **前端**：仅 `npm run codegen`（UI 放步骤 4）。
- **迁移**：建 `payment` + 三索引（一条 additive 迁移；不动 `invoice`）。
- **测试（必覆盖）**：纯函数——UNPAID/PARTIALLY/PAID 三档边界、收满 `SENT→COMPLETED`、`paid_total==0`、多笔求和无二次舍入、`due` 恒 ≥0；service——录款后 `invoice.due_amount`/`paid_status`/`status` 落库正确；超额 `422`；对 `DRAFT`/`CANCELLED` 发票录款 `422`；跨公司发票 `404`；owner-only；收款方式 name 快照（删字典项后 payment.name 仍在）。
- **审查要点（盲审）**：① 重算是**纯函数 + 落库 wrapper** 两层，纯函数被单测覆盖（红线 1）；② `base_amount=amount`、`exchange_rate=1`、非本位币被拒（D2）；③ 金额全 `Decimal`/`NUMERIC(18,3)`、求和不二次舍入（D9）；④ `company_id` 由 service 注入、无散落 `where company=`（红线 2）；⑤ 生命周期只在引擎里改，未走 `transition_status`，未触碰 DRAFT/CANCELLED（D3）；⑥ `text` 列用对（红线 10）；⑦ 契约与 `schema.d.ts` 一致、无漂移。
- **DoD**：见 roadmap §5。

### 步骤 2 · 编辑 / 删除收款 + 即时重算 + 生命周期回退
- **目标**：改小/删除一笔收款时欠款回升、`PAID→PARTIALLY_PAID`、`COMPLETED→SENT` 自动退回。
- **契约**：`PUT /payments/{id}`、`DELETE /payments/{id}`（均 → `200 InvoicePaymentsResponse`）。
- **实现任务**：
  - **后端**：`services/payment.py` 加 `update_payment`（取款→其发票→改字段→超额守卫→重算→写回聚合体）、`delete_payment`（删款→重算→写回）；`api/payments.py` 加两路由。删除返回聚合体（非 204，见契约注）。
  - **前端**：`npm run codegen`（UI 步骤 4）。
- **迁移**：无。
- **测试（必覆盖）**：首款+尾款收满→`PAID`/`COMPLETED`；改尾款变小→`PARTIALLY_PAID` 且 `COMPLETED→SENT`；删一笔→欠款回升、状态回退；删到零笔→`UNPAID`、`status` 回 `SENT`；编辑触发超额→`422`；跨公司 payment `404`；owner-only。**集成测试**（`db_client`，镜像 `tests/test_quote_convert_reactivate_integration.py` 风格）。
- **审查要点（盲审）**：① 编辑/删除复用步骤 1 同一纯重算函数，无重复实现（红线/复用）；② 回退路径 `COMPLETED→SENT` 正确且不误碰 `DRAFT/CANCELLED`；③ 超额守卫在编辑路径同样生效；④ 全部走 `Decimal`、无漂移；⑤ 契约无漂移。
- **DoD**：见 roadmap §5。

### 步骤 3 · 全局收款概览 `GET /payments` + 过滤
- **目标**：一个跨发票的收款列表，支持按客户/方式/日期过滤。
- **契约**：`GET /payments`（`PaymentListResponse`，query 见契约）。
- **实现任务**：
  - **后端**：`services/payment.py::list_payments`（JOIN invoice/customer 取 `invoice_number`/`customer_name`；`q`/`customer_id`/`payment_method_id`/`date_from`/`date_to` 过滤；分页 `total`；排序）；`api/payments.py` 加路由。**镜像 `services/invoice.py` 的列表/分页/排序**。
  - **前端**：`npm run codegen`。
- **迁移**：无。
- **测试（必覆盖）**：过滤组合、空 `q`、日期范围边界、分页 `total`、排序、跨公司隔离、owner-only。
- **审查要点（盲审）**：① 过滤全部 `company_id` 收敛；② 列表项只暴露概览字段（无内部多余泄漏，本里程碑无敏感字段但保持最小暴露）；③ 分页/排序与既有列表端点一致；④ 契约无漂移。
- **DoD**：见 roadmap §5。

### 步骤 4 · 前端：发票收款面板 + 收款概览页
- **目标**：在发票详情就地登记/编辑/删除收款看实时状态；顶层有收款总览。
- **契约**：对齐步骤 1–3 的 `schema.d.ts`。
- **实现任务**：
  - **后端**：必要小修（错误码 / 文案 / sort）。
  - **前端**：
    - `stores/payments.ts`；发票详情页加**收款面板**（列出 `items` + 「登记收款」表单 [日期/金额/方式/备注] + 行内编辑/删除 + 确认框；面板头显示 `paid_total`/`due_amount` + `paid_status` badge；金额/状态**只读后端聚合体**，前端不本地权威算钱）。
    - 顶层导航加「收款 / Payments」概览页（搜索 + 客户/方式/日期过滤 + 分页 + 点行跳到对应发票）。**镜像 `views/` 下既有列表页（如 estimates/quotes 列表）的结构**。
    - 录款成功/编辑/删除后用返回的聚合体就地刷新发票状态（badge、欠款、可否再录）。
  - **迁移**：无。
- **测试 / 自检**：`npm run build`；`schema.d.ts` 无漂移。（无逐步人工 walkthrough——见文末里程碑级自测点。）
- **审查要点（盲审）**：① 前端**不本地权威算钱**，金额/状态一律取后端聚合体（红线 1）；② 录款表单只发原始输入（日期/金额/方式/备注）；③ 对 `DRAFT` 发票 UI 不显示「登记收款」或显式禁用并提示先发出；④ `prod build` 下动态 prop 按钮不丢 `@click`（见记忆 [[vue-loading-prop-vif-prod-bug]]）；⑤ i18n key 占位齐全（具体文案步骤 5 补）。
- **DoD**：见 roadmap §5。

### 步骤 5 · 收尾：i18n + UX + 部署自测点
- **后端**：补 docstring（payment 重算/守卫）；错误映射、空 `q`、排序收尾；确认金额列 `NUMERIC`、文本列 `text`。
- **前端**：`payments.*` / `recordPayment` / `paidStatus` / `due` 文案进 `en.json` + `zh.json`；收款面板移动端布局、空态、状态 badge 统一、确认框文案。
- **验收**：走下方「🟢 部署自测点」（里程碑级，作者一次性人工审计）；`ruff` / `mypy --strict` / `pytest`（含集成）/ `npm run build` / codegen freshness 全绿。
- **DoD**：见 roadmap §5。

## 🟢 部署自测点（里程碑验收 · 作者末轮一次性人工 walkthrough）
> 逐步不再人工走（逐步靠测试 + 盲审）；这一组是**整个 M7 完成后**作者人工审计用。本地集成：`docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build`；浏览器 `http://localhost:${APP_HOST_PORT:-8000}`。

1. **部分收款**：对一张 `SENT` 发票登记首款（< 含税合计）→ `paid_status=PARTIALLY_PAID`、`due_amount` 相应减少、`status` 仍 `SENT`。
2. **收满自动 COMPLETED**：再登记尾款收满 → `paid_status=PAID`、`due_amount=0`、`status` 自动变 `COMPLETED`。
3. **多次付款**：三笔（首/中/尾）累加正确，`paid_total = Σ amount`，无几分钱漂移。
4. **编辑/删除即时回退**：把尾款改小 → 退回 `PARTIALLY_PAID` 且 `COMPLETED→SENT`；删一笔 → 欠款回升、状态回退；删到零笔 → `UNPAID`/`SENT`。
5. **超额拦截**：登记超过当前欠款的金额 → 被拒（`422`），`due_amount` 不变负。
6. **前置守卫**：对 `DRAFT` 发票尝试登记收款 → 被拒并提示先发出发票。
7. **收款方式快照**：选 M4 一个收款方式登记 → 落 name 快照；去 M4 删该方式 → 历史 payment 仍显示原 name（FK `SET NULL`）。
8. **概览页**：收款概览按客户/方式/日期过滤；点一行跳到对应发票详情；发票面板与概览数字一致。
9. **隔离 / cascade / 单币种**：跨公司取 payment `404`；删一张 `DRAFT` 发票正常；尝试非本位币金额被拒（`base_*` 与原币一致）。
10. CI 四关全绿；`schema.d.ts` 无漂移。

## 验收结论（收尾时回填）
- **完成日期**：2026-06-13
- **验收**：通过。**执行方式 = orchestrator 全自动模式**（5 个原子步骤，每步「干净 Sonnet implementer → 干净 Opus 盲审 → Sonnet fixer 返工 → 复审 → per-step autosquash」）。**盲审收敛**：步骤 1 抓到 1 个阻断 bug（录款金额未量化→收满发票卡死，已修+加一致性测试）；步骤 2/3/4 各 1 轮返工（scale 一致性 / ruff B007 / 概览页日期时区偏移）后 PASS；步骤 5 零 finding 直接 PASS。**自动化（最终 HEAD 第一手复验）**：`ruff` 绿、`mypy --strict` 绿（76 文件）、单测 **404 passed**、集成 **641 passed**、`schema.d.ts` 无漂移、`npm run build` 绿。**里程碑级人工 walkthrough**：部署自测点 **1–8 通过**；#9 单币种拒绝的 **UI 验收**待 FX 前端落地后补（隔离/cascade 由集成测试覆盖）。详见 `review-notes/M7-report.md`（实现报告）与 `review-notes/M7-acceptance.md`（验收报告）。
- **walkthrough 期间两处小修**（一并提交）：① 概览页操作列列宽溢出（`PaymentList.vue` 100→140）；② 收款 422 错误文案中文→英文（对齐仓库「后端错误英文」约定）。
- **walkthrough 发现的深层问题 → 顺延 M7.5**：真实发票 `F2026-009` 含税总额存为 3 位（`3865.166`）、UI 显示 `3865.17`，收款按「分」走永远凑不满。根因是 M5 把应付总额也只量化到 3 位、未落到货币最小单位（分）。本轮 M7 不改引擎，治本放 **M7.5**（货币舍入口径修正，方法 B，M8 前）——见 `docs/plan/milestones/M7.5.md`。
- **已知遗留 / 顺延项**：
  - **收款 sub-cent 不可收满（3 位总额）→ M7.5（货币舍入口径修正）。**
  - **单币种拒绝的 UI 验收 → 待 FX 前端落地后补走自测点 #9。**
  - 独立 / 未分配收款（不挂发票）→ 后续 additive（`invoice_id` 改 nullable）。
  - 超额 / 信用余额 / 退款（负数收款）→ 后续。
  - 正式收款编号 + 收款收据 PDF / 邮件 → M9。
  - 外币收款 / 收款日汇率 / 汇兑损益（§7.4.5 现金口径）→ 顺延。
  - 在线支付 / 银行流水自动对账 → vNext（roadmap §4.x）。
