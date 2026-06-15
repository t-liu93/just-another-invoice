# M<x> · <里程碑名>

> 🌐 [English](_TEMPLATE.md) · **中文**

> 进入本里程碑前 JIT 产出。先读 `docs/plan/roadmap.md` 的 §2 全局约束 + M<x> 那一格，再读相关上游文档（已冻结的前置里程碑 `M<y>.md`；涉及 VAT 则 `docs/insight/btw-aangifte-2026-guide.md`）。

## 目标与范围
- **目标**：<一句话>
- **纳入（IN）**：<bullets，照 roadmap M<x> 展开>
- **不纳入（OUT / 留到后续）**：<明确边界>
- **对应文档**：roadmap M<x>；相关前置 `M<y>.md`（涉及 VAT 则 `docs/insight/btw-aangifte-2026-guide.md`）

## 待回填的产品决策（动手前先定）
- [ ] <如：单位是否可选的细节 / VAT 数字口径 / 内容块字段……>

## 契约（先行）
> 本里程碑新增/改动的 API。先定再分头实现。
- `METHOD /api/v1/...` — <用途>：请求 `<schema>` → 响应 `<schema>`

## 数据模型 / 迁移
- 新增/改动表：<表、关键列、外键、cascade、company_id 预留>
- Alembic：<迁移要点>

## 原子步骤清单
> 每步 = 一个原子改动（单人开发不强制 PR，CI 绿即可合 main），过 roadmap §5 的 DoD。后端/前端两栏可并行。

### 步骤 1 · <名>
- **契约**：<本步涉及的 schema>
- **后端**：<models/schemas/services/api 任务；算钱在 services>
- **前端**：<store/view/component；对着 schema.d.ts>
- **迁移**：<有则列>
- **测试**：<pytest，算钱逻辑必测>
- **DoD**：见 roadmap §5

### 步骤 2 · <名>
...

## 🟢 部署自测点（里程碑验收）
- <照 roadmap M<x> 的部署自测点；docker compose up 后手动走一遍>

## 验收结论（收尾时回填）
- 完成日期：
- 验收：<部署自测点是否通过>
- 已知遗留 / 顺延项：
