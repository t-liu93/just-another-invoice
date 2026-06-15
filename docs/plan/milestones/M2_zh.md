# M2 · 公司档案 + 三层设置（补全）

> 🌐 [English](M2.md) · **中文**

> 进入本里程碑前 JIT 产出。先读 `docs/plan/roadmap.md` 的 §2 全局约束 + M2 那一格，再读分析文档 §2.1（Company）、§2.4.1（编号）、§2.4.2（多币种）、§3.5（三层设置）、§G（设置/账户）、§7.2.1（v1 范围·设置）。
> **M1 已完成**：认证（密码 + 强制 MFA）、邮件/SMTP 底座、三层设置表 `setting`（M1 仅验收 GLOBAL 层）、`user` 表（`company_id`/`role` 预留列、无 FK）。M2 在此之上加**第二条纵向功能切片**：能配置「你的业务主体」，并把三层设置的真实语义补全。

## 目标与范围
- **目标**：一个**能填写并持久化公司业务档案**（抬头 / logo / VAT / 地址 / 本位币）的应用；并把 M1 占位的三层设置回退（`user → company → global`）**做实**，落第一个真实 USER 级偏好（暗黑模式）端到端验证。
- **纳入（IN）**：
  - **`company` 业务档案表（单例，v1 一行）**：name、VAT 号、KvK 号、地址（内联标准字段）、联系方式、**本位币（ISO 4217 代码字符串）**、`logo_id`（FK → `binary_asset`）。`GET/PUT /company`；首次保存即置 `onboarding.completed=true` 并把 owner 的 `user.company_id` 指向本公司。
  - **Logo 存储**：新建通用小 blob 表 **`binary_asset`**（`bytea` + mime + 元数据）；`company.logo_id` 可空 FK。上传 / 替换 / 删除 / serve；**SVG 上传清洗**（红线 7），栅格图按 mime 白名单 + 大小上限收。
  - **三层设置补全**：把 `services/settings` 的 `user → company → global` 回退做实（按登录用户的 `company_id` 解析 COMPANY 层 scope），写时失效缓存照旧；GLOBAL+COMPANY+USER 三层都有真实读写 + 单测。
  - **编号模板（company 级配置，仅存不跑）**：把编号模板 + 自定义起始号 / 跳号作为 **COMPANY 级类型化设置**存起来 + 编辑 UI；**渲染 / 序列 / 并发安全引擎留 M5 消费**（本里程碑不实现编号引擎）。
  - **用户偏好（暗黑模式持久化到账号）**：把 M1 仅存 localStorage 的主题偏好持久化为 **USER 级设置**，登录后从账号加载（收掉 M1 顺延项）。
  - **Onboarding 改造**：完成点后移——注册 owner → 绑 MFA → **填公司档案（必经）** → 完成；**SMTP 配置作为「可跳过」可选步骤**。
  - **前端**：公司档案页（身份表单 + logo 上传）、编号模板设置区、用户偏好页（主题）、onboarding 引导扩展。
- **不纳入（OUT / 留到后续）**：
  - **编号引擎本身**（占位符渲染、DB 序列 / 唯一约束 + 重试、并发安全、实际出号）→ **M5**；M2 只存配置。
  - **公司领域偏好的其余项**（财年 / 日期格式 / 时区 / 默认到期天数 / 计税偏好）→ 留到**各自消费的里程碑**（默认到期天数→M5、日期格式 / 时区→M9 PDF 等），避免无消费者的死设置。
  - **币种字典 + 汇率**（可增删改的币种记录、汇率 provider）→ **M4**；M2 本位币只是一个校验过的 ISO 4217 代码字符串，M4 再建字典（届时可考虑迁成 FK）。
  - **客户地址表**（BILLING/SHIPPING `type` 多地址）→ **M3**；公司只有单一地址，用内联列即可，不提前建地址表。
  - 多用户 / RBAC 真实鉴权（v1 owner 全权）、多公司切换 UI、RLS 真实实现、storage 云盘 / file 上传抽象（收据走 M8，与本里程碑的 DB 小 blob 分属两个概念）。
