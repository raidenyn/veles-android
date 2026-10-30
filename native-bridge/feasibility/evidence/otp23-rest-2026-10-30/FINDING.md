# Remaining OTP-23 physical cases — 2026-10-30

**Candidate dispositions, not self-approved acceptance:** M-04, A-01 and W-10 have new physical
evidence for independent Kimi K3.1 Pro review. The parent `openai/gpt-6.1-sol` wrote the operational
code, ran the hardware and curated this set. No reviewer is attributed as implementer/operator.
The matrix has not yet adopted these candidates. No OTP-24 implementation or authorization.

## M-04: actual displayed guidance and same-state no-reset recovery

`m04/result.json` is a new driver **PASS**, `failures=[]`, `guidance_required=true`:

- Same Chrome **25216**, disposable profile, extension ID, synthetic sentinel digest
  `7eb9c0c9…5878b7`, signed binary `e7c716c5…eb91c`, Team `7T39M26Z99`, **one** extension load.
- Baseline: scan started, one own-service test advertiser found, native host closed.
- Owner selected **“VelesOtp23PoC is OFF”** in the attended question. The phase-specific nonce was
  echoed in `m04/owner-off.json`; no new popup observation was supplied, so `prompt=unknown` is retained.
- Native reply: `scan_failed`, `stage=access_denied`, `recoverability=retryable`. The **actual rendered
  connector** had visible, focused guidance with the exact Settings path, app name and retry steps.
- Owner selected **“Saw guidance; switched ON”**, bound to the next nonce (`owner-on.json`).
- Recovered: scan started, one advertiser found, guidance cleared, stop and `shutdown_complete`;
  same extension state, unchanged binary. Profile removed, no surviving host, registration removed.

The exact displayed text is in the raw DOM readback, not just a driver constant. This run never
reset TCC, reloaded the extension, re-signed the Mac fixture, or reinstalled it. The missing prompt
subject is separately established by the already reviewed **M-03** fresh signed-host observation
on this same identity/binary; the historical M-04 unknown prompt is not retroactively rewritten.
The owner-approved M-03 reset happened in a separate earlier run, not as M-04 recovery.

## A-01 M3: duplicate callback on a live physical connection

Adopted inputs: `windows-first/capture/a01-m3.json`, `.trace.log`,
`controller/a01-m3-phone-ack.json`, and `controller/a01-m3-server-final.json`.

Chrome **156.0.8078.76**, medium/unelevated existing administrator account, unchanged Windows host
`8521de4d…ae33`, original sharing implementation running on a real OnePlus 6T:

1. One physical connection, handle **`59a74e60a73d24f5`**; byte-exact 16-byte proof and 4096-byte echo.
2. The instrumentation invoked the **real server callback** with the already-admitted real
   `BluetoothDevice`, `GATT_SUCCESS / STATE_CONNECTED`. This is **controlled callback injection on
   a real link**, not a claim that Android naturally emitted a duplicate, and not a mock device.
3. A nonce-bound phone acknowledgement shows unchanged peer, ready/subscribed sets and original
   connection-admission timestamp after the callback ran. Same native port/handle then exchanged a
   second, different 4096-byte record byte-exact (received digest **`bf321705…6eea67`**).
4. Disconnect/scan stop/shutdown completed, native trace exit 0. Phone recorded exactly three
   received records and the one explicitly labelled injection. No new production test hook.

## A-01 M4: actual CCCD-before-MTU over the radio

`controller/a01-m4-client-retake3.json` and paired `a01-m4-server-retake3-final.json`:

- OnePlus 10 Pro is an actual Android GATT central, 6T the real peripheral; matching run ID.
- First CCCD write callback is **status 6 (`GATT_REQUEST_NOT_SUPPORTED`)**, before any client MTU
  callback. A valid early 4-byte synthetic record write receives no notification during the bounded
  300-ms negative observation; server state remains not-ready/unsubscribed with zero delivered records.
- Explicit MTU request returns **517 / GATT_SUCCESS**; next CCCD write succeeds; a valid record gets
  the expected 8-byte framed response. The server receives **only one** record, after valid setup.
- Client JUnit **OK (1 test)**; server capture closed. This is real ATT/CCCD traffic, not a synthetic
  callback-order injection. The 300-ms absence is bounded, not a universal delivery-absence claim.

Normal Windows MTU 517 precedes CCCD in the fresh M3 trace. For the Mac clause,
`mac-phone-order-corroboration.json` rechecks immutable, already accepted M-08 raw files: on **both
phones**, the actual Android `onMtuChanged(517)` event precedes `subscribed` in the Chrome-owned Mac
run `mac-m08-shutdown-20261029T043432Z`. This is phone-side observation for the accepted Mac run,
**not** an ATT MTU derived from CoreBluetooth's max-write limit. Old evidence is unchanged.

### Android setup history and identity

The first central attempt could not scan (fixture did not declare SCAN; the shell-adopted permission
and ignored app-op did not establish scanning). Subsequent ordinary-permission attempt timed out
while the phone was asleep. Both failed captures/logs remain. Owner then confirmed **“10 Pro is
unlocked”**; the passing run used a normal runtime request, no shell identity adoption.

Only the isolated `.otp23` fixture/test packages were updated, using the **same approved key**.
Actual old/new certificate digest: `2081d17da5ec1a400e710720eba4a16939db774da54cdce936be30f4a848506e`.
The extra SCAN declaration exists only in the explicit external fixture manifest, not production or
default-debug source. Passing APKs: main **`0e32c572…57649b`**, test **`5a8e9e30…bd1b41`**. The sharing
implementation is unchanged. Build commands, source, inputs and signature comparisons are retained.
Both original APKs were restored **with the same key and `install -r -t`**, no uninstall/data clearing:
main **`063ac7ea…677a5e`**, test **`8ca89381…ede1c`**, both phones; see `a01-restoration.json`.

## W-10: three deliberately controlled OS states

All adopted native observations use unchanged host **`8521de4d…ae33`**, Chrome **156.0.8078.76**,
existing administrator account with **medium, unelevated** Chrome/host launch context.

| State | Adopted observation | Actual result |
|---|---|---|
| Radio OFF | `windows-first/capture/radio-off.json` plus matching state transition/restore | `scan_failed / radio_off / retryable` |
| No available adapter | `windows-reboot/capture/adapter-absent-v2.json`, verified disabled device (problem 22) and zero enumerated radios | `scan_failed / no_adapter / fatal` |
| Access denied | `windows-reboot/capture/access-held.json`, within the independently recorded exclusive holder interval | `connect_failed / access_denied / fatal` |

The third case is **controlled exclusive access to the one synthetic GATT service**, not a Windows
privacy-policy or standing user-permission denial. The holder's typed Windows SDK call recorded
`OpenAsync=Success`, `SharingMode=Exclusive`, and successful real notification subscription.
`exclusive-direct.json`: held from **08:20:29.3298086Z**, releasing by dedicated process exit at its
recorded finish. The native denial at **08:20:55–56Z** falls inside that interval. After holder process
**54712** exited, `access-recovered.json` connected and exchanged the byte-exact 4-byte reply on the
same target-hint digest as `access-baseline-v2.json`. This is a fatal **request** classification;
external release and a new request permit recovery. It is not an automatic retry promise.

### Windows setup failures and restoration

- Pre-reboot CIM disable failed; its incorrectly named `adapter-absent.json` still shows radio ON
  and scan started. It is **invalid setup, never absent-adapter credit**. Native PnP utility also
  reported a reboot-pending operation. Readback found persistent disabled flag 1 (pre-test flag was
  not captured, so its original cause is not established). Only that flag was cleared via SetupAPI;
  running Bluetooth remained ON. The owner then selected **“Restarted and signed in”** after an
  explicit request to save work, reboot and use the same existing account.
- Fresh post-reboot disable genuinely succeeded, verified problem 22/zero radios, followed by
  native `no_adapter`. Adapter enabled, persistent disabled flag cleared, radio ON; recovery scan
  succeeded. The automatic restore watchdog was dry-run before changes and removed after recovery.
- Earlier exclusive-holder construction attempts failed on PowerShell WinRT projection/close or
  identifier handling, and one stale-target lookup was unreachable. These are retained setup ERRORs,
  **not native AccessDenied evidence**. Adopted holder uses typed SDK collection access and direct
  `OpenAsync(Exclusive)` on the discovered own service; no forced policy, pairing or new account.
- Final cleanup: no owned process/holder/task or native registration, adapter problem/config flags
  0, radio ON. Both hardware sessions were ended. No automatic PC reboot was issued.

## Review/publication boundary

Fresh local forced Spotless and `spotlessCheck detekt lintDebug testDebugUnitTest protocolCheck`
passed (actual command exits/times in `controller/gate-results.json`). Source/evidence checks and
the independent Kimi review still gate adoption. Earlier Opus reviews do **not** cover this new
connector/driver/instrumentation code. This is an uncommitted worktree; the prior R-01 committed-tree
review must not be silently claimed for it. The latest user-selected reviewer is Kimi K3.1 Pro,
not Opus. No expected physical criterion or waiver is changed by this candidate package.

`provenance.json` declares two precise full own-phone address redactions in duplicate M3 copies;
every other published byte in those files is unchanged. Raw originals and excluded private target
identifiers remain in a protected local backup (not an off-host backup). No APK, key, model API
header/error body, raw model transcript or personal application inventory is published.
