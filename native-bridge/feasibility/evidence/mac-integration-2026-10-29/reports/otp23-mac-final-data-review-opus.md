# OTP-23 Mac final data review: M-08 causal-premise check and M-11 multi-attempt adjudication (Opus 5.5, 2026-10-29)

## 0. Role envelope (safe metadata only)

- **Reviewer:** a fresh Claude Code session, model `claude-opus-5-5`, started for this task. It is
  **not** the implementer `752e1974-0617-470b-a425-dfbda97ac728` and **not** the parent controller
  (`gpt-6.1-sol`). No subagents were used.
- **Not done:** no hardware, Mac, phone, Chrome, TCC, signing, build or registration action. No
  source or evidence edits, no Gradle, no commit, push or GitHub.
- **Scratch:** tmpfs only (`/dev/shm/opusrev`, deleted at the end). Every Python run that read data
  used `python3 -I`.
- **Only file written:** this report.
- **Matrix:** `matrix.py check` still reports `matrix ok: NO-GO`, 23 PASS / 6 BLOCKED / 3 waived.
  Nothing was promoted, and every raw/frozen file and status is preserved.

**Integrity checks (re-run here):**

| item | result |
|---|---|
| `mac-retake-2026-10-29/SHA256SUMS` | 79 entries, `sha256sum -c` OK |
| `mac-release-attributed-2026-10-29/SHA256SUMS` | 6 entries, OK |
| `mac_release_attribution.py` | `69546ee5…` |
| `mac_release_bound.py` | `a49b3450…` |
| M-11 private stage, `m11_dev_capture.py` | `976f61cf…` |
| M-11 private stage, `package-dev.sh` | `fa6ba4e8…` |
| M-11 private stage, test | `50aa32eb…` |
| `all-attempts.tgz` | members byte-equal to the three extracted `m11-capture.json` files and to `owner-answer.json` |

## VERDICT

**M-08:**
- **The parent's concern is correct.** The 16 B *lower* bracket used by Option A,
  `origin ≥ e16 − m16`, is **not guaranteed** by the Android source or API. The earlier proof premise
  is corrected below (§1).
- **The attributed verdicts still stand** under a strictly source-proven premise set (§2):
  - every scenario still has **exactly one** feasible phone→handle bijection, the same one as before
  - the attributed upper origin bound `hi` is unchanged
  - so `DIS_trace_lo`, the margins against the **global** last exchange, and both stop guards are
    **identical**
  - `within exit + 30 s` still passes, with a looser `DIS_trace_hi`
- **Recommendation:** adopt `PASS` / `m08_creditable=true` for shutdown-1, port-disconnect-1 and
  tab-close-1 **on the basis of the corrected-premise recomputation in §2**. Do **not** adopt the
  recorded `lo` / `width_ms` / `DIS_trace_hi` numbers. A small, required code correction (§3) must
  happen before the attribution script is committed or reused as an approved analyzer.

**M-11:** the multi-attempt data satisfies the launch, TCC authorization and scan-result parts of the
criterion, with the caveats in §5. **One piece of required evidence is missing:** a recorded account
class read from real output (§5.4).
- The minimum follow-up is a seconds-long, read-only command capture. It needs no Chrome, TCC, device
  or owner-question loop.
- Once that record exists, I recommend a **scoped M-11 PASS** (§6).
- Until then M-11 stays BLOCKED.

M-03 and M-04 are unaffected.

---

## 1. M-08: is the notify_api stamp guaranteed to come before host receipt? (source and API check)

**Source binding of what ran on the phones:**
- Both phones report
  `installed_app_sha256 063ac7ea…7a5e` (`mac-retake-2026-10-29/provenance.json:36`).
- This equals `sha256(/tmp/opencode/otp23-3dbe3db.apk)`.
- All six transports carry `source_version_name 0.0.3-35-3dbe3db-…`.
- `git diff 3dbe3db HEAD -- app/src/main/java/me/nagaev/veles/sharing/` is empty, and the working
  tree is clean there.
- So the source read below is the source that ran.