- **对应文档**：分析文档 §2.1、§2.4.1、§2.4.2、§3.5、§G、§7.2.1；roadmap M2 / §2（红线 2/3/5/7/12）/ §3.3。

## 已定的产品 / 技术决策（动手前定 · 本轮已拍板）
- [x] **Logo 存储**：建通用小 blob 表 `binary_asset`（非塞 settings、非 company 内联 `bytea`），`company.logo_id` FK `ON DELETE SET NULL`；为日后别的小 blob 留余地。**与 M8 收据（file/disk storage 抽象、较大文件）明确分属两个概念**。
- [x] **编号规则**：M2 **只存配置**（COMPANY 级类型化设置 + 编辑 UI），引擎留 **M5**。
- [x] **偏好范围**：M2 精简——公司核心身份 + 三层回退做实 + **暗黑模式**作首个真实 USER 级偏好；其余领域偏好留到各自里程碑。
- [x] **Onboarding**：插入「填公司档案」为**必经步**，SMTP 作为**可跳过**可选步骤；`onboarding.completed` 的置真点从「MFA 绑定」**后移到「公司档案首次保存」**。
- [x] **本位币**：company 上存 **ISO 4217 三字母代码字符串**（校验合法集），不建字典、不等 M4。
- [x] **`user.company_id` FK 收口**：M2 落 `company` 表后补**真 FK**（`ON DELETE RESTRICT`，v1 单例不允许在有用户引用时删公司；红线 2/3）。
- [x] **`setting.scope_id` 仍维持单列、v1 不加 FK**：scope_id 是多态（指向 company 或 user），单 FK 表达不了；v1 单用户单公司下删除几乎不发生，referential 一致性 + 清理放 service 层；多用户落地（post-v1）再考虑拆列 + CHECK + cascade。**此项不阻塞 M2 的回退功能**（回退靠按用户解析 scope，与 FK 无关）。
- [x] **SVG 安全**：接受 SVG 但**上传时清洗**（白名单 sanitizer，剥 `<script>`/外链/事件属性，红线 7）；栅格图限 PNG/JPEG/WebP + 大小上限（暂定 ≤ 512 KB）。

## 契约（先行 · 前后端各自对着写）
> 业务端点一律 `/api/v1/*`。改契约就 `npm run codegen` 重生成 `schema.d.ts`，CI drift 关强制无漂移（红线 11）。沿用 M1 的 cookie 会话 + `current_mfa_user` 依赖；owner-only 端点复用 `_owner_only` 模式。

**公司档案（owner，需完整会话）**
- `GET /api/v1/company` → `200 CompanyRead`（未创建则 `204` 或 `200` 带空标记，前端据此判断是否走 onboarding 填档步）。
- `PUT /api/v1/company` body `CompanyWrite {name, vat_id?, coc_number?, email?, phone?, website?, address_line1?, address_line2?, postal_code?, city?, country_code?, base_currency}` → `200 CompanyRead`。**首次成功保存**：置 `onboarding.completed=true` + 关联 `owner.company_id`。

**公司 Logo（owner，需完整会话）**
- `PUT /api/v1/company/logo`（multipart `file`）→ `200 CompanyLogoRead {logo_url, mime_type, byte_size}`；mime 不在白名单 / 超限 → `400`；SVG 经清洗后入库。
- `DELETE /api/v1/company/logo` → `204`（删 `binary_asset` 行 + 置 `company.logo_id=null`）。
- `GET /api/v1/company/logo` → `200` 二进制流（`Content-Type` = 存的 mime，带缓存头）；无 logo → `404`。

**编号模板配置（owner，需完整会话；仅存，M5 消费）**
- `GET /api/v1/settings/numbering` → `200 InvoiceNumberingConfig`（无则返默认）。
- `PUT /api/v1/settings/numbering` body `InvoiceNumberingConfig {template, sequence_start, ...}` → `200 InvoiceNumberingConfig`（COMPANY 级设置，scope=当前 company）。

