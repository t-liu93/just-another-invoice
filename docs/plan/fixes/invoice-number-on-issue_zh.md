# 修复 · 发票编号延迟到「开具时」分配（草稿不占号）

> 🌐 [English](invoice-number-on-issue.md) · **中文**

> Bug 修复类改动 —— **不属于里程碑**（放在 `docs/plan/fixes/` 下，刻意置于 roadmap 里程碑地图之外）。实现前先读 `docs/plan/roadmap.md` §2 全局约束（**尤其红线 4——并发安全编号，以及红线 11——OpenAPI→TS 重生成**）；编号引擎 `services/numbering.py`；发票服务 `services/invoice.py`（`create` / `transition_status` / `clone_quote_to_invoice` 三条路径）。每个原子步骤**自包含**——干净的 agent 仅凭「本文档 + 仓库（CLAUDE.md / memory / 现有代码）+ 作者几句话」即可落地，**不依赖对话历史**。
>
> **状态**：**设计已冻结**（2026-06-22）。作者得空时实现。

## 缘起
2026-06-22 生产走查时，作者看到报价 `Q2026-006`，但实际只有 **5** 张报价——一张被删的草稿把 `005` 永久跳掉了。对**报价**而言这没问题：报价没有法定连号义务，跳号可接受。但排查发现，**发票用的是完全相同的编号机制**，这就是个真问题了：

- 发票号在**创建那一刻**就分配（`allocate_invoice_number`），**草稿（DRAFT）也照分配**，而且有**两条**创建路径：普通创建（`_build_and_persist_invoice`）与报价转发票（`clone_quote_to_invoice`）。
- `delete_invoice` **只允许删 `DRAFT`** 发票（硬删除；号码从不回收——见红线 4）。
- ⇒ 系统唯一允许的删除，恰恰就是会在发票号序列里留下**永久缺口**的那种删除。

一张从未开出去的草稿没有法律存在性，**本就不该占用一个法定发票号**。（EU/荷兰规则只要求每张*已开具*发票带有唯一、成体系的标识；并不强制严格连号——非连号 / UUID 式方案均合规——但在丢弃的草稿上白白咬号、跳号是不可取的。）

**修复**：把发票号的分配点从「创建」挪到 **`DRAFT → SENT`（开具）** 这一步。草稿不带号；删草稿零成本、不留缺口。永远不需要回收号码，红线 4 原封不动。

## 目标与范围
- **目标**：发票**只在开具时**（`DRAFT → SENT`）获得法定号码。`DRAFT` 发票**不带** `invoice_number` / `sequence_number`；删草稿不占号、不跳号。
- **IN（包含）**：
  - 把 `invoice.invoice_number` 与 `invoice.sequence_number` 改为**可空**（Alembic 迁移；`customer_sequence_number` 本就可空）。
  - 把 `allocate_invoice_number` 从**两条**创建路径（`_build_and_persist_invoice` 创建分支 + `clone_quote_to_invoice`）移到 `transition_status` 的 **`DRAFT → SENT`** 分支（幂等——仅当 `invoice_number is None` 时分配；同事务、行锁——红线 4 保持不变）。
  - PDF（`templates/pdf/invoice.html` + `services/pdf.py`）+ 前端：未编号草稿渲染为 **「Concept / 草稿」**（不编造号码）。
  - `schemas/invoice.py`：`invoice_number` / `sequence_number` → 可选；重生成 `schema.d.ts`（红线 11）；前端处理 null。
  - 守卫：**未编号草稿不可发邮件、不可记录收款**（必须先开具）。
  - 测试：仅开具时分配、幂等、**删草稿不跳号**、并发、两条创建路径都延迟分配。
- **OUT（不含 / 顺延）**：
  - **报价（`quote`）不动**——保持创建即分配；无法定连号义务（作者决定）。
  - **不做号码回收 / 计数器回退**——明确否决：那正是红线 4 禁止的、易竞态的 `max+1` 反模式。延迟分配使回收变得没有必要。
  - **不复用 `unique_hash`**——它仍为 M9 公开链接预留（`models/invoice.py`：「Reserved for M9 public link; not active in M5」）。草稿在内部用 `id`（UUID 主键）标识。
  - **草稿上「预览下一个号」**——可选 UI 锦上添花；默认只显示 Concept（不带号）。日后想加，凭现成的只读 `get_next_sequence_info` 即可。
  - **历史重新编号**——不做。已有的带号记录保留其号；只有改动后新建的草稿才不带号。（作者的线上实例当前 **0 张发票**，故无存量数据顾虑；该迁移对任何自托管者都是纯增量 / 非破坏性的。）
- **相关文档**：roadmap §2 红线 4 与 11；`services/numbering.py`；`services/invoice.py`。税务背景：`docs/insight/btw-aangifte-2026-guide.md`（仅针对已开具发票的编号）。

