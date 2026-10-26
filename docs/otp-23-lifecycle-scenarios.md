# OTP-23 lifecycle scenarios and M6 (two centrals, one phone)

Driver: `native-bridge/feasibility/scripts/scenario_matrix.py` (CLI + platform adapters),
`scenario_matrix_core.py` (pure logic), `test_scenario_matrix.py` (mocked tests). It covers only what
`physical_validate.py` / `macos_poc.py` do not: link-active teardown paths, the direct stdin-EOF case and the
M6 role driver. It does **not** repeat the two-phone size matrix.

Everything is foreground (no background exemption is measured), uses a fresh disposable Chrome profile and
DevTools port per scenario, the existing registered host name/origin/extension id, and creates no server,
service or installer. Only processes this run owns are ever terminated (a host counts as owned only when our
Chrome is in its ancestry, or it is our own `Popen` child). Pre-existing host processes are recorded and left alone.

This driver adjudicates nothing in `acceptance/otp23-matrix.json`; the parent records matrix rows from the
result files. New-account tests stay waived/NOT RUN; no other waiver is implied.

## Scenarios (`lifecycle --scenario ...`)

Every Chrome scenario discovers two phones, connects both, **proves each link with an exact echo** (never assumes an
active link), then starts a fresh scan so scan + two links are all active when teardown begins. Fewer than two
phones is `BLOCKED / insufficient_phones`, never PASS.

| Scenario | Teardown | Verified |
|---|---|---|
| `shutdown` | unawaited `connect` to an absent device is pending, then `shutdown` | pending request terminated (error, none left in the connector), `shutdown_complete` is the single, last host output in the host trace, exit 0 with stdout drained, host + Windows `cmd.exe` wrapper gone, zero host windows and network endpoints; the `cmd.exe` wrapper must also own no visible window and no network endpoint (a hidden console is allowed and recorded separately) |
| `port-disconnect` | `port.disconnect()` | host + wrapper gone, **no** `shutdown`/`shutdown_complete` in the trace; the mechanism is recorded as `stdin_closed` (host logged its own EOF exit) or `no_exit` (host gone without an exit line, i.e. killed by Chrome) rather than assumed |
| `tab-close` | `Target.closeTarget` on the connector tab | same checks as `port-disconnect` |
| `stdin-eof` | direct native-framed process (registered binary, origin argument, pipes): hello, start_scan, find, connect 2 phones, echo proof, rescan, **close stdin (not shutdown)** | exit 0, stdout read to EOF on a frame boundary, process gone, trace `exit` line with `stdout_drained=true`, no `shutdown_complete` |
| `stdin-eof-truncated` | direct process, stdin closed after 2 of 4 header bytes (no phones needed) | exit 2, framing-violation trace stage, stdout ends on a frame boundary |
| `failure-isolation` | disconnect one link, stale send, survivor exchange, shutdown | `unknown_handle`, survivor 4096-byte exact echo, clean shutdown |
| `adapter-toggle` | none | always `BLOCKED / requires_operator`: toggling the Bluetooth adapter needs an explicit user action; the driver never does it |

`stdin-eof*` launch the host directly, so on macOS Bluetooth TCC is attributed to the launching process, not Chrome.
Each such result carries `provenance.chrome_provenance: false` and the context note; it never counts as Chrome
provenance evidence. No TCC reset, no signing substitution (see CLAUDE.md "Stable Signing Identities").

Result shape (every scenario): `id`, `title`, `expected`, `actual`, `status` (`PASS|FAIL|BLOCKED|PARTIAL`),
`error_class` (`none, insufficient_phones, ambiguous_target, target_not_found, permission_denied, unsupported,
environment, host_error, timeout, assertion, requires_operator`), `detail`. Permission denials classify as
`permission_denied` (BLOCKED), never a pass or a fabricated outcome. Per-scenario safe host traces
(`<scenario>.host-trace.log`) hold lengths, stages and handles only.

## M6 (`m6` on each central, then `m6-adjudicate`)

One central serves **one** phone; both real centrals (Windows and Mac) run concurrently against the **same** phone.
Both runs discover; neither picks a peripheral silently. Choose exactly one of:

* `--target-label TEXT`: exact advertised label of the shared phone, or
* `--sole-advertiser`: the parent isolated advertising to exactly one known physical phone (record the action with
  `--isolation-action`; it is an operator assertion).

