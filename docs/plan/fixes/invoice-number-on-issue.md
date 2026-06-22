# Fix · Defer Invoice Numbering to Issue Time (Drafts Carry No Number)

> 🌐 **English** · [中文](invoice-number-on-issue_zh.md)

> Bug-fix-class change — **not a milestone** (lives under `docs/plan/fixes/`, deliberately outside the roadmap milestone map). Before implementing, read `docs/plan/roadmap.md` §2 global constraints (**especially red line 4 — concurrency-safe numbering, and red line 11 — OpenAPI→TS regeneration**); the numbering engine `services/numbering.py`; the invoice service `services/invoice.py` (the `create` / `transition_status` / `clone_quote_to_invoice` paths). Each atomic step is **self-contained** — a clean agent can land it from "this doc + repo (CLAUDE.md / memory / existing code) + a few words from the author", no conversation history required.
>
> **Status**: **Design frozen** (2026-06-22). To be implemented when the author finds time.

## Origin
During the 2026-06-22 production walkthrough the author saw quote `Q2026-006` while only **5** quotes existed — a deleted draft had permanently skipped number `005`. For **quotes** this is fine: quotes have no legal numbering obligation, so gaps are acceptable. The investigation found that **invoices share the exact same numbering mechanism**, which is a real problem:

- The invoice number is allocated at **creation** (`allocate_invoice_number`), **including for `DRAFT`** invoices, and via **two** create paths: the normal create (`_build_and_persist_invoice`) and the quote→invoice conversion (`clone_quote_to_invoice`).
- `delete_invoice` only allows deleting **`DRAFT`** invoices (hard delete; the number is never recycled — see red line 4).
- ⇒ The only deletions the system permits are exactly the ones that leave a **permanent gap** in the invoice number sequence.

A draft that was never issued has no legal existence and **should not consume a legal invoice number**. (The EU/NL rule only requires each *issued* invoice to bear a unique, systematic identifier; strict consecutiveness is not mandatory — non-sequential / UUID-style schemes are allowed — but burning and skipping numbers on discarded drafts is undesirable.)

**Fix**: allocate the invoice number at the **`DRAFT → SENT` (issue)** transition rather than at creation. Drafts carry no number; deleting a draft costs nothing and leaves no gap. No number recycling is ever required, so red line 4 stays intact.

## Goals & Scope
- **Goal**: an invoice receives its legal number **only when it is issued** (`DRAFT → SENT`). A `DRAFT` invoice carries **no** `invoice_number` / `sequence_number`; deleting a draft consumes and skips no number.
- **IN (included)**:
  - Make `invoice.invoice_number` and `invoice.sequence_number` **nullable** (Alembic migration; `customer_sequence_number` is already nullable).
  - Move `allocate_invoice_number` out of **both** create paths (`_build_and_persist_invoice` create branch + `clone_quote_to_invoice`) into the **`DRAFT → SENT`** branch of `transition_status` (idempotent — only when `invoice_number is None`; same transaction, row-locked — red line 4 preserved).
  - PDF (`templates/pdf/invoice.html` + `services/pdf.py`) + frontend: render an unnumbered draft as **"Concept / 草稿"** (no fabricated number).
  - `schemas/invoice.py`: `invoice_number` / `sequence_number` → optional; regenerate `schema.d.ts` (red line 11); frontend handles null.
  - Guard: an **unnumbered draft cannot be emailed or have a payment recorded** (it must be issued first).
  - Tests: number allocated only at issue, idempotent, **delete-draft leaves no gap**, concurrency, both create paths defer.
