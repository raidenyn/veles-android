# M-11 manual adjudication: DRAFT (conditional; not a row change)

**Status:** this is a draft prepared during curation. **It needs independent review before any matrix
use.** It does not edit any raw capture: all three keep `ended_by: deadline` and their `problems`,
and attempt 2 keeps `pending_at_host_budget_not_denial`.

**Basis:**
- the independent review `../mac-integration-2026-10-29/reports/otp23-mac-final-data-review-opus.md`
  §5–6, a conditional PASS
- its only open requirement (§6.1, account class), now met by the record
  `owner-records/account-class.txt` (`a2bdc40c…`, 2026-10-29T11:01:19Z: `raiden`, uid 501, `admin`)

## Proposed scoped outcome

**Scoped M-11 PASS (proposed):**
- **Scope:** the unsigned (linker ad-hoc) dev-registered host `c36894dc…`, with the distinct identity
  `me.nagaev.veles.bridge.dev` / `me.nagaev.veles.otp23.dev`, on the existing **admin** account
  (`raiden`, uid 501).
- **Launch:** Chrome launched it from a disposable profile (attempts 2–3).
- **Authorization:** `not_determined` (host reply, attempt 2) → owner-reported prompt "VelesOtp23Dev"
  and Allow (external, late) → authorized, `scan_state started` (attempt 3).
- **Comparison:** the signed `e7c716c5…` showed `allowed_always` and scan start, and the signed
  fixture's grant is unchanged.

## Caveats carried with the outcome (all required)

1. Attempt 1 is a **setup FAIL** (relative manifest path from `--stage .`), retained. It is not an OS
   rejection.
2. Attempts 2 and 3 **ended by deadline**. The owner answers arrived 36 min and 3 h 17 min after
   their runs and were **never accepted** by the script. They are external, operator-captured
   observations. The attempt-3 "No dialog visible" is not a during-run observation.
3. There is no dev host trace and no explicit authorization enum. The authorized state is inferred from
   the `permission_pending` semantics and the actual `scan_state started` reply.
4. **Discovery was not compared.** No phone advertised during M-11.
5. The script hash `976f61cf…` is **controller-attested**, not self-recorded by the capture.
6. The developer grant persists on the account. No TCC reset or toggle was done.
7. The operator was the parent controller gpt-6.1-sol, not an Opus session.
8. **M-03 and M-04 are unaffected.** The original prompt owner remains unobserved, and the M-04 off/on
   toggle is not approved. No waiver is inferred.

## What would make this draft invalid

- The independent reviewer finds a fact here that the raw data doesn't support.
- The account record turns out not to be from the same account (uid 501) that ran the attempts.