**用户偏好（需完整会话）**
- `GET /api/v1/settings/me` → `200 UserPreferences {theme}`（`theme`: `system|light|dark`，无则按 GLOBAL 默认回退）。
- `PUT /api/v1/settings/me` body `UserPreferences` → `200 UserPreferences`（USER 级设置，scope=当前 user）。

**沿用 / 微调（M1 已有）**
- `GET /api/v1/auth/bootstrap` → `{registration_open, onboarding_completed}`（语义不变；`onboarding_completed` 的置真点改到 `/company` 首存）。

## 数据模型 / 迁移
> 每步建表 / 改列 = 一条 Alembic 迁移。

- **`company`**（业务档案单例表，v1 一行）：
  - `id` UUID PK；`name` `text`；`vat_id` `text` 可空；`coc_number` `text` 可空（NL KvK 号，发票抬头常用）；`email`/`phone`/`website` `text` 可空；地址内联：`address_line1`/`address_line2`/`postal_code`/`city` `text` 可空、`country_code` `char(2)` 可空（ISO 3166，给 M10 的 EU 判定铺路）；`base_currency` `char(3)` 非空（ISO 4217）；`logo_id` UUID 可空 FK → `binary_asset.id`（`ON DELETE SET NULL`）；`created_at`/`updated_at`。
  - **`description` 类用 `text`**（红线 10）。单例约束：service 层保证只有一行（或加部分唯一索引）。
- **`binary_asset`**（通用小 blob 表）：
  - `id` UUID PK；`content` `bytea` 非空；`mime_type` `text` 非空；`filename` `text` 可空；`byte_size` `int` 非空；`sha256` `text` 可空（日后去重留口）；`created_at`/`updated_at`。
- **`user` 改列**：`company_id` 从「可空无 FK」补为**可空 + FK → `company.id`（`ON DELETE RESTRICT`）**（红线 2/3）。
- **`setting`**：不改表结构；M2 在其上落 COMPANY 级（编号模板，scope=company.id）与 USER 级（主题，scope=user.id）真实读写。
- **RLS**：M0 留的会话钩子继续空置；`company`/`binary_asset` 预留 `company_id` 不在本里程碑开 RLS（v1 单公司，company 即租户根）。

---

## 原子步骤清单
> 每步 = 一个原子改动（单人开发不强制 PR，CI 绿即可合 `main`），过 roadmap §5 DoD。后端 / 前端两栏尽量并行（都对着上面契约 + `schema.d.ts`）。**算钱不在本里程碑**；编号引擎也不在（仅存配置）。

### 步骤 1 · `company` 表 + 业务档案 CRUD + `user.company_id` FK 收口
- **契约**：`GET /company`、`PUT /company`。
- **后端**：
  - `models/company.py`：`Company` ORM（见数据模型，`logo_id` 先建可空列，FK 到 `binary_asset` 在步骤 2 一并落 / 或本步先无 FK、步骤 2 补——见迁移备注）。
  - `schemas/company.py`：`CompanyWrite`（身份字段，`base_currency` 校验 ISO 4217、`country_code` 校验 ISO 3166）、`CompanyRead`（含 `has_logo`/`logo_url`）。
  - `services/company.py`：`get_company()`（单例读）、`upsert_company()`（建 / 改；**首次创建**时置 `onboarding.completed=true`、关联 `owner.company_id`，单事务）。
  - `api/company.py`：`GET/PUT /api/v1/company`（owner-only，复用 `_owner_only`）。
  - `config.py`：无新增（本位币校验用静态 ISO 表 / `pycountry`，依赖按需加）。
  - Alembic：建 `company` 表；`user.company_id` 补 FK → `company.id`（`ON DELETE RESTRICT`）。
- **前端**：
  - `views/settings/CompanyProfile.vue`：身份表单（name/VAT/KvK/地址/本位币）+ 保存。
  - `stores/company.ts`（Pinia）：读 / 存公司档案。
  - 设置区导航加「公司档案」入口；`npm run codegen` 重生成 `schema.d.ts`。
