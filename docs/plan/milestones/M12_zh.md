# M12 · 发票生命周期扩展：预付款/最终发票与贷项通知单

> 🌐 [English](M12.md) · **中文**

> **状态**：🟡 已于 2026-08-28 与作者冻结设计；尚未开始实现。本文是 M12 实现时的权威设计。只有所有原子步骤、自动化门禁、盲审和作者最终 walkthrough 全部完成后，才可关闭验收。

## 执行模式

- `AGENTS.md` 规定的默认模式仍是手动模式。只有作者明确要求编排模式（orchestrator mode）或直接生成时，才运行自动 implementer/reviewer/fixer 循环。
- 在编排模式下，每一步都使用全新的 implementer，产出一份中文实现简报和一个实现提交；全新的 reviewer 只能看到本设计、Roadmap、该简报和本步 diff；有发现时交给全新的 fixer 并产生 `--fixup` 提交；复审最多五轮；收敛后对该步 autosquash。
- OpenAI harness 的默认角色映射为：orchestrator/reviewer 使用 `gpt-5.6-sol` + `xhigh`，implementer/fixer 使用 `gpt-5.6-terra` + `high`。
- 每步简报只保存在 `review-notes/M12-step<n>-impl.md`。所有步骤完成后写 `review-notes/M12-report.md`；作者根据该报告只做一次里程碑最终 walkthrough。

## 依赖与现有能力

- **M5/M6 单据**：发票/报价 CRUD、持久化的行与税务快照、报价接受以及 quote→DRAFT-invoice 转换。
- **发票编号修正**：发票号只在发出时分配，使用现有并发安全的序列/设置链路。
- **M7/M7.5 付款与金额**：多笔付款、付款方式快照、后端 `Decimal` 计算、最小货币单位舍入以及应付/已付状态重算。
- **M9 输出**：按 locale 解析的 PDF 预览/下载、邮件附件、清洗和 `email_log`。
- **M10 报表**：基于已发出普通发票的 BTW、ICP、P/L 和 Dashboard 投影。
- **M11.5 仅凭证定金**：来源为 Quote 的付款、不可变 Quote 来源、混合税率 `payment_tax`、quote→invoice 挂接、最终发票抵扣以及非 VAT 收款凭证。
- **Roadmap 不变量**：契约优先 OpenAPI、金额计算仅在后端、规范化税表、PostgreSQL RLS、DB/ORM 级联、发出时编号以及生产环境 additive migration 仍为硬要求。

## 目标与范围

**目标**：把现有发票生命周期扩展为一套可审计的单据家族，支持普通发票、正式分阶段预付款/最终开票、更正、退款和精确输出留存，同时不改变历史已发出单据，也不重复计算现金、VAT 或收入。

纳入范围：

- `STANDARD`、`ADVANCE`、`FINAL` 和 `CREDIT_NOTE` 四种正式单据类型。
- 每张已接受 Quote 都有明确且不可变的开票模式：直接开票、M11.5 仅凭证定金，或正式预付款/最终开票。
- 一条正式 Quote 链可有一张或多张 Advance Invoice，之后最多一张 Final Invoice。
- Advance 支持 gross amount 和 percentage 输入，后端依据已接受 Quote 的快照做确定性分配。
- 针对任意已发出 Standard、Advance 或 Final Invoice 的全额和受控部分 Credit Note。
- 与 Credit Note 关联的实际 Refund 台账记录，以及 Refund Confirmation PDF/邮件。
- 受引导的直接/Advance 重开、纠正错误 Credit Note 的补偿发票，以及整个正式项目取消时的批量草稿生成。
- 相互独立的生命周期、结算与冲销状态；项目/单据链汇总；只追加的单据链生命周期事件。
- BTW/ICP/P&L/Dashboard 只计一次、结构化更正期间 warning、发出时交易方快照以及精确 PDF 成品留存。
- 一个统一的发票列表（类型筛选/徽标），以及完整的 Quote/单据链时间线。

明确排除并延期：

- Standalone Credit Note、未分配客户余额和客户 credit wallet。
- 对尚未转换的 M11.5 Quote 定金退款，或把 receipt-only 付款移动/重新应用到其他单据。
- 超额付款、用户输入负数付款以及银行流水对账。
- 多币种/FX 以及正式跨境 Advance/Final 开票。
- 按选中行或里程碑输入 Advance，以及一张 Quote 同时存在多张未关闭 Advance DRAFT。
- 第五种 `DEBIT_NOTE` 类型，或对 Credit Note 再开 Credit。
- 应用内管理 VAT 申报状态、申报期间锁定或自动提交 suppletie。
- 荷兰语 UI/PDF locale；M12 只交付英文和中文，未来增加 NL locale 前重新核验面向荷兰的术语。

## 法律与术语依据

本设计记录产品行为，不构成法律意见。实现与评审必须保留下列荷兰官方依据：