## 冻结决策（D1–D8）
- **D1 · 分配点 = `DRAFT → SENT`**：编号发生在 `transition_status` 中，当 `current == DRAFT and new_status == SENT and inv.invoice_number is None`，在调用方已开启的事务内、提交之前进行；原样复用 `allocate_invoice_number`（行锁）。**幂等**——绝不对已编号的发票重复分配。
- **D2 · 草稿不带号**：创建时（普通创建**与**报价转发票）`invoice_number = NULL`、`sequence_number = NULL`、`customer_sequence_number = NULL`。内部标识 = `id`（UUID 主键）。
- **D3 · 不回收、序列保持单调**（红线 4）：分配只是往后挪了；仍是一条公司序列 + 行锁 + 唯一约束。一旦开具（`SENT`+），发票永不丢号。`CANCELLED` 只能从 `DRAFT` 到达（且 `SENT → {}` 是锁定的），故被取消的发票一定是未编号的——无缺口、无可回收。
- **D4 · 草稿展示 = 「Concept / 草稿」、不带号**（默认）：草稿上绝不显示编造或预览的号码，杜绝被误认作最终法定号的可能。（凭 `get_next_sequence_info` 做的可选灰色预览，是日后的 UI 取舍，不在本次范围。）
- **D5 · 报价不动**：`quote` 保持创建即分配 + 删除跳号。不同单据类型，无法定连号需求。
- **D6 · 可空迁移、非破坏性**：对 `invoice_number` + `sequence_number` 执行 `ALTER COLUMN ... DROP NOT NULL`。唯一约束 `uq_invoice_company_number (company_id, invoice_number)` 不变（PG 把 NULL 视作互不相同，故多张未编号草稿可共存；一旦有号，唯一性照样生效）。已有行保留原值。降级注意：若存在未编号草稿，重新加回 `NOT NULL` 会失败（对一个向前的修复可接受——回填，或禁止降级）。
- **D7 · 契约变更 → codegen**（红线 11）：`InvoiceRead.invoice_number: str | None`、`InvoiceRead.sequence_number: int | None`；跑 `npm run codegen`，CI 校验无漂移；前端 null 处理全部更新。
- **D8 · 开具门控动作**：发邮件、记录收款都要求发票**已编号**（已开具）；对未编号草稿以清晰的 400 拒绝。（BTW 报表在 `services/reporting/btw.py` 中已按 `status ∈ {SENT, COMPLETED}` 过滤营收，草稿天然排除——此处**无需改动**。）

## 数据模型 / 迁移
- **表 `invoice`** —— 唯一改动：`invoice_number TEXT` → 可空；`sequence_number BIGINT` → 可空。`customer_sequence_number` 本就可空。不新增表或列。
- **约束不变**：`uq_invoice_company_number (company_id, invoice_number)` 保留；PG 中 NULL 互不相同 ⇒ 允许多张未编号草稿，且一旦有号唯一性照样生效。
- **Alembic**：单条 revision，对两列做 `DROP NOT NULL`。精神上只向前（降级重新加回 `NOT NULL`，仅在无 NULL 行时安全）。

## 契约
- `InvoiceRead.invoice_number` 与 `InvoiceRead.sequence_number` 变为可选（可空）。⇒ **必须跑 `npm run codegen`**，CI 校验无漂移（红线 11）。不新增端点；现有的 `DRAFT → SENT` 状态迁移端点现在会在服务端触发分配。

## 原子步骤清单
> 每步 = 一个原子改动，过 roadmap §5 DoD。**编号逻辑必须测试**（红线 4）；契约变更 ⇒ codegen 无漂移（红线 11）。

### 步骤 1 · 把发票编号列改为可空（模型 + 迁移）
- **后端**：`models/invoice.py` → `invoice_number: Mapped[str | None]`、`sequence_number: Mapped[int | None]`（`nullable=True`）。Alembic 迁移对两列 `DROP NOT NULL`。
- **迁移**：一条 revision；upgrade 去掉 `NOT NULL`；downgrade 加回（写明「若存在 NULL 草稿则失败」的注意事项）。
- **测试**：插入带 NULL 号码/序号的草稿成功；两张 NULL 号草稿共存（唯一约束 OK）；同公司内重复的非空 `invoice_number` 仍违反唯一约束。
- **盲评点**：① 唯一约束完好 + 允许多张 NULL 草稿；② 没有别的代码路径在 DB 层依赖这两列非空；③ 降级注意事项已写明。

