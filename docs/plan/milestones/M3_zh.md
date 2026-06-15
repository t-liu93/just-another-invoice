# M3 · 客户（Customer）

> 🌐 [English](M3.md) · **中文**

> 进入本里程碑前 JIT 产出（**lean 版**）。先读 `docs/plan/roadmap.md` 的 §2 全局约束 + M3 那一格，再读分析文档 §2.2（客户与目录）、§2.3（Customer/Address 实体）、§7.2（v1 范围 · 无客户门户）。范围以 roadmap M3 格为准——本文不重复其论证，只把**已定决策 + 原子步骤**钉死。
> **M2 已完成**：`company` 单例表（含本位币 ISO 4217 字符串口径）、`binary_asset` blob 表、三层设置回退做实、`user.company_id` 真 FK。M3 加**第一张真正的「每公司业务数据」表**：客户档案 + 多地址，并首次落地「核心业务表挂 `company_id`」基线（红线 2 / §3.3）。
> **本里程碑由单个 agent（Claude）端到端实现，不 fan-out。** 可与 M4 并行（互不依赖）。

## 目标与范围
- **目标**：一个**能增删改查客户档案 + 账单/收货地址**的应用：列表（搜索 + 分页）、新建/编辑/删除、填 VAT 号与默认币种、维护两类地址；为 M5 单据（客户选择）与 M10（ICP / 反向征收按国家 + VAT 判定）铺好数据底座。**顺带把 M2 公司档案的地址对齐到同一套结构化字段**（B 决策，步骤 3），全局一套地址模型。
- **纳入（IN）**：
  - **`customer` 表（每公司业务数据，挂 `company_id`）**：`name`（显示名）、`contact_name`、`company_name`、`email`、`phone`、`website`、`vat_id`（VAT 号，给 M10 铺路）、`currency`（ISO 4217 字符串，客户默认币种）、`extra`（JSONB 长尾承载）。CRUD + 列表（`q` 搜索 name/email/company_name + `limit/offset` 分页）。**owner-only**（复用 `_owner_only`），写时 service 注入 `company_id`（前端不传）。
  - **`address` 表（BILLING / SHIPPING）**：欧洲/荷兰式结构化字段（`street`/`house_number`/`house_number_addition`/`postal_code`/`city`/`province`/`country_code`），FK → `customer`（`ON DELETE CASCADE`），`UNIQUE(customer_id, type)`——每客户每类型至多一条。**随客户嵌套写读**（`CustomerWrite.addresses[]` / `CustomerRead.addresses[]`），不另起独立地址端点。
  - **校验**：`currency` 校验 ISO 4217、地址 `country_code` 校验 ISO 3166（**复用 M2/company 已有的校验器**）；`email` 基本格式；`addresses[]` 内 `type` 不得重复（→ `400`）。
  - **前端**：客户列表页（搜索 + 分页 + 删除确认）、客户编辑页（标量字段 + 账单/收货地址两块 + 默认币种选择）；列表入口接进现有导航。
  - **公司地址对齐（B 决策，步骤 3）**：把 M2 公司档案地址从老式 `address_line1/2` 迁成**同一套结构化字段**，复用步骤 2 抽出的 `AddressFields` 子 schema + `AddressFieldsForm.vue` 组件；发票渲染时公司抬头与客户地址同构。
- **不纳入（OUT / 留到后续）**：
  - **客户门户 / 登录**（`password`、`enable_portal`）→ **永久砍**（v1 客户 = 纯数据记录，分析文档 §7.2）。
  - **客户编号 prefix（`CUSTOMER_SERIES` 占位符）+ 客户级序号** → **M5**（编号引擎消费时再加列），M3 不碰编号。
  - **「默认币种有交易后锁定」的强制** → M3 **无单据/收款可锁**，规则**延后到 M5（开票）/ M7（收款）**有交易时再强制；M3 阶段 `currency` 可自由改。
  - **币种字典 + 汇率**（可增删改的币种记录、FK）→ **M4**；M3 的 `currency` 仅是校验过的 ISO 4217 字符串（与 M2 `company.base_currency` 同口径，M4 届时一并考虑迁 FK）。
  - **自定义字段 UI / `CustomFieldValue` 多态表** → v1 不做；长尾先塞 `extra` JSONB，不做结构化编辑面。
  - **真·多地址**（一客户任意多条同类型地址）、地址簿复用、RLS 真实启用（仅预留 `company_id`，红线 2 留口）。
  - **地址自动补全**（邮编 + 门牌号 → 自动填街道/城市）→ **后续 follow-on**：荷兰首选 **PDOK Locatieserver**（官方 BAG、免费、无 key），国际可选 **Google Places**（需 key，走类型化设置 + 后端代理，红线 7/9）。M3 只做结构化字段为其铺路，**不实现**。
- **对应文档**：分析文档 §2.1（Company 地址）/ §2.2 / §2.3 / §7.2；roadmap M3 / §2（红线 2 / 3 / 10 / 12）/ §3.3。

## 已定的产品 / 技术决策（动手前定 · 本轮已拍板）
- [x] **地址建模 = 独立 `address` 表 + `type`（BILLING/SHIPPING）**（roadmap 已定）；**每客户每类型至多一条**，靠 `UNIQUE(customer_id, type)` 兜底，与 InvoiceShelf `billingAddress()/shippingAddress()` 各取一条一致。真·多地址 v1 不做。
- [x] **地址写入方式 = 随客户嵌套**：`CustomerWrite.addresses[]` 一起 upsert（v1 至多两条，嵌套足够，不另开 `/customers/{id}/addresses` 端点）。后端按 `type` diff：新增 / 更新 / 缺省即删。
- [x] **地址字段 = 欧洲/荷兰式结构化**（**不用**自由文本 `address_line1/2`，那样接不了自动补全）：`street`（街道名）+ `house_number`（号码，**text** 以容 `12-14`）+ `house_number_addition?`（toevoeging：`A`/`bis`/公寓号）+ `postal_code?` + `city?` + `province?`（State/省份，**荷兰留空**）+ `country_code?`。无 ISO 字段表标准（ISO 19160 仅概念模型）；渲染顺序按荷兰习惯（号码在街道名之后），参考 Google i18n-address-data / UPU S42。
- [x] **国家字段 = `country_code`（ISO 3166-1 alpha-2）+ UI 标签 "Country/Region"**：规避争议领土；数据仍是 ISO 码。**同一标签同步应用到公司档案**。
- [x] **公司地址一并对齐（B 决策，放步骤 3）**：M2 公司档案地址迁到与客户**相同的结构化字段**（复用 `AddressFields` + `AddressFieldsForm.vue`），全局一套地址模型；独立步骤、复用步骤 2 成果，放最后做。迁移对既有公司行**尽力保留**（`address_line1`→`street`），`address_line2` 丢弃，其余结构化列置空待补（dev 期单行，作者一次性补全）。
- [x] **联系方式归客户级**：`email`/`phone`/`website` 在 `customer` 行（一客户一套），**不挂每条地址**；表单从上到下：客户信息 → 地址块。
- [x] **默认币种 = ISO 4217 字符串**（沿用 M2 `company.base_currency` 口径，不等 M4 字典，不建 FK）；**锁定规则延后**到有交易的里程碑（M5/M7）。
- [x] **国家放账单地址 `country_code`**（不在 `customer` 行再开一个 country 列）；**VAT 号放 `customer` 行**。M10 的 ICP / 反向征收判定 = `customer.vat_id` + 账单地址 `country_code`。
- [x] **`customer.company_id` NOT NULL FK → `company.id`（`ON DELETE RESTRICT`）**：沿用 M2 对租户根的保护（单例公司不允许在有客户引用时删）；这是「核心业务表从 M2/M3 起挂 `company_id`」基线的首次落地（红线 2 / §3.3）。**RLS 仍留口不开**（v1 单公司，company 即租户根）。
- [x] **`address.customer_id` FK `ON DELETE CASCADE`**：删客户连带删其地址，走 **DB 级级联**，不手写遍历删除（红线 3）。
- [x] **`extra` 用 JSONB**（`NOT NULL default '{}'`）承载长尾（备注 / 社媒等非结构化），v1 不做自定义字段 UI（红线 12 是「VAT 数据驱动」，此处只是长尾承载，不写死枚举）。
- [x] **鉴权 = owner-only**（复用 M1/M2 `_owner_only` + cookie 会话 + `current_mfa_user`）；写时 `company_id` 由 service 从当前用户注入，前端不传（红线 2：不让 scope 散落到前端/查询条件）。
- [x] **`description` 类一律 `text`**（红线 10）；`country_code` `char(2)`、`currency` `char(3)`、`type` 用 PG enum。

## 契约（先行 · 前后端各自对着写）
> 业务端点一律 `/api/v1/*`。改契约就 `npm run codegen` 重生成 `schema.d.ts`，CI drift 关强制无漂移（红线 11）。沿用 M1 cookie 会话 + `current_mfa_user`；owner-only 复用 `_owner_only`。