- 荷兰税务局的[发票要求](https://www.belastingdienst.nl/wps/wcm/connect/bldcontentnl/belastingdienst/zakelijk/btw/administratie_bijhouden/facturen_maken/factuureisen/factuureisen)要求的内容包括发票日期、一个或多个序列中的唯一连续编号、交易双方/VAT 信息，以及与发票日期不同时的供货或预付款日期。
- 荷兰税务局的[预付款发票指南](https://www.belastingdienst.nl/wps/wcm/connect/bldcontentnl/belastingdienst/zakelijk/btw/administratie_bijhouden/facturen_maken/factuureisen/aangepaste_regels_facturen/u_verstuurt_voorschotnotas)说明预付款通知单上的 VAT，以及在最终发票中收取剩余金额/VAT 的做法。
- 根据荷兰税务局的[发票制 VAT 规则](https://www.belastingdienst.nl/wps/wcm/connect/bldcontentnl/belastingdienst/zakelijk/btw/btw_aangifte_doen_en_betalen/bereken_het_bedrag/hoe_berekent_u_het_btw_bedrag/factuurstelsel)，发票/依法必须开具的预付款发票日期以及实际收到自愿预付款的时间决定相关 VAT 事件。
- 荷兰税务局的[VAT 更正指南](https://www.belastingdienst.nl/wps/wcm/connect/bldcontentnl/belastingdienst/zakelijk/btw/btw_aangifte_doen_en_betalen/aangifte_corrigeren/)区分不同更正方式；由于 JAI 不跟踪已申报 return，M12 只发出结构化 warning，绝不声称某项更正已经申报。
- 当前[荷兰行政规则](https://wetten.overheid.nl/BWBR0051015?g=2026-01-01&labelid=17326184&z=2026-03-10)使用 `creditfactuur`。参考的荷兰概念为 `factuur`、`voorschotnota`、`eindfactuur`/`definitieve factuur` 和 `creditfactuur`；它们不会在 M12 中引入 NL 应用语言。

冻结的 EN/ZH 产品术语：

| 类型 | 英文 | 中文 |
| --- | --- | --- |
| `STANDARD` | Standard Invoice | 普通发票 |
| `ADVANCE` | Advance Invoice | 预付款发票 |
| `FINAL` | Final Invoice | 最终结算发票 |
| `CREDIT_NOTE` | Credit Note | 贷项通知单 |
| 退款输出 | Refund Confirmation | 退款确认单 |

## 已冻结的产品与架构决策

- [x] **D1 · Quote 选择模式**：不新增 Customer 分类。Quote 初始为 `UNSET`；第一次相关动作在同一事务中把它锁定为 `DIRECT_INVOICE`、`RECEIPT_ONLY` 或 `FORMAL_ADVANCE`。后来删除草稿或付款都不会解锁。
- [x] **D2 · 禁止混用模式**：同一 Quote 不能同时出现直接转换、仅凭证定金和正式 Advance/Final 单据。现有 M11.5 行为保留为 `RECEIPT_ONLY` 分支。
- [x] **D3 · 正式开票 VAT 边界**：新建的正式 Advance/Final 链只支持 `NL_DOMESTIC`；不支持的 treatment 明确返回 `422`。Credit Note 继承并可更正来源已经支持的所有 VAT treatment，包括 ICP 影响。
- [x] **D4 · Advance 输入**：用户输入 gross amount 或 percentage。percentage 始终以原始已接受 Quote 的 gross total 为基数；只有后端分配 taxable/VAT bucket。不支持选择行/里程碑。
- [x] **D5 · 一张 Advance 草稿**：一张正式 Quote 最多有一张 open Advance DRAFT。发出或删除后可建下一张；删除不改变 Quote mode。
- [x] **D6 · Final 不受付款阻塞**：未付款的 Advance 不阻止创建或发出 Final。付款始终挂在原始 charge document 上。
- [x] **D7 · 受守卫的 Final 编辑**：Final DRAFT 可以增加或减少行、数量、价格、折扣和税率，但 Quote、Customer、币种和 `NL_DOMESTIC` treatment 固定；日期不能早于已应用的 Advance；每个已应用 VAT bucket 必须仍有覆盖；UI 显示 Quote→Final variance。
- [x] **D8 · Final 冻结**：存在 Final DRAFT 时，禁止新发出 Advance、为 Advance 开 Credit Note 以及重开 Advance。删除 Final DRAFT 后解除冻结。整条链最多一张 Final DRAFT/已发出 Final。
- [x] **D9 · 自动且不可变的应用关系**：创建 Final 时，按稳定顺序应用每张已发出 Advance 当时剩余的净 charge。应用快照永不改变；后来对 Advance 的 Credit 是独立更正事件。
- [x] **D10 · 更正绑定来源**：Standard、Advance 和 Final 发出后都可在任何阶段独立开 Credit。Credit Note 只冲销来源单据实际剩余的 charge/tax basis；绝不重写来源或 Final application history。
- [x] **D11 · 部分 Credit 输入**：用户选择全额剩余，或选择来源行。每个选中行输入 quantity 或固定 gross amount；后端从不可变的剩余来源快照推导全部 net/VAT 值。
- [x] **D12 · 不允许 Credit-of-Credit**：不能对 Credit Note 再开 Credit。错误发出的 Credit Note 生成关联补偿发票 DRAFT：只有安全地处于 Final 之前时才可为 `ADVANCE`，否则为补充 `STANDARD` 发票。
- [x] **D13 · Credit 独立编号序列**：Standard/Advance/Final 共用现有发票序列。Credit Note 使用单独、类型化、可配置、并发安全的连续序列。
- [x] **D14 · Refund 台账**：退款是 `direction=REFUND`、amount 为正数且关联已发出 Credit Note 的 `Payment`。没有 standalone refund、负数付款输入或未分配 credit。
- [x] **D15 · 可修改现金且整链复验**：incoming payment 与 refund 仍可编辑/删除，但每次 mutation 都锁定并复验完整受影响链；任何导致 over-refund、coverage 无效或结算不一致的 mutation 都原子拒绝。
- [x] **D16 · 独立状态**：保留生命周期 `DRAFT/SENT/COMPLETED/CANCELLED`；增加正交的 settlement 与 credit 状态。`paid_status` 新增 `NOT_APPLICABLE`；`due_amount` 和 `refund_due_amount` 分开。
- [x] **D17 · 收入时点**：发出 Advance 不产生 P/L 收入。发出 Final 时确认当时完整项目 net；Standard 不变。Standard/Final Credit Note 在 Credit 日期冲减收入。Advance Credit 仅当 Final 已发出时冲减收入；`affects_revenue` 在 Credit 发出时冻结。
- [x] **D18 · 更正是有日期的事件**：BTW/ICP 按已发出 Credit Note 自己的日期读取。JAI 提供结构化跨期间/以前 return warning，但不提供申报状态或锁。
- [x] **D19 · 受引导重开边界**：`Credit + replacement DRAFT` 仅适用于 direct Standard，以及尚无 Final DRAFT 的 Advance。Receipt-only 最终 Standard 和已发出的 Final 可被 credit/refund，但重新开始必须走新 Quote/手工流程。
- [x] **D20 · 整项目取消**：preview 为每张仍有剩余的正式 charge document 构建一项更正；确认时原子创建多张 Credit DRAFT。用户逐张检查并发出。
- [x] **D21 · 一个发票工作区**：不新增顶层模块。Invoices 列表增加类型筛选/徽标；Quote 提供开票模式动作和完整链时间线；只能从来源单据发起 Credit。
- [x] **D22 · 只审计链**：为 Quote/单据链动作增加只追加的生命周期/事件轨迹。M12 不引入通用的全数据库审计系统。
- [x] **D23 · 供货/预付款日期**：`supply_or_advance_date` 在 DRAFT 可选，发出时默认解析为 `invoice_date`。实际付款日期/方式/reference/note 继续作为结构化 Payment 数据，也可显示在输出中。
- [x] **D24 · 发出时交易方快照**：发出时冻结卖方/买方法定身份、地址、VAT/KVK 字段、locale、不可变 logo reference、`issued_at` 和发出用户。之后修改主数据不能改变正式单据身份。
- [x] **D25 · 精确成品**：每次实际 Download，以及每次成功 Send 已发出的正式单据或 Refund Confirmation，都保留精确 PDF bytes、SHA-256、locale 和 filename。Preview/DRAFT 不留存。相同 hash 去重；结算状态变化可以产生新的留存成品；`EmailLog` 关联实际发出的 bytes。
- [x] **D26 · 单币种**：M12 保持当前每条链单币种行为。不局部实现多币种/FX。

## 领域模型与状态规则

### 单据与 Quote 枚举

- `InvoiceDocumentKind = STANDARD | ADVANCE | FINAL | CREDIT_NOTE`。
- `QuoteSettlementMode = UNSET | DIRECT_INVOICE | RECEIPT_ONLY | FORMAL_ADVANCE`。
- `PaymentDirection = INCOMING | REFUND`。
- `InvoiceSettlementStatus = OPEN | PARTIALLY_SETTLED | SETTLED | REFUND_DUE`。
- `InvoiceCreditStatus = NOT_CREDITED | PARTIALLY_CREDITED | CREDITED`。
- 现有 `InvoicePaidStatus` 增加 `NOT_APPLICABLE`；Credit Note 使用此值，因为不能对它记录普通 incoming payment。
- 仅请求使用的枚举包括 `AdvanceInputMode = GROSS_AMOUNT | PERCENTAGE` 和 `CreditLineInputMode = QUANTITY | GROSS_AMOUNT`。

### Quote mode 转移矩阵

| 当前 mode | 第一次相关动作 | 锁定 mode | 后续允许流程 |
| --- | --- | --- | --- |
| `UNSET` | 现有 Quote→Invoice 转换 | `DIRECT_INVOICE` | 一条 direct Standard 链、付款和符合条件的重开 |
| `UNSET` | 新建现有 Quote payment | `RECEIPT_ONLY` | M11.5 定金→一张完整 Standard invoice |
| `UNSET` | 新建 Advance DRAFT | `FORMAL_ADVANCE` | Advance(s)→Final |
| 任意已锁定 mode | 其他行中的动作 | 不变 | `409 MODE_CONFLICT` |

mode 选择与第一次动作在一个锁住 Quote 的事务中提交。动作失败时仍为 `UNSET`。正式链必须至少有一张已发出 Advance，才能创建 Final。

### 结算公式

下列所有金额都有交易币种和 `base_*` 对应值，由后端 services 根据锁定快照重算：

```text
net_charge = payable_before_payments - issued_credit_total
net_cash = incoming_payment_total - refund_total
due_amount = max(net_charge - net_cash, 0)
refund_due_amount = max(net_cash - net_charge, 0)
```

- `payable_before_payments` 表示该单据承担的 charge：Standard/Advance 的完整 charge，或扣除冻结 Advance applications 后 Final 的 residual。
- 单据链汇总只对独立 charge document 和 correction 各计算一次。M11.5 receipt-only 定金仍是针对唯一 Standard invoice 的 cash，不是独立正式 charge。
- Credit entitlement 按 Credit Note 发出顺序、再按 ID 分配。Refund 不能超过其 Credit Note 剩余可退 entitlement，也不能超过链的 `refund_due_amount`。
- lifecycle 是历史状态，不代替 settlement。已发出单据即使变成 `CREDITED`，仍可保持 `SENT`；`COMPLETED` 也不会抹去 credit/refund 状态。
- 对 charge document：`refund_due_amount>0` 时 settlement 为 `REFUND_DUE`；两个 due 值都为零时为 `SETTLED`；正数 incoming cash 只覆盖部分 net charge 时为 `PARTIALLY_SETTLED`；否则为 `OPEN`。Credit Note 的 `paid_status=NOT_APPLICABLE` 且普通 due 为零；其已分配可退 entitlement 仍有剩余时为 `REFUND_DUE`，否则为 `SETTLED`。使用 `refunded_amount` 区分部分/全额退款，不再虚构另一套 lifecycle。

### 正式 Advance 分配

1. Quote 必须为 `ACCEPTED`、未转换、`NL_DOMESTIC`、单币种，且 mode 为 `UNSET` 或 `FORMAL_ADVANCE`。
2. gross 输入舍入到币种最小单位。percentage 输入必须为正，应用到原始已接受 Quote gross 后只舍入一次到最小货币单位。
3. 把 Quote 持久化的行/单据税务快照聚合成稳定 VAT bucket。
4. 减去当时仍有效的已发出 Advance 净覆盖；Final 之前的 Advance Credit 会重新释放容量。
5. 使用 M11.5 的整数最小货币单位 largest-remainder 算法，把请求 gross 按比例分配到剩余 taxable/VAT component。
6. 所有 component 非负，每个 bucket 满足 taxable+VAT=gross，累计净 Advance 不得超过已接受 Quote 快照。
7. `calculate` 无副作用。创建时持久化同一个权威结果；发出时在 Quote/单据锁下重新验证。

### Final applications 与编辑

1. 创建 Final 时锁住 Quote，要求不存在 open Advance DRAFT、至少一张已发出 Advance、且尚无 Final，然后以已接受 Quote 为初始完整项目内容做快照。
2. 对每张已发出 Advance 计算 `issued charge − Final 前已发出 credits`；忽略零剩余。按发出日期、单据号、ID 应用剩余 Advance。
3. 持久化单据级和逐 VAT bucket application 快照。Final `payable_before_payments = full Final gross − application gross`。
4. 未付款的 Advance 余额仍应付在对应 Advance 上；只有 residual Final charge 可接受 Final payment。项目 due 从整条链派生。
5. 编辑 Final DRAFT 时在 services 重新定价，把可编辑完整项目 tax bucket 与冻结 application 重新分配；任一 bucket 出现负 residual 即拒绝。Quote、Customer、币种和 treatment 永不改变。
6. 响应暴露原始 Quote totals、编辑后 Final 完整项目 totals、variance 和已应用 Advance 明细。发出时冻结结果；之后的 Advance Credit 永不重写 application rows。

### Credit basis 与发出

1. 每张 charge document 发出时，根据应用后的实际 charge 创建不可变 `invoice_credit_basis_line`：Standard/Advance 使用自己的 charge 快照；Final 只使用 residual charge basis。
2. Credit DRAFT 只引用一张已发出且不是 Credit 的来源。DRAFT 不预占 coverage；calculate/create 使用当前剩余 basis，发出时锁定并复验。过期/竞争草稿返回 `409`。
3. 全额覆盖消耗所有剩余 basis component。部分 quantity 不能超过来源剩余 quantity。部分 gross 不能超过该行剩余 gross，并确定性拆分到该行剩余 net/VAT component。
4. description、quantity/unit、VAT treatment/rate/effect、currency/base amount 和来源引用都继承快照，不允许自由选择税务输入。
5. Credit 日期不能早于来源 invoice date。`supply_or_advance_date` 采用统一默认规则。
6. Credit 发出时持久化 `affects_revenue`：Standard/Final 为 true；Advance 仅在链上已有已发出 Final 时为 true。
7. 即使并发发出，已发出 credit coverage 在 quantity、gross、taxable、VAT 或 base amount 任一维度都不能超过来源 basis。

### 重开、补偿与取消

- 受引导的 replacement 从一张已发出 Credit Note 开始，该 Credit 全额/部分更正了符合条件的 direct Standard 或 Final 前 Advance。系统从已 credit basis 创建一张关联 DRAFT；该 DRAFT 在相同来源/链不变量下可编辑，只在发出时取得新编号。
- compensating invoice 从错误发出的 Credit Note 开始，并镜像该 Credit 的 charge/tax basis。只有被更正来源是 Advance 且 Quote 仍安全处于 Final 之前时，它才是 Advance；否则是 supplemental Standard。这是新的正向 tax/revenue event，不是删除 Credit。
- 整个正式项目取消时，preview 列出正式链上每张仍有 credit basis 的已发出 charge document：Advance、Final 和任何关联 supplemental Standard。确认操作锁定整条链，原子创建每个来源各一张全额剩余 Credit DRAFT；只有用户逐张检查并发出后，才产生编号/tax/report event。

### Payment 与 Refund mutation

- `INCOMING` 保留现有正数 amount 规则，关联 charge Invoice，或仅在 M11.5 中关联 Quote。`REFUND` 为正数、关联一张已发出 Credit Note，且绝不产生 `payment_tax`。
- Refund create/edit/delete 按既定全局顺序锁定 Quote（如有）、排序后的 charge sources、已发出 Credits、全部 incoming/refund rows、Final applications 和 aggregate rows。
- 每次 mutation 重算受影响单据与链。现有分支的 overpayment、over-refund、credit 前 refund、跨公司关联，以及会使现有 Refund 失去 issued Credit coverage 的 edit/delete 都被拒绝。
- Refund Confirmation 显示退款日期、方式、reference、note、Credit Note/来源引用、amount 和退款后剩余 entitlement。它不是 Credit Note，也不是 VAT invoice。

## 已锁定 API 契约

所有路由都位于 `/api/v1`。契约变更先于依赖实现落地，并重新生成已提交的 `frontend/src/api/schema.d.ts`，不得 drift。

### 现有 schema 扩展

- `InvoiceRead` 和 `InvoiceListItem`：增加 `document_kind`、nullable `quote_id`、`supply_or_advance_date`、发出/交易方快照 metadata、`payable_before_payments`、incoming/credited/refunded totals、`due_amount`、`refund_due_amount`、`paid_status`、`settlement_status`、`credit_status`，以及适用时的 source/replacement context。
- `QuoteRead`：增加 `settlement_mode`、`settlement_mode_locked_at` 和精简 chain totals。
- `PaymentRead` 和 `PaymentListItem`：增加 `direction`、nullable `credit_note_id`/number 和 refund context。全局 payments 支持按 `direction` 和 document kind 筛选。
- `InvoicePaidStatus` 增加 `NOT_APPLICABLE`。所有新枚举均由 OpenAPI 生成，不在 TypeScript 手写重复定义。
- 现有通用 Invoice update/status、Quote conversion、Quote payment、Payment mutation、PDF preview/download 和 send endpoint 都改为 mode/kind-aware，同时保持 legacy response compatibility。

### 单据链读取

- `GET /quotes/{quote_id}/document-chain -> DocumentChainRead`。
- `GET /invoices/{invoice_id}/document-chain -> DocumentChainRead`。
- `DocumentChainRead` 包含 Quote/mode 身份、有序 node 与 relation、生命周期 event、Quote/Final variance、charge/credit/payment/refund/application totals、`due_amount`、`refund_due_amount` 和可用 next actions。它是后端权威投影，不是前端计算结果。

### Advance 与 Final 命令

- `POST /quotes/{quote_id}/advance-invoices/calculate`，请求/响应为 `AdvanceCalculationRequest -> AdvanceCalculationRead`；无副作用。
- `POST /quotes/{quote_id}/advance-invoices`，请求/响应为 `AdvanceDraftCreate -> InvoiceRead`；创建唯一 open Advance DRAFT，并在同一事务把 `UNSET`→`FORMAL_ADVANCE`。
- `PUT /advance-invoices/{invoice_id}`，请求/响应为 `AdvanceDraftUpdate -> InvoiceRead`；仅 DRAFT。
- `POST /quotes/{quote_id}/final-invoice`，请求/响应为 `FinalDraftCreate -> InvoiceRead`；创建唯一 Final DRAFT 和 application snapshots。
- 现有 Invoice update/status endpoint 负责发出 Advance/Final，并执行 kind-specific guards；不支持的通用操作返回结构化错误。

### Credit、重开与取消命令

- `POST /invoices/{source_invoice_id}/credit-notes/calculate`，请求/响应为 `CreditCalculationRequest -> CreditCalculationRead`；无副作用。
- `POST /invoices/{source_invoice_id}/credit-notes`，请求/响应为 `CreditDraftCreate -> InvoiceRead`。
- `PUT /credit-notes/{credit_note_id}`，请求/响应为 `CreditDraftUpdate -> InvoiceRead`；source/treatment/currency 不可变。
- `POST /credit-notes/{credit_note_id}/replacement -> InvoiceRead`。
- `POST /credit-notes/{credit_note_id}/compensating-invoice -> InvoiceRead`。
- `POST /quotes/{quote_id}/cancellation/preview -> ProjectCancellationPreview`。
- `POST /quotes/{quote_id}/cancellation/create-credit-drafts -> ProjectCancellationResult`；全部草稿创建成功，或一张都不创建。

`CreditCalculationRequest` 必须且只能选择 `full_remaining=true`，或非空 `lines[]`。每一行标识一个来源 basis line，并且只能选择 `quantity` 或 `gross_amount` 之一；前端绝不发送 net 或 VAT。

### Refund、输出与 artifact 命令

- `GET /credit-notes/{credit_note_id}/refunds -> RefundCollectionRead`。
- `POST /credit-notes/{credit_note_id}/refunds` 使用现有原始 payment fields 加正数 amount；返回权威 refund/document-chain aggregates。
- 现有 `PUT/DELETE /payments/{payment_id}` 支持 `REFUND`，direction/link 不可变并复验整链。
- `GET /payments/{refund_id}/refund-confirmation/preview` 渲染但不留存。
- `GET /payments/{refund_id}/refund-confirmation` 下载并留存精确 bytes。
- `POST /payments/{refund_id}/send-refund-confirmation` 发送成功后，把留存 artifact 关联到 `EmailLog`。
- `GET /invoices/{invoice_id}/artifacts` 和 `GET /invoices/{invoice_id}/artifacts/{artifact_id}` 暴露留存的正式单据 artifact。
- `GET /payments/{refund_id}/artifacts` 和 `GET /payments/{refund_id}/artifacts/{artifact_id}` 暴露留存的 Refund Confirmation artifact。

### 设置与错误

- Credit 编号设置镜像现有 invoice-number 设置：类型化 prefix/padding/next-start 控制、独立 sequence state 和 preview；现有 invoice 设置继续覆盖 Standard/Advance/Final。
- `404`：资源不存在或跨公司，且不泄漏身份。
- `409`：mode conflict、过期 calculation/basis、竞争 draft/Final、lifecycle conflict 或 serialization/concurrency 重试耗尽。
- `422`：amount/percentage/quantity/date 无效、不支持 VAT treatment、VAT bucket 未覆盖、over-credit、over-refund 或关系无效。
- 错误体使用稳定机器可读 code 加适合本地化显示的 detail；前端按 code 分支，绝不按英文文本分支。

## 数据模型与 Additive Migration

### 修改现有表

- **`quote`**：增加非空 `settlement_mode`（default/backfill 见下）和 nullable `settlement_mode_locked_at`。
- **`invoice`**：增加非空 `document_kind`；nullable 且有索引的 Quote provenance；`supply_or_advance_date`；`issued_at`/`issued_by_user_id`；具有 base-currency 对应值的权威 payable/credited/refunded/due/refund-due aggregates 和 settlement/credit status cache。所有正式单据继续使用现有 invoice-number 存储字段；按 kind 选择序列。
- **`payment`**：增加非空 `direction`；nullable 且有索引的 `credit_note_id`。DB check 保证 incoming row 使用 Invoice/Quote context 且无 Credit；refund row 恰好使用一个 Credit，且没有 Quote-origin tax allocation。
- 保留 `quote.converted_invoice_id` 以向后兼容；新流程以 `document-chain` relation 为权威。

### 新规范化表

- **`invoice_party_snapshot`**：一对一的发出快照，包含卖方/买方名称、法定/商号身份、结构化地址、VAT/KVK/contact 字段、locale、不可变 logo reference，以及 provenance quality（`NATIVE_ISSUE | MIGRATED_CURRENT_STATE`）。
- **`final_advance_application`**：Final↔Advance 关系、稳定顺序，以及交易币种/base taxable、VAT、gross application totals。
- **`final_advance_application_tax`**：每个已应用 source/target VAT bucket 一行，保存 rate/treatment/effect/ICP 快照和交易币种/base amounts。
- **`invoice_credit_basis_line`**：按已发出单据行保存不可变 source charge basis，包括 charge quantity、net/VAT/gross、base amounts 和 tax snapshot identity。
- **`invoice_correction`**：一张 Credit Note→一张来源 Invoice，包含发出时 totals 和冻结的 `affects_revenue`。
- **`invoice_correction_line`**：选中的 basis line、mode/input provenance，以及 credited quantity/net/VAT/gross/base snapshots。
- **`invoice_relation`**：用于 `REPLACEMENT_OF` 与 `COMPENSATES_CREDIT` 的有类型正向单据关系；correction 与 Final application 语义继续留在各自专用规范化表中。
- **`document_chain_event`**：只追加 company/Quote/Invoice actor、event type、timestamp 和结构化安全 metadata，用于 mode lock、create/issue/cancel/delete、application、credit、replacement、compensation、payment 和 refund 生命周期事件。
- **`document_artifact`**：所属 Invoice 或 refund Payment、artifact kind、精确 PDF bytes、SHA-256、locale、filename、创建原因（`DOWNLOAD | SEND`）、创建时间和 renderer version；唯一 owner+kind+hash 对精确 bytes 去重。`email_log` 增加 nullable artifact FK。
- Credit 编号 sequence/settings 使用现有类型化设置/编号架构，不使用 `max+1` column。

每个新业务 root/child 在需要处遵循现有 `company_id`/RLS ownership pattern。所有金额 column 使用 `NUMERIC`/`Decimal`；description 使用 `text`；FK 和 ORM relation 管理 cascade。tax/application/correction row 保持规范化，不把 nullable tax FK 堆到宽表中。

### Legacy backfill 与兼容性

1. 所有现有 Invoice 变成 `STANDARD`；`payable_before_payments` 从持久化 total 初始化。不要重算价格、税、付款、状态或报表历史。
2. 只要 Quote 有任意 Quote-origin M11.5 Payment，就回填为 `RECEIPT_ONLY`；已经转换且没有 Quote-origin Payment 的 Quote 回填为 `DIRECT_INVOICE`；其他 Quote 均为 `UNSET`。
3. 从 `quote.converted_invoice_id` 回填 Invoice 的 Quote provenance；保留兼容 backlink。
4. 所有现有 Payment 回填为 `INCOMING`。
5. 现有已发出 Invoice 从持久化行/税务快照获得完整 Standard credit-basis row，绝不读取当前 VAT dictionary。
6. 现有已发出 Invoice 的交易方快照使用 migration 当时的 Company/Customer 数据，并标记 `MIGRATED_CURRENT_STATE`；不得虚构历史 `issued_at`/actor。新发出使用 `NATIVE_ISSUE`。
7. event log 从 M12 动作开始；migration 可以记录明确的 migration marker，但绝不伪造历史用户事件。
8. 在当前 head 后使用多份可评审 additive migration。不删除 table/column，也不 collapse migration baseline。upgrade/downgrade/head 测试覆盖 PostgreSQL constraint、RLS 和生产形态 legacy rows。

## 报表与审计语义

### BTW 与 ICP

- Standard charge 行为继续按 invoice date。正式 Advance 发出时在 Advance invoice date 增加其 tax event。Final 发出时只增加 frozen application 之后的 residual tax；它不重新读取 Advance payment。
- Receipt-only M11.5 继续使用现有 payment-date `payment_tax` 加最终完整 invoice offset 的路径。正式与 receipt-only 来源由 Quote mode 保证互斥。
- 已发出 Credit Note 在 Credit 日期产生与来源 tax snapshot 精确对应的负向 event。继承跨境来源的 treatment 和 `requires_icp`，因此 ICP 得到相应负向更正。
- Refund cash 不产生 VAT event。Payment mutation 永不改变正式 Advance/Final/Credit tax snapshot。
- 当更正在不同/更早期间时，报表暴露结构化 warning record，其中含来源/Credit number、event date/period、amount 和 correction-guidance code。绝不声称某个 return 已申报或未申报。

### P/L 与 Dashboard

- Standard 发出时按现有方式确认 net revenue。Advance 发出不确认收入。Final 发出时确认其编辑后的完整 project net，而不只是 residual payable。
- Standard/Final Credit Note 在 Credit 日期减去 correction net。Advance Credit 仅在持久化的 `affects_revenue=true` 时减收入；Final 前的 Advance Credit 不影响 P/L，因为之后的 Final 会重新建立当时完整 project basis。
- 因此 Final 之后取消整个正式项目时，通过分别 credit 每张 Advance charge、residual Final charge 和任何关联 supplemental Standard，可使收入归零，而不修改 application history。
- Dashboard totals 使用同一 reporting services/events；不创建第二条计算路径。

### 只追加生命周期审计

- event 与动作在同一事务中写入，只含 ID/code 和非敏感快照。用户输入 HTML、SMTP credential 和原始 PDF bytes 绝不进入 event metadata。
- event 没有 update/delete API。读取受 RLS 限制。只有合法删除尚未发出的 root 时，DB/ORM cascade 才可移除其 draft-chain event；已发出单据的 event history 按现有记录保留策略留存。

## 发出快照、PDF、邮件与 Artifact 留存

- DRAFT preview 使用当前 draft data，不创建 artifact。发出时先验证/默认 `supply_or_advance_date`，分配正确编号，冻结交易方/credit basis/tax metadata，并原子提交。
- 每份已发出 PDF 都显著标明类型，并包含必填编号/日期/供货或预付款日期、冻结卖方/买方信息以及来源/链引用。Final 列出每个冻结 Advance application；Credit 列出来源和被更正行；Standard/Advance/Final 的 settlement section 按需列出结构化 payment/refund。
- Download 先渲染 bytes，保存/去重精确 bytes，再返回相同 bytes。成功 Send 先保存/去重，并为实际 attachment 提交 `EmailLog.artifact_id`；失败 Send 按现有规则记失败，但没有成功发送 artifact relation。
- Payment/refund 状态变化后的后续 Download 可能产生不同 hash，并单独留存。重复的相同渲染复用已有 artifact。历史 artifact 不可变，即使主数据或结算改变仍可下载。
- Refund Confirmation 有独立 label/template/默认 EN/ZH email settings 和 artifact owner。它明确引用 Credit/source，但既不是 VAT invoice，也不替代 Credit Note。
- 现有 escaping、`nh3`、SVG/logo、SSRF、RFC 6266 filename 和 multipage-footer 防护继续作为回归门禁。Jinja/Vue 只负责展示，绝不做权威金额分配。

## 编号、事务与并发

- Standard/Advance/Final 只在 `DRAFT→SENT/COMPLETED` 时从现有 invoice sequence 分配编号。Credit Note 在同一 lifecycle 点从独立 Credit sequence 分配。DRAFT preview 不消耗编号。
- 两条序列都通过现有 typed sequence service、DB uniqueness 和 retry path 支持配置 start、prefix/padding 和跳号；绝不使用 `max+1`。
- 全局锁顺序：**Quote → 排序后的 charge/source Invoices → 排序后的 Credit Notes → Payments/Refunds → Final applications/correction rows → numbering state**。只需要后缀的 service 也只能按该顺序取得适用前缀。
- mode lock + 第一次动作、Advance issue、Final create/issue、Credit issue、replacement/compensation create、project-cancellation draft create、payment/refund mutation 和 numbering 各自在一个外层事务中完成，中途不得 commit。
- 必须保证的竞态结果：并发新建 Advance 只留下一个 DRAFT；累计 Advance issue 不得超过容量；并发 Final 只留下一个；并发 Credit 不得 over-credit；payment/refund edit 不得破坏 coverage；invoice/Credit number 都唯一；失败的 stale command 返回 `409`，且不留下部分 row/event/artifact。

## 原子步骤

### Step 1 · 契约、单据底座与生产安全迁移

- **目标**：引入共享类型/状态、发出 metadata 与兼容性底座，同时不改变 legacy 财务行为。
- **契约**：冻结全部 enum 和现有 schema extension；增加 supply/advance date、party-snapshot marker 与类型化 Credit-number settings；重新生成 `schema.d.ts`。
- **后端**：additive Quote/Invoice/Payment columns；party snapshot、credit basis 与 Credit numbering foundation；严格按设计 backfill；mode/kind-aware serializer 和通用 lifecycle guard；为普通 Standard 新发出实现 native issue snapshot/default date。
- **前端**：使现有 Invoice/Quote/Payment 页面兼容新非破坏字段，并把 Standard 显示为 legacy default；只增加 Credit numbering settings。
- **必须覆盖**：生产形态 upgrade；legacy kind/mode/payment/amount/status 保持；issued-basis 快照来源；migrated party provenance 且不伪造时间；新 Standard issue snapshot/default date；numbering start/retry/gap；RLS/FK/check/cascade；downgrade/head；codegen/build。
- **盲审重点**：不重算历史 money/tax/report；没有 `max+1`；前端无金额逻辑；没有 nullable-FK tax blob；现有 Standard create/edit/issue/payment 行为等价。
- **DoD**：Roadmap §5 的所有质量门禁通过。

### Step 2 · Quote mode 锁、单据链投影与生命周期事件

- **目标**：在增加正式单据前，让所有新旧分支明确且可观察。
- **契约**：实现两条 document-chain GET 路由和 `DocumentChainRead`；暴露可用动作与机器可读 mode conflict。
- **后端**：在现有 conversion/Quote-payment path 中实现原子 first-action mode lock；有序 chain projection；只追加 event service；保留 `converted_invoice_id` 兼容性，同时把 relation/provenance 作为新权威。
- **前端**：在 Quote/Invoice 页面显示 locked/unset billing mode 和初始只读 chain/timeline；尚不提供 Advance/Credit command。
- **必须覆盖**：UNSET→三种 mode、失败动作 rollback、删除不解锁、所有跨 mode conflict、DIRECT 与 RECEIPT_ONLY 回归、legacy backfill、event 原子性/顺序/安全、跨公司读取和 query-count 上限。
- **盲审重点**：不增加 Customer classification；M11.5 创建/转换语义不变；chain total 来自 services；event 不泄密且不可修改；不把兼容 backlink 当成新权威。
- **DoD**：Roadmap §5 的所有质量门禁通过。

### Step 3 · 正式 Advance 引擎与 API

- **目标**：使用精确混合税率分配，一次一张地计算、创建、编辑、发出并收取正式 Advance。
- **契约**：实现 Advance calculate/create/update command 和 kind-aware issue/read/list response。
- **后端**：基于已接受 Quote 快照的纯 gross/percentage allocator；考虑 Final 前 credit 后的容量；唯一 open DRAFT constraint；发出时复验/编号/credit-basis/event；已发出 Advance 接受普通 incoming payment。
- **前端**：只完成集成安全的类型 label/route 和生成类型；完整 Advance workflow 留到 Step 10。
- **必须覆盖**：gross、以 original total 为基数的 20/50/30 percentage、21/9/0 mixed bucket、cent tail、累计容量、credit 后容量重开、一张 draft、删除/重建、无效 status/treatment/date、未付/部分/全额付款、stale issue、double create/issue 与 rollback。
- **盲审重点**：使用持久化 Quote snapshot 而非实时 dictionary；percentage 只舍入一次；taxable+VAT=gross 不变量；issue 而非 draft 消耗编号；无 selected-line input；不得提前混入 P/L/report 工作。
- **DoD**：Roadmap §5 的所有质量门禁通过。

### Step 4 · 正式 Final application 与受守卫编辑

- **目标**：创建一张可编辑 Final，展示完整项目但只收取未应用 residual。
- **契约**：实现 Final create、application/variance read 和 kind-aware DRAFT update/issue。
- **后端**：快照 Advance 净剩余；持久化 application/tax row；计算 residual charge 与 credit basis；执行唯一 Final、最早日期、无 open Advance DRAFT、bucket coverage 与 Final freeze；Final payment 只针对 residual。
- **前端**：只做 generated-type compatibility 和基本 kind display；完整 editor/timeline integration 留到 Step 10。
- **必须覆盖**：50/50 与 20/50/30；未付款 Advance；Final 前完全 credited Advance；编辑 increase/decrease/rate/discount；Quote→Final variance；bucket/total/date failure；删除 Final DRAFT 解冻；以后 Advance Credit 不改变 application；double Final 与 create rollback。
- **盲审重点**：payment 不是 application；application amount 规范化且冻结；Final revenue basis 与 residual charge 分开；不存在负 bucket；所有锁遵循全局顺序。
- **DoD**：Roadmap §5 的所有质量门禁通过。

### Step 5 · Credit Note 计算、DRAFT 与发出核心

- **目标**：全额或部分更正任意已发出 charge document，且不修改来源。
- **契约**：实现 Credit calculate/create/update、source context、credit status 和独立 issue numbering。
- **后端**：不可变 remaining basis engine；full、quantity 和 gross-line allocation；继承 VAT/ICP snapshot；issue-time over-credit revalidation；冻结 `affects_revenue`；重算 source/chain aggregate 和 event。
- **前端**：只做 type/list compatibility 与 kind label；完整 Credit editor 留到 Step 10。
- **必须覆盖**：Standard/Advance/Final source；full/partial quantity/partial gross；discount 与 mixed/0/cross-border treatment；多张 Credit 的 remainder；date；Credit-of-Credit rejection；stale/concurrent over-credit；lifecycle/credit status；独立 sequence collision/retry。
- **盲审重点**：Final basis 只含 residual；没有自由 VAT input 或普通负数行；source 保持 immutable/issued；Advance `affects_revenue` 使用 issue-time Final state 且永不漂移。
- **DoD**：Roadmap §5 的所有质量门禁通过。

### Step 6 · 重开、补偿与正式项目取消

- **目标**：明确表达更正后的后续动作，不增加 Debit Note，也不重写历史。
- **契约**：实现 replacement、compensating-invoice、cancellation preview/create command 以及 typed relation。
- **后端**：执行 DIRECT Standard/Final 前 Advance 的 replacement boundary；把错误 Credit basis 镜像为安全的 compensating kind；preview 剩余 project basis；原子创建全部 cancellation Credit DRAFT；写入 provenance/event。
- **前端**：尚不做完整 workflow；只生成契约并做最小 relation rendering。
- **必须覆盖**：partial/full replacement；Advance replacement capacity；Final-DRAFT freeze；receipt-only/Final rejection；Final 前/后 compensation kind；多单据 cancellation、忽略 zero-remaining、stale preview、任一失败全 rollback 和之后逐张独立 issue。
- **盲审重点**：没有静默 reactivation/delete；新正向单据有新 issue number/event；cancellation 只创建 DRAFT；correction/source/application history 均不变。
- **DoD**：Roadmap §5 的所有质量门禁通过。

### Step 7 · Refund 台账与统一结算引擎

- **目标**：表达针对已发出 Credit 的实际现金退回，并让每个可修改现金动作保持有效。
- **契约**：增加 Payment direction/refund context、Credit refund routes、refund aggregate schema 和 Refund Confirmation routes（渲染在 Step 9 落地）。
- **后端**：chain settlement equation；正数 Refund CRUD；按 Credit 顺序分配 entitlement；incoming/refund mutation 时整链锁定/复验；权威 document/chain state recomputation。
- **前端**：现有 payment 页面兼容 direction；完整 refund UX 留到 Step 10。
- **必须覆盖**：未付/部分付款/已付 source credit；Credit 只减少 due 而无需 refund；partial/full refund；多 Credit/refund；编辑/删除 date/amount；credit 前 refund/over-refund；会使 refund 无覆盖的 incoming edit/delete；拒绝未转换 receipt-only Quote 定金退款，但支持已转换 receipt-only Standard 的 Credit/refund；concurrency 与 rollback。
- **盲审重点**：Refund 没有 VAT snapshot；正数 amount + 明确 direction；cash 永不改变 Credit/Final tax basis；chain formula 每行只计一次；legacy incoming payment 行为稳定。
- **DoD**：Roadmap §5 的所有质量门禁通过。

### Step 8 · BTW、ICP、P/L 与 Dashboard 投影

- **目标**：只计一次地投影所有新正式事件，并提供有用更正 warning。
- **契约**：以 document kind/source reference 和稳定 warning code 扩展 report row/summary warning；不增加 filing-state contract。
- **后端**：正式 Advance/Final residual/Credit tax event；继承的负向 ICP；收入时点与冻结 Advance-credit 规则；共享 Dashboard service；隔离 M11.5 receipt-only path。
- **必须覆盖**：同/跨季度 50/50 与 20/50/30；未付款 Advance；Final 前/后 Advance Credit；Standard/Final Credit；完整 cancellation 归零；NL mixed/0 与来源已支持的所有 cross-border treatment；refund no-op；formal tax 对 payment mutation no-op；所有 M10/M11.5 回归。
- **盲审重点**：formal VAT 由 invoice/credit date 而不是 refund date 驱动；receipt-only `payment_tax` 不丢不重；Final P/L 使用 full project net，而 BTW 使用 residual；warning 不断言 filing status。
- **DoD**：Roadmap §5 的所有质量门禁通过。

### Step 9 · 正式输出、精确 Artifact 与 Refund Confirmation

- **目标**：生成法律身份清楚的 EN/ZH 单据，并保留实际下载或发送的精确 bytes。
- **契约**：实现 artifact read/download、Refund Confirmation preview/download/send 和 `EmailLog.artifact_id`；使现有 PDF/send endpoint kind-aware。
- **后端/PDF/邮件**：kind-specific template/subject/placeholder；issue party snapshot；Advance application/source correction/payment/refund section；bytes-first hash/dedup/storage pipeline；不可变历史 artifact read；refund-owner audit。
- **前端**：共享 preview/download/send 调用可编译；完整入口留到 Step 10。
- **必须覆盖**：每种 kind/locale；必填字段/source ref；Company/Customer 修改后 party data 稳定；Preview/DRAFT 无 artifact；Download bytes 与存储完全相同；重复 hash 去重；settlement change 新 artifact；成功 Send 精确关联；失败 Send 行为；refund confirmation；escaping/logo/SSRF/filename/footer/multipage。
- **盲审重点**：renderer 不做金额计算；下载历史 bytes 时绝不重渲染；email attachment 等于关联 hash；storage/RLS/retention 防跨公司；Refund Confirmation 不标成 invoice/Credit。
- **DoD**：Roadmap §5 的所有质量门禁通过，包括 Docker 内 PDF 字体/渲染。

### Step 10 · 完整统一前端工作流

- **目标**：暴露完整 mode→Advance→Final→Credit→Refund 链，同时不复制后端规则。
- **前端**：一个 Invoice list，包含 kind filter/badge 和 due/refund/credit state；Quote billing-mode action card 与完整 timeline；带 calculate preview 的 gross/percentage Advance form；带 application/variance/freeze error 的 Final full-project editor；从 source 发起的 Credit full/quantity/gross editor；replacement/compensation/cancellation preview；Refund panel 与 confirmation；artifact history/download；PDF/email action；响应式 EN/ZH/empty/loading/error/confirmation/accessibility state。
- **后端**：只处理集成中发现的小型 contract/error/localisation 修正；不增加产品范围。
- **必须检查**：codegen freshness、production build、EN/ZH key symmetry；前端无 net/VAT/due/status 算术；action availability 遵循后端 code；没有重复 chain/list row 或 null navigation；生产 click/loading safeguard；窄屏 table/form/dialog。
- **盲审重点**：所有 form 只发送 intent/raw input；mode boundary 和 source-only Credit entry 清楚；未付款 Advance 不会 disable Final；receipt-only UX 保持简单；artifact 与 live preview 区分清楚。
- **DoD**：Roadmap §5 的所有质量门禁通过。

### Step 11 · 收尾、全量回归与里程碑报告

- **目标**：只做质量收尾；不引入功能。
- **执行**：Ruff；strict mypy；默认和完整 PostgreSQL integration suite；每份 additive migration 的 upgrade/downgrade/head；OpenAPI→TS freshness；前端 production build；EN/ZH symmetry；Docker build 和容器内 PDF rendering。
- **复查**：普通 Invoice/Quote conversion 与 numbering；M7 payments；M9 preview/download/email/receipt；M10 BTW/ICP/P&L/Dashboard；M11.5 receipt-only deposits、offsets 与 receipt email；RLS/cascade/security/query-count/concurrency suites。
- **文档**：用实际结果更新中英文 milestone/roadmap；写 `review-notes/M12-step11-impl.md`；盲审收敛后写 `review-notes/M12-report.md`，整合下方 walkthrough。
- **盲审重点**：没有设计项被静默延期；验收证据可复现；所有 review finding 已收敛；review notes 保持 ignored/untracked；只有明确请求编排模式时，实现才每步留下一个 commit。
- **DoD**：Roadmap §5 的所有质量门禁通过。

## 🟢 最终里程碑 Walkthrough

里程碑结束时，使用默认 dev Compose 命令，根据 `review-notes/M12-report.md` 只运行一次。适合审计时记录 ID/编号、截图或 report total。

1. **迁移与 legacy 回归**：升级生产形态 DB；确认旧 Invoice 为 Standard，旧已转换 Quote 按 backfill 为 DIRECT 或 RECEIPT_ONLY，旧 payment/total/status/report 未漂移，且 legacy Invoice 仍能 preview/download/send 并收款。
2. **Direct Standard 流程**：接受 Quote，选择直接转换，发出/付款，检查 kind/status/chain/party snapshot/artifact，并证明 receipt-only/formal action 此后会拒绝且不改变链。
3. **M11.5 receipt-only 回归**：记录两笔 Quote deposit，取得本地化非 VAT receipt，转换/发出完整 Standard，支付余额，并确认历史 BTW offset 与简洁 UI 不变。
4. **50/50 正式流程**：接受 `NL_DOMESTIC` Quote，以 percentage 创建 50% Advance，发出/付款，再创建/发出 Final；核对完整项目展示 + frozen application + residual due，最后支付 Final。
5. **20/50/30 混合税率流程**：针对 21%/9%/0 Quote 创建 20% 和 50% Advance，检查 cent allocation 与 tax snapshot，再发出 30% residual Final；total charge、cash、VAT 和 project due 必须与 Quote 精确一致。
6. **未付 Advance + Final 编辑**：发出但不支付 Advance；创建 Final，在 coverage 内增加再减少 price/line/discount/rate，检查 Quote variance；验证未覆盖 VAT bucket/date/customer/currency change 失败；发出合法 Final 并确认 Advance/Final 各自 due balance。
7. **Final freeze 与并发**：Final 为 DRAFT 时，证明新 Advance issue/Advance Credit/replacement 被阻止；删除后证明动作恢复；并发创建两张 Final，最终恰好一张且无孤立 application/event。
8. **按来源类型开 Credit Note**：分别对 Standard、Advance 和 Final 发出 Credit；测试 full remaining、selected quantity、selected gross；检查不可变 source reference、独立 number series、source credit status 与 remaining basis；尝试 Credit-of-Credit 和 over-credit。
9. **正式项目取消**：preview 多 Advance+Final cancellation，原子创建 Credit DRAFT，逐张检查/发出；证明 chain charge/due、BTW 和 P/L 通过独立 source event 归零，而 Final application 不变。
10. **跨境更正与期间 warning**：在后续季度 credit 一张现有支持 EU/reverse-charge/export 的 Standard source；验证继承的 tax treatment、适用时的负向 BTW/ICP、结构化 correction warning，以及应用不声称/锁定 filing。
11. **Refund 结算**：覆盖未付、部分付和全额付 source，包括一张已转换的 receipt-only Standard；展示 Credit 只减少 due 而不退款，之后做 partial/full Refund 和 Refund Confirmation。拒绝对未转换 Quote 定金退款。尝试 over-refund，以及编辑/删除会使 Refund 无覆盖的 incoming cash；每个非法动作都必须 rollback。
12. **重开与补偿**：创建受引导 direct Standard replacement 和 Final 前 Advance replacement；确认 receipt-only/Final replacement 被拒。分别在 Final 前和 Final 后补偿一张错误 Credit，验证 Advance 与 supplemental Standard kind 以及完整 provenance。
13. **交易方快照稳定性**：发出单据后修改 Company/Customer legal name、address、VAT/KVK、locale 和 logo。新的 live artifact render 使用冻结 party snapshot，所有旧 artifact 保持 byte-identical。
14. **Artifact 留存**：Preview DRAFT/已发出单据时没有留存 row；Download 后验证返回 SHA-256 等于留存 artifact；重复下载去重；改变 settlement 后 Download 产生新 hash；Send 后证明 `EmailLog` 引用精确 attachment。对 Refund Confirmation 重复验证。
15. **统一 EN/ZH UX 与输出**：在一个列表筛选四种 kind；遍历 Quote/Invoice timeline；使用每个 source-only action；检查响应式 form 和权威 total；以 EN/ZH preview/download/send 每种正式 kind 与 Refund Confirmation，核对 label、reference、filename 和清洗内容。
16. **Rollback/隔离/编号竞态**：运行跨公司请求、stale calculate→issue、并发 Advance capacity、Credit coverage、Refund coverage 和两条 numbering-series race；验证 `404/409/422` code、唯一连续 issue number，且无部分 money/tax/event/artifact row。
17. **报表与全局等式**：对完整 walkthrough 数据集，将 document-chain charge/cash/due/refund total 与 Payments、BTW、ICP、P/L 和 Dashboard 对账；每个 event 只能出现一次，不得出现 Advance revenue 或 Refund VAT。
18. **部署门禁**：运行完整自动化收尾矩阵与 Docker startup/build；在 milestone report 中记录准确 command/count/result 以及全部盲审收敛情况。

## 验收结论

尚未验收。收尾时用完成日期、各步提交、自动化门禁结果、盲审收敛摘要和作者 walkthrough 结论替换本段。只有届时才能在中英文 Roadmap 中把 M12 标为 🟢，并激活 M13。
