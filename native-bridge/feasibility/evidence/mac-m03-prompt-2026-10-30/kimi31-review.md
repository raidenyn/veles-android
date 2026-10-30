# Independent OTP-23 M-03 evidence review — 2026-10-30

## Verdict

**APPROVED.** M-03 **only** may now be promoted to **PASS**, with its existing expected
requirement unchanged and without a waiver. This is a fresh, owner-approved observation on the
existing administrator account after an app-specific Bluetooth reset; it is not recovery of the
historical prompt and is not a universal macOS attribution claim.

I authorize the bounded mechanical integration described below **without a second substantive
review**, provided it preserves the raw evidence and chronology, stays within that exact scope, and
produces the expected check results. M-04, W-10 and A-01 remain **BLOCKED**; W-01, M-01 and M-02
remain the only waived **NOT RUN** rows. The resulting matrix is **26 PASS / 3 BLOCKED / 3 waived
NOT RUN**, still **NO-GO**, and OTP-24 is not authorized.

Reviewer role: independent user-selected Kimi K3.1 Pro, using the actual configured
`kimi-code-plan-global/k3.1-pro` route, with no delegation. The operational capture/curation role
remains attributed to the parent `openai/gpt-6.1-sol`; the prompt action and UI-name report remain
attributed to the account owner. I made no repository edits and ran no hardware or permission
operations.

Paths below are relative to the repository root unless absolute. The evidence root is
`native-bridge/feasibility/evidence/mac-m03-prompt-2026-10-30/` (abbreviated below as `M03/`).

## Findings by severity

No Critical, High, Medium, or Low defect was found in the M-03 evidence or its proposed disposition.
The following are **informational, non-blocking residuals** that the integration must preserve:

| ID | Severity | Finding and exact evidence |
|---|---|---|
| R-1 | Informational / evidence limitation | The human report is operator-captured, not an independent transcript or screenshot, and says exactly **`ValesOtp23PoC. Clicked allowed`**. The bundle separately says **`VelesOtp23PoC`**. The spelling discrepancy must not be silently corrected or treated as a different prompt. The OS request-ID chain independently supplies the signed-host attribution and Allow result. See `M03/owner-record.json:3-12`, `M03/restart1/owner-answer.json:1-7`, `M03/restart1/bundle-info.json:2-4`, `M03/restart1/signed-prompt-chain.ndjson:4-7`, and `M03/restart1/signed-tcc-subject.ndjson:20,27,30`. |
| R-2 | Informational / scope limit | The observation follows the explicitly approved reset of only `BluetoothAlways` for `me.nagaev.veles.bridge`; it does not identify the historical prompt, establish behavior for another signing identity/account, or waive M-04. See `M03/owner-record.json:5-17`, `M03/restart1/result.json:56-69`, and `M03/FINDING.md:5-21,57-71`. |
| R-3 | Informational / evidence boundary | The waiting run does **not** print `allowed_always` and finds no device. It proves `permission_pending` followed by a successful scan on the same retained Native Messaging port. Explicit `allowed_always` appears only in the subsequent fresh Chrome/host launch. No discovery or phone claim may be added. See `M03/restart1/host.trace.log:4-15`, `M03/restart1/result.json:200-204`, `M03/restart1/verify-grant.trace.log:1-12`, and `M03/FINDING.md:38-41,57-62`. |
| R-4 | Informational / unresolved retained failure | The interrupted first attempt remains `ERROR` with `access_denied`; its cause is unproven, and its injected abort answer is not a prompt observation. It must not be rewritten or used as M-03 credit. See `M03/interrupted/result.json:143-180,194-199`, `M03/interrupted/host.trace.log:4-9`, `M03/interrupted/owner-answer.json:1`, and `M03/FINDING.md:23-29`. |

## Requirement-by-requirement determination

1. **The canonical gate is unchanged.** M-03 requires the directly signed final-identifier fixture,
   stable signing identity, usage descriptions, and an observed prompt owner on the existing
   account (`native-bridge/feasibility/acceptance/otp23-matrix.json:442-446`). The RFC retains the
   existing-account prompt owner even after the new-account and signing-provenance amendments
   (`docs/rfcs/2026-08-21-otp-external-devices.md:1587-1605,1607-1624`). The new evidence satisfies
   the missing observation; no expected text needs amendment.

2. **The owner scope was followed.** The approval was M-03-only, for a fresh prompt on the existing
   account using the unchanged signed host after an app-specific reset; it expressly excluded a
   global/other-app reset, re-signing/key export, a new account, M-04, waivers and OTP-24
   (`M03/owner-record.json:5-17`). The restarted capture executed exactly
   `/usr/bin/tccutil reset BluetoothAlways me.nagaev.veles.bridge`, with return code 0
   (`M03/restart1/result.json:56-69`). The earlier accidental Chrome closure and restart are
   preserved rather than hidden (`M03/owner-record.json:9-10`).

