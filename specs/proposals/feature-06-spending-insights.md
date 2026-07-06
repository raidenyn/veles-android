# Feature: Spending insights from intercepted transactions

**Type:** New feature
**Priority:** Medium — differentiator, builds on feature-01
**Effort:** Medium (~3 days), hard dependency on feature-01 (OTP history)

## Motivation

Veles already parses what most budgeting apps can't get without bank API access: **merchant,
amount, and currency at authorization time**. OTP-confirmed online purchases flowing through the
app form a passive, on-device transaction feed. Surfacing simple aggregates turns Veles from a
convenience utility into something users open on purpose.

## Design

1. **Data.** Reuse `OtpEvent` rows from `feature-01` (merchant, amount, currencyCode,
   receivedAt, bankName). No new capture logic needed. OTP-less transactions are invisible —
   state this honestly in the UI ("online purchases confirmed via OTP only").
2. **Screen.** New `insights` route:
   - This month total per currency (no FX conversion — sum per currency separately).
   - Top merchants (name, count, total) for the selected month.
   - Simple month-over-month bar (last 6 months), Compose-drawn (`Canvas`/`drawBehind`), no
     charting dependency.
3. **Merchant normalization.** Raw merchant strings are noisy (`AMZN Mktp`, `AMAZON.CO.TH`).
   Phase 1: group by exact string, uppercase-trimmed. Phase 2: user-editable merchant aliases
   (small `merchant_aliases` table, long-press a merchant → "merge with…").
4. **Privacy.** Everything stays on-device in the existing Room DB; respects `feature-01`'s
   incognito toggle and deletions.

## Non-goals

Budgets, categories, FX conversion, bank-account balance tracking, any network calls.

## Testing

- DAO aggregate-query tests (per-month, per-merchant sums across currencies).
- ViewModel tests for month selection and alias grouping.
- Screenshot/Compose test for the empty state (no transactions yet).