- **OUT (not included / deferred)**:
  - **Quotes (`quote`) unchanged** — keep allocate-at-create; no legal numbering obligation (author's decision).
  - **No number recycling / counter rollback** — explicitly rejected: it is the race-prone `max+1` anti-pattern red line 4 forbids. Deferring allocation makes recycling unnecessary.
  - **`unique_hash` not repurposed** — it stays reserved for the M9 public link (`models/invoice.py`: "Reserved for M9 public link; not active in M5"). Drafts are identified internally by their `id` (UUID PK).
  - **"Preview next number" on drafts** — an optional UI nicety; the default is Concept-only (no number). Easy to opt in later via the existing read-only `get_next_sequence_info`.
  - **Historical renumbering** — none. Existing numbered rows keep their numbers; only newly created drafts are unnumbered. (The author's live instance currently holds **0 invoices**, so there is no existing-data concern; the migration is purely additive / non-destructive for any self-hoster.)
- **Related docs**: roadmap §2 red lines 4 & 11; `services/numbering.py`; `services/invoice.py`. Tax background: `docs/insight/btw-aangifte-2026-guide.md` (issued-invoice numbering only).

## Frozen Decisions (D1–D8)
- **D1 · Allocation point = `DRAFT → SENT`**: numbering happens in `transition_status` when `current == DRAFT and new_status == SENT and inv.invoice_number is None`, inside the caller's open transaction and before commit; reuse `allocate_invoice_number` (row-locked) verbatim. **Idempotent** — never re-allocate an already-numbered invoice.
- **D2 · Drafts carry no number**: on create (normal create **and** quote→invoice conversion) `invoice_number = NULL`, `sequence_number = NULL`, `customer_sequence_number = NULL`. Internal identity = `id` (UUID PK).
- **D3 · No recycling; sequence stays monotonic** (red line 4): allocation is simply moved later; still one company sequence + row lock + unique constraint. Once issued (`SENT`+) an invoice never loses its number. `CANCELLED` is only reachable from `DRAFT` (and `SENT → {}` is locked), so cancelled invoices are always unnumbered — no gap, nothing to recycle.
- **D4 · Draft display = "Concept / 草稿", no number** (default): never show a fabricated or preview number on a draft, removing any chance it is mistaken for a final legal number. (An opt-in greyed preview via `get_next_sequence_info` is a later UI choice, out of scope here.)
- **D5 · Quotes untouched**: `quote` keeps allocate-at-create + gap-on-delete. Different document type, no legal numbering need.
- **D6 · Nullable migration, non-destructive**: `ALTER COLUMN ... DROP NOT NULL` on `invoice_number` + `sequence_number`. The unique constraint `uq_invoice_company_number (company_id, invoice_number)` is unchanged (Postgres treats NULLs as distinct, so multiple unnumbered drafts coexist; uniqueness still holds once numbered). Existing rows keep their values. Downgrade caveat: re-adding `NOT NULL` fails if any unnumbered drafts exist (acceptable for a forward fix — backfill or block the downgrade).
- **D7 · Contract change → codegen** (red line 11): `InvoiceRead.invoice_number: str | None`, `InvoiceRead.sequence_number: int | None`; run `npm run codegen`, CI no-drift; all frontend null-handling updated.
- **D8 · Issue-gated actions**: emailing and recording a payment require a **numbered** (issued) invoice; reject on an unnumbered draft with a clear 400. (BTW reporting already filters revenue to `status ∈ {SENT, COMPLETED}` in `services/reporting/btw.py`, so drafts are excluded — **no change** there.)

## Data Model / Migration
- **Table `invoice`** — only change: `invoice_number TEXT` → nullable; `sequence_number BIGINT` → nullable. `customer_sequence_number` is already nullable. No new tables or columns.
- **Constraints unchanged**: `uq_invoice_company_number (company_id, invoice_number)` stays; NULLs are distinct in PG ⇒ many unnumbered drafts allowed, and uniqueness is still enforced once a number exists.
- **Alembic**: a single revision performing `DROP NOT NULL` on the two columns. Forward-only in spirit (the downgrade re-adds `NOT NULL` and is only safe when no NULL rows exist).

## Contract
- `InvoiceRead.invoice_number` and `InvoiceRead.sequence_number` become optional (nullable). ⇒ **`npm run codegen` is required**, and CI enforces no drift (red line 11). No new endpoints; the existing `DRAFT → SENT` transition endpoint now triggers allocation server-side.

## Atomic Steps Checklist
> Each step = one atomic change passing roadmap §5 DoD. **Numbering logic must be tested** (red line 4); the contract change ⇒ codegen no-drift (red line 11).

### Step 1 · Make invoice number columns nullable (model + migration)
- **Backend**: `models/invoice.py` → `invoice_number: Mapped[str | None]`, `sequence_number: Mapped[int | None]` (`nullable=True`). Alembic migration `DROP NOT NULL` on both.
- **Migration**: one revision; upgrade drops `NOT NULL`; downgrade re-adds it (document the "fails if NULL drafts exist" caveat).
- **Tests**: inserting a draft with NULL number/sequence succeeds; two NULL-numbered drafts coexist (unique constraint OK); a duplicate non-null `invoice_number` within one company still violates the unique constraint.
- **Blind-audit points**: ① unique constraint intact + multiple NULL drafts allowed; ② no other code path relies on these being non-null at the DB layer; ③ downgrade caveat documented.

### Step 2 · Defer allocation to `DRAFT → SENT`
- **Backend**:
  - Remove number allocation from **both** create paths: `_build_and_persist_invoice` (create branch, ~L414–425) and `clone_quote_to_invoice` (~L614–631). New invoices persist with `invoice_number = None`, `sequence_number = None`, `customer_sequence_number = None`, `status = DRAFT`.
  - In `transition_status`: when `current == DRAFT and new_status == SENT and inv.invoice_number is None`, call `allocate_invoice_number(...)` within the same transaction before `commit`, and assign the returned number/seq onto `inv`. Keep the `IntegrityError → rollback → ValueError` handling. Idempotent: skip if already numbered.
- **Tests** (numbering must be tested): create via both paths → number is `None`; `DRAFT → SENT` → number allocated and the company sequence advances by exactly 1; **create a draft then delete it → company sequence unchanged → the next issued invoice has no gap** (the core regression); two drafts issued → serialized, consecutive, no duplicate; re-issue path (`CANCELLED → DRAFT → SENT`) allocates exactly once.
- **Blind-audit points**: ① a number is **never** allocated at create (grep both paths); ② idempotent — re-transitioning an already-numbered invoice does not re-allocate; ③ allocation stays row-locked + same-transaction (red line 4); ④ both create paths (normal + quote-convert) defer; ⑤ delete-draft leaves no gap (assert the sequence `next_value`).

### Step 3 · PDF / document rendering for unnumbered drafts
- **Backend**: `templates/pdf/invoice.html` — where `invoice.invoice_number` is printed (`<title>` ~L5 and the number row ~L48–49), fall back to a `labels.draft` string ("Concept" / "草稿") when it is null. Add that label to the i18n label set (EN / NL / ZH) assembled by `services/pdf.py`.
- **Tests**: rendering a draft (null number) → no real number, shows the Concept label in both spots; rendering a `SENT` invoice → shows the number.
- **Blind-audit points**: ① both template spots handled; ② no fabricated/preview number on a draft (D4); ③ label present for all locales.

### Step 4 · Schema, frontend, and issue-gated guards
- **Backend**: `schemas/invoice.py` → `invoice_number: str | None`, `sequence_number: int | None`. Add guards so emailing (`services/email.py` send entry) and recording a payment (`services/payment.py`) reject an invoice whose `invoice_number is None` (must be issued first) with a clear 400. (BTW already excludes drafts — no change.)
- **Frontend**: regenerate `schema.d.ts` (`npm run codegen`, no drift). Update the 5 reference sites (`views/invoices/InvoiceList.vue`, `views/invoices/InvoiceEdit.vue`, `views/payments/PaymentList.vue`, `stores/invoices.ts`) to render "草稿 / Concept" (or an em-dash) when the number is null; ensure list sort/search tolerate null.
- **Tests**: backend guard tests (email / payment on an unnumbered draft → 400); `npm run build` green.
- **Blind-audit points**: ① `schema.d.ts` regenerated, no drift; ② all 5 frontend sites null-safe (no crash, no literal "undefined"); ③ email + payment guards present and tested; ④ BTW confirmed unchanged (drafts already excluded).

### Step 5 · Regression + closure
- **Tests**: full backend suite (`pytest` unit + integration) green; end-to-end regression: create a draft (number `None`) → delete it → create another draft → `DRAFT → SENT` → it receives the next number with **no gap inherited from the deleted draft**.
- **Blind-audit points**: ① the regression genuinely proves "deleting a draft skips no number" (the reason this fix exists); ② existing invoice tests updated for "create yields no number" are convention-correct, not implementation-accommodating.

## 🟢 Deployment Self-Test Points (acceptance)
> Default dev Compose (`docker compose -f docker-compose.yml -f docker-compose.dev.yml up -d`) full stack, then a manual walk.
- Create a new invoice → it appears as a **Concept / 草稿 with no number** in the list, the editor, and the (preview) PDF.
- Delete that draft → create another invoice → issue it (`DRAFT → SENT`) → it receives the **next** number with **no gap** caused by the deleted draft.
- Convert a quote into an invoice → the resulting invoice is a **DRAFT with no number**; issuing it allocates the number.
- Try to email / record a payment on an unnumbered draft → **blocked** with a clear message; after issuing, both work.
- Run the BTW report over a period that contains a draft → the draft is **not** counted (unchanged behavior).

## Acceptance Conclusion (fill at closure)
- Completion date:
- Implementation (commits / mode):
- Automation (`ruff` + `mypy --strict` + `pytest` + codegen no-drift + `npm run build`):
- Acceptance (self-test points passed?):
- Known carryover / deferred:
