# M<x> · <Milestone Name>

> 🌐 **English** · [中文](_TEMPLATE_zh.md)

> JIT output before entering this milestone. First read `docs/plan/roadmap.md` §2 global constraints + the M<x> row, then read relevant upstream docs (frozen preceding milestones `M<y>.md`; if VAT-related, `docs/insight/btw-aangifte-2026-guide.md`).

## Goals & Scope
- **Goal**: <One sentence>
- **IN (Included)**: <Bullets, expanded from roadmap M<x>>
- **OUT (Not included / deferred)**: <Clear boundary>
- **Related docs**: roadmap M<x>; preceding `M<y>.md` (if VAT-related, `docs/insight/btw-aangifte-2026-guide.md`)

## Product Decisions to Fill In (define before starting, checked items have defaults)
- [ ] <e.g.: whether units are optional / VAT numeric precision / content block fields…>

## Contract (define first)
> API additions/changes in this milestone. Define first, then implement in parallel.
- `METHOD /api/v1/...` — <Purpose>: request `<schema>` → response `<schema>`

## Data Models / Migrations
- New/modified tables: <table, key columns, FKs, cascade, company_id reserved>
- Alembic: <migration notes>

## Atomic Steps Checklist
> Each step = one atomic change (single-person dev, no forced PR, CI green → merge to main), meets roadmap §5 DoD. Backend/frontend rows can run in parallel.

### Step 1 · <Name>
- **Contract**: <schemas involved in this step>
- **Backend**: <models/schemas/services/api tasks; calculation in services>
- **Frontend**: <store/view/component; against schema.d.ts>
- **Migration**: <if any>
- **Tests**: <pytest; calculation logic must be tested>
- **DoD**: See roadmap §5

### Step 2 · <Name>
...

## 🟢 Deployment Self-Test Points (Milestone Acceptance)
- <Per roadmap M<x> self-test points; manual walkthrough after docker compose up>

## Acceptance Conclusion (fill at closure)
- Completion date:
- Acceptance: <whether self-test points passed>
- Known carryover / deferred items:
