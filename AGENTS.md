# AGENTS.md · Yet Another Ledger (`jai`)

> 🌐 **English** · [中文](AGENTS_zh.md)

> For every agent working in this repo (Claude Code / Codex, any model). This file holds only the **invariant rules + signposts**; the concrete, milestone-evolving content lives in `docs/plan/` — **don't duplicate it here, read the source**.
>
> (`CLAUDE.md` is a symlink to this file `AGENTS.md`; the Chinese mirror is `AGENTS_zh.md` — see "Documentation language".)

## What the project is
A self-hosted **invoicing / personal-company management system**, for the author's own use + open-source self-hosting (Dutch sole-trader conventions). **FastAPI + Vue 3 SPA, single container, PostgreSQL.** Python package name `jai`.

## Where to read first (signposts — don't duplicate their content here)
- **Domain / scope / decisions (authoritative)**: `docs/plan/roadmap.md` (overview + constraints + milestone map) + each `docs/plan/milestones/M<x>.md` (frozen per-milestone decisions).
- **Dutch VAT / BTW filing conventions (authoritative)**: `docs/insight/btw-aangifte-2026-guide.md` (structured guide to the tax authority's official 2026 filing instructions).
- **Master roadmap + global constraints + milestone map**: `docs/plan/roadmap.md`
- **Before implementing**: read the current milestone's `docs/plan/milestones/M<x>.md`.
  **The currently active milestone = the one marked 🟡 in the `roadmap.md` progress table** ("how far we've got" is tracked only in that table, not in this file).

## Red lines (hold in every milestone; stop before violating · details in roadmap §2)
1. **Money math only in the backend `services/`**: the frontend only collects raw input; all amounts are `Decimal` (DB `NUMERIC`, scale≈3), with rounding rules and positions fixed.
2. **Multi-tenancy via Postgres RLS, no manual scoping**: core business tables reserve `company_id`; don't scatter `where company=`.
3. **No hand-written cascade deletes**: use DB foreign keys + ORM cascade.
4. **Concurrency-safe numbering**: no `max+1`; use DB sequences / unique constraints + retry; support custom starting numbers and gap-skipping.
5. **Typed settings**: the three-tier settings use Pydantic/enums + caching; don't sprinkle `'YES'/'NO'` everywhere.
6. **Normalized tax tables**: no single wide table hung with a pile of nullable FKs; split into document-level / line-level tables or proper polymorphism.
7. **Sanitize user input before rendering**: filter before it enters PDF/HTML (XSS/SSRF).
8. **Lock the exchange-rate snapshot**: foreign currency locks the EUR tax base at the invoice date; history doesn't drift.
9. **No in-app self-update**: upgrades go through rebuilding the container image.
10. **Use `text` for `description`**, don't repeat the 255-char limit mistake.
11. **OpenAPI→TS type generation**: regenerate whenever the contract changes; CI enforces no drift.
12. **VAT is data-driven**: rates/categories are user-editable records, not hardcoded enums; the "category → filing box" mapping is decoupled from the rate table.

## Architecture & conventions
- **Directory layering** (backend): under `backend/src/jai/`: `models/ schemas/ services/ api/ auth/` + `config.py db.py main.py`.
  - `api/` = thin routes, orchestration only, calling `services/`;
  - **all business / money-math logic lives in `services/`**;
  - `schemas/` (Pydantic request/response) and `models/` (SQLAlchemy ORM) are **separate**; the schema layer does no money math.
- **Frontend**: under `frontend/src/`: `api/ stores/ views/ components/ composables/ router/ ...`.
- **Backend stack**: FastAPI · SQLAlchemy 2.0 (async) · Alembic · fastapi-users · pydantic-settings · asyncpg/PostgreSQL · uv · Python 3.12.
- **Frontend stack**: Vue 3 + TypeScript · Pinia · Vue Router · Vite · Naive UI · ECharts; `openapi-typescript` generates `frontend/src/api/schema.d.ts` (**committed to the repo**).
- **Contract-first**: define/lock the API schema before writing code; frontend and backend each code against it; whenever the contract changes, run `npm run codegen`.
- **API prefix**: business routes always `/api/v1/*`; health check `/api/health`. The backend serves the SPA for all other routes.

## Documentation language (English-first + Chinese mirror)
- **All checked-in documentation is English-first**: the canonical file `X.md` is in English. This applies to everything under `docs/`, the `README`, and these agent files (`AGENTS.md` / `AGENTS_zh.md`).
- **Every doc ships a Chinese mirror `X_zh.md`** that is **semantically identical** to the English — it exists for the author's reading. The two versions must stay in lockstep: **when you change one, update the other in the same change**; never let them drift.
- **Naming convention**: English = `X.md`, Chinese = `X_zh.md` (e.g. `readme.md` / `readme_zh.md`, `roadmap.md` / `roadmap_zh.md`). The English file keeps the plain canonical name.
- **Cross-reference** each pair with a blockquote on the line right under the H1:
  - in `X.md` (English): `> 🌐 **English** · [中文](X_zh.md)`
  - in `X_zh.md` (Chinese): `> 🌐 [English](X.md) · **中文**`
- Inter-document links point to the canonical `.md` in both versions (don't rewrite them to `_zh`).

## Common commands
> These all become available once the M0 scaffolding lands; the commands themselves are a stable convention.

- Backend (`cd backend`): `uv sync` · `uv run ruff check .` · `uv run mypy --strict src` · `uv run pytest` · `uv run uvicorn jai.main:app --reload` · `uv run alembic upgrade head`
- Frontend (`cd frontend`): `npm install` · `npm run dev` · `npm run build` (`vue-tsc + vite`) · `npm run codegen`
- Dev full stack: `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d` (explicitly load the base Compose + dev override)
- Dev Postgres only: `docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d postgres`
- Production / normal deployment: `docker compose up -d` (single-container app + Postgres)

## Workflow & quality gates
- **Atomic changes**: do one small, independently deployable, test-backed thing at a time.
- **Solo development, no mandatory PR**: self-test + CI green is enough to merge straight to `main` (open a branch/PR when you want human review).
- **Definition of Done** (all pass each step): `ruff` + `mypy --strict` + `pytest` green; if the contract changed, regenerate `schema.d.ts` with no drift; the frontend can `build`; **money / numbering / exchange-rate logic must have unit tests**; `docker build` passes; no red line violated.

## Implementation / Review briefs
- **Briefs live on disk only, never in version control (hard requirement)**: `review-notes/` is `.gitignore`d (`review-notes/*`); all briefs/reports **exist only as local files**, visible to the author in the workspace at any time. **Never `git add -f` or commit `review-notes/` files into git by any means** (once tracked, gitignore is void and history gets polluted; this project already had to scrub history once for this). Writing a brief = write straight to disk with the file tools, **no add, no commit**.
- **Implementation brief (each step)**: after each implementation round completes (planning doesn't count), write a Chinese implementation brief under `review-notes/`, covering at least: (a) what was implemented this round; (b) automated test results; (c) manual walkthrough steps. In orchestrator mode, name it `review-notes/M<x>-step<n>-impl.md`.
- **Milestone-level implementation report (end of milestone)**: after all steps of a milestone are done, produce an extra `review-notes/M<x>-report.md` — ① detailed; ② author-readable; ③ containing the **full manual walkthrough steps for this milestone** (integrating each `M<x>.md`'s "🟢 deployment self-test points"). This is the input for the author's manual walkthrough.
- **Manual walkthrough timing**: from M7 on, **gradually stop walking each step manually** (per-step gate = automated tests green + blind review with no findings); the manual walkthrough **converges to once at the end of the milestone**, with the author going through the milestone-level report above. **Default startup**: dev Compose (`docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d`), not splitting into manually starting frontend/backend separately.
- **Review input**: when the author asks for a review, prefer the implementation brief the author specifies; if unspecified, auto-read the latest implementation brief under `review-notes/`, then review against the incremental diff and the relevant design docs.
- **Review output**: only write a Chinese review report under `review-notes/` when there are change requests / findings; if there are none, just say so in chat, no extra file.

## Commit conventions (hard requirement)
- Commit messages use **English Conventional Commits**: `feat:` / `fix:` / `docs:` / `docs(plan):` / `refactor:` / `chore:` …
- **Absolutely no AI/Claude attribution**: no `Co-Authored-By`, no "authored by Claude"-style wording.
- **Only commit / push when the author explicitly asks.**

## Per-round commit rhythm (implement / rework / wrap-up)
> The author drives a feature's commit rhythm with these three keywords; **the keyword itself is the "explicit request to commit" authorization** (refining the blanket "only commit when the author explicitly asks" above, no conflict). All three follow the "Commit conventions" above (English Conventional Commits, no AI attribution).
> **In orchestrator mode these three happen automatically per step**, and the autosquash is **per-step** (each atomic step squashed into one commit) rather than per-feature — see the "Agent orchestration" section.

1. **Implement**: when the author says "implement", finish it, set a Conventional Commits message for the feature, and `git commit` one round.
2. **Rework**: when the author says "rework", **don't open a separate commit**; fixup the reworked implementation commit instead: `git commit --fixup=<target impl commit sha>`.
3. **Wrap-up**: when the author says the feature is "finished / wrapping up", auto-squash the chain of implementation commits + all fixups into **one** commit.
   - Command: `GIT_SEQUENCE_EDITOR=: git rebase --autosquash <commit before the feature's starting point>` (this environment doesn't support interactive `-i`; use `GIT_SEQUENCE_EDITOR=:` for a non-interactive autosquash).
   - autosquash only folds each fixup back into its target implementation commit; if the feature produced **multiple** implementation commits, squash those together in the same rebase too, so the feature ends with a single commit.

## Agent orchestration (execution model since M7)
> Since M7, milestone implementation supports two execution modes. **Manual mode is the default**; only when the author **explicitly names orchestrator mode / direct generation** do you run the fully automated loop below. The design docs (`docs/plan/milestones/M<x>.md`) already write each atomic step as **self-contained + with blind-review points**, so both modes can hook in.

### Two modes
- **Manual mode (default)**: the author just asking you to implement a step and output an implementation brief = manual mode. **No auto-spawning sub-agents, no auto review/fix loop, no auto commit** (commits still per the "commit rhythm" keyword authorization). **Without the author explicitly naming orchestrator mode, always this.**
- **Orchestrator mode (fully automated)**: the author opens a new Opus (Extra High Reasoning) conversation, **you are the orchestrator**, and you drive sub-agents through the specified steps / milestone per the loop below. **The author naming orchestrator mode is itself the explicit authorization for this round's commits (impl / fixup / per-step autosquash).**

### Three kinds of sub-agent (model defaults; prompt can override)
- **implementer / fixer**: same kind, consistent logic; default **Sonnet + high reasoning**.
- **reviewer**: default **Opus + extra high reasoning**.
- When the author explicitly specifies a different model / reasoning level in the prompt, **the prompt wins**.

### Per-step loop (orchestrator mode · when the author asks to "implement step by step")
**Which step is decided by the orchestrator, advancing step by step** (step 1 → 2 → …, one step per iteration). Each atomic step runs a full round before moving to the next:

1. **Implement (implementer)**: spawn a clean implementer; the instructions must include:
   - Implement **only the currently specified step**, no free-styling (no doing other steps / no sneaking in refactors).
   - **Complete tests**: cover both Happy Flow + Corner Cases.
   - **Don't pollute the machine**: if temporary verification is needed during implementation, clean up temp files afterward, **don't touch the machine's production environment** (DB / containers / files).
   - On completion, write the **Chinese implementation brief for this step** per "Implementation / Review briefs".
   - Land one **implementation commit** (= this step's feature commit).
2. **Blind review (reviewer)**: spawn a **brand-new** reviewer, **giving only**: (a) the milestone design doc (`M<x>.md` + roadmap); (b) the just-written implementation brief; (c) this step's diff. **No access to the implementer's conversation / thinking** (black-box blind review). Focus: ① does it **fully follow the design doc**; ② any **drift from the design doc**; ③ code **bugs + latent risks**.
   - Findings → write a **Chinese review brief** into `review-notes/` (the author may read it).
   - No findings → the step ends.
3. **Rework (fixer)**: findings → spawn a fixer, input = **design doc + that review brief**; after fixing, land a **`--fixup` commit** (pointing at this step's implementation commit, see "commit rhythm").
4. **Re-review**: after rework, **spawn a reviewer again to re-review**; as long as there are **new findings**, keep reworking → re-reviewing until **no findings**. **Rework cap = 5 rounds**; if findings remain after 5, **stop and escalate to the author for manual intervention.**
5. **Close out the step**: once this step's impl + all fixups are settled, the orchestrator does **one per-step autosquash**, squashing this step's implementation commit + its fixups into **this step's single commit** (command per "commit rhythm", base = the commit before this step's implementation commit). ⇒ at milestone completion **each step leaves one commit**.
6. **Next step**: repeat 1–5 until all steps of the milestone are done.

### Milestone wrap-up
- All steps done → produce a **milestone-level implementation report** (`review-notes/M<x>-report.md`, requirements per "Implementation / Review briefs").
- The author does a **manual walkthrough** against it; change requests during the walkthrough go through **manual conversation** (no more automated loop).

## Maintaining this file
Only change this file when the **foundations** change: tech stack, the red lines / commands / conventions above, the execution model (Agent orchestration), or adding a new agent tool. **Advancing milestones doesn't require touching it** — that only updates `docs/plan/`. **This file is bilingual (English-first + Chinese mirror): any change here must update `AGENTS_zh.md` in the same change so the two stay semantically identical (see "Documentation language").**