Same-phone identity is **not** a typed label: both centrals must report a non-empty, equal `target_label_sha256` taken from
the actual advertisement (a central whose selected advertisement has no label is `BLOCKED`). The parent-supplied
`--phone-label` (physical model) must agree but alone proves nothing.

Required: `--role windows|mac` (must match the platform), `--pair-run-id ID` (same on both), `--phone-label`, and exactly
one barrier. **`--barrier-file PATH` is the real barrier**: after both `READY` stage files exist, the parent writes the same
JSON `{"nonce": "<random>", "epoch_ms": <parent time>}` to that configured path on both machines. Each central stores the
nonce **hashed**, its stage `plan_sha256`, the stage write time and its actual release time. `--start-at-epoch` is
diagnostic only (no nonce) and can never support an M6 result. Existing results are never overwritten without `--overwrite`.

Flow per central: fresh Chrome + host, discover, select, connect **one** link (the scan stays active while connecting, as
`physical_validate.py` does; `--connect-order scan-stopped` reverses that), stop the scan, write the hash-only
`<out>.stage.json` (also prints `READY <hash>`), wait for the barrier, then 20+ (`--exchanges`, minimum 20) 4096-byte
exchanges, each independently seeded (`seed = base + 13*i mod 256`; defaults Windows 41, Mac 173, `--seed-base` overrides),
verifying exact echo bytes, connection handle and routing and recording start/end wall-clock ms, size, seed and SHA-256
digests (no bytes). Optional isolation: `--run-until-epoch T` keeps one central exchanging; `--disconnect-at-epoch D`
disconnects the other central's link at D and records the time (a disconnect without a recorded time fails the check).
A clean single central reports `PARTIAL` (own part only), never an M6 pass.

`m6-adjudicate --windows W.json --mac M.json --clock-offset-ms N --clock-uncertainty-ms U --clock-provenance TEXT` is pure.
The clock offset (windows minus mac), its uncertainty and how the parent measured them are **required**; missing means
`BLOCKED`, never an assumed zero. It requires: both roles once, same pair id, equal non-empty advertised-label hashes, one
link per central (two phones on one central is rejected), 20+ exact exchanges, no system errors or unexpected
disconnects, the same barrier nonce hash on both with READY staged before release and release times within
`U + --barrier-tolerance-ms` (default 2000), and overlapping real I/O: overlap >= `--min-overlap-ms` (default 1000) + 2U,
counting only exchange pairs that overlap by more than 2U (default >= 3 pairs).

Android notify return codes are not adjudicated here: phone transport evidence (`otp23-transport-evidence.json`, from the
new debug fixture) is collected separately and the parent judges it by hand. The adjudication always lists
`android_notify_return_codes` as `BLOCKED` and `m6_discharged` is always `false`; there is no
override. A clean desktop result is `verdict: PARTIAL` with `desktop_concurrency: PASS`. The parent decides the matrix row.

## Commands

Run from `native-bridge/feasibility`, with the host registered by `register-host.ps1` / `macos-register.sh` and the Veles
fixture advertising on the phone(s). Python 3.9+ stdlib only.

**Windows (interactive desktop session, same user that registered the host):**

```powershell
python scripts\scenario_matrix.py lifecycle --scenario all --out-dir evidence\lifecycle-windows
python scripts\scenario_matrix.py m6 --role windows --pair-run-id RUN1 --phone-label "OnePlus 6T" `
    --sole-advertiser --isolation-action "only the 6T advertises" --barrier-file C:\Temp\m6-release.json `
    --out evidence\m6-windows.json
```

**macOS (Aqua session: Terminal in the logged-in GUI session, same user and approved signing identity/bundle; no ad-hoc
substitution):**

```bash
python3 scripts/scenario_matrix.py lifecycle --scenario all --out-dir evidence/lifecycle-mac
python3 scripts/scenario_matrix.py m6 --role mac --pair-run-id RUN1 --phone-label "OnePlus 6T" \
    --sole-advertiser --isolation-action "only the 6T advertises" --barrier-file /tmp/m6-release.json \
    --out evidence/m6-mac.json
```