- **测试**：pytest——单例 upsert、首存置 `onboarding.completed`+关联 owner、本位币 / 国别码非法值 400、owner-only 鉴权、`GET` 未创建时的空响应。
- **DoD**：见 roadmap §5。

### 步骤 2 · Logo 存储（`binary_asset` 表 + 上传 / serve + SVG 清洗）
- **契约**：`PUT /company/logo`、`DELETE /company/logo`、`GET /company/logo`。
- **后端**：
  - `models/binary_asset.py`：`BinaryAsset` ORM（见数据模型）；`company.logo_id` FK → `binary_asset`（`ON DELETE SET NULL`）。
  - `services/assets.py`：mime 白名单（PNG/JPEG/WebP/SVG）+ 大小上限校验；**SVG 清洗**（`nh3`/`bleach` 白名单或 `lxml` 剥 `<script>`/外链/`on*` 事件属性，红线 7）；`set_company_logo()`（建新 asset、指 `logo_id`、删旧 asset）、`clear_company_logo()`。
  - `api/company.py`：挂 logo 三端点；`GET` 返 `Response(content, media_type=mime)` + 缓存头（`ETag`/`Cache-Control`）。
  - Alembic：建 `binary_asset` 表；`company.logo_id` FK（若步骤 1 未建则本步补）。
- **前端**：
  - `CompanyProfile.vue` 加 logo 上传 / 预览 / 删除（`<input type=file>` + 预览 `/api/v1/company/logo`）。
- **测试**：pytest——上传往返（存取一致）、mime 白名单外 400、超限 400、**SVG 含 `<script>` 被清洗**、替换 logo 删旧行、`GET` 返回正确 mime、无 logo 404。
- **DoD**：见 roadmap §5；**SVG 清洗必须有单测**（红线 7）。

### 步骤 3 · 三层设置回退做实 + 编号模板配置（仅存）+ 主题偏好 API
- **契约**：`GET/PUT /settings/numbering`、`GET/PUT /settings/me`。
- **后端**：
  - `services/settings.py`：把 `get_setting` 的回退做实——新增按登录用户解析 scope 的便捷入口（如 `get_effective_setting(session, key, user, value_type)`：依次探 `USER(user.id) → COMPANY(user.company_id) → GLOBAL`）；修掉 M1 占位逻辑里「非起始层 scope 一律置 None」的近似（COMPANY 层应使用 `user.company_id`）。缓存键 / 写时失效不变。
  - `schemas/setting.py`：`InvoiceNumberingConfig`（`template` 默认 `{{SERIES:INV}}-{{SEQUENCE:6}}`、`sequence_start` int 等；**字段集保持最小，M5 消费时再扩**）、`UserPreferences`（`theme: Literal["system","light","dark"]`）；加对应 `SETTING_KEY_*` 常量。
  - `api/settings.py`：加 `GET/PUT /settings/numbering`（owner-only，COMPANY 级，scope=company.id）、`GET/PUT /settings/me`（USER 级，scope=user.id）。
- **前端**：
  - `CompanyProfile.vue` 或同区加「单据编号」配置块（模板 + 起始号；附提示「**M5 起生效**」）。
  - `views/settings/Preferences.vue`：主题选择（system/light/dark），保存到账号。
  - `composables/useTheme.ts`：登录后从 `/settings/me` 加载主题、切换时 `PUT` 持久化（localStorage 退为缓存）。
- **测试**：pytest——**三层回退**（USER 覆盖 > COMPANY > GLOBAL，真实 scope 解析）、缓存命中 / 失效、编号配置往返、主题偏好往返、owner-only。
- **DoD**：见 roadmap §5；**三层回退逻辑必须有单测**（红线 5）。

### 步骤 4 · Onboarding 改造（插入「填公司档案」必经步 + SMTP 可跳过）
- **契约**：无新端点（复用 `/auth/bootstrap` + `/company` + `/settings/smtp`）。
- **后端**：
  - `auth/`（MFA verify）：**移除**首绑时置 `onboarding.completed` 的逻辑（置真点改到 `/company` 首存，已在步骤 1 落地）；MFA 仍升级为完整会话。
  - 确认 `onboarding.completed` 仅由 `/company` 首存置真（幂等）。
