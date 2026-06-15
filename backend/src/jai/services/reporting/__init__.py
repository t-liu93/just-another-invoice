"""M10 reporting engine – pure-read aggregation over existing persisted columns.

Submodules:
- ``pl``  : P/L (profit & loss) report (step 1).
- ``btw`` : BTW/VAT return report (step 2, future).
- ``icp`` : ICP (Intrastat Community Purchases) report (step 3, future).
- ``expenses``: Expense report by category (step 4, future).
- ``dashboard``: Dashboard KPI aggregation (step 5, future).

Design constraints (D1):
- **No business writes** – every function in this package is pure-read;
  only SELECT / aggregate queries are issued.
- All monetary results are ``Decimal`` (red-line 1).
- Rounding: take already-persisted to-the-cent amounts, sum them, never
  re-round the aggregate (D6).
"""