**客户 CRUD + 列表（owner，需完整会话）**
- `GET /api/v1/customers` query `{q?: string, limit?: int=50, offset?: int=0}` → `200 CustomerListResponse {items: CustomerRead[], total: int}`（`q` 模糊匹配 name / email / company_name）。
- `POST /api/v1/customers` body `CustomerWrite` → `201 CustomerRead`。
- `GET /api/v1/customers/{id}` → `200 CustomerRead`（含 `addresses[]`）；不存在 / 跨公司 → `404`。
- `PUT /api/v1/customers/{id}` body `CustomerWrite` → `200 CustomerRead`（地址按 `type` diff 增改删）。
- `DELETE /api/v1/customers/{id}` → `204`（DB cascade 删地址）。

**Schema 形状（最小集，M5 消费时再扩）**
- `AddressWrite { type: "BILLING"|"SHIPPING", street?, house_number?, house_number_addition?, postal_code?, city?, province?, country_code? }`
- `AddressRead` = `AddressWrite` + `{ id }`
- `CustomerWrite { name, contact_name?, company_name?, email?, phone?, website?, vat_id?, currency?, extra?: object, addresses?: AddressWrite[] }`
  - 校验：`currency` ∈ ISO 4217（复用 company 校验器）；各地址 `country_code` ∈ ISO 3166；`email` 基本格式；`addresses[]` 的 `type` 不重复（重复 → `422`/`400`）。
- `CustomerRead { id, name, contact_name?, company_name?, email?, phone?, website?, vat_id?, currency?, extra, addresses: AddressRead[], created_at, updated_at }`（不含 `company_id`：对外隐式 = 当前公司）。

**公司档案（B 决策 · 沿用 M2 端点 `GET/PUT /api/v1/company`，仅换地址字段）**
- `CompanyWrite`/`CompanyRead` 的地址部分由 `address_line1/address_line2` 换成结构化 `AddressFields`（`street`/`house_number`/`house_number_addition`/`postal_code`/`city`/`province`/`country_code`）；**端点签名不变**，但 schema 变 → `npm run codegen` 重生成 `schema.d.ts`。

## 数据模型 / 迁移
> 一条 Alembic 迁移建两表 + enum + 索引。`models/__init__.py` 注册 `Customer` / `Address`；`AddressType` 进 `models/_enums.py`。

- **`customer`**：
  - `id` UUID PK；`company_id` UUID **NOT NULL** FK → `company.id`（`ON DELETE RESTRICT`）；
  - `name` `text` NOT NULL；`contact_name`/`company_name`/`email`/`phone`/`website`/`vat_id` `text` 可空；
  - `currency` `char(3)` 可空（ISO 4217）；`extra` `JSONB` NOT NULL `server_default '{}'`；
  - `created_at`/`updated_at`（同 company 写法）。
  - 索引：`ix_customer_company_id`；用于搜索的 `name`/`email`（可加 `ix_customer_company_name` 或留待需要时加）。
- **`address`**（欧洲/荷兰式结构化字段）：
  - `id` UUID PK；`customer_id` UUID NOT NULL FK → `customer.id`（`ON DELETE CASCADE`）；
  - `type` `AddressType`（PG enum：`BILLING` / `SHIPPING`）NOT NULL；
  - `street` `text` 可空（街道名）；`house_number` `text` 可空（号码，text 以容 `12-14`）；`house_number_addition` `text` 可空（toevoeging）；
  - `postal_code` `text` 可空；`city` `text` 可空；`province` `text` 可空（State/省份，荷兰留空）；`country_code` `char(2)` 可空（ISO 3166，UI 标签 "Country/Region"）；
  - `created_at`/`updated_at`；
  - 约束：`UNIQUE(customer_id, type)`（每客户每类型至多一条）。
- **`AddressType`**（`_enums.py`，`enum.StrEnum`）：`BILLING="BILLING"` / `SHIPPING="SHIPPING"`。
- **`company` 改列（B 决策，步骤 3）**：删 `address_line1`/`address_line2`，加 `street`/`house_number`/`house_number_addition`/`province`（`postal_code`/`city`/`country_code` 保留）。Alembic：`address_line1`→`street` 尽力保留，`address_line2` 丢弃，新列置空。
- **RLS**：继续留口不开（`customer.company_id` 仅预留；v1 单公司）。

---

## 原子步骤清单
> 每步 = 一个原子改动（单人开发不强制 PR，CI 绿即可合 `main`），过 roadmap §5 DoD。后端 / 前端两栏尽量并行（都对着上面契约 + `schema.d.ts`）。**本里程碑无算钱、无编号引擎**。