### 1.1 Single-fragment 16 B echo: NOT guaranteed

- `SharingGattServer.kt:304-305`: `notifyCharacteristicChanged(...)` returns **first**. Only then does
  `telemetry.notifyResult(...)` run. That takes `epochMillis()` inside `add()` at the end
  (`SyntheticTransportFixture.kt:172-182, 261-267`), after a `@Synchronized` monitor acquisition and
  `sha256Hex` in `peerFor`.
- The API is a synchronous binder call into the Bluetooth process, which queues the notification to
  its own stack threads. Nothing in the API contract or the app source orders the over-the-air PDU
  after the app thread's return from that call.
- Once the return happens, the stamp is further delayed by:
  - binder reply latency
  - app-thread preemption or GC
  - the monitor
  - hashing

  The ±1 ms quantization bounds none of these.
- **Consequence:** for the 20 B single fragment, the radio may deliver, CoreBluetooth may hand over,
  and the host may log `out … type=message` **before** `e16` is stamped. The inequality
  `e16 < m16_h`, and with it `origin ≥ e16 − m16_h`, is an empirical expectation, not a causal fact.
- **The earlier premise is therefore wrong.** It is stated in
  `otp23-mac-m08-physical-review-opus.md` §2: "the phone's echo notify_api happens before the host
  logs out … message". The same sentence for 16 B appears in the frozen `mac_release_bound.py`
  docstring.
- **Corrected statement:** for a single-fragment echo, only `e16 > i16_h` (the upper origin bound) is
  causal.

### 1.2 First fragment of the multi-fragment 4096 B echo: guaranteed

Every captured transport shows the 4096 B echo as **9** `notify_api` events: 8 × 512 B plus 1 × 36 B.
My parser asserted that the payload sums to 4096 B in all 6 transports.

The control flow is all on the main looper (`SharingGattServer.kt:27, 33, 309-321`):

1. The write handler posts. Then `transport.enqueue` and `pump(id)` run (`:121-135`). Inside that
   main-looper message:
   - `nextFragment` sets `awaiting = true` (`GattTransport.kt:179`)
   - `notifyCharacteristicChanged(fragment 1)` is called
   - then `notifyResult` stamps `e_first`
2. Fragment *k+1* can only be produced by `nextFragment`, which returns `null` while `awaiting`
   (`GattTransport.kt:169`). `awaiting` is cleared **only** in `notificationComplete`
   (`:191-192`). That is called only from `onNotificationSentOnMain`, a **later** main-looper message
   (`SharingGattServer.kt:220-226`).
   - Any other `pump` caller (another write, the synthetic push) is also a main-looper message and
     also blocked by `awaiting`. The synthetic push is off: `notify_calls = 10 = 1 + 9`, with no
     unsolicited record.
3. So `e_first` < the call for the final fragment < the final fragment on air < host reassembly (the
   core emits the message only on the final fragment) < the host `out … type=message` stamp.
   - That stamp is taken in the stdout writer *before* `write_frame` (`runtime.rs:79`), on the
     monotonic `Instant` (`trace.rs:50`), floor ms.
4. **Conclusion:** `origin ≥ e_first − m4096_h − 1` is causal. The −1 covers floor quantization of
   both stamps.

The same argument holds for **every non-final fragment**. The penultimate one is the tightest, 22–68 ms
tighter. It is valid but **not needed** (§2).

### 1.3 Upper bounds: guaranteed

- `in … type=send` is stamped in the stdin reader **before** the frame is forwarded to the core, so
  before any GATT write (`runtime.rs:50-53`, trace then `tx.send`).
- The phone must receive the whole record before it can echo.
- So both `e16 > i16_h` and `e_first > i4096_h` hold, even more so because the stamps are taken late.
- **Conclusion:** `origin ≤ e − i_h + 1` for both sizes.

### 1.4 Subscribe anchor: corroboration only

- **Phone:** `subscribed` is stamped *before* `sendResponse` in the same main-looper block
  (`SharingGattServer.kt:190-207`).