- **前端**：
  - `router/` + onboarding 流：注册 → MFA 绑定 → **公司档案填写步（必经）** → 完成 → dashboard；已登录但 `onboarding_completed=false` 的 owner 进站路由到公司档案步。
  - **SMTP 可跳过步**：公司档案后给一个「配置邮件（可跳过）」可选步骤，跳过直达 dashboard。
  - 移除 / 调整 M1 遗留的 dashboard 引导提示（若有）。
- **测试**：pytest——MFA verify 后 `onboarding_completed` 仍为 false、`/company` 首存后变 true；前端基本路由 / 表单校验。
- **DoD**：见 roadmap §5。

## 🟢 部署自测点（里程碑验收 · 人工走）
> 本地集成：`docker compose -f docker-compose.yml -f docker-compose.dev.yml up --build`；浏览器走 `http://localhost:${APP_HOST_PORT:-8000}`。
1. **首启引导（改造后）**：全新库 → 注册 owner → 绑 MFA → **被要求填公司档案（必经）** → （可选）跳过 / 填 SMTP → 进 dashboard；`onboarding.completed` 置真。
2. **公司档案持久化**：编辑抬头 / VAT / KvK / 地址 / 本位币并保存，刷新后回显；本位币 / 国别码非法值被拒。
3. **Logo**：上传 PNG 与 SVG → 预览正确；替换 → 旧文件被删；删除 → 回到无 logo；（核对 SVG 内嵌脚本已被清洗）。
4. **编号模板（仅存）**：编辑编号模板 + 起始号并保存、回显；UI 明示「M5 起生效」。
5. **三层偏好回退**：切换暗黑模式 → 刷新 / 换浏览器重新登录后主题跟随**账号**（USER 级）；（开发者侧）核对 `user → company → global` 回退在单测中绿。
6. CI 四关全绿；`schema.d.ts` 无漂移。

## 验收结论
- **完成日期**：2026-06-08
- **状态**：🟢 完成（步骤 1–4 全部合入 `main`，人工验收通过）。
- **交付概览**：
  - **步骤 1 · `company` 表 + 业务档案 CRUD + `user.company_id` FK 收口**：`company` 单例表（name / vat_id / coc_number / email / phone / website / 地址内联 / `country_code` `char(2)` / `base_currency` `char(3)` / `logo_id`，`description` 类一律 `text`）；`GET/PUT /api/v1/company`（owner-only，复用 `_owner_only`）；`upsert_company()` **首次保存**在单事务内置 `onboarding.completed=true` 并关联 `owner.company_id`；`base_currency` 校验 ISO 4217、`country_code` 校验 ISO 3166（非法值 `400`）；`user.company_id` 补真 FK → `company.id`（`ON DELETE RESTRICT`，红线 2/3）。
  - **步骤 2 · Logo 存储（`binary_asset` + 上传 / serve + SVG 清洗）**：通用小 blob 表 `binary_asset`（`bytea` + mime + filename + byte_size + `sha256` 去重留口）；`company.logo_id` FK `ON DELETE SET NULL`；`PUT/DELETE/GET /api/v1/company/logo`；mime 白名单（PNG/JPEG/WebP/SVG）+ 大小上限；**SVG 上传清洗**（剥 `<script>`/外链/`on*` 事件属性，红线 7，有单测）；替换 logo 删旧 asset；`GET` 返存储 mime + 缓存头，无 logo `404`。
  - **步骤 3 · 三层设置回退做实 + 编号模板（仅存）+ 主题偏好**：`services/settings` 的 `get_effective_setting` 做实 `USER(user.id) → COMPANY(user.company_id) → GLOBAL` 真实 scope 回退（修掉 M1「非起始层 scope 置 None」的占位近似），缓存键 / 写时失效不变；`GET/PUT /api/v1/settings/numbering`（COMPANY 级、owner-only，`InvoiceNumberingConfig`，默认模板 `{{SERIES:INV}}-{{SEQUENCE:6}}`，**仅存配置、引擎留 M5 消费**）；`GET/PUT /api/v1/settings/me`（USER 级，`UserPreferences {theme: system|light|dark}`）。
  - **步骤 4 · Onboarding 改造**：`POST /api/v1/auth/mfa/verify` **不再**置 `onboarding.completed`（置真点后移到 `/company` 首存）；前端引导流 注册 → MFA 绑定 → **公司档案（必经）** → **SMTP（可跳过，`?from=onboarding` / 回程 `?resume=done`）** → dashboard；onboarding 已完成时普通访问 `/onboarding` 重定向 dashboard。
  - **前端**：`views/settings/CompanyProfile.vue`（身份表单 + logo 上传 / 预览 / 删除 + 「单据编号」配置块，附「M5 起生效」提示）、`views/settings/Preferences.vue`（主题，持久化到账号）、`composables/useTheme.ts`（登录后从 `/settings/me` 加载、切换时 `PUT`，localStorage 退为缓存）、`stores/company.ts`、`Onboarding.vue` + 路由守卫。