### 步骤 1 · `customer` 表 + 核心 CRUD + 列表（搜索 / 分页）
- **契约**：`GET /customers`（列表 + `q`/`limit`/`offset`）、`POST /customers`、`GET /customers/{id}`、`PUT /customers/{id}`、`DELETE /customers/{id}`（本步先只做**标量字段**，地址留步骤 2）。
- **后端**：
  - `models/customer.py`：`Customer` ORM（标量字段 + `company_id` FK `RESTRICT` + `extra` JSONB）；`models/__init__.py` 注册。
  - `schemas/customer.py`：`CustomerWrite`（标量 + `currency`/`email` 校验，**复用 company 的 ISO 4217 校验器**）、`CustomerRead`、`CustomerListResponse`。
  - `services/customer.py`：`list_customers(session, company_id, q, limit, offset)`（按 `company_id` 过滤 + `q` ILIKE name/email/company_name + 总数）、`get/create/update/delete`（**create/update 注入 `company_id`**，跨公司取用一律按 `company_id` 过滤后 `404`）。
  - `api/customers.py`：五个端点，owner-only；挂进 `api/__init__.py` 路由。
  - Alembic：建 `customer` 表 + 索引。
- **前端**：
  - `stores/customers.ts`（Pinia）：列表 / 取详情 / 增改删 + 搜索/分页状态。
  - `views/customers/CustomerList.vue`：表格 + 搜索框 + 分页 + 删除确认；`views/customers/CustomerEdit.vue`：标量字段表单（含默认币种选择）。
  - 导航加「客户」入口；`npm run codegen` 重生成 `schema.d.ts`。
- **测试**：pytest——按 `company_id` 隔离（跨公司 `404`）、`q` 搜索命中、分页 `total`、currency/email 非法值 `422`、owner-only、CRUD 往返。
- **DoD**：见 roadmap §5。

### 步骤 2 · `address` 表 + 账单/收货地址（嵌套写读 + cascade）
- **契约**：在 `CustomerWrite`/`CustomerRead` 加 `addresses[]`（`AddressWrite`/`AddressRead`），端点不变（嵌套）。
- **后端**：
  - `models/address.py`：`Address` ORM（`customer_id` FK `CASCADE` + `type` enum + `UNIQUE(customer_id, type)`）；`AddressType` 进 `_enums.py`；`models/__init__.py` 注册；`Customer.addresses` relationship（`cascade="all, delete-orphan"`，与 DB `ON DELETE CASCADE` 对齐，红线 3）。
  - `schemas/address.py`（新建，供复用）：抽 **`AddressFields` 基类**（结构化字段 + `country_code` ISO 3166 校验）；`AddressWrite` = `AddressFields` + `type`、`AddressRead` = `+ id`。`schemas/customer.py`：`CustomerWrite.addresses`/`CustomerRead.addresses`；校验 `type` 不重复。**`AddressFields` 步骤 3 供公司复用。**
  - `services/customer.py`：create/update 里**按 `type` diff 地址**（新增 / 更新 / 缺省删）；读取带出 `addresses`。
  - Alembic：建 `address` 表 + enum + 唯一约束。
- **前端**：
  - 抽**可复用** `components/AddressFieldsForm.vue`（结构化字段：街道 / 号码 / 附件 / 邮编 / 城市 / 省 / Country-Region）；`CustomerEdit.vue` 用它渲染「账单地址 / 收货地址」两块；详情 / 列表按需展示账单地址。**该组件步骤 3 供公司档案复用。**
- **测试**：pytest——嵌套创建带两类地址、更新改地址、删一类地址、**重复 `type` 被拒**、**删客户级联删地址**（DB cascade）、`country_code` 非法 `422`。
- **DoD**：见 roadmap §5；**级联删除依赖 DB 外键、不手写遍历**（红线 3，需有「删客户→地址随删」单测）。

### 步骤 3 · 公司地址对齐（B 决策 · 复用步骤 2 的结构化地址件）
- **契约**：沿用 `GET/PUT /api/v1/company`；`CompanyWrite`/`CompanyRead` 地址部分换成 `AddressFields`（端点签名不变）。重生成 `schema.d.ts`。
- **后端**：
  - `models/company.py`：删 `address_line1`/`address_line2`，加 `street`/`house_number`/`house_number_addition`/`province`（其余保留）。
  - `schemas/company.py`：`CompanyWrite`/`CompanyRead` 复用步骤 2 的 `AddressFields`（连同 `country_code` ISO 3166 校验）。
  - `services/company.py`：upsert 适配新地址字段（有引用旧列处一并改）。
  - Alembic：company 改列迁移（`address_line1`→`street` 尽力保留，`address_line2` 丢弃，新列置空）。