### 步骤 2 · 把分配延迟到 `DRAFT → SENT`
- **后端**：
  - 从**两条**创建路径移除号码分配：`_build_and_persist_invoice`（创建分支，约 L414–425）与 `clone_quote_to_invoice`（约 L614–631）。新发票以 `invoice_number = None`、`sequence_number = None`、`customer_sequence_number = None`、`status = DRAFT` 落库。
  - 在 `transition_status` 中：当 `current == DRAFT and new_status == SENT and inv.invoice_number is None` 时，在同一事务内、`commit` 之前调用 `allocate_invoice_number(...)`，并把返回的号码/序号赋给 `inv`。保留 `IntegrityError → rollback → ValueError` 处理。幂等：已编号则跳过。
- **测试**（编号必须测试）：两条路径创建 → 号码为 `None`；`DRAFT → SENT` → 分配号码且公司序列恰好 +1；**创建一张草稿再删除 → 公司序列不变 → 下一张开具的发票无缺口**（核心回归）；两张草稿先后开具 → 串行化、连续、无重号；再开具路径（`CANCELLED → DRAFT → SENT`）恰好分配一次。
- **盲评点**：① 创建时**绝不**分配号码（grep 两条路径）；② 幂等——对已编号发票再迁移不会重复分配；③ 分配仍是行锁 + 同事务（红线 4）；④ 两条创建路径（普通 + 报价转化）都延迟；⑤ 删草稿不留缺口（断言序列 `next_value`）。

### 步骤 3 · 未编号草稿的 PDF / 单据渲染
- **后端**：`templates/pdf/invoice.html` —— 在打印 `invoice.invoice_number` 处（`<title>` 约 L5，号码行约 L48–49），当其为 null 时回退到 `labels.draft` 字符串（「Concept」/「草稿」）。把该 label 加入 `services/pdf.py` 组装的 i18n 标签集（EN / NL / ZH）。
- **测试**：渲染草稿（null 号码）→ 无真实号码、两处都显示 Concept 标签；渲染 `SENT` 发票 → 显示号码。
- **盲评点**：① 两处模板都处理到；② 草稿上无编造/预览号码（D4）；③ 各语言 label 齐备。

### 步骤 4 · Schema、前端，以及开具门控守卫
- **后端**：`schemas/invoice.py` → `invoice_number: str | None`、`sequence_number: int | None`。加守卫：发邮件（`services/email.py` 发送入口）与记录收款（`services/payment.py`）对 `invoice_number is None` 的发票以清晰的 400 拒绝（须先开具）。（BTW 已排除草稿——无需改动。）
- **前端**：重生成 `schema.d.ts`（`npm run codegen`，无漂移）。更新 5 处引用点（`views/invoices/InvoiceList.vue`、`views/invoices/InvoiceEdit.vue`、`views/payments/PaymentList.vue`、`stores/invoices.ts`），号码为 null 时显示「草稿 / Concept」（或破折号）；确保列表排序/搜索能容忍 null。
- **测试**：后端守卫测试（对未编号草稿发邮件 / 收款 → 400）；`npm run build` 通过。
- **盲评点**：① `schema.d.ts` 已重生成、无漂移；② 5 处前端引用全部 null 安全（不崩、不出现字面 "undefined"）；③ 邮件 + 收款守卫齐备且有测试；④ 确认 BTW 不变（草稿本就排除）。

### 步骤 5 · 回归 + 收尾
- **测试**：完整后端套件（`pytest` 单元 + 集成）全绿；端到端回归：创建一张草稿（号码 `None`）→ 删除 → 再建一张草稿 → `DRAFT → SENT` → 它拿到下一个号、且**不继承被删草稿留下的缺口**。
- **盲评点**：① 该回归确实证明「删草稿不跳号」（本修复存在的理由）；② 为「创建不产生号码」而更新的既有发票测试，是「按约定修正」而非「迁就实现」。

## 🟢 部署自测点（验收）
> 默认 dev Compose（`docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d`）全栈起来，再手动走查。
- 新建一张发票 → 在列表、编辑器、（预览）PDF 里都显示为 **不带号的 Concept / 草稿**。
- 删掉该草稿 → 再建一张发票 → 开具它（`DRAFT → SENT`）→ 它拿到**下一个**号，且**没有**因被删草稿造成的缺口。
- 把一张报价转成发票 → 结果是一张**不带号的 DRAFT**；开具时才分配号码。
- 尝试对未编号草稿发邮件 / 记录收款 → 被**拦截**并给出清晰提示；开具后两者都可用。
- 对包含一张草稿的时间段跑 BTW 报表 → 该草稿**不**计入（行为不变）。

## 验收结论（收尾时填）
- 完成日期：
- 实现（提交 / 模式）：
- 自动化（`ruff` + `mypy --strict` + `pytest` + codegen 无漂移 + `npm run build`）：
- 验收（自测点是否通过）：
- 已知遗留 / 顺延：
