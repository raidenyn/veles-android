# M-11 final manual adjudication (2026-10-29): scoped PASS

**Row:** M-11, "Unsigned dev-registered host on the EXISTING account: launch, TCC authorization and
scan, compared with the signed fixture."

**This is a separate manual adjudication.** No raw capture is marked PASS, and no raw field is
rewritten.

**Who did what:**
- **Integration:** written by the Claude Code `claude-opus-5-5` integration session.
- **Approval basis:** the independent micro-review
  `../reports/otp23-mac-safe-attribution-verification-opus.md` §4–5, by the fresh Opus session
  `e30e474d-65cc-44c2-99c7-d0195cfc4631`. It **recommends a scoped manual M-11 PASS**, and its account
  condition (from `../reports/otp23-mac-final-data-review-opus.md` §6.1) is satisfied.
- **Superseded draft:** the frozen draft `../../mac-m11-dev-2026-10-29/adjudication-draft.md`
  (`b668aed3…`) stays as indexed history, byte-unchanged. This document supersedes it.

## Evidence

**Frozen M-11 captures.** `../../mac-m11-dev-2026-10-29/`: 25 entries, `SHA256SUMS 071d14f1…`, not
modified.

**Account record.** `owner-records/account-class.txt`, sha256
`a2bdc40c7d6119e01513ae3c3b9b71a287ad910d114ff172883043303050702d`.
- **Label: post-run, same account, read-only, operator-captured.** It was taken at
  2026-10-29T11:01:19Z, about 3 h 40 min after attempt 3 ended.
- **Content:** `raiden`, uid 501, `id -Gn` includes `admin`, and `dseditgroup` says "yes raiden is a
  member of admin". The micro-review verified this independently.
- **Binding:** uid 501 equals `chrome.context.uid` in attempts 2 and 3.

**Read-only TCC log supplement.** `../../mac-tcc-supplement-2026-10-29/` was added **after** the frozen
curation and is labelled post-run. It corroborates the developer prompt and the OS decision through a
separate channel:
- msgID `95540.2`: `AUTHREQ_PROMPTING` at 05:29:58.733307Z, inside attempt 2
- `AUTHREQ_RESULT` at 06:17:25.367351Z: `authValue=2 authReason=2 authVersion=1 error=(null)`
- `REPLY`

It is **not** a host trace and **not** a CoreBluetooth enum, and it fills no captured null. It found
no original signed-host prompt.

## Scope of the PASS

**Account:** the existing **admin** account (`raiden`, uid 501).

**Developer host:**

| field | value |
|---|---|
| bundle id | `me.nagaev.veles.bridge.dev` |
| `CFBundleName` | `VelesOtp23Dev` |
| Native Messaging name | `me.nagaev.veles.otp23.dev` |
| raw binary | `c36894dca9d47717410cf833ca4f181894d1144e48bc0f301232a2be307864ea`, the unsigned counterpart of the signed `cd971f0` fixture |
| signature | only the automatic **linker ad-hoc** signature (`adhoc,linker-signed`, TeamIdentifier not set) |

There is **no** Apple Development or Developer ID signing.

**Launch.** Chrome launched it from a disposable profile in attempts 2 and 3 (`hello ready`).

**Authorization:**
- Attempt 2: the actual native reply was `scan_failed/permission_pending`, which the host emits only
  when `CBManager.authorization == NotDetermined` at its 10 s budget.
- Attempt 3: `scan_state started` at +1.0 s, classified `authorized_started`. "Authorized" is
  functional and source-inferred, because a scan starts only when authorized and PoweredOn. **No
  `allowed_always` enum is fabricated.**

**Comparison with the signed fixture.** The signed baselines in M-08 actually have `allowed_always`,
scans and `device_found`.

**Unchanged state:**
- The original `e7c716c5…` passed `codesign --strict` before and after every attempt.
- The original grant: the owner's read-only check reported "Still enabled, unchanged".
- Only the owned dev manifest was registered, and it was removed after every attempt. The disposable
  profiles were removed.
- **The new dev grant persists.** It was not removed or reset.

## Caveats (carried verbatim in substance from micro-review §5; all part of the outcome)

1. **Attempt 1 is a setup FAIL, retained.** The runbook's relative `--stage .` produced a relative
   Native Messaging manifest executable path and `hello` = synthesized `port_closed`. It is **not**
   an OS rejection of the unsigned host. The packager exited 126 when run directly (mode 0644), and
   the same bytes then ran via `bash`. There was no source or signing change.
2. **Attempts 2 and 3 ended by deadline.** Their deadlines are kept (720.9 s and 3600.3 s), with
   `answer: null` and the `problems` exactly as captured. Attempt 2 stays
   `pending_at_host_budget_not_denial`.
   - The owner's actual dev-prompt name ("VelesOtp23Dev") and **Allow** were delivered late (answer
     file 06:18:28Z, 36 min after the deadline) and were **never bound** to the capture.
   - The attempt-3 "No dialog visible" report came about 3 h 17 min after the run. It is **not**
     proof of absence during the run, and the `.tmp` guard (`test ! -e m11-capture.json`) prevented
     the rename.
   - No ack or status is rewritten retroactively.
3. **No host trace.** The dev host trace is absent (its relative `VELES_OTP23_TRACE` path is the
   probable cause). `post_init_authorization: null` means "not traced". It is not an unknown final
   state, and no enum is substituted.
4. **No discovery comparison.** No phone was advertising during M-11, so discovery is neither
   compared nor claimed.
5. **Script provenance is controller-attested.** Script `976f61cf…` is attested by the controller,
   not self-recorded as a capture pin. The local stage copy matches.
   - **Code:** implemented by Opus `752e1974` and reviewed by Opus `7b4ee6ca`.
   - **Physical operator:** the parent controller **gpt-6.1-sol** (OpenCode, provider openai). The
     fresh Opus physical-operator call `c401a87c` hit API 429 **before any tool**.
   - **No Opus model operated M-11.**
6. **No inference about the original signed prompt.**
   - **M-03:** the original signed prompt owner remains **unobserved**. The distinct dev dialog name,
     the current `VelesOtp23PoC` Settings entry and the TCC supplement's current signed
     `AUTHREQ_SUBJECT` records do not discharge it.
   - **M-04:** the tied clause stays **unmet**. No M-04 off/on toggle was approved or performed.

## Outcome

**M-11 PASS (scoped, manual).**
- **Operator:** parent controller gpt-6.1-sol, which executed the unchanged reviewed script.
- **Owner role:** the account owner answered the developer dialog. That answer was reported late and
  never accepted by the script.
- **Account:** admin.

**Not affected:** M-03, M-04 and every other row are unchanged. This is not a waiver and does not
authorize OTP-24.
