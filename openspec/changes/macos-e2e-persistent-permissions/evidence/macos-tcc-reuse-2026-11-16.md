# macOS Bluetooth TCC reuse — experiment record (2026-11-16)

Machine: MacBook ("Sirius"), macOS 26.5.2 (25F84), Apple Silicon, user uid 501 (admin).
Purpose: establish whether one Bluetooth grant can be reused across rebuilds and paths, and which
launch paths defeat it. Read-only except one person-approved Bluetooth grant; no TCC reset, no
keychain or ACL change, no key export.

**Name note:** this run used the app name `VelesOtp23PoC.app` (what `macos-test-bundle.sh` produced
at the time). The canonical name is now `VelesOtpDebug.app`; the bundle identifier and designated
requirement are unchanged, so the identity results below apply to both names. The post-rename run is
recorded at the end.

## Precondition

`tccd` recorded `TCCAccessResetInternal` (a deliberate `tccutil reset BluetoothAlways`) for three
app identities before the experiment, so no grant existed at the start:

| local time | identifier |
| --- | --- |
| 10:57:19 | `me.nagaev.veles.otp23.poc` |
| 10:57:21 | `me.nagaev.veles.bridge` |
| 10:57:23 | `me.nagaev.veles.bridge.dev` |

## Existing test bundles (the observed problem)

| bundle on the Mac | identifier | signature | designated requirement |
| --- | --- | --- | --- |
| `~/homepc/work/veles-otp11/VelesNativeBridge.app` | `me.nagaev.veles.bridge` | ad-hoc | `cdhash H"561c66cd2c9d2d181b66feed8f5c97a49e3e4e85"` |
| `~/homepc/work/otp10-host/VelesNativeBridge.app` | `me.nagaev.veles.bridge.dev` | ad-hoc | `cdhash H"bd4954d1e9ce9da0616d19dc1493ffed9fd2f956"` |
| `~/homepc/work/otp23-m11-dev/target-dev/VelesOtp23Dev.app` | linker identifier | ad-hoc | `cdhash H"f6bb74327e770f29e7fc86ed0d7c0293ab490e82"` |

Two ad-hoc bundles of the same probe source, built from different binaries, also had different
identifiers and requirements (`probe-v1` → `cdhash H"1ee11b32..."`, `probe-v2` →
`cdhash H"5c93c265..."`). An ad-hoc rebuild is therefore a new TCC identity.

## Canonical signed test identity

Bundle: `VelesOtp23PoC.app`, identifier `me.nagaev.veles.otp23.poc`, `CFBundleName VelesOtp23PoC`.
Certificate `Apple Development: yuriy@nagaev.me (ZWYYS8X98S)`, Team `7T39M26Z99`.
The designated requirement was identical for every build:

```text
identifier "me.nagaev.veles.otp23.poc" and anchor apple generic and certificate
leaf[subject.CN] = "Apple Development: yuriy@nagaev.me (ZWYYS8X98S)" and
certificate 1[field.1.2.840.113635.100.6.2.1] /* exists */
```

## Runs

All probe bundles and the real host bundle were launched with LaunchServices (`open -n --stdout ...
--stderr ... App.app --args ...`) from the Mac's logged-in GUI session. Signing ran in that session
(the login keychain); signing over SSH is not used.

1. **Probe v1** (source sha256 `8b483650...`, signed executable `deffdf8f...`), first launch:
   `authorization_before = not_determined`, `authorization_after = allowed_always`,
   `central_state = powered_on`, waited 11.32 s for the one-time Allow, finished 04:15:42Z
   (`evidence/probe-grant-v1.json`). `tccd` at 11:15:42: `TCCDEvent type=Create`,
   `kTCCServiceBluetoothAlways`, `identifier=me.nagaev.veles.otp23.poc`; the stored requirement
   matched with `SecStaticCodeCheckValidity ... status: 0`; result
   `ReqResult(Auth Right: Allowed (User Consent))`.

