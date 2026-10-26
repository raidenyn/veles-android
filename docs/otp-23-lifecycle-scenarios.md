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
| `shutdown` | unawaited `connect` to an absent device is pending, then `shutdown` | pending request terminated (error, none left in the connector), `shutdown_complete` is the single, last host output in the host trace, exit 0 with stdout drained, host + Windows `cmd.exe` wrapper gone, zero host windows and network endpoints |
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

* `--target-label TEXT`: exact advertised label of the shared phone (ambiguous or missing label is BLOCKED; labels are
  stored only as a short hash), or
* `--sole-advertiser`: the parent isolated advertising to one phone; refuses unless exactly one advertises.

Required: `--role windows|mac` (must match the platform), `--pair-run-id ID` (same on both), `--phone-label "OnePlus 6T"`
(parent-provided physical model, no addresses), and exactly one barrier: `--barrier-file PATH` (local file the parent
creates on both machines at the same moment) or `--start-at-epoch SECONDS`.

Flow per central: fresh Chrome + host, discover, select, connect **one** link, write the hash-only
`<out>.stage.json` (`stage: READY`, plan hash, label hash; also prints `READY <hash>`), wait for the barrier, then 20+
(`--exchanges`, minimum 20) 4096-byte exchanges, each independently seeded (`seed = base + 13*i mod 256`; defaults
Windows 41, Mac 173, `--seed-base` overrides). Each exchange verifies the exact echo bytes, connection handle and
routing and records start/end wall-clock ms, size, seed and SHA-256 digests (no bytes). Optional isolation:
`--run-until-epoch T` keeps one central exchanging; `--disconnect-at-epoch D` disconnects the other central's link at D
and records it. A clean single central reports `PARTIAL` (own part only), never an M6 PASS.

`m6-adjudicate --windows W.json --mac M.json [--clock-offset-ms N]` is pure and requires: both roles present once, same
pair id, same phone label (and same label hash when both have one), one link per central (two phones on one central is
rejected), 20+ exact exchanges, no system errors or unexpected disconnects, and **overlapping real I/O**
(default >= 1000 ms and >= 3 overlapping exchange pairs). `--clock-offset-ms` is windows clock minus mac clock, supplied by
the parent; it is recorded, not verified. A missing peer is `BLOCKED`; contradictions are `FAIL`.

Android notify return codes are not exposed by the existing OTP-06 fixture, and no debug endpoint or Android change was
added. The adjudication therefore records requirement `android_notify_return_codes` as `BLOCKED`, and a clean desktop
result is `PARTIAL` with `m6_discharged: false`. `PASS` is only possible if the parent supplies real evidence through
the core API (`notify_codes_evidence`); the parent decides what the matrix row says.

## Commands

Run from `native-bridge/feasibility`, with the host registered by `register-host.ps1` / `macos-register.sh` and the Veles
fixture advertising on the phone(s). Python 3.9+ stdlib only.

**Windows (interactive desktop session, same user that registered the host):**

```powershell
python scripts\scenario_matrix.py lifecycle --scenario all --out-dir evidence\lifecycle-windows
python scripts\scenario_matrix.py m6 --role windows --pair-run-id RUN1 --phone-label "OnePlus 6T" `
    --sole-advertiser --barrier-file C:\Temp\m6-release --out evidence\m6-windows.json
```

**macOS (Aqua session: Terminal in the logged-in GUI session, same user and approved signing identity/bundle; no ad-hoc
substitution):**

```bash
python3 scripts/scenario_matrix.py lifecycle --scenario all --out-dir evidence/lifecycle-mac
python3 scripts/scenario_matrix.py m6 --role mac --pair-run-id RUN1 --phone-label "OnePlus 6T" \
    --sole-advertiser --barrier-file /tmp/m6-release --out evidence/m6-mac.json
```

Parent: wait for both `READY` stage files, then create the barrier file on both machines (or give both the same
`--start-at-epoch`). Then `python3 scripts/scenario_matrix.py m6-adjudicate --windows evidence/m6-windows.json --mac evidence/m6-mac.json --out evidence/m6-adjudication.json`.

Pure checks: `cd native-bridge/feasibility/scripts && python3 -m unittest test_scenario_matrix`.

Every result and `lifecycle.json` records source/binary provenance: `source_revision` from git when available, else
`available: false` with the stage and a bounded reason (archives have no `.git`; nothing is invented), SHA-256 of the host
binary, this driver, the connector and `identity.rs`, platform/Python versions, and pre-existing host pids left untouched.

## Limitations

* Not executed on hardware by this change; only mocked/pure tests and a fake native-framed host process have run.
* `port-disconnect` / `tab-close` cannot always demand `stdout_drained` (Chrome tears both pipes down); it is recorded.
* macOS has no `cmd.exe` wrapper; window hygiene uses CoreGraphics and network endpoints use `lsof` when available, otherwise
  the field says `unavailable`.
* Cross-machine overlap depends on wall-clock agreement; the adjudicator trusts the parent-supplied offset.
* `failure-isolation` duplicates part of the existing matrix on purpose (it is optional); `adapter-toggle` is never performed.
* The M6 cross-peer disconnect stage needs the two optional epoch flags and is not required by the adjudicator unless a
  deliberate disconnect is present.