- **Host:** `link.notify.notify()` in bluest 0.6.9 calls `setNotifyValue:true` and returns only on
  `peripheral:didUpdateNotificationStateForCharacteristic:error:` with no error
  (`bluest-0.6.9/src/corebluetooth/characteristic.rs:262-305`; crate sha `a0c911a4…`). Only after
  that does `macos.rs:498-503` trace `conn N subscribed` and emit `Connected`, which becomes the
  `out … type=connected connection_handle=…` line. `src/` at HEAD equals cd971f0 for `macos.rs`; the
  only changes are winrt/closejoin/lib.
- **Gap:** the link `phone sendResponse → host delegate callback` relies on CoreBluetooth implementing
  the CCCD enable as an acknowledged ATT Write Request and calling back only on its response. That is
  closed-source OS behaviour. The Android telemetry doesn't record `responseNeeded`.
- **Status:** strong, but **not** source-proven here. It is used below **only as corroboration**.

## 2. M-08: recomputation from the frozen data with only safe inequalities

**Method.** I wrote an independent scratch parser (not the repo modules) that reads the frozen trace,
transports and stops. For each phone *p* and handle *h*:

- `hi(p,h) = min(e16 − i16_h, e_first − i4096_h) + 1` (same as Option A)
- `lo_safe(p,h) = e_first − m4096_h − 1` (16 B lower bound **dropped**)

I enumerated both bijections. Phone epochs are never compared across phones. Wall-clock stability was
re-checked: `max−min(epoch_ms − elapsed_realtime_ms)` is **1 ms** per transport, in 6 of 6.

**Feasibility widths** (ms; negative = empty):