- **自动化测试**：默认套件 **187 passed / 120 deselected**（无需 DB）；集成套件 **120 passed**（PostgreSQL）；`ruff` / `mypy --strict`（34 源文件）/ 前端 `npm run build` 均通过；`schema.d.ts` 无漂移。SVG 清洗、三层回退（真实 scope 解析）、编号配置 / 主题偏好往返、owner-only、MFA verify 后 onboarding 仍 false + 公司首存后变 true 均有单测（红线 5/7）。
- **人工验收（部署自测点 1–6）**：全部通过（作者已走查，无大问题）。首启引导（注册 → MFA → 公司档案必经 → 可跳过 SMTP → dashboard，`onboarding.completed` 置真）、公司档案持久化 + 非法本位币 / 国别码被拒、Logo 上传 / 替换删旧 / 删除 + SVG 清洗、编号模板仅存回显、暗黑模式跟随账号（USER 级回退）、CI 四关 + `schema.d.ts` 无漂移，均符合预期。
- **已知遗留 / 顺延项**：
  1. **编号引擎本身**（占位符渲染、DB 序列 / 唯一约束 + 重试、并发安全、实际出号）→ **M5**；M2 仅存配置（红线 4 在 M5 兑现）。
  2. **设置入口统一 + UX 重构**（右上角单齿轮 → Affine 式可展开面板）→ **M2.5**（已在 roadmap 列为独立里程碑，纯前端、非阻塞，可与 M3/M4 并行）。
  3. **公司领域其余偏好**（财年 / 日期格式 / 时区 / 默认到期天数 / 计税偏好）→ 各自消费的里程碑（默认到期天数→M5、日期 / 时区→M9），避免无消费者的死设置。
  4. **币种字典 + 汇率 provider** → **M4**；M2 本位币只是校验过的 ISO 4217 字符串（届时可考虑迁成 FK）。
  5. **`setting.scope_id` 仍单列无 FK**（scope_id 多态指向 company/user，单 FK 表达不了）→ post-v1 多用户时再拆列 + CHECK + cascade；v1 单用户单公司下 referential 一致性放 service 层（不阻塞回退功能）。
  6. **测试卫生修复（本轮收尾）**：`tests/test_settings_numbering_preferences.py` 三个测试类原先漏标 `@pytest.mark.integration`，导致 15 个需 DB 的用例泄漏进默认（无 DB）套件——干净环境下默认 `pytest` 会因连不上 Postgres 报 15 个 error。已补 module-level `pytestmark = pytest.mark.integration` 恢复「默认套件无需 DB」约定。**CI 一直是绿的**（backend-quality job 的 `pytest` 与 `pytest -m integration` 两步都挂了 Postgres service），故此前未暴露。
  7. **`Onboarding.vue` 前端表单校验较薄**（非阻塞）：靠后端 `CompanyWrite` 必填 + ISO 4217/3166 校验兜底。
