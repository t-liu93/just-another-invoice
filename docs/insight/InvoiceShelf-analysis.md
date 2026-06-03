# InvoiceShelf 全量分析与新项目参考

> **用途**：本文档是对开源发票系统 InvoiceShelf 的全量分析，作为「用 Python 重做一套发票/账目系统」的参考资料，供项目作者与未来新项目的 AI agent 阅读。
>
> **写法约定**：凡涉及 InvoiceShelf 的具体实现，尽量分三层表达——
> 1. **InvoiceShelf 怎么做的**（Laravel/Vue 现状）
> 2. **背后的通用模式/原理**（与语言无关）
> 3. **Python 等价方案**（新项目落地时的对应物）
>
> **新项目技术栈**（2026-06-02 已大体敲定，详见〔决策记录〕）：后端 **FastAPI + SQLAlchemy(async) + Alembic + fastapi-users**，数据库 **PostgreSQL**；前端 **Vue 3 SPA + Pinia + Vite + Naive UI + ECharts**（TypeScript + OpenAPI→TS 类型生成）；**单容器部署**（FastAPI 托管前端静态产物）。已有同构参考项目可复用：`~/workspace/trading-journal`。
>
> **新项目工作名**：**Just Another Invoice**（仓库 `just-another-invoice`，Python 包 / slug `jai`）。
>
> **被分析对象版本**：InvoiceShelf `v2.3.3`（Laravel 13 / PHP 8.4 / Vue 3），系 [Crater](https://github.com/crater-invoice-inc/crater) 的 fork，AGPLv3。

---

## 🚀 开工指引（未来开新项目时，先读这一节）

**这份文档是什么**：用 Python(FastAPI) 重做一套发票系统的**参考基线**——记录了全部已定决策、v1 范围、开发顺序与避坑点。它定**方向与约束**，不是逐行施工图；每个阶段的细节（表结构 / 端点 / 公式）在动手时再跟 AI 细化。

**怎么用（3 步）**：
1. **搬过去**：把本文件 `cp` 进新项目（如 `docs/invoiceshelf-analysis.md`）。它自包含、可移植，不依赖 InvoiceShelf 代码。
   > 注：我们讨论时积累的 **memory 绑在 InvoiceShelf 这个目录上、不会自动带走**——新项目靠本文档即可；若想保留对话上下文，也可把 memory 文件一并复制过去。
2. **喂给 AI**：在新项目里对 agent 说——
   > “先读 `docs/invoiceshelf-analysis.md`，重点看 **决策记录 + 7.2 路线图 + 7.3 避坑清单 + 7.4 VAT 模型**，然后我们从 **7.2.5 的 P0** 开始设计。”
3. **按阶段推进**：P0 地基 → P1 认证+公司档案 → P2 主数据/字典 → P3 单据核心 → P4 收款+开支 → P5 PDF/邮件 → P6 报表/仪表盘 → P7 收尾。每个 Phase 走一轮「细化设计 → 实现」。

**关键约束（agent 必须遵守，详见 7.3）**：算钱在后端 ｜ 金额用 Decimal ｜ 多租户用 Postgres RLS ｜ 不手写级联删除 ｜ 编号可跳号且并发安全 ｜ 渲染用户输入要清洗 ｜ 不做应用内自更新 ｜ VAT 按「处理类别」建模。

**快速导航**：已定决策→〔决策记录〕｜做什么→ 7.2 ｜别踩什么→ 7.3 ｜荷兰报税→ 7.4 ｜领域模型→第 2 章 ｜架构与 Python 对应→第 3 章 ｜架构母版→ `~/workspace/trading-journal`。

---

## 进度追踪

| 章节 | 状态 |
| --- | --- |
| 第 0 章 · 文档说明 | 🟢 完成（见文首） |
| 第 1 章 · 项目全景 | 🟢 完成 |
| 第 2 章 · 领域模型与业务流程 | 🟢 完成 |
| 第 3 章 · 架构与技术模式 | 🟢 完成 |
| 第 4 章 · 技术栈与依赖清单 | ⚪ 本版略过（栈已定，见决策记录 / 3.6） |
| 第 5 章 · 仓库内容盘点 | ⚪ 本版略过（多为专有包袱，不复制） |
| 第 6 章 · 现状评估：踩坑与可改进点 | 🟢 浓缩进 7.3 避坑清单 |
| 第 7 章 · 新项目规划（功能优先级 + 技术选型） | 🟢 完成（含 7.4 荷兰 VAT 模型） |

> 本文档为**初版**：用于日后开始实现时，作为与 AI agent 讨论「具体实现步骤与方式」的基线。第 4/5 章有意略过（理由见各章）。

---

## 决策记录（Decision Log）

> 随讨论持续累积；第 7 章会据此做正式的「v1 / 后续 / 改进」规划。日期为决策当天。

### 2026-06-02 · v1 定位与领域取舍

**v1 定位**：面向**个体户 / 单人业务**（自由职业程序员、接私活的蓝领等），**第一个用户就是作者本人**，按自己真实需求裁剪，砍掉一切非必要功能。

| 主题 | InvoiceShelf 现状 | v1 决策 |
| --- | --- | --- |
| **多租户** | 多公司 + 多用户，`user_company` 多对多，处处 `company_id` | **砍**。单公司单用户。公司信息退化为**单例「业务档案/设置」**（抬头/logo/VAT/地址/本位币/编号规则） |
| **三大单据** | Quote(Estimate) / Invoice / RecurringInvoice | **保留**（Estimate → 改名 **Quote**） |
| **行项目** | InvoiceItem / EstimateItem | **保留**（= 单据上的一行：描述/数量/单价/金额） |
| **目录 Item** | 电商式可复用商品库为核心 | **重设计**：以**自由填写行项目**为主（重 `description`、可多行）；目录降级为**可选便利项**（如 “Labor/hour”）；`unit` 改为**可选**（待细化） |
| **Customer** | 是可登录实体（客户门户） | **去掉登录/门户**。Customer = 纯数据记录，但**必须含地址**（发票抬头客户信息） |
| **Payment / Expense** | 收款 / 开支 | **保留** |
| **Transaction（在线支付）** | 接 Stripe 等网关 | **v1 不做**（不接任何在线支付） |
| **多币种** | 公司本位币 + `base_*` + 汇率 provider | **保留**（预留 ECB 汇率源） |
| **自定义字段 / 三层设置 / 参考数据** | CustomField、Setting/CompanySetting/UserSetting | 暂无定论，**后续讨论** |
| **金额存储** | 整数·最小货币单位（分） | 改用 **BigDecimal / `Decimal`**（DB `NUMERIC`），精度到分甚至 1/1000（scale≈3）；定死舍入规则与位置 |
| **算钱位置** | 前端算好、后端照单全收 | **后端权威计算 + 校验**；前端只录原始输入（可本地预览） |

**待进一步讨论**：`unit` 是否可选的细节；目录便利项的具体形态；参考数据（国家/收款方式/开支分类）；Convert（报价→发票）是否保留及命名。

**贯穿全程的设计原则**：
- **「多租户友好的 schema + 单租户的简单逻辑」**：v1 按单公司单用户*实现*，但数据模型不写死成单例——把「公司/业务主体」建成一个实体（哪怕只一行），核心表保留归属关系，使未来扩到多公司多用户是“加表加 UI + 一次迁移”，而非重写。多公司切换、`user_company`、按公司权限隔离、客户门户等**应用层复杂度 v1 直接不做**。`company_id` 现在就挂还是日后 Alembic 补列，留到建表时定。
- **当前为 high-level 设计**：停在「实体 + 关系 + 关键业务规则」这一层；字段级细节（地址结构、电话/邮编、社交账号等长尾“额外存储”信息）**延后到实现时**再定（很可能用 JSON/自定义字段承载长尾）。

### 2026-06-02 · 第 3 章评审：技术栈与架构决策

**参考母版**：作者已上线的同构项目 `~/workspace/trading-journal`（单容器；后端 FastAPI + SQLAlchemy(async) + Alembic + fastapi-users + pydantic-settings + uv；前端 Vue3 + Pinia + Vite + Naive UI + ECharts + TS + openapi-typescript 类型生成；多阶段 Dockerfile 把前端 `dist` 拷进后端 `STATIC_DIR`，uvicorn 单容器托管 API+SPA）。**新项目 = 此架构骨架 + 发票领域 + PostgreSQL/RLS，前端可大量复用。**

| 主题 | 决策 |
| --- | --- |
| 后端框架 | **FastAPI**（作者有经验） |
| ORM / 迁移 | **SQLAlchemy 2.0 (async) + Alembic** |
| 数据库 | **PostgreSQL**（开发期即用，不用 SQLite；为 RLS 铺路） |
| 多租户 | **Postgres 行级安全 (RLS)**；schema 友好（用户绑定到公司）；中远期定位**单公司多用户** |
| 认证 | V1=**用户名+密码 + MFA(TOTP)**；哈希 **Argon2**；用 **fastapi-users** 起步；用户绑 company + 带 Role（建表即留） |
| 认证·未来可选 | Passkey/WebAuthn、OAuth/SSO（Google/Microsoft） |
| 授权 | V1 从简（owner/基础角色），RBAC 后补 |
| API | FastAPI best practice，Pydantic 请求/响应模型分离；**算钱不进 schema 层**；统一前缀 **`/api/v1`**，其余路由服务 SPA |
| 部署 | **单容器**：FastAPI 托管前端构建产物。开发期前后端分离（前端 `npm run dev`，后端 `uv`+uvicorn） |
| 设置系统 | **保留三层 global/company/user**，单表 key-value + `level` 字段（+scope id），按 user→company→global 回退 |
| 文件存储 | V1 **仅本地**（容器挂载卷）；云/Blob **只留抽象接口**不实现 |
| PDF | 对 dompdf/Gotenberg 均不满意；Python PDF 库**待选**（WeasyPrint 等候选） |
| 邮件 | V1 **仅 SMTP**，无服务商 SDK |
| 队列 | 是否需要**待定**（V1 可能不需要） |
| 定时任务 | **APScheduler**；只需循环发票生成 + 逾期标记 |
| 插件/模块系统 | **完全不做** |
| 自定义字段 | **要**，但形态≈「单据上的标准内容块/默认条款」：保修政策、T&C(可引到官网)、银行账户信息、Payment terms。区别于任意 EAV，**细化待议** |
| 前端 | **Vue 3 SPA + Pinia + Vue Router + Vite + Naive UI + ECharts + TS**，OpenAPI→TS 类型生成 |
| 移动端 | 后端权威 + 干净 REST/OpenAPI ⇒ 前端可换；未来 RN 仅作另一个 API 客户端，可行 |

---

## 第 0 章 · 文档说明

_见文首。_

## 第 1 章 · 项目全景

### 1.1 定位与类别
- **一句话**：InvoiceShelf 是一个**自托管、开源**的 **发票 + 报价 + 开支** 管理 Web 应用，面向个人与中小企业。
- **血缘与现状**：开源项目 **Crater** 的 fork（迁移文件里大量 `update_crater_version_*` 为证），当前 **v2.3.3**，**AGPLv3**。
- **形态**：**Laravel 13 单体后端 + Vue 3 SPA 前端**，REST API 驱动；**多公司多租户**；自带客户门户、循环发票、PDF、邮件、备份、网页安装器与应用内自更新。
- **本文档立场**：作者正在用它，但要用 **Python (FastAPI)** 重做一套更贴合自身（荷兰个体户）的系统。故本文档既是对它的全量分析，也是新项目的取舍依据——决策见〔决策记录〕，规划见第 7 章。

### 1.2 功能全景图

| 领域 | 功能点 | 详见 |
| --- | --- | --- |
| **单据** | 报价 Quote / 发票 Invoice / 循环发票 RecurringInvoice / 行项目 / 计税 / 折扣 / 编号 / 状态机 | 2.2–2.4 |
| **收支** | 收款 Payment（部分/多次）/ 开支 Expense（分类/收据）/ 在线支付 Transaction | 2.2–2.3 |
| **客户与目录** | 客户 + 多地址（账单/收货）/ 商品目录 Item / 单位 Unit | 2.2 |
| **税与币种** | TaxType（百分比/定额/复合）/ 多币种 + 汇率 provider + 汇率快照 | 2.4 |
| **输出** | PDF（DomPDF/Gotenberg，多模板，占位符 + 清洗）/ 邮件（SMTP/Mailable，EmailLog，已读回执） | 3.6 |
| **报表** | 盈亏 / 税务汇总 / 客户销售 / 商品销售 / 开支 | 7.1-F |
| **平台** | 多租户（`company` 头 + scope）/ 三 guard 认证 / Bouncer 权限 / 自定义字段(EAV) / 模块插件系统 / i18n(36 语言) / 网页安装器 / 应用内自更新 / 备份 | 3.x |
| **客户门户** | 客户登录、查看自己单据、在线接受/拒绝报价、（接模块后）在线付款 | 3.1, 3.3 |

### 1.3 角色与使用场景
- **InvoiceShelf 的角色**：
  - **Owner**：公司所有者，全权。
  - **内部 User**：经 Bouncer 角色/能力获得公司内权限，可在多家公司间切换。
  - **Customer**：外部客户，有**独立门户登录**，看自己的单据、接受报价、（接模块后）在线付款。
- **典型业务流**：开报价 → 客户接受 → 转发票 → 发邮件 → 收款 →（开支独立成线）→ 季度出报表。
- **新项目 v1 的角色对照**（详见第 7 章）：**只有一个 owner**；**客户不登录**（纯数据）；多用户 / 角色的关系 schema 预留、v1 暂不做 UI。

## 第 2 章 · 领域模型与业务流程

> **本章已与作者确认的新项目决策**（贯穿全章的 🔧 标注以此为准）：
> - 术语：`Estimate` → **`Quote`（报价）**；`Invoice`、`Payment` 沿用。下文用「Quote（= InvoiceShelf 的 Estimate）」标注以便对照源码。
> - **算钱在后端**：前端只录原始输入（数量/单价/折扣率/税率），后端计算并校验最终金额后落盘。
> - **多币种保留**：每个公司有 base currency，落盘以 base currency 为权威口径；开票可选外币；预留接入汇率源（如 ECB）。

### 2.1 实体关系全景

#### 2.1.1 实体分组清单

| 分组 | 实体 | 角色 |
| --- | --- | --- |
| **租户核心** | `Company`、`User`、`user_company`(pivot)、`Address` | 公司=租户根；用户多对多归属公司，公司有单一 owner |
| **三大单据** | `Quote`(Estimate) 报价 / `Invoice` 发票 / `RecurringInvoice` 循环发票 | 业务主线，结构高度同构 |
| **单据明细** | `InvoiceItem` / `EstimateItem` | 行项目（循环发票复用 `InvoiceItem` 作模板） |
| **收支** | `Payment` 收款 / `Expense` 开支 / `Transaction` 在线交易 | 钱的进出 |
| **目录与税** | `Item` 商品 / `Unit` 单位 / `Tax` / `TaxType` | 可复用商品库 + 税定义 |
| **客户侧** | `Customer`（同时是登录实体）/ `Address`（账单/收货） | 客户 + 多地址 + 客户门户账号 |
| **多币种** | `Currency` / `ExchangeRateProvider` / `ExchangeRateLog` | 币种、汇率驱动、汇率快照 |
| **扩展/元数据** | `CustomField` / `CustomFieldValue` / `Note` / `EmailLog` / `Media` / `Module` | EAV 自定义字段、笔记模板、邮件日志、附件、插件 |
| **设置三层** | `Setting`(全局) / `CompanySetting`(公司) / `UserSetting`(用户) | key-value |
| **参考数据** | `Country` / `PaymentMethod` / `ExpenseCategory` | 字典/分类表 |

#### 2.1.2 关系骨架（文字版 ER）

```
Company (租户根)
├─1:1─ Address(公司地址)        ├─1:N─ CompanySetting
├─N:M─ User (user_company)      ├─N:1─ owner: User
├─1:N─ Customer ─1:N─ Address(billing/shipping)
│         ├─1:N─ Invoice / Quote / RecurringInvoice / Payment / Expense
│         └─N:1─ Currency        (客户级币种，有交易后锁定)
├─1:N─ Item ─1:N─ Tax(默认税)    ├─N:1─ Unit / Currency
├─1:N─ TaxType ─1:N─ Tax         ├─1:N─ PaymentMethod / ExpenseCategory / Unit
│
├─1:N─ Invoice
│        ├─1:N─ InvoiceItem ─(N:1)─ Item ─1:N─ Tax(行级税)
│        ├─1:N─ Tax(单据级税) ─N:1─ TaxType
│        ├─1:N─ Payment       ├─1:N─ Transaction(在线支付)
│        ├─N:1─ Customer / Currency / creator(User)
│        └─N:1─ RecurringInvoice (nullable，来源)
│
├─1:N─ Quote(Estimate) ─1:N─ EstimateItem / Tax   （结构同 Invoice，无收款维度）
├─1:N─ RecurringInvoice ─1:N─ InvoiceItem(模板) / Tax ─1:N─ Invoice(生成的)
├─1:N─ Payment ─N:1─ Invoice(nullable) / PaymentMethod / Transaction(nullable)
└─1:N─ Expense ─N:1─ ExpenseCategory / Customer(nullable) / PaymentMethod  ＋ 收据(Media)

横切：
  CustomFieldValue ─(morph)→ {Customer, Invoice, Quote, Payment, Expense, Item}
  EmailLog         ─(morph)→ {Invoice, Quote, Payment}
  Media            ─(morph)→ {Company logo, Customer avatar, Expense receipt, …}
```

#### 2.1.3 三个值得记住的全局事实

1. **`Customer` 本身是一个可登录实体**（在 PHP 里它 `extends Authenticatable`，有 `password`、API token、角色能力），这就是「客户门户」的账号载体。→ 系统里有**两类人**：内部 `User`（后台）和外部 `Customer`（门户）。
2. **几乎所有删除都是代码里手写的“级联删除”**（`Company::deleteCompany`、`Customer::deleteCustomers`、`Invoice::deleteInvoices` 逐层遍历子表 `->delete()`），**不依赖数据库外键 `ON DELETE CASCADE`**。这是个脆弱点（容易漏删产生孤儿数据），见 7.3 避坑清单。
   - 🔧 新项目建议：用数据库级外键约束 + ORM 的 cascade（SQLAlchemy `relationship(cascade=...)` / `ondelete="CASCADE"`），少手写删除链。
3. **新建公司时 `Company::setupDefaultData()` 会一次性播种**：Bouncer 角色（super admin + 全部能力）、默认收款方式（Cash/Check/Credit Card/Bank Transfer）、默认单位（box/cm/kg…）、以及一大批默认 `CompanySetting`。→ 「开一家新公司」是一个有副作用的初始化流程，不是单纯 insert 一行。

### 2.2 核心对象详解

> 字段只列**有领域含义的关键列**（金额类如无特别说明均为「整数·最小货币单位（分）」，见 2.5）。`base_*` = 该金额 × 汇率，换算为公司本位币，详见 2.4。

#### Company（公司 / 租户根）
- 关键列：`name`、`owner_id`、`slug`（用于客户门户 URL）、`vat_id`/`tax_id`（2024 年新增）。
- 是所有业务数据的租户边界（其余表都挂 `company_id`）。角色（Bouncer Role）的 `scope` = `company_id`，实现“权限按公司隔离”。

#### User（内部用户）/ Customer（客户）
- `User`：后台用户，多对多归属多家公司；通过 Bouncer 拥有公司内的角色与能力；可被设为某公司 owner。
- `Customer`：客户 + 门户账号。关键列：`name`(显示名)、`contact_name`、`company_name`、`email`、`phone`、`website`、`prefix`(用于编号占位符 `CUSTOMER_SERIES`)、`enable_portal`(bool)、`password`、`currency_id`。
  - **业务规则**：客户一旦有了任何单据/收款，其 `currency_id` 不可再改（`updateCustomer` 返回 `you_cannot_edit_currency`）。
  - 地址：`hasMany(Address)`，用 `type`(BILLING/SHIPPING) 区分，`billingAddress()/shippingAddress()` 各取一条。

#### Item（商品/服务目录）
- 关键列：`name`、`description`、`price`(整数·分)、`unit_id`、`currency_id`、`tax_per_item`(bool)。
- 作用是**开单时的模板**：选一个 Item 带出名称/单价/默认税，但行项目落到 `InvoiceItem`/`EstimateItem` 后是**值拷贝**（快照），改目录价不影响历史单据。
- `Item.taxes()` 只取 `invoice_item_id IS NULL AND estimate_item_id IS NULL` 的 Tax 行——即“这个商品的默认税”。

#### Invoice（发票）— 系统核心
- 单据头关键列：`invoice_number`(展示编号)、`sequence_number`(公司级序号)、`customer_sequence_number`(客户级序号)、`unique_hash`(Hashids，公开链接用)、`reference_number`、`invoice_date`、`due_date`、`status`、`paid_status`、`overdue`/`sent`/`viewed`(bool)、`sub_total`、`discount_type`、`discount`(百分比 float)、`discount_val`(折后额，分)、`tax`、`total`、`due_amount`(待收)、`tax_per_item`、`discount_per_item`、`exchange_rate`、`currency_id`、`template_name`(PDF 模板)、`notes`、`sales_tax_type`/`sales_tax_address_type`(美式销售税)、`recurring_invoice_id`(来源)、`creator_id`。外加全部对应 `base_*`。
- 子表：`items`(InvoiceItem) / `taxes`(Tax) / `payments`(Payment) / `transactions` / `emailLogs`(morph)。
- 行为（“胖模型”，业务逻辑写在 Model 静态/实例方法里）：
  - `createInvoice / updateInvoice`：建/改单（编号、序号、Hashids、行项目、税、自定义字段、汇率日志）。**更新时先删光 items+taxes 再重建**。
  - `addInvoicePayment / subtractInvoicePayment`：调整 `due_amount` 并重算状态。⚠️ 命名反直觉：`subtract…` 是“收到一笔款”（欠款减少），`add…` 是“撤销一笔款”。
  - `getAllowEdit`：按公司设置 `retrospective_edits` 决定已发出/已付款的发票还能不能改。

#### InvoiceItem / EstimateItem（行项目）
- 关键列：`item_id`(可空，目录来源)、`name`、`description`、`quantity`、`price`、`discount_type`/`discount`/`discount_val`、`tax`、`total`，+ `base_*`。
- 行级税挂在 `Tax` 表上（`invoice_item_id`/`estimate_item_id`）。

#### Quote（Estimate，报价）
- 与 Invoice 几乎同构，但**没有收款维度**（无 `paid_status`/`due_amount`/payments）。状态见 2.3。
- 特有行为：`checkForEstimateConvertAction()`（转换为发票后对原报价的处置）、`getInvoiceTemplateName()`（把报价模板名映射成发票模板名）。

#### RecurringInvoice（循环发票）
- 是一张**发票模板 + 调度规则**。关键列：`frequency`(**cron 表达式**)、`starts_at`、`next_invoice_at`、`limit_by`(NONE/COUNT/DATE)、`limit_count`/`limit_date`、`status`(ACTIVE/ON_HOLD/COMPLETED)、`send_automatically`(bool)，以及一份发票快照字段（sub_total/tax/total/discount/…）。
- 行为：`generateInvoice()`（判断是否到期/是否超限 → `createInvoice()` 克隆出真实 Invoice → `updateNextInvoiceDate()` 用 cron 算下次）。由 webhook 周期触发（见 2.3）。

#### Payment（收款）
- 关键列：`payment_number`、`sequence_number`/`customer_sequence_number`、`unique_hash`、`payment_date`、`amount`、`base_amount`、`exchange_rate`、`currency_id`、`invoice_id`(可空)、`payment_method_id`、`customer_id`、`transaction_id`(可空，在线支付)、`notes`、`settings`(JSON)。
- 行为：`createPayment`（建收款时若关联发票→`invoice->subtractInvoicePayment()`）；`generatePayment($transaction)`（在线支付成功后整额结清发票）。
- **副作用钩子**：`booted()` 里 `created/updated` 事件 → `dispatch(GeneratePaymentPdfJob)`，**异步生成收据 PDF**。

#### Expense（开支）
- 关键列：`expense_date`、`amount`、`base_amount`、`exchange_rate`、`currency_id`、`expense_category_id`、`customer_id`(可空)、`payment_method_id`、`expense_number`、`notes`。
- **无行项目**：就是“一笔金额 + 一张收据（Media，`receipts` 集合）+ 分类”。支持自定义字段、可复制（DuplicateExpense）。

#### Tax / TaxType（税）
- `TaxType`：公司维护的税种定义。关键列：`name`、`percent`(float)、`fixed_amount`(整数·分)、`compound_tax`(bool，复合税/税上税)、`type`(GENERAL/MODULE)。
- `Tax`：一次具体计税的落地记录，关键列：`tax_type_id`、`percent`、`amount`(整数·分)、`fixed_amount`、`base_amount`、`currency_id`。
- ⚠️ **结构特点**：`Tax` 用**一张宽表 + 多个可空外键**来表达“这条税属于谁”：`invoice_id` / `estimate_id` / `recurring_invoice_id` / `invoice_item_id` / `estimate_item_id` / `item_id` 同时存在，谁非空就属于谁。这是手写的多态，便于 SQL 聚合（报表里 `groupBy(tax_type_id)` 求和），但约束弱、可空列多。
  - 🔧 新项目可考虑：要么用 ORM 正规多态（`taxable_type`/`taxable_id`），要么干脆分「单据级税表」「行级税表」两张。

### 2.3 状态机与业务闭环

#### 2.3.1 三条状态线

**Invoice 双状态**（两条线独立）：
```
单据生命周期 status :  DRAFT ──发送──▶ SENT ──客户打开──▶ VIEWED ──付清──▶ COMPLETED
收款状态 paid_status:  UNPAID ──收到部分──▶ PARTIALLY_PAID ──收满──▶ PAID
辅助布尔: sent / viewed / overdue
```
- 收款金额变化时由 `getInvoiceStatusByAmount(due)` 推导：`due==0` → COMPLETED/PAID；`due==total` → 回到“之前状态”/UNPAID；否则 → PARTIALLY_PAID。
- ✅ 把“单据流转”和“收款进度”拆成两个维度，清晰，建议抄。

**Quote（Estimate）单状态**：
```
DRAFT → SENT → VIEWED → ACCEPTED / REJECTED / EXPIRED
```
（报价无收款维度；客户可在门户里 Accept/Reject。）

**RecurringInvoice 状态**：`ACTIVE ⇄ ON_HOLD → COMPLETED`（到达 COUNT/DATE 上限自动 COMPLETED）。

#### 2.3.2 核心业务闭环

```
        ┌───────────── RecurringInvoice ──(cron 到点)──┐
        │                                              ▼
   Quote(Estimate) ──Convert(接受)──▶  Invoice  ──应用收款──▶  Payment
        │   转换后原报价: 删除/标记ACCEPTED/不动            │  (减 due_amount, 重算 paid_status)
        └── 客户门户 Accept/Reject                         └─ 在线支付(Transaction) → 自动整额 Payment
   Expense 独立成线（不进发票），仅用于支出统计/利润表
```

- **Convert（报价→发票）**：把 Quote 的客户、行项目、税、金额拷贝成一张新 Invoice，按 `estimate_convert_action` 设置处置原报价。模板名做 `estimate*→invoice*` 映射。
  - 🔧 新项目（已定）：v1 **保留 Convert**（报价被接受 → 转发票），命名沿用；详见 7.2.1。
- **循环发票生成**：`generateInvoice()` 克隆出 DRAFT 发票，`due_date = 今天 + invoice_due_date_days(默认7)`；若 `send_automatically` 则立即发邮件。

### 2.4 横切业务规则

#### 2.4.1 序列号 / 单据编号（`SerialNumberFormatter`）
- 每张单据维护**两个自增序列**：公司级 `sequence_number`、客户级 `customer_sequence_number`（“取该范围内 max+1”）。
- 对外展示的 `invoice_number` 由**占位符模板**渲染，模板存 `CompanySetting`（如默认 `{{SERIES:INV}}{{DELIMITER:-}}{{SEQUENCE:6}}`）。支持的占位符：`SEQUENCE:n`(补零)、`CUSTOMER_SEQUENCE`、`CUSTOMER_SERIES`(客户 prefix)、`SERIES`、`DATE_FORMAT:fmt`、`RANDOM_SEQUENCE:n`、`DELIMITER`。
- 另有 `unique_hash` = `Hashids(id)`，用于公开 PDF 链接，避免暴露自增主键。
- ✅ 模板化编号 + Hashids 都值得抄。⚠️ “max+1” 在并发下会撞号（见 7.3 避坑清单）。🔧 Python 端可用 DB 序列/`SELECT … FOR UPDATE`/唯一约束+重试，编号模板用一个小渲染函数实现。**注：新项目还要求编号可自定义起始/跳号（迁移衔接），见 7.2.1。**

#### 2.4.2 多币种与汇率（🔧 新项目要保留）
- 每条业务记录都带 `currency_id` + `exchange_rate`，并为每个金额冗余一份 `base_* = 金额 × exchange_rate`（换算成公司本位币）。
- 当单据币种 ≠ 公司本位币时，写一条 `ExchangeRateLog` 快照（`ExchangeRateLog::addExchangeRateLog($model)`）。
- 汇率来源：`ExchangeRateProvider`（支持 currency_converter / currency_freak / currency_layer / open_exchange_rate 四种驱动）。
- 🔧 新项目落地建议：
  - 本位币归属于 Company；金额**双存**（原币 + base 本位币），报表只用 base。
  - 汇率抽象成 provider 接口，**预留 ECB feed**；锁一个 `exchange_rate` 快照后历史不漂移。**新项目最终锁定口径见 7.4.5**（开票日锁 EUR 税基、收款日另算现金/汇兑）。
  - 若某公司只用单币种，可让 `exchange_rate=1`、`base_*=金额`，逻辑统一、无分支。

#### 2.4.3 计税（tax）
- 两种模式由 `tax_per_item` 设置切换：
  - **按单据计税**（`NO`）：税挂在 Invoice/Quote 上。
  - **按行项目计税**（`YES`）：税挂在每个 InvoiceItem/EstimateItem 上；出 PDF 时再按 `tax_type_id` 汇总。
- `TaxType.compound_tax` 支持**复合税**（在含前序税的基础上再计税）。`fixed_amount` 支持**定额税**（非百分比）。还有美式 `sales_tax_type`/`sales_tax_address_type`（按地址算销售税，多由 Module 扩展）。
- 🔧 新项目（你在荷兰）用 **VAT（增值税，含税/不含税 + 标准/低税率）**，比美式 sales tax 简单；计税逻辑务必放后端（见 2.5）。**完整 VAT 模型与荷兰 BTW 申报口径见 7.4。**

#### 2.4.4 折扣（discount）
- 两级折扣：单据级 + 行级（由 `discount_per_item` 控制）。
- 每处折扣用两个字段表达：`discount_type`(`percentage`/`fixed`) + `discount`(百分比值) + `discount_val`(折算成的实际金额·分)。

### 2.5 金额与精度（强烈建议照抄的约定）
- **所有钱都用整数存最小货币单位（分）**：`total/sub_total/tax/discount_val/price/amount/fixed_amount` 全部 `cast => integer`。只有“比率类”才用 float：`discount`(百分比)、`percent`(税率)、`exchange_rate`。
- 好处：加减完全精确，不踩浮点坑；展示时再除以 100 + 按货币格式化（`format_money` 辅助函数）。
- ⚠️ 但 InvoiceShelf 的**致命弱点**：这些金额是**前端 Vue 算好后整包提交、后端原样 `create()` 落库**，后端不重算、不校验（见 `Invoice::createInvoice` 直接存 `$request->total` 等）。
  - 后果：① 业务规则只活在前端，移动端/API 需各自重写易不一致；② 客户端可伪造金额。
  - 🔧 **新项目（已确认）反过来做**：前端只传 `{item_id?, name, quantity, unit_price, discount_rate, tax_type_ids}` 等**原始输入**；后端一个 `pricing`/`calculation` 服务负责：行小计 → 折扣 → 计税（含复合/定额）→ 单据合计 → base 换算，统一产出并落盘。前端可本地预览，但**以后端结果为准**。
  - Python 实现要点：用 `Decimal` 或整数分做中间计算，明确**四舍五入规则与舍入位置**（逐行舍入 vs 合计舍入会差几分钱，税务上需固定口径）。

---

> **第 2 章小结**：发票系统的“心脏”是 *带行项目的单据（Quote/Invoice/RecurringInvoice）+ 收款(Payment) + 开支(Expense)*，外加 *目录(Item)、税(Tax/TaxType)、客户(Customer)、多币种* 四套支撑。最该继承的是「整数存钱 / 双状态 / 模板化编号 / 多币种 base 值」；最该改的是「把算钱搬到后端 / 别手写级联删除 / 税表结构正规化 / 编号并发安全」。

> **待补**：2.2 中 Customer/Item 等少数字段的完整列清单（如需逐列对照建表，可在写第 3 章数据库部分时一并补全）。

## 第 3 章 · 架构与技术模式

> **写法**：每节给「① InvoiceShelf 现状 → ② ⚠️ 坑（如有）→ ③ 🔧 Python 等价 / v1 建议」。
> **Python 栈约定**：**FastAPI + SQLAlchemy(async) + Alembic + fastapi-users**（已定，见〔决策记录〕）；下文 Python 等价以此为准。
> **v1 视角**：守住「**多租户友好 schema + 单租户简单逻辑**」「无客户登录」「无在线支付」「算钱在后端」这条基线。

### 3.1 整体架构

**① 现状**：一个 Laravel **单体（monolith）**同时承担三件事：
- 提供 REST API（`/api/v1/...`）
- 吐出 SPA 外壳（`resources/views/app.blade.php`，里面挂载 Vue）+ 公开 PDF 路由（`/invoices/pdf/{hash}`）
- 跑后台逻辑（队列、定时任务、邮件）

前端是**两个独立 SPA**：后台管理（admin）+ 客户门户（customer），都用 Vue 3，通过 axios 调同一套 API（用请求头区分租户/身份）。

```
浏览器 ─┬─ Admin SPA (Vue) ──┐
        └─ Customer SPA (Vue)─┤ axios → /api/v1/* (JSON, REST)
                              │
          Laravel ───────────┤── 控制器 → FormRequest 校验 → 胖模型/服务 → Eloquent → DB
                              ├── Blade app.blade.php（SPA 外壳）
                              ├── /…/pdf/{hash}（DomPDF/Gotenberg 出 PDF）
                              └── 队列 worker + 调度器（循环发票/状态检查/邮件）
```

- 🔧 **新项目（已定）**：**单容器部署**——FastAPI 既提供 REST API + PDF 端点，又用静态路由**托管前端 Vite 构建产物**（SPA + catch-all fallback）。这与 InvoiceShelf 的「单体托管前端」同思路，也与作者已有项目 `~/workspace/trading-journal` 的 Dockerfile 一致（多阶段：前端 `vite build` → `dist` 拷进后端 `STATIC_DIR`，`uvicorn` 单容器跑）。**开发期前后端分离**：前端 `npm run dev`（Vite），后端 `uv` + `uvicorn`。
- 🔧 **v1**：只保留 admin 一个 SPA（客户门户砍掉）；API 一律 `/api/v1` 前缀，其余路由吐 SPA 静态文件。

### 3.2 多租户（最该改写的机制之一）

**① 现状**：靠一个 **`company` 请求头**确定“当前公司”。
- `CompanyMiddleware`：若请求没带 `company` 头、或当前用户无权访问该公司，就回退到“该用户的第一家公司”，并写回请求头。
- 然后**每个模型自己写 `scopeWhereCompany()`**：`where('company_id', request()->header('company'))`，控制器查询时手动 `->whereCompany()`。
- 权限（Bouncer）也按公司 scope：`ScopeBouncer` 中间件把 Bouncer 的 scope 设成 company id；角色/能力表带 `scope` 列（`DefaultScope` 自动加 `where scope = ? or scope is null`）。

**② ⚠️ 坑**：租户隔离是**“开发者纪律驱动”的手动 scope**，不是数据库或 ORM 层的强制隔离。**任何一条查询忘了 `whereCompany`，就会跨租户串数据**。这在一个 30+ 模型的系统里是实打实的风险。

**③ 🔧 Python 等价 / v1**：
- v1 单租户：**这套全砍**，不用 `company` 头、不用到处 scope。
- 但按「schema 友好」原则：把**「公司/业务主体」建成一张表**（v1 只一行），租户级表预留归属（现在挂 `company_id` 或将来 Alembic 补列）。
- 将来若真要多租户，**别学“每条查询手动加过滤”**。更稳的两条路：
  1. **Postgres 行级安全（RLS）**——数据库层强制隔离，应用忘加过滤也漏不了（最稳，推荐）。
  2. **SQLAlchemy 统一过滤层**——用 `with_loader_criteria` 事件 / 自定义 Session，或把数据访问收敛到 repository 层，在**一个地方**加租户过滤，而不是散落各处。

### 3.3 认证与授权

**① 现状**：三套 guard（`config/auth.php`）：
| guard | 驱动 | 用户表 | 用途 |
| --- | --- | --- | --- |
| `web` | session | `users` | 后台 SPA（Sanctum stateful cookie） |
| `api` | token | `users` | 个人访问令牌，给移动端/API |
| `customer` | session | `customers` | 客户门户登录 |

- 用 **Sanctum**：SPA 走 stateful cookie，移动端走 token。API 限流 180/min。
- 授权用 **Bouncer**（角色 role + 能力 ability，按公司 scope）；控制器里 `$this->authorize()` + `app/Policies/*`；能力清单在 `config/abilities.php`，建公司时 `setupRoles()` 播种 super admin。

**③ 🔧 Python 等价 / v1**：
- **v1（已定）**：用 **fastapi-users** 起步——用户名+密码（**Argon2** 哈希）+ **MFA(TOTP)** + 邮件密码重置；SPA 同源 cookie / JWT 皆可。**客户不登录** → 无 `customers` guard，Customer 退化成纯数据。
- **授权**：v1 owner 全权、RBAC 从简；但**用户绑 company + 带 Role 的关系建表即留**，将来多用户/RBAC 后补（≈ 自建 RBAC 表或 `pycasbin`）。Bouncer 的「ability + role + scope」是其参照。
- **未来可选**（见 7.2.3）：Passkey/WebAuthn、OAuth/SSO、API token。

### 3.4 API 设计

**① 现状**：
- **版本化前缀** `/api/v1/`。
- 路由 = **RESTful 资源路由 + 大量单动作控制器**（`__invoke`）：标准 CRUD 走资源控制器，动词类操作各开一个端点，如 `POST /invoices/{id}/send`、`/clone`、`/status`、`/convert`；批量删除用 `POST /invoices/delete`（带 `ids`）。
- **校验**：FormRequest 类（`rules()` 定规则；外加 `getXxxPayload()` 组装入库数据）。`authorize()` 多半返回 `true`，授权交给 Policy。
- **序列化**：API Resource（`InvoiceResource` 显式列字段；关联用 `$this->when(...->exists())` 条件加载）。响应统一包在 `data` 里，分页/计数放 `meta`。

**② ⚠️ 坑**：FormRequest 把**校验 + 组装入库 payload + 算 `base_*`** 混在一起（`getInvoicePayload`），且**直接信任前端传来的 `total/sub_total/tax`**（规则只校验 `numeric/required`）。校验层干了业务层的活。

**③ 🔧 Python 等价**：
- **Pydantic** 同时把 Laravel 的「FormRequest + Resource」两件事干得更干净：**请求模型**（入参 schema）与**响应模型**（出参 schema）分开，类型安全、自动文档（FastAPI 的 OpenAPI）。
- 版本化：router `prefix="/api/v1"`。动词端点：`POST /invoices/{id}/send` 同理。
- 🔧 **关键纠偏**：schema 层只校验**形状与基本约束**；**金额计算放进独立的 `pricing`/`service` 层**（见 2.5）。请求模型只收 `{customer_id, items:[{name, description, quantity, unit_price, discount, tax_type_ids}], ...}` 这类**原始输入**，服务端算出 `sub_total/tax/total/base_*` 再落库。

### 3.5 设置系统（三层 key-value）

**① 现状**：`Setting`（全局）/ `CompanySetting`（公司级，`option`/`value` 两列）/ `UserSetting`（用户级）。`getSetting/getAllSettings/setSettings` 一组静态方法存取。
- ⚠️ **全是字符串**（`'YES'/'NO'`、数字也存成字符串），且 `getSetting` 到处被调、**每次打 DB**（没看到缓存）。一个发票详情页可能触发几十次设置查询。

**③ 🔧 Python 等价 / v1**：
- 🔧 **新项目（已定）**：**保留三层**（global / company / user），实现为**一张表：key-value + `level` 字段**（必要时加 scope id 指向具体公司/用户）。三层各有用途——全局（外观/系统级默认）、公司（抬头/logo/VAT/本位币/编号规则/计税偏好）、用户（个人偏好）。
- 实现建议：单表存取 + **内存缓存**；值尽量**类型化访问**（Pydantic / 枚举封装，避免 `'YES'/'NO'` 这种 stringly-typed）；读取按 `user → company → global` 优先级回退。

### 3.6 文件 / PDF / 邮件 / 队列 / 定时任务

**① 现状**：
- **文件/媒体**：Spatie MediaLibrary；`FileDisk` 可配 local / S3 / Dropbox；媒体多态挂在模型上；非本地盘用临时 URL。
- **PDF**：两种驱动——**DomPDF**（Blade 模板 → PDF）或 **Gotenberg**（HTML→PDF 服务）。`template_name` 选模板（发票/报价各 3 套）。出 PDF 前有一套**占位符替换引擎**（`getFieldsArray/getFormattedString`：`{COMPANY_NAME}`、`{BILLING_CITY}`、自定义字段 slug…），并做 `htmlspecialchars` + **`PdfHtmlSanitizer` 清洗**（防 SSRF，是近期安全修复）。可同步 stream，也可异步存盘（`save_pdf_to_disk` 设置 + `GeneratePaymentPdfJob` 等 Job 在 created/updated 时 dispatch）。
- **邮件**：Mailable 类 + `EmailLog` 追踪 + “已读”回执（邮件里嵌像素/链接回调）。
- **队列**：database/redis 队列 + Job。
- **定时**：`routes/console.php` 注册调度——`check:invoices:status`/`check:estimates:status` 每日跑（标记逾期/过期）；**每个 ACTIVE 循环发票按自己的 `frequency`(cron 表达式) + 公司时区注册成一条调度**。触发方式：系统 cron，**或外部 webhook** `POST /webhook/...` → `Artisan::call('schedule:run')`（容器/无 cron 环境友好）。

**③ 🔧 Python 等价**：
| 能力 | Python 选择 |
| --- | --- |
| 文件存储 | `boto3`/`fsspec`/`smart_open`（S3）+ 本地；抽象一个 storage 后端 |
| PDF | **WeasyPrint**（HTML+CSS→PDF，做发票很合适）或 渲染 HTML + Gotenberg/Playwright；模板用 **Jinja2** |
| 占位符/清洗 | 保留这个思路：把用户输入渲染进 PDF/HTML 前**务必清洗**（`bleach`/白名单）防 XSS/SSRF |
| 邮件 | SMTP / 邮件服务商 SDK + 自建 `email_log` 表 |
| 队列/异步 | **Celery** / RQ / Dramatiq / **arq**(asyncio) |
| 定时 | **Celery beat** / **APScheduler** / 系统 cron；循环发票用 **`croniter`** 算下次时间 |
- 🔧 **v1**：PDF（核心）+ 邮件（核心）保留；队列可先用最轻的（甚至同步生成 PDF，量小无所谓）；定时任务 v1 只需「循环发票生成 + 逾期标记」两件事。

### 3.7 自定义字段 + 插件/模块系统

**① 现状**：
- **自定义字段（EAV）**：`CustomField`（定义：type/slug/model_type/company_id）+ `CustomFieldValue`（多态 `morphMany`，值存进类型对应的列）。`HasCustomFieldsTrait` 复用。支持 Customer/Invoice/Estimate/Payment/Expense/Item。
- **模块系统**：`invoiceshelf/modules`（`nwidart/laravel-modules` 的 fork）——可安装/启用的插件（如 `Payments` 模块、美式 `Sales Tax`）。模块能注入路由、JS/CSS、TaxType（`TYPE_MODULE`）。

**③ 🔧 Python 等价 / v1**：
- **v1 都不要**：自定义字段对单人 v1 是过度设计；你那个「Item 重描述、自由填写」的诉求，本身就替代了一部分自定义字段的需求。模块系统更是 v1 之外。
- 真要存长尾/可扩展信息（你提的“社交账号”等）：**Postgres `JSONB` 列**比 EAV 简单得多，查询也够用。EAV 仅在“用户可自定义字段并要结构化查询”时才值得。
- 插件系统将来若需要：Python 用 entry points / `pluggy`。

### 3.8 前端架构

**① 现状**：Vue 3 SPA + **Pinia**（按领域分 store：invoice/customer/item/...）+ Vue Router + Vite + Tailwind4 + **vue-i18n** + **Vuelidate**（校验）+ axios（`@/scripts/http` 封装）。`main.js` 创建一个全局 `InvoiceShelf` 类并把 Vue/router/pinia 挂到 `window`（为模块系统注入留口子，略 hacky）。
- store 里既存表单状态，又有**计算 getter**（`getSubTotal/getTotalTax/getTotalCompoundTax/getTotal`）——**这就是“前端算钱”的所在地**；action 负责调 API。

**② ⚠️ 坑**：金额业务规则活在前端 store 的 getter 里 → 移动端/任何别的客户端都得重写一遍，极易不一致；也是被篡改面。

**③ 🔧 v1 / 选型**：
- **算钱搬后端后**，前端这些 getter 退化成**纯展示预览**：要么前端轻量重算只为即时预览（以后端为准），要么编辑时调一个后端 `POST /invoices/calculate` 拿权威结果回显。
- **前端框架（已定）**：**Vue 3 + Pinia + Vue Router + Vite + Naive UI + ECharts + TypeScript**，沿用 `~/workspace/trading-journal` 的写法与 **OpenAPI→TS 类型生成**（见〔决策记录〕）。
- **移动端**：业务逻辑都在后端 + 契约即 OpenAPI，未来上 React Native 只是“再接一个客户端”，与现在选 Vue 不冲突（RN 列为预留，见 7.2.3）。

---

> **第 3 章小结**：InvoiceShelf 的架构是「Laravel 单体 + 双 Vue SPA + 头部驱动的多租户 + Bouncer 权限 + Spatie 媒体/备份 + 模板化 PDF + webhook 驱动调度」。对你 v1 最有价值的继承：**版本化 REST API、Resource/Schema 分离、模板化 PDF + 输入清洗、cron 表达式驱动循环发票**。最该改写：**多租户别用手动 scope（要么 RLS、要么统一过滤层）、算钱进后端服务层、设置类型化、客户门户与模块系统先不做**。

## 第 4 章 · 技术栈与依赖清单

> **本版有意略过**。新项目技术栈已在〔决策记录〕敲定（FastAPI + SQLAlchemy/Alembic + fastapi-users + PostgreSQL；Vue3 + Pinia + Vite + Naive UI + ECharts + TS），不再逐一镜像 InvoiceShelf 的 Composer/npm 依赖——直接以 `~/workspace/trading-journal` 为依赖母版。InvoiceShelf 关键能力的 Python 对应物，已集中在 **3.6 的对照表**及第 3 章各节。

## 第 5 章 · 仓库内容盘点

> **本版有意略过**。InvoiceShelf 仓库里除核心代码外的东西（网页安装向导、应用内自更新、Spatie 备份、Crater 迁移史、`_ide_helper*`、Docker、36 语言 i18n、模块系统等）**大多是其专有包袱，新项目不会复制**（取舍见 7.2.2 / 7.2.3）。某天若需细查可单独再补。

## 第 6 章 · 现状评估：踩坑与可改进点

> **已浓缩进 [7.3 避坑清单]**（开工时贴显示器的 11 条纪律）；各处坑点亦以 ⚠️ 就地标注在第 2、3 章。

## 第 7 章 · 新项目规划（功能优先级 + 技术选型）

### 7.1 InvoiceShelf 全功能清单 + v1 圈定

> **用法**：下表逐项列出 InvoiceShelf 的功能 + 我的**预判**。作者按模块逐块过，标注：**要(v1) / 不要 / 改**。确定后我把「预判」列改成「已定」并补上你的意见。
>
> **预判图例**：✅ 建议 v1 必做 ｜ 🔜 建议 v1 之后再加 ｜ ❌ 建议不做 ｜ 🔧 要做但形态需改 ｜ 🔒 已在决策记录中拍板
>
> **状态列**：⬜（作者已于 2026-06-02 逐条圈定；下表「预判」列保留作对照，**最终 v1 范围以 7.2 路线图为准**）。

#### A. 单据与核心业务流

| 功能 | 一句话 | 预判 | 状态 |
| --- | --- | --- | --- |
| Quote（报价）CRUD + 列表 | 建/改/查报价单 | ✅🔒（Estimate→Quote） | ⬜ |
| Quote 状态流转 | draft/sent/viewed/accepted/rejected/expired | ✅ | ⬜ |
| Quote → Invoice 转换（Convert） | 报价被接受后落成发票 | 🔧（保留与否/命名待议） | ⬜ |
| Quote 克隆 | 复制一份报价 | 🔜 | ⬜ |
| Invoice（发票）CRUD + 列表 | 系统核心 | ✅ | ⬜ |
| Invoice 双状态 | 生命周期 + 收款状态 | ✅ | ⬜ |
| Invoice 克隆 | 复制一份发票 | 🔜 | ⬜ |
| Invoice 标记为已发送/状态变更 | 手动改状态 | ✅ | ⬜ |
| Recurring Invoice（循环发票）CRUD | 按 cron 周期自动生成发票（适合 retainer 客户） | 🔜（你说保留，但是否 v1 待定） | ⬜ |
| 循环发票自动发送 | 生成后自动发邮件 | 🔜 | ⬜ |
| 行项目（自由填写） | 描述/数量/单价/金额，重 description | ✅🔒 | ⬜ |
| 可复用目录项（labor/hour 等） | 便利项，非核心 | 🔧🔒（降级为可选） | ⬜ |
| 行项目单位（unit） | 单位可选 | 🔧🔒（改为可选） | ⬜ |
| 按单 / 按行计税 | tax_per_item 切换 | ✅ | ⬜ |
| 含税 / 不含税 | tax_included | ✅ | ⬜ |
| 单据级 / 行级折扣 | 百分比或定额 | ✅ | ⬜ |
| 逾期标记 | 定时扫描标记 overdue | ✅ | ⬜ |
| 已发/已付后能否再改（retrospective edits） | 可配置锁定 | 🔜 | ⬜ |
| 编号模板（序列号占位符） | `{{SERIES}}{{SEQUENCE:6}}` 等 | ✅ | ⬜ |
| 公开 PDF 链接（Hashids）+ 链接过期 | 不暴露自增 id | ✅ | ⬜ |

#### B. 收款与开支

| 功能 | 一句话 | 预判 | 状态 |
| --- | --- | --- | --- |
| Payment 收款（关联发票/独立） | 记录收款、扣减欠款 | ✅🔒 | ⬜ |
| 部分付款 / 多次付款 | 一张发票多笔收款 | ✅ | ⬜ |
| 收款方式（PaymentMethod） | Cash/Bank/… 字典 | ✅ | ⬜ |
| 收款收据 PDF + 邮件 | 发收据给客户 | 🔜 | ⬜ |
| Expense 开支 + 分类 | 记支出、归类 | ✅🔒 | ⬜ |
| 开支收据上传 | 附票据图片/PDF | ✅ | ⬜ |
| 开支复制 | 快速复制一笔 | 🔜 | ⬜ |
| 在线支付（Transaction/Stripe） | 网关收款 | ❌🔒 | ⬜ |

#### C. 客户与目录

| 功能 | 一句话 | 预判 | 状态 |
| --- | --- | --- | --- |
| Customer 管理（CRUD） | 客户档案 | ✅🔒 | ⬜ |
| Customer 地址（账单/收货） | 发票抬头客户信息 | ✅🔒 | ⬜ |
| Customer 每客户币种 | 客户默认货币 | ✅ | ⬜ |
| 客户门户登录 | 客户自助查看 | ❌🔒 | ⬜ |
| 客户门户内接受/拒绝报价 | 门户交互 | ❌🔒 | ⬜ |
| 客户统计页 | 该客户汇总数据 | 🔜 | ⬜ |
| 自定义字段 → 单据标准内容块 | 保修/T&C/银行信息/付款条款 | 🔧🔒（细化待议） | ⬜ |

#### D. 税与多币种

| 功能 | 一句话 | 预判 | 状态 |
| --- | --- | --- | --- |
| 税种管理（TaxType） | 百分比/定额/复合税 | ✅（你大概率用 VAT） | ⬜ |
| 多币种 + 本位币 | 开票选外币，本位币落盘 | ✅🔒 | ⬜ |
| 汇率快照（下单锁汇率） | 历史不漂移 | ✅ | ⬜ |
| 自动汇率 provider（4 种 API） | 拉实时汇率 | 🔜（预留 ECB） | ⬜ |
| 批量设置汇率 | bulk exchange rate | 🔜 | ⬜ |

#### E. PDF / 邮件 / 模板

| 功能 | 一句话 | 预判 | 状态 |
| --- | --- | --- | --- |
| 发票/报价 PDF 生成 | 核心输出 | 🔧🔒（换 Python PDF 库） | ⬜ |
| 多套 PDF 模板（各 3 套） | 可选版式 | 🔜（v1 一套够） | ⬜ |
| 自定义 PDF 模板（模块） | 用户自带模板 | ❌🔒 | ⬜ |
| 邮件发送（SMTP） | 发单据给客户 | ✅🔒（仅 SMTP） | ⬜ |
| 邮件正文模板 + 占位符 | `{INVOICE_NUMBER}` 等 | ✅ | ⬜ |
| EmailLog 邮件日志 | 记录发了什么 | 🔜 | ⬜ |
| 已读回执（viewed 追踪） | 客户是否打开 | 🔜 | ⬜ |
| 地址格式占位符 | 抬头排版模板 | 🔜（可简化为固定排版） | ⬜ |
| Notes 默认备注/笔记模板 | 每单自由备注 + 可复用模板 | ✅（保留，独立于标准内容块） | ⬜ |

#### F. 报表与仪表盘

| 功能 | 一句话 | 预判 | 状态 |
| --- | --- | --- | --- |
| Dashboard（图表） | 概览 + ECharts 图 | ✅（你要图表） | ⬜ |
| 盈亏报表（P/L） | 收入-支出 | ✅（报税有用） | ⬜ |
| 税务汇总报表 | VAT 申报口径 | ✅（荷兰报 VAT） | ⬜ |
| 客户销售报表 | 按客户统计 | 🔜 | ⬜ |
| 商品销售报表 | 按商品统计 | 🔜（你目录弱化，价值低） | ⬜ |
| 开支报表 | 按分类统计支出 | 🔜 | ⬜ |

#### G. 设置与账户

| 功能 | 一句话 | 预判 | 状态 |
| --- | --- | --- | --- |
| 公司信息（抬头/logo/VAT/地址） | 业务档案 | ✅🔒 | ⬜ |
| 偏好（财年/日期格式/时区/默认到期天数/计税偏好） | 公司级偏好 | ✅ | ⬜ |
| 三层设置（global/company/user） | 单表 + level | ✅🔒 | ⬜ |
| 收款方式 / 开支分类 / 税种 管理页 | 字典维护 | ✅ | ⬜ |
| 用户管理（多用户） | 增删后台用户 | 🔜🔒（单公司多用户方向，建表预留） | ⬜ |
| 角色权限（RBAC） | 角色 + 能力 | 🔜🔒 | ⬜ |
| 文件存储配置（本地/S3/Dropbox） | 多盘可选 | 🔧🔒（V1 仅本地，留接口） | ⬜ |
| PDF / 邮件 / 通知 配置页 | 各项设置 | ✅（邮件配置） | ⬜ |

#### H. 认证

| 功能 | 一句话 | 预判 | 状态 |
| --- | --- | --- | --- |
| 用户名 + 密码登录（Argon2） | 基础登录 | ✅🔒 | ⬜ |
| MFA（TOTP） | 二次验证 | ✅🔒 | ⬜ |
| 密码重置（邮件） | 忘记密码 | ✅ | ⬜ |
| Passkey / WebAuthn | 无密码登录 | 🔜🔒 | ⬜ |
| OAuth / SSO（Google/Microsoft） | 第三方登录 | 🔜🔒 | ⬜ |
| API token（移动端） | 令牌鉴权 | 🔜 | ⬜ |
| 客户认证（customer guard） | 门户登录 | ❌🔒 | ⬜ |

#### I. 运维 / 平台（多为 InvoiceShelf 专有包袱）

| 功能 | 一句话 | 预判 | 状态 |
| --- | --- | --- | --- |
| 网页安装向导 | 浏览器里装应用 | ❌（容器化部署替代） | ⬜ |
| 应用内自动更新 | 后台点按钮升级 | ❌（容器镜像替代） | ⬜ |
| 数据备份 | Spatie backup | 🔜（pg_dump + 卷快照） | ⬜ |
| 模块 / 插件系统 | 可装扩展 | ❌🔒 | ⬜ |
| i18n 多语言 | 国际化 | ✅（v1：EN + ZH） | ⬜ |
| 多公司管理 + 切换 | 多租户 UI | 🔜🔒（schema 预留，v1 不做 UI） | ⬜ |
| 移动端 App（React Native） | 独立 App | 🔜🔒 | ⬜ |

### 7.2 v1 路线图

> 基于 7.1 作者逐条圈定（2026-06-02）。**这是 v1 的权威范围**；7.1 表的「预判」列仅作对照。

#### 7.2.1 v1 范围（IN）
- **认证**：用户名+密码（Argon2）、MFA（TOTP）、密码重置（邮件）。
- **客户**：CRUD + 账单/收货地址 + 每客户默认币种 + 预留可扩展字段。
- **报价 Quote**：CRUD + 列表；**简化状态** draft/sent/accepted/rejected/expired（去掉 viewed，因无客户登录）；**到期自动置 expired**（后端定时）；**转 Invoice**（Convert 保留）。
- **发票 Invoice**：CRUD + 列表 + 双状态（生命周期 + 收款）；标记状态；**已付发票存档 + 可重新发送**；克隆（低优，可加）。
- **行项目**：自由填写、**description 用 text 不限 255**；数量必填、**单位可选**；可选**可复用目录项**（如 labor/hour，便利非核心）。
- **计税**：默认按单、可切按行；**每行可选不同税种**；VAT 灵活税率（高/低/零）；整单**含税与不含税都显示**。
- **折扣**：单据级 + 行级。
- **编号**：模板化 + **可自定义起始序号 / 跳号**（迁移旧系统衔接，高优）。
- **多币种**：公司本位币 + 开票可选外币；外币按**开票日汇率**锁 EUR 税基（VAT 合规），收款日汇率用于现金/汇兑（可后置）。详见 7.4.5。
- **收款 Payment**：独立实体且关联发票；**部分/多次付款**（首/中/尾款，大项目必需）；收款方式（内置常用 + 可配）；**PDF 下载（高优）**；收据邮件（低优）。
- **开支 Expense**：分类（对齐 NL/EU 税口径）；收据上传（图片/PDF）；**AI 智能填写**（票据图片→视觉大模型→自动填金额/日期/供应商/分类）；**周期性开支**（替代“复制”，固定成本定周期自动计算）。
- **PDF**：发票/报价 PDF 生成（**一套模板**）+ 下载（**无公开链接**，手动发客户）。
- **邮件**：SMTP + 正文模板/占位符 + **Email log（后端存日志）**；**无已读回执**。
- **报表/仪表盘**：盈亏 P/L、**税务汇总（VAT 申报口径，需自研）**、开支报表、Dashboard（ECharts 图表）。
- **设置**：公司业务档案（抬头/logo/VAT/地址/本位币）、**三层设置**（global/company/user，单表+level）、字典维护（收款方式/开支分类/税种）、邮件配置、PDF 配置。
- **文档内容模板**：常见工种（如充电桩安装）的报价/发票存成**可复用模板**（自动把内容填进去），换抬头即可（区别于 PDF 版式模板）。
- **标准内容块**：保修政策 / T&C（可引到官网）/ 银行账户信息 / 付款条款（公司级默认、单据可覆盖）。
- **单据 Notes**：每张单据保留**自由备注**（放“不知道该填在哪”的额外内容）+ 可复用备注模板；**与标准内容块分开**。
- **i18n**：v1 支持 **EN / ZH**（荷兰语暂不做）。
- **平台**：self-host 单容器；**备份用脚本**（v1）。

#### 7.2.2 v1 不做（OUT）
客户登录 / 客户门户 / 门户接受报价；在线支付（Stripe/Transaction）；网页安装向导；**应用内自更新（安全：不给网络控制容器的入口）**；模块/插件系统；自定义 PDF 模板（仅留接口）；已读回执；Quote 克隆；**循环发票（Recurring Invoice）**。

#### 7.2.3 建表即预留、但 v1 不实现
多用户 + 角色权限（RBAC）；多公司切换（多租户 UI；schema 用 **Postgres RLS** + `company_id` 预留）；文件云存储（S3/Blob，留 storage 抽象，v1 仅本地挂载）；自动汇率 provider（ECB）+ 批量汇率；自定义 PDF 模板（CSS 接口）；Passkey（future）；OAuth/SSO；API token；移动端 RN；客户汇总统计；已发/已付单据**禁改锁**（v1 默认可改）；客户/商品销售报表。

#### 7.2.4 ⭐ v1 新增需求（InvoiceShelf 没有，需自研）
1. **AI 开支智能填写**——票据图片 → 视觉大模型 → 自动填单（强需求）。
2. **周期性开支**——固定成本按周期自动生成/计算。
3. **税务汇总报表（VAT 申报）**——NL/EU 口径，InvoiceShelf 完全没有。
4. **可跳号/自定义起始的编号**——迁移旧系统衔接。
5. **报价到期自动置 expired**——后端定时。
6. **外币双口径**——开票日锁 EUR 税基（VAT），收款日算现金/汇兑。
7. **文档内容模板**——常见工种报价/发票一键复用。
8. **i18n EN/ZH**。
9. **开支分类对齐 NL/EU 税口径**。
> 注：税务汇总报表的具体计算口径，作者已同意留到**实现阶段**细化讨论。

#### 7.2.5 建议开发顺序（阶段）
- **P0 地基**：项目骨架（仿 `~/workspace/trading-journal`）+ PostgreSQL + Alembic + 单容器 Dockerfile + CI + i18n 脚手架 + Decimal/货币基础类型。
- **P1 认证 + 公司档案**：fastapi-users（用户名密码）+ MFA(TOTP) + 密码重置；公司业务档案（单例）+ 三层设置（单表+level）。
- **P2 主数据/字典**：客户（CRUD+地址+币种+可扩展字段）；税种(VAT)；收款方式；开支分类；币种/汇率基础。
- **P3 单据核心**：行项目模型（自由填写+目录）；**后端 pricing 服务**（税/折扣/含税不含税/base 换算，权威计算）；发票（CRUD+双状态+编号可跳号）；报价（简化状态+到期自动 expired+转发票）；文档内容模板 + 标准内容块。
- **P4 收款 + 开支**：收款（部分/多次, 关联发票, **收款时锁汇率**）；开支（分类+收据+**周期性开支**）；**AI 开支智能填写**。
- **P5 输出**：PDF 生成（一套模板）+ 下载；SMTP 邮件 + 模板/占位符 + Email log。
- **P6 报表/仪表盘**：P/L + **税务汇总(VAT)** + 开支报表 + Dashboard(ECharts)。
- **P7 收尾**：备份脚本；i18n 补全 EN/ZH；打磨。
- 各阶段建表统一预留：RLS/`company_id`、user-role、storage 抽象、汇率 provider 接口、PDF CSS 接口。

### 7.3 避坑清单（= 第 6 章浓缩，开工时贴显示器上）

逐条都是 InvoiceShelf 的 ⚠️ → 新项目的纪律：

1. **算钱在后端**：前端只录原始输入；后端 pricing 服务权威计算+校验。金额一律 **Decimal**（scale≈3），定死舍入规则与位置。
2. **多租户别手动 scope**：用 **Postgres RLS**；即便 v1 单租户，也把数据访问收敛、留 `company_id`，别散落 `where company=`。
3. **别手写级联删除**：用 DB 外键 + ORM cascade，避免孤儿数据。
4. **编号并发安全**：别 `max+1`；用 DB 序列 / 唯一约束 + 重试；且支持自定义起始与跳号。
5. **设置别 stringly-typed**：类型化访问（Pydantic/枚举）+ 缓存，别满地 `'YES'/'NO'`。
6. **税表结构规范化**：别用一张宽表挂一堆可空 FK；用规范多态或「单据级/行级」分表。
7. **渲染用户输入要清洗**：进 PDF/HTML 前过滤（XSS/SSRF），沿用 InvoiceShelf 近期的 sanitizer 思路。
8. **汇率锁快照**：外币按**开票日**锁 EUR 税基（VAT 合规），收款日另算现金/汇兑；历史不漂移。详见 7.4.5。
9. **不做应用内自更新**：升级走重新部署容器镜像，别给网络留控制容器的入口。
10. **description 用 text**：别再犯 255 上限的错。
11. **OpenAPI→TS 类型生成**：沿用 `trading-journal` 做法，前后端类型一致、契约不漂。

### 7.4 荷兰 VAT（BTW）模型与申报

> InvoiceShelf 完全缺失、而作者刚需的部分。结论基于 2026-06-02 讨论；具体申报数字口径留实现阶段细化。

#### 7.4.1 记账原则
- **销项（output，开票）与进项（input / voorbelasting，开支）都把「不含税净额」与「税额」分开存。**
- 外币见 7.4.5；**落盘以本位币 EUR 为准**。
- 开支侧进项可由 **AI 拍照识别票据自动填**（净额 / 税额 / 税率 / 供应商 / 日期 / 分类）。

#### 7.4.2 核心：每个行项目带「VAT 处理类别」（不只是税率 %）
多个情形税率都可能是 0%，但报在不同格子，所以**按类别建模**。销售侧枚举建议：

| 类别 | 含义 | 税率 | 报表去向 |
| --- | --- | --- | --- |
| `NL_STANDARD_21` | 国内标准 | 21% | 1a |
| `NL_REDUCED_9` | 国内低税 | 9% | 1b |
| `NL_ZERO` | 国内 0% | 0% | 1e |
| `NL_EXEMPT` | 免税（vrijgesteld） | — | （不计销项） |
| `NL_REVERSE` | 国内反向征收（你作分包开票，注明 btw verlegd） | 0% | 客户的 2a |
| `EU_B2B` | 欧盟内 B2B（intra-community） | 0% | 3b + **ICP 清单** |
| `EXPORT_NON_EU` | 出口欧盟外 | 0% | 3a |

进项 / 开支侧另含：国内正常进项（→ 5b 可抵）、欧盟内采购自核（→ 4b 销项+进项对冲）、欧盟外进口（→ 4a）、国内反向征收作接收方（→ 2a 自核）。

> **⚠️ 必须可配置、可扩展（作者硬要求）**：上表是**荷兰默认种子值，不要写死成代码枚举**。税率与 VAT 类别应是**数据驱动、用户可增删改**的记录（NL 默认 21/9/0；将来别国如 DE 19/7、FR 20/10/5.5 由用户自加）。注意「类别 → 申报格子」的映射是**国别特定**的（NL 用 BTW 的 1a/3b…，别国申报表结构不同），所以多国支持时申报报表要按国家各自定义映射——**v1 只做荷兰，但类别/税率表与报表映射逻辑要解耦**，给未来留口。

#### 7.4.3 三大地域（作者确认全都有）
1. **国内**：21% / 9% / 0% / 免税。
2. **欧盟内**：intra-community，B2B 0% + 需报 **ICP**（按客户 VAT 号列明）。
3. **欧盟外**：出口 0%。

#### 7.4.4 为支持申报需要的字段
- **客户**：国家 + **VAT 号**（判定欧盟内 B2B、出 ICP、反向征收都靠它）。
- **公司**：VAT 号、**KOR 开关**（见 7.4.6）、申报周期（季度）。
- **每行**：VAT 类别（7.4.2）+ 净额 + 税额。
- **每笔开支**：净额 + 进项税额 + **是否可抵扣**（招待费等部分/不可抵要标）。

#### 7.4.5 汇率与 VAT（修订早前“仅收款时锁”的决定）
外币单据存**两个 EUR 口径**：
- **开票日汇率 → EUR 税基**：VAT 申报合规要求（税局按供货/开票日或当月公布汇率）。**主口径、落盘。**
- **收款日汇率 → 实际到账 / 汇兑损益**：现金口径，**可后置**实现。

#### 7.4.6 KOR 口子（v1 不启用，留开关）
作者现在**不在 KOR**、正常收 VAT。留一个 KOR 配置开关：开启后 —— **服务不收销项 VAT**，但**采购仍付进项 VAT 且不可抵扣**（KOR 下不能退进项）。

#### 7.4.7 反向征收 / 跨境（按作者理解修订，含一处建议会计确认）
- **国内 B2B（你给本地客户/总包开票、或你雇本地分包）**：作者实际做法 = **正常计 21% VAT**——开票方收税，收票方用**进项抵扣**（销项进 5a、进项进 5b、净额 5c = 5a − 5b）。这对绝大多数国内业务是对的，**v1 默认按此**。
- **欧盟内（intra-community）**：
  - 你**卖给**欧盟 B2B 客户 → **你开 0%** 发票（3b + ICP）。
  - 你**从**欧盟供应商**买** → 对方开 0%，**你自核**采购 VAT（4b 记销项、5b 抵进项，净额对冲）。
- **欧盟外**：卖 = 出口 0%（3a）；买 = 进口，走进口 VAT/海关（4a）。⚠️ 注意：intra-community 仅限**欧盟内**；真·欧盟外不是 intra-community，而是进口。
- ⚠️ **一处建议跟会计确认**：荷兰建筑/安装分包有**反向征收特例**（verleggingsregeling——分包对总包**不收 VAT、发票注明 “btw verlegd”**），是否适用取决于你是否属于“对总包的建筑分包”。**模型保留 `NL_REVERSE` 类别以备适用**，但 v1 默认走正常 21%。

#### 7.4.8 申报基准与周期
- **factuurstelsel（按开票日）**；**季度**申报。
- 报表 = 按季度 + 按 VAT 类别聚合进 BTW 各格（1a/1b/1e/2a/3a/3b/4a/4b/5a/5b/5c）+ 生成 **ICP 清单**（欧盟内 B2B 按客户 VAT 号）。