3. **The signed production-shaped identity remained unchanged.** The restarted run verifies the
   final identifier and Team before launch and records the same approved executable SHA-256 before
   and after (`M03/restart1/result.json:5-31,226-230`). Final strict verification and the approved
   CDHash are retained (`M03/restart1/final-readback.json:40-58`). The bundle carries both Bluetooth
   usage descriptions and `CFBundleName = VelesOtp23PoC`
   (`M03/restart1/bundle-info.json:2-12`). This remains the approved Apple Development OTP-23
   minimum, not a new Developer ID/notarization/CI claim.

4. **Chrome parentage is direct and recorded.** Chrome PID 15893 launched host PID 15918; the host's
   recorded PPID is 15893, with the process ancestry retained
   (`M03/restart1/result.json:72-84,102-141`). The TCC records independently identify that same PID
   and executable path as `me.nagaev.veles.bridge`
   (`M03/restart1/signed-prompt-chain.ndjson:1,4-6`).

5. **A fresh TCC PROMPTING/RESULT chain exists and agrees with the host.** For request `15918.2`,
   `AUTHREQ_PROMPTING` names BluetoothAlways and signed subject/responsible process
   `me.nagaev.veles.bridge`, PID 15918 (`M03/restart1/signed-prompt-chain.ndjson:6`). The matching
   `AUTHREQ_RESULT` has `authValue=2`, `authReason=2`, and null error
   (`M03/restart1/signed-prompt-chain.ndjson:7`). The subject-filtered OS record expressly says
   `Allowed (User Consent)` for that host (`M03/restart1/signed-tcc-subject.ndjson:30`). The host had
   first reported `not_determined`, then `permission_pending` at its ten-second budget
   (`M03/restart1/host.trace.log:4-8`; `M03/restart1/result.json:143-154`). Thus the signed host,
   rather than Chrome, is the observed prompt subject **for this run**.

6. **The owner answer arrived during the active wait, before the deadline.** Waiting began at
   02:23:47.561684Z with a 1800-second deadline; the answer was consumed at 02:26:04.161597Z,
   approximately **136.6 seconds later** (`M03/restart1/result.json:157-175`). The raw answer file
   preserves the exact spelling and Allow action (`M03/restart1/owner-answer.json:1-7`). No late
   answer was injected after a deadline.

7. **The post-Allow scan used the same retained port and host.** The driver creates one Native
   Messaging connection and one connector session, waits for the owner answer, then calls
   `startScan` again without reconnecting (`M03/source/observe_prompt.py:71-106`). The connector
   maintains that port until explicitly disconnected (`M03/source/extension/connector.js:109-132`).
   The single-host trace shows request `r2-9s8r0j` returning `permission_pending`, then
   `r3-0hnyu7` starting, followed by stop and `shutdown_complete`
   (`M03/restart1/host.trace.log:7-15`; `M03/restart1/result.json:177-213`). The initial pending
   reply was not rewritten into a success.

8. **`allowed_always` is confined to the separate fresh verification launch.** That later capture
   expressly says no reset, retains the same binary hash, and starts after the waiting run ended
   (`M03/restart1/verify-grant.json:1-14,48-50`; `M03/restart1/result.json:226-231`). Its new host
   PID 16759 directly logs `allowed_always`, scan started/stopped, and clean exit
   (`M03/restart1/verify-grant.trace.log:1-12`). This corroborates the grant without falsely adding
   an `allowed_always` transition to the waiting run.

9. **M-04 remains independently unmet.** Its expected clause requires `access_denied` with exact
   System Settings guidance, an observed prompt owner, and no TCC reset
   (`native-bridge/feasibility/acceptance/otp23-matrix.json:466-470`); the RFC separately requires
   exact guidance and recovery after denial (`docs/rfcs/2026-08-21-otp-external-devices.md:1598-1600`).
   The new M-03 reset/run neither displayed that guidance nor performed M-04's controlled no-reset
   denial/recovery. M-04 therefore stays BLOCKED. Its integration note may recognize the new M-03
   prompt-owner observation, but must not carry forward a broad, now-stale claim that no signed-host
   prompt owner has ever been observed, and must not promote M-04.

## Fresh verification performed by this reviewer

I inspected every retained raw capture, operational source copy, provenance record, finding and
owner record in `M03/`, plus the canonical matrix and RFC. I did not rely only on `FINDING.md`.

Read-only checks completed successfully:

- Recomputed SHA-256 for every source and published file in `M03/provenance.json:6-162`:
  **26/26 copies matched their recorded digest and were byte-identical; 0 failures**.
- Parsed the raw JSON/NDJSON/traces and checked 19 semantic assertions covering the scoped reset,
  unchanged hash, direct Chrome parentage, pending state, owner deadline, exact-name distinction,
  matching TCC request ID/subject/Allow result, chronology, same-host scan, absence of waiting-run
  `allowed_always`, empty discovery events, fresh-launch-only `allowed_always`, and retained first
  error/abort: **all 19 passed**.