Parent: wait for both `READY` stage files, then write the same `{"nonce": ..., "epoch_ms": ...}` JSON to the barrier path on
both machines. Then `python3 scripts/scenario_matrix.py m6-adjudicate --windows evidence/m6-windows.json --mac evidence/m6-mac.json --clock-offset-ms N --clock-uncertainty-ms U --clock-provenance "7-sample midpoint, half-RTT" --out evidence/m6-adjudication.json`.

Pure checks: `cd native-bridge/feasibility/scripts && python3 -m unittest test_scenario_matrix`.

Every result and `lifecycle.json` records source/binary provenance: `source_revision` from git when available, else
`available: false` with the stage and a bounded reason (archives have no `.git`; nothing is invented), SHA-256 of the host
binary, this driver, the connector and `identity.rs`, platform/Python versions, and pre-existing host pids left untouched.

## Limitations

* Not executed on hardware by this change; only mocked/pure tests and a fake native-framed host process have run.
* `port-disconnect` / `tab-close` cannot always demand `stdout_drained` (Chrome tears both pipes down); it is recorded.
* macOS has no `cmd.exe` wrapper; window hygiene uses CoreGraphics and network endpoints use `lsof` when available, otherwise
  the field says `unavailable`.
* Cross-machine overlap depends on the parent's clock measurement; the adjudicator trusts (and records) the supplied offset, uncertainty and provenance but cannot verify them.
* `failure-isolation` duplicates part of the existing matrix on purpose (it is optional); `adapter-toggle` is never performed.
* The M6 cross-peer disconnect stage needs the two optional epoch flags and is not required by the adjudicator unless a
  deliberate disconnect is present.

## Windows lifecycle `access_denied` after the grant (investigation of the local artifacts)

Artifacts: `/tmp/opencode/otp23-postgrant-windows-lifecycle/` (+ `.json`), native host `6cf72ed0...`. They are the first
failure and must be kept; the driver now refuses to overwrite existing results without `--overwrite`.

Observed: the four Chrome-launched scenarios were `BLOCKED / permission_denied (stage=access_denied)` on connect, while
the direct stdin-EOF case connected both phones and echoed both links with the same binary.

Ruled out from the traces: a single host pid per scenario (no multi-host ownership problem; pid normalization is plain
integers); distinct, well-formed handles (`device_handle` 16 hex, `platform_hint.len=20`, identical to the working direct
case); `hygiene_before.top_level_windows: 1` is Chrome's hidden `cmd.exe` wrapper window (the driver aggregated host and
wrapper; now reported separately as `hygiene_before` / `wrapper_hygiene_before`), not a host window.

Where it fails: `src/winrt.rs` only maps `GattCommunicationStatus::AccessDenied` (3) to `access_denied`. The trace shows
the failing connections reach `conn N service` and `mtu=517`, then fail on the very next call
(`GetCharacteristicsForUuidWithCacheModeAsync`, the write characteristic) after ~3 ms; in LC-shutdown one of two connections
did succeed, ~1.3 s later. This is a Windows GATT refusal at characteristic discovery, not a device-access/Bluetooth-consent
denial (those are `access_denied_user/system` at an earlier stage).

Differences from the working paths that the new driver code controlled (and now records or aligns):

1. **Scan order.** The failing Chrome scenarios stopped the scan and then connected; the passing `physical_validate.py`
   connects while the scan runs and stops it afterwards. (The direct case also connects after stopping, and passed, so this
   alone is not proven to matter.) The default is now `--connect-order scan-active` (matching the baseline) for lifecycle
   and M6; `scan-stopped` reproduces the earlier order.
2. **Token context.** Chrome (and its host) run with the limited medium-integrity token (`ChromeRun(medium=True)`), the
   direct host inherits the driver's token. Results now record `detail.integrity` (chrome, host, wrapper / host, driver) so
   the hypothesis can be checked from the next run's evidence instead of assumed.

Not proven, so not claimed: that either difference is the cause; a Windows-side exclusive/stale GATT session left by the
immediately preceding host (the 1.3 s late success on one phone is consistent with it); Android-side state. The driver never retried a failed connect, so recovery by retry is untested. Minimal operational discriminators for the parent (each a fresh out dir):
`lifecycle --scenario shutdown --connect-order scan-active`, then the same with `--connect-order scan-stopped`, and the
direct `stdin-eof` case immediately before/after; compare `detail.integrity` and the trace `conn N` stages. No Rust or
Android code was changed and no permission cause is asserted without proof.