| case | O(6T,h1) | O(6T,h2) | O(10Pro,h1) | O(10Pro,h2) | feasible bijections | unique mapping (= Option A's) |
|---|---|---|---|---|---|---|
| shutdown (h1 `0535`, h2 `e7b2`) | **−230** | 256 | 258 | 504 | 1 | 6T→e7b2, 10Pro→0535 |
| port-disconnect (h1 `5643`, h2 `c710`) | 273 | 533 | **−224** | 282 | 1 | 6T→5643, 10Pro→c710 |
| tab-close (h1 `0939`, h2 `7185`) | **−242** | 270 | 235 | 550 | 1 | 6T→7185, 10Pro→0939 |

**Why uniqueness now rests on one pair per scenario.** In each scenario exactly one phone×handle pair
is infeasible, by 224–242 ms, which is far above any quantum. That single pair rules out one of the
two bijections. The 16 B lower bound used to make the other pair infeasible too; that is no longer
needed.

**What the infeasibility rests on.** Each infeasible pair rests only on single-clock differences:

- **Phone clock:** `e_first − e16` on one phone.
- **Host clock:** `m4096_h − i16_h` on one host trace.

Example (shutdown, 6T vs `0535`):
- 6T's first 4096 fragment came **945 ms** after its own 16 B echo.
- `0535`'s whole 4096 B message reached the host only **713 ms** after `0535`'s 16 B send.
- So 6T cannot be `0535`.

**Release checks under the unique mapping (trace ms):**

| case / phone | DIS_trace (safe) | margin vs **global** last | exit + 30 s | stop > DIS | stop ≥ hi + exit + 30 s |
|---|---|---|---|---|---|
| shutdown 6T | [4143, 4399] | **+84** (4059) | ≤ 34139 ✓ | ✓ | ✓ |
| shutdown 10Pro | [4152, 4410] | **+93** | ✓ | ✓ | ✓ |
| port-disconnect 6T | [6036, 6309] | **+85** (5951) | ≤ 36032 ✓ | ✓ | ✓ |
| port-disconnect 10Pro | [6005, 6287] | **+54** | ✓ | ✓ | ✓ |
| tab-close 6T | [5167, 5437] | **+54** (5113) | ≤ 35190 ✓ | ✓ | ✓ |
| tab-close 10Pro | [5173, 5408] | **+60** | ✓ | ✓ | ✓ |

**The decisive numbers are identical to the recorded `release-bound-attributed.json` values:**
- `DIS_trace_lo` 4143/4152, 6036/6005, 5167/5173
- margins 84/93, 85/54, 54/60

That is because they depend only on `hi`. The global-last-exchange clause, `exit + 30 s` and both stop
guards are unchanged. No default was weakened, and no optimistic mapping was chosen.

**Corroboration only** (§1.4 subscribe anchor added as a lower bound):
- The same unique mapping results.
- Widths are 52–69 ms, matching Option A's 49–63 ms.
- With the penultimate fragment added, it is still the same mapping.

**Hull results are unaffected.** The frozen hull's release checks also use only its own (looser) `hi`.
Its 16 B lower bracket enters only the fail-closed empty-intersection test. So shutdown-1's hull PASS
is not invalidated, and the port/tab hull FAILs stay recorded as they are.

**Measurand caveat (unchanged, stated for honesty).**
- `DIS_phone` is the app-observed `disconnected` stamp: a posted `onConnectionStateChange` →
  `remove()`. That is later than the controller-level link drop.
- "Release after last proven-alive exchange" is therefore a statement about the phone-observed
  release, the same measurand as the Windows rows. It is not a controller LL-terminate timestamp.
- The +54 ms minimum margin is a recorded quantity, not a bound on that internal delay.

**What is not adoptable from `mac-release-attributed-2026-10-29/`:**
- **Overstated precision:**
  - `origin_interval.lo`
  - `width_ms`
  - `bracket16[0]`
  - `dis_trace_interval[1]`
  - `margin_vs_exit_plus_bound_ms`

  Wherever `lo` came from `bracket16`, these overstate precision. In port-disconnect both phones'
  `lo` comes from `bracket16`.
- **Invalid premise in prose:** `FINDING.md` and `derivation-record.json` state the invalid 16 B
  premise.
- **The folder stays untouched as history.**
- **Binding values:** the parent's adjudication should cite §2 above as the corrected-premise values.

## 3. M-08: required code correction before the attribution script is committed or reused

**Why it is required even though the frozen verdicts survive.**
- The script can deem a pair infeasible from an unproven bound.
- In a future run, a post-return stamp delay could then eliminate the **true** mapping and leave a
  wrong one "unique". That would be an optimistic attribution.
- So the script as written is unsound as a reusable approved analyzer.

**Minimal patch to `scripts/mac_release_attribution.py`:**
- `candidate()`:
  - record the 16 B lower value as `bracket16_lower_not_causal_recorded_only`
  - use `lo = e4096 − m4096 − quantum` and `hi = min(e16 − i16 + quantum, e4096 − i4096 + quantum)`
  - feasibility is `lo <= hi` on those two values
- Docstring: replace the `O(p,h)` formula and add the §1.1/§1.2 justification, with source line
  references.
- **Do not edit** the frozen `mac_release_bound.py`. Its premise sentence is corrected by this
  review's record only.

**Test changes (`test_mac_release_attribution.py`):**
1. **Frozen replay:** update the expected widths and `DIS_trace_hi` to the §2 table. `DIS_trace_lo`,
   margins, mappings and verdicts stay asserted unchanged.
2. **New synthetic test:** a case where only the 16 B lower bound separates the two mappings must now
   come out **ambiguous**, so the hull verdict stands.
3. **New mutation:** re-adding the 16 B lower bound must fail the new test.
4. Re-check the existing hand-computed synthetic cases (unique, zero-feasible, global-vs-own,
   quantization) and recompute their expectations under the new `lo`.
5. Keep M1–M4.

**Outputs.** Regenerate into a **new** derived folder, e.g. `mac-release-attributed-safe-2026-10-29/`.
Keep `mac-release-attributed-2026-10-29/` byte-preserved as superseded history.

**Review gate.** One independent micro-review of that diff and its replay is needed.

**If the parent prefers not to change code:** the adjudication may rest on §2 alone. In that case the
current script must **not** be committed as an approved method; label it superseded (premise invalid).

**No new hardware is needed for M-08.** The safe existing anchors suffice.

## 4. M-11: chronology (from the records; UTC)

| time | event | source |
|---|---|---|
| 05:26:31–05:26:32 | **Attempt 1.** User-scope manifest `path` is the **relative** `target-dev/VelesOtp23Dev.app/Contents/MacOS/otp23-host` (from the reviewed runbook's `--stage .`, README-M11-PREP.md:82). `hello` → synthesized `port_closed`. No scan. | `m11-attempt-1/m11-capture.json` `9f9e33d2…` |
| 05:29:57 | **Attempt 2** starts with absolute `--stage`. `hello` ready. | `m11-attempt-2/m11-capture.json` `209fe71d…` |
| 05:29:58 | Nonce `ac6b44f7-…` request issued | same |
| +10.1 s | Scan reply `scan_failed / permission_pending / retryable` | same |
| 05:42:00 | Attempt 2 ends: `ended_by: deadline`, `waited_s 720.9`, `answer null`, `rescans 0`, `pending_at_host_budget_not_denial`. `shutdown_complete`. | same |
| 06:18:28 | Answer file written: `dialog_observed true`, `prompt_name_shown "VelesOtp23Dev"`, `choice allow`. That is **36 min after** the capture ended. The Allow click time itself is not recorded. | `owner-answer.json` `0bda401f…` (= in-dir copy) |
| 06:21:17 | **Attempt 3**, same dev binary `c36894dc…` and identity. Nonce `8a055ccc-…`. | `m11-attempt-3/m11-capture.json` `f04c9045…` |
| +1.0 s | Scan reply `scan_state status started`, handle `361362ad…` | same |
| 07:21:19 | Attempt 3 ends: `ended_by: deadline`, `waited_s 3600.3`, `answer null`. Scan stopped, `shutdown_complete`. | same |
| 10:38 | Owner says `dialog_observed: false` for `8a055ccc`, **3 h 17 min after** the run ended. Written to `owner-answer-attempt3.json` and a leftover, never-renamed `m11-attempt-3/.owner-answer-8a055ccc.tmp`. Read-only Settings check: "Still enabled, unchanged" (VelesOtp23PoC). | `owner-answer-attempt3.json`, `original-grant-owner-check.json` |
| 10:45:35 | `observation-timeline.json` compiled | — |

**Unchanged in every attempt, recorded before and after each:**
- approved fixture `e7c716c5…`, `codesign --verify --strict` true
- no user-scope `plain` or `.dev` manifest, `all_veles_named []`
- disposable profile removed
- Chrome/156.0.8078.74, uid 501 over ssh
- extension id matches

**Dev bundle:**
- `CFBundleIdentifier me.nagaev.veles.bridge.dev`, `CFBundleName VelesOtp23Dev`
- `codesign -dvv`:
  - `flags=0x20002(adhoc,linker-signed)`, `Signature=adhoc`
  - `TeamIdentifier=not set`, `Info.plist=not bound`
  - `Identifier=otp23_host-b86638eeb61c91b4`
- That is the automatic linker ad-hoc signature, recorded truthfully. There is no Apple Development
  signature.
- Raw `c36894dc…` matches M-03's recorded unsigned counterpart of the cd971f0 fixture.

**Late-file labelling.** Both late answer files and the `.tmp` sit inside the attempt directories and
inside `all-attempts.tgz`, but they are **post-run controller artifacts**. They must be labelled as
such and never presented as capture inputs. The capture JSONs (`answer: null`) are authoritative for
what each run accepted.

**Script provenance gap.** The capture JSON does not self-record the script's sha. "Unchanged reviewed
script `976f61cf…`" is controller-attested; the local stage copy matches. Record that as attestation,
not measurement.

## 5. M-11: adjudication against the expected criterion

> Expected: "The unsigned dev-registered binary is launched by Chrome from a disposable profile; its
> TCC authorization and scan result are recorded and compared with M-03..M-06; account class recorded
> truthfully (admin or standard)."

### 5.1 Launch by Chrome from a disposable profile: satisfied (attempts 2 and 3)

- The extension in a fresh `--user-data-dir` profile got `hello ready` through Native Messaging
  `me.nagaev.veles.otp23.dev`.
- At that moment the only Veles user-scope manifest was the dev one, pointing at the absolute dev
  bundle path.
- The plain manifest was absent before and after. So the signed fixture could not have answered.
- **Attempt 1 is a setup FAIL** (relative manifest path), retained. It is **not** an unsigned/TCC
  rejection.
- **Process facts:**
  - The packager exit 126 (not executable, then run via `bash`) is a runbook defect with no effect on
    the bytes.
  - The relative `--stage .` was a defect in the reviewed runbook, which the earlier approval missed.

### 5.2 TCC authorization: recorded, with qualification

**Attempt 2: `not_determined` (host-measured).**
- `permission_pending` is emitted **only** when `CBManager.authorization == NotDetermined` at the 10 s
  adapter-wait timeout (`macos.rs:181-192`, `macos_map.rs:111-115`, same source as cd971f0).
- So the host's own reply is a deterministic read of the authorization enum.
- It is classified pending, not denial.

**Owner observation (operator-captured, nonce-keyed, delivered late, NOT accepted by the run).**
- The dialog named **VelesOtp23Dev**; the owner chose **Allow**.
- This is an external observation about the **dev** identity's prompt. It must not be represented as
  part of the attempt-2 capture.

**Attempt 3: authorized (inferred from the actual reply).**
- `scan_state started` is reachable only after `adapter.wait_available()` succeeds. In bluest 0.6.9
  that requires `CBManagerState.PoweredOn` (`corebluetooth/adapter.rs:110-121`), and CoreBluetooth
  doesn't report PoweredOn to an unauthorized client.
- The reply came at 1.0 s, versus the 10.1 s budget expiry in attempt 2.
- `authorized_started` is therefore a sound classification of the actual reply.

**Qualification.**
- No explicit `allowed_always` enum readback exists for the dev host. The trace is absent.
- **Probable cause:** `VELES_OTP23_TRACE` was set to the *relative* `capture/m11-attempt-N/…`. The
  host resolves it against its own working directory, and `trace.rs:27` silently skips the trace when
  the open fails (no parent creation).
- That cause is unverified; the absence itself is a fact.
- `post_init_authorization: null` means "not traced". It does **not** mean "unknown state", and no
  enum may be written in its place.

**State transition.** `not_determined` (attempt 2) → owner Allow (reported) → authorized (attempt 3).
No TCC reset or toggle occurred in between. The only identity-relevant action between the two
measurements is the owner-reported Allow, so the transition is consistent with it and with nothing
else recorded.

### 5.3 Scan result and comparison: satisfied for authorization and scan start; discovery not compared

| item | signed fixture `e7c716c5…` (M-05/M-06, M-08 retake ×3 traces) | unsigned dev `c36894dc…` (M-11) |
|---|---|---|
| launched by Chrome from a disposable profile | yes | yes (attempts 2–3) |
| initial authorization | historic grant; not re-measured | `not_determined` (host reply, attempt 2) |
| prompt shown / name | **unobserved** for the original grant (M-03) | owner-reported "VelesOtp23Dev" (late, external) |
| post-grant authorization | `allowed_always` (traced) | authorized ⇒ `scan_state started` (reply; no trace) |
| scan | `scan_state started` | `scan_state started` |
| discovery | `device_found` (phones advertising) | **not measured**: no phone advertised; **not claimed** |
| grant lifetime | Settings entry VelesOtp23PoC, unchanged after M-11 (owner read-only check) | the dev grant persists on the account (no TCC action taken); Settings entry not inspected |

**On discovery.** The M-11 text asks for "TCC authorization and scan result". The authorization-gated
result, scan start, is present. Discovery depends on whether advertisers are present, and it is not
the identity variable. I therefore do **not** require it, but it must be listed as not compared.

### 5.4 Account class: NOT recorded (precise missing evidence)

- The only "admin" in the capture is the literal string in the script's `"what"` field:
  `"existing admin account"` (`m11_dev_capture.py:336`). That is a hard-coded label, not a reading.
- No file in `otp23-m11-controller-20261029/` or elsewhere under `/tmp/opencode` holds `id -Gn` output.
  I searched.
- The controller's statement that it read `id -Gn` is therefore unrecorded. The criterion clause
  "account class recorded truthfully" is not yet met by evidence.

### 5.5 Boundaries kept

- **M-03:** the original signed fixture's prompt owner stays **unobserved**. The dev dialog name says
  nothing about who owned the original VelesOtp23PoC prompt, and it is not transferred.
- **M-04:** the tied clause is not closed. No TCC reset or permission toggle was approved or done.
- **Attempt 3:** the late "no dialog" answer is **not** a during-run observation and is not bound in
  the capture. Its deadline and `problems` stay recorded as they are.
- **Raw statuses:** none are changed. The three captures keep `ended_by: deadline` and their
  `problems`. Attempt 2 keeps `pending_at_host_budget_not_denial`.
- **M-11 PASS** would come only from a separate manual adjudication record, not from editing raw data.

## 6. M-11: minimum follow-up and recommended adjudication

### 6.1 Required: one read-only account-class record

- **Command** (seconds, no Chrome/TCC/device, no owner question), on the same Mac account (uid 501)
  that ran the attempts:
  `date -u +%FT%TZ; id -un; id -u; id -Gn; dseditgroup -o checkmember -m "$(id -un)" admin`
- **Where:** save the verbatim stdout/stderr, with its sha, as
  `otp23-m11-controller-20261029/account-class.txt`.
- **Label:** operator-captured, post-run, same account. Group membership is not a per-run variable.
- **Earlier output:** if the controller still holds the earlier `id -Gn` output verbatim, it may be
  recorded with its original time and labelled as such. Never reconstruct it.

### 6.2 After 6.1: scoped M-11 PASS (manual adjudication record)

**Scope:** "unsigned (linker ad-hoc) dev-registered host `c36894dc…`, distinct identity
`me.nagaev.veles.bridge.dev` / `me.nagaev.veles.otp23.dev`, on the existing <recorded class> account."

**Measured facts to state:**
- launched by Chrome from a disposable profile
- authorization `not_determined` → (owner-reported prompt "VelesOtp23Dev", Allow) → authorized, with
  `scan_state started`
- compared with signed `e7c716c5…`: `allowed_always` + scan started

**Caveats to carry:**
1. Attempt 1 is a setup FAIL (relative manifest path), retained.
2. Attempts 2 and 3 ended by deadline. The owner answers arrived 36 min and 3 h 17 min after their
   runs and were never accepted by the script. They are external observations.
3. There is no dev host trace and no explicit enum. The authorized state is inferred from
   `permission_pending` semantics and scan start.
4. Discovery was not compared (no advertiser).
5. The script hash is controller-attested.
6. The dev grant persists.
7. M-03 and M-04 are untouched.

**Not required:** a new device or TCC attempt.

### 6.3 Optional: for any future M-11-style run (not required now)

- Pass absolute `--stage` and `--out-dir` (or make the script resolve both with `.resolve()` before
  building the manifest and `VELES_OTP23_TRACE`).
- `chmod +x package-dev.sh`.
- Let `authorized_started` end the watch after a short bounded grace, and take the owner's
  dialog/no-dialog answer **before** launch where possible. The docstring at `m11_dev_capture.py:233`
  says "(or an authorized started scan)", but the code requires an answer. That is what produced the
  3600 s wait with the scan already started at 1 s.
- Self-record the script sha in the capture.

## 7. Matrix

The matrix stays **23 PASS / 6 BLOCKED / 3 waived, NO-GO**.

**Row decisions belong to the parent:**
- **M-08:** creditable on §2 (corrected premise), with §3 required before code reuse or commit.
- **M-11:** scoped PASS once §6.1 exists.

Nothing in `acceptance/`, `docs/`, the evidence folders or `/tmp/opencode` sources was modified by
this review.