- Recomputed the committed matrix totals as **25 PASS / 4 BLOCKED / 3 NOT RUN**, with blocked rows
  W-10/M-03/M-04/A-01 and waived rows W-01/M-01/M-02. Promoting only M-03 yields exactly
  **26 PASS / 3 BLOCKED / 3 waived NOT RUN**, with open rows W-10/M-04/A-01.

I did **not** run repository test suites, builds, hardware, integrated-tree checks, or any
permission/signing operation. No such result is claimed here.

## Privacy review

The candidate evidence contains the expected existing-account paths and public signing-certificate
identifiers needed for this provenance record. I found no private key, credential, raw model API
error/header dump, or raw model transcript in the candidate M-03 files. The public extension key is
not a private credential.

Do not publish `/tmp/opencode/otp23-m03-20261030/kimi31-events.jsonl`,
`/tmp/opencode/otp23-m03-20261030/kimi31-stderr.log`, or any raw model API errors, headers or
transcripts. Archive only safe, actual reviewer metadata (for example the configured model route,
actual session ID supplied by the controller, independent role and no-delegation fact). Do not infer
or fabricate a session ID.

## Bounded integration authorization

The parent may perform the following mechanically, without another substantive review:

1. **M-03 and chronology**
   - Append truthful 2026-10-30 observations to M-03's `actual`, the matrix `sources`, and D-02;
     preserve all prior chronology and the unchanged M-03 `expected` text.
   - Set only M-03 to `PASS`, with empty `blocker`, empty `waiver`, `account: admin`, and a named
     operator attributing capture/curation to parent `openai/gpt-6.1-sol` and the fresh prompt
     Allow/UI-name report to the account owner. Do not name this reviewer as operator.
   - Add the relevant M03 raw/finding/owner references and the archived copy of this review to
     M-03's evidence. Record the actual independent reviewer route
     `kimi-code-plan-global/k3.1-pro` separately from the operator role.

2. **M-04 and the decision**
   - Append that M-03 observed the signed-host prompt, while M-04's exact displayed Settings/retry
     guidance and coordinated no-reset denial/recovery were not run and remain unmet. Keep M-04
     BLOCKED with a truthful blocker. Do not waive or promote it.
   - Keep W-10 and A-01 BLOCKED and only W-01/M-01/M-02 waived NOT RUN. D-02 must state 26/3/3,
     NO-GO, and no OTP-24 authorization.

3. **Renderer and current-state tests**
   - Update `native-bridge/feasibility/scripts/matrix.py:114-118` by appending a current
     2026-10-30 M-03 integration note while retaining the 2026-10-29 section as history; regenerate
     `docs/otp-23-requirements-matrix.md` rather than hand-editing generated output.
   - Update current expectations in `native-bridge/feasibility/scripts/test_matrix.py`, including
     `OPEN_ROWS` at lines 19-21, the accepted evidence bases at lines 44-56, the 2026-10-29
     historical assertions at lines 249-344 without deleting their history checks, and the current
     M-03/M-04 assertions at lines 650-677.
   - Add a meaningful M-03 evidence-integrity test that verifies the new SHA256SUMS and the raw
     invariants: unchanged expected text and identity hash; retained interrupted
     ERROR/`access_denied`/abort-not-observation; restarted `permission_pending`; exact owner
     spelling distinct from CFBundleName; answer within 1800 seconds; direct Chrome parentage;
     matching signed-host PROMPTING/RESULT request ID with Allow; same-host subsequent scan; no
     waiting-run `allowed_always` or discovery claim; fresh-verification-only `allowed_always`;
     M-04/W-10/A-01 still BLOCKED; and exact 26/3/3, NO-GO state.

4. **Documentation and archival**
   - Append 2026-10-30 chronology to the RFC Status and OTP-23 section without rewriting the
     2026-10-29 historical record (`docs/rfcs/2026-08-21-otp-external-devices.md:75-80,1666-1689`),
     and append corresponding current-state sections to the OTP-23 narrative documents rather than
     altering their dated history.
   - Archive this report under the M-03 evidence set, plus safe actual model/session metadata, and
     add a SHA256SUMS covering the published evidence/report/metadata. Do not edit frozen old
     evidence or publish raw model event/error logs.

5. **Required integration checks**
   - Regenerate the matrix document, then run the matrix validator, matrix tests and relevant
     evidence checks. `matrix.py decision` must still exit 1 by design and report exactly
     `NO-GO: 3 row(s) open: W-10, M-04, A-01`, with waived NOT RUN rows exactly
     `W-01, M-01, M-02`; validation/render/tests must pass.
   - Inspect the final diff to confirm that only this bounded integration occurred: no application
     or proof implementation, no production UX, no new signing/reset/waiver, no edits to frozen old
     evidence, and no OTP-24 authorization.

If the implementation deviates from these bounds, changes an expected requirement, alters raw
history, or the checks do not produce the stated results, this pre-authorization does not apply;
stop and request another review instead of broadening the change.