2. **Probe v2**, a different binary (source sha256 `7a761575...`, signed executable `7327c23a...`)
   in the same bundle identifier and path: `authorization_before = allowed_always`,
   `authorization_after = allowed_always`, waited 0.27 s, finished 04:17:54Z, no prompt
   (`evidence/probe-grant-v2.json`).

3. **Probe v2 copy** at a different path (`.../out2/VelesOtp23PoC.app`): `allowed_always`, waited
   0.26 s, finished 04:18:20Z, no prompt (`evidence/probe-grant-copy.json`).

4. **No prompt after the grant:** `log show --start "2026-11-16 11:16:00" --info --predicate
   'process == "tccd" AND eventMessage CONTAINS "CFUserNotification" AND eventMessage CONTAINS
   "me.nagaev.veles"'` returned only the header — no prompt record for any Veles identifier.

5. **Real production host.** `native-bridge/host/scripts/macos-test-bundle.sh` built
   `real/VelesOtp23PoC.app` from the production `veles-native-host` (source executable sha256
   `cce923c7...`, post-sign `574a6b7c...`, cdhash `704413274f51391938103746c7285a411aa86fe8`). Its
   designated requirement is byte-for-byte the requirement above, so it matches the stored grant.

6. **Real host, real CoreBluetooth.** A driver app carrying the same identifier and certificate was
   launched through LaunchServices and spawned the real `veles-native-host` from the real bundle. The
   native-messaging transcript is `ready` → `scan_state started` → `scan_state stopped` →
   `shutdown_complete`, host exit 0, finished 04:20:52Z
   (`evidence/real-host-driver-result.json`). `tccd` recorded subject
   `me.nagaev.veles.otp23.poc` and `Allowed (User Consent)`; no prompt.

7. **Negative control: direct execution from SSH.** The same real host, executed directly over SSH,
   replied `scan_failed` with `stage: permission_pending`. `tccd` attributed the request to
   `responsible=com.apple.sshd-keygen-wrapper` and resolved `AUTHREQ_SUBJECT
   subject=/usr/libexec/sshd-keygen-wrapper` (authValue 1, unknown), not to the host bundle, even
   though the grant existed. Launch attribution, not only the signature, decides reuse.

## Evidence validity

The exact capture predicate was:

```text
log show --start <before launch> --end <after first scan> --style ndjson --info \
  --predicate 'process == "tccd" AND (eventMessage CONTAINS "<bundle id>" OR \
  eventMessage CONTAINS "<executable path>" OR eventMessage CONTAINS "kTCCServiceBluetoothAlways")'
```

The raw windows are preserved in `evidence/logs/`:

| file | window (local) | what it shows |
| --- | --- | --- |
| `grant-v1.ndjson` | 11:15:25–11:15:45 | the one-time `AUTHREQ_PROMPTING` and the `TCCDEvent Create` |
| `reuse-v2.ndjson` | 11:17:52–11:17:58 | coverage records, no prompt and no grant write |
| `reuse-copy.ndjson` | 11:18:17–11:18:23 | coverage records, no prompt and no grant write |
| `real-host.ndjson` | 11:20:50–11:20:56 | coverage records, no prompt and no grant write |
| `ssh-negative.ndjson` | 11:19:25–11:19:40 | subject `sshd-keygen-wrapper`, not the host bundle |
| `no-prompt-after-grant.ndjson` | 11:16:00–11:22:00 | `{"count":0,"finished":1}` for `CFUserNotification`/`AUTHREQ_PROMPTING` filtered on `me.nagaev.veles`; a convenience filter, not the coverage evidence |

The service clause is deliberate: a `TCCDEvent` grant write carries the service and identifier but
may carry no executable path, so a path-only filter would drop it. Each reuse window contains the
positive control — `AUTHREQ_SUBJECT subject=me.nagaev.veles.otp23.poc`, its `staticCode` record and a
`Handling access request to kTCCServiceBluetoothAlways` record — and a separate identifier/service
search finds no `TCCDEvent Create/Delete` and no `AUTHREQ_PROMPTING` for that subject. `Allowed (User
Consent)` in a `Handling access request` line is the stored consent, not a new click; a new grant is
the `TCCDEvent Create`.

## Implementation verification (2026-11-16, 11:45–11:49 local)

After the rule was written, the current host source was rebuilt and re-verified through the pinned
harness:

- source: `native-bridge/host` at `ab15de4`, synced to the Mac and built with
  `cargo build --locked --release --bin veles-native-host` (cargo 1.98.0);
- source executable sha256 `a1314f776f76a8b623ade70063ddb629c1838a1f94698b84b8c251c90262ee38`,
  post-sign `e7f5f65403c1c1dbd25d28a6efce45af0e5660c77a46424d0773d957a9880800`, cdhash
  `e1264e695cb72ae0809d02fde4a6b399c7ce92c1`;
- packaged by `native-bridge/host/scripts/macos-test-bundle.sh -b <binary> -o <fresh dir>`; its
  `codesign -d -r-` equals the baseline above;
- the preserved driver (`evidence/mac-tcc-probe/`) launched the real host from that bundle:
  `ready → scan_state started → scan_state stopped → shutdown_complete`, host exit 0
  (`evidence/impl-real-driver-result.json`);
- the TCC window (45 records, `evidence/logs/impl-real.ndjson`) contains 4 `AUTHREQ_SUBJECT` records
  for `me.nagaev.veles.otp23.poc` and 3 `Handling access request to kTCCServiceBluetoothAlways`
  records, with 0 `AUTHREQ_PROMPTING`, 0 `CFUserNotification` and 0 `TCCDEvent` `Create`/`Delete`.

No prompt appeared and no grant was written during the run.

## Rename verification (2026-11-16, 12:18 local)

The test bundle was renamed from `VelesOtp23PoC.app` to `VelesOtpDebug.app` (same bundle identifier,
same signing identity):

- `macos-test-bundle.sh` now sets `APP_NAME=VelesOtpDebug.app`,
  `RECORD_NAME=VelesOtpDebug.identity.txt` and `CFBundleName VelesOtpDebug`; its shell test passes
  (`68 checks, 0 failed`).
- The same host binary was packaged into `impl-debug/VelesOtpDebug.app`; its `codesign -d -r-` equals
  the baseline above and `CFBundleName` is `VelesOtpDebug`.
- The pinned driver launched the real host from that bundle: `ready → scan_state started →
  scan_state stopped → shutdown_complete`, host exit 0
  (`evidence/impl-debug-driver-result.json`).
- The TCC window (`evidence/logs/impl-debug.ndjson`, 45 records) has 4 `AUTHREQ_SUBJECT` records and
  3 Bluetooth handling records with 0 `AUTHREQ_PROMPTING`, 0 `CFUserNotification` and 0 `TCCDEvent`
  `Create`/`Delete`.

The rename did not change the designated requirement or create a prompt; the existing grant was
reused.

## Limits

- The exact harness sources and invocations are preserved in `evidence/mac-tcc-probe/` (README): a
  LaunchServices driver for the real host, the authorization probe, and the direct-SSH negative
  control. They are evidence tooling, not production tools.
- The experiment used a minimal probe and a driver app with the canonical identity. The grant is
  per bundle identifier + designated requirement, so the real app with the same requirement reuses
  it; the real host's own scan is recorded above.
- Chrome's native-messaging launch was not re-run in this experiment. The repository already
  records a Chrome-launched signed host as the TCC subject and responsible process
  (`native-bridge/feasibility/evidence/mac-m03-prompt-2026-10-30`), which is why the rule names that
  route as the primary one.
- No claim is made here about the production distribution identity (CI credentials, Developer ID,
  notarization); the Apple Development test identity is test provenance only.