- **前端**：
  - `CompanyProfile.vue` 地址块改用 `AddressFieldsForm.vue`；国家标签统一 "Country/Region"。
- **测试**：pytest——公司 upsert 往返新字段、`country_code` 非法 `422`；既有 company 套件仍绿。
- **DoD**：见 roadmap §5；改了契约 → `schema.d.ts` 无漂移。

### 步骤 4 · 收尾：i18n + 列表体验打磨 + 部署自测点
- **后端**：无新端点（必要的小修：搜索排序、空 `q` 行为确认）。
- **前端**：
  - i18n：`customers.*` 文案进 `en.json` / `zh.json`（列表 / 表单 / 地址 / 删除确认 / 空态）。
  - 列表打磨：排序（按 name / 创建时间）、空态、删除确认文案、分页交互；编辑页校验提示对齐后端 `422`。
- **验收**：走下方「🟢 部署自测点」；`npm run build` 绿、`schema.d.ts` 无漂移。
- **DoD**：见 roadmap §5。

## 🟢 部署自测点（里程碑验收 · 人工走）
> 本地集成：`docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build`；浏览器 `http://localhost:${APP_HOST_PORT:-8000}`。
1. **新建客户**：填 name / 联系人 / 公司名 / email / 电话 / 网站 / VAT 号 / 默认币种，保存 → 列表出现该客户。
2. **地址**：编辑客户填账单地址 + 收货地址（含国家）并保存，刷新回显；同一类型只能存一条（重复 type 被拒）。
3. **编辑 / 校验**：改字段保存生效；非法默认币种（非 ISO 4217）/ 非法国别码被拒并给提示。
4. **列表搜索 + 分页**：按 name / email / 公司名搜索命中；多客户时分页正常。
5. **删除**：删除客户有确认；删除后其地址一并消失（开发者侧核对 DB cascade 在单测中绿）。
6. **隔离**：所有客户数据隐式归当前公司（开发者侧核对 service 按 `company_id` 过滤、跨公司取用 `404` 在单测中绿）。
7. **公司地址对齐（B）**：公司档案地址用同一套结构化字段（街道 / 号码 / 附件 / 邮编 / 城市 / 省 / Country-Region）编辑并回显；与客户地址表单同构。
8. CI 四关全绿；`schema.d.ts` 无漂移。

## 验收结论（收尾时回填）
- **完成日期**：2026-06-09（步骤 1–4 全部落地：客户 CRUD/列表 → 嵌套地址 → 公司地址对齐 → 列表打磨 + i18n）。
- **验收**：**部署自测点 1–8 全部通过**。证据（2026-06-09 复跑）：
  - **质量门**：`ruff check` ✅ All checks passed；`mypy --strict src` ✅ no issues（40 files）。
  - **自动化测试**：`pytest` 单元 **232 passed**；`pytest -m integration`（customer + company）**48 passed**。其中开发者侧自测点由集成测试兜底——
    - 自测点 5（删除级联）：`test_customer_integration` 覆盖「删客户 → 地址随 DB cascade 删」；
    - 自测点 6（隔离）：覆盖按 `company_id` 过滤 + 跨公司取用 `404`；
    - 自测点 2/3（嵌套地址·重复 type 拒·`422` 校验）、自测点 4（`q` 搜索命中 + 分页 `total`）均有用例。
  - **契约**：`schema.d.ts` 用临时 server 重生成后 **零漂移**（CI codegen-freshness 关等价通过）；前端 `vue-tsc + vite build` ✅。
  - **部署冒烟**：已部署 GHCR 镜像（`jai-app`，healthy）`/api/health` 返回 ok；live OpenAPI 含全部 M3 端点，且证实 **步骤 3**（`CompanyWrite` 已是结构化地址、`address_line1` 已删）与 **步骤 4**（`GET /customers` 含 `sort_by`）均在镜像中——自测点 1/7 的 API/部署面已验证；最终浏览器逐点 walkthrough 由作者确认通过。
  - **CI 四关绿**，工作树干净。
- **已知遗留 / 顺延项**：
  - 客户编号 prefix（`CUSTOMER_SERIES`）+ 客户级序号 → M5。
  - 默认币种「有交易后锁定」强制 → M5 / M7。
  - `currency` 仍是 ISO 4217 字符串（非 FK）→ **M4 币种字典时考虑迁 FK**（与本轮 M4 planning 衔接）。
  - 长尾走 `extra` JSONB，无自定义字段 UI（v1 不做）。
  - 地址自动补全（PDOK / Google Places）→ follow-on，未实现（结构化字段已为其铺好底）。
  - RLS 仅预留 `company_id`，未真实启用（v1 单公司，红线 2 留口）。
