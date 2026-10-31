# OTP-23 Mac M-08 retake: result (2026-10-29, captures and analyses frozen at 04:46Z)

## 0. Role envelope

- **Operator/curator:** Claude Code, `claude-opus-5-5`, session
  `752e1974-0617-470b-a425-dfbda97ac728`, no subagents. This is the same session that finished the
  minimum-fix phase as implementer.
- **Authorization:**
  - owner route record `otp23-mac-opus-continuation-owner-record.json`
  - the parent's PHYSICAL M08 instruction, given after the independent APPROVED micro-verification
    `otp23-mac-m08-fix-verification-opus.md` (a fresh Opus session, 7b4ee6ca, not me)
- **Historical roles, unchanged:** GLM wrote the code phase and the first fixes; Kimi K3 did the
  code verification; Opus did the preflight.
- **Evidence:** `native-bridge/feasibility/evidence/mac-retake-2026-10-29/`. `SHA256SUMS` is
  `f952c123a1c74a5b1c811f4faa2453ed5f81cae49e39e3f974136798e4ea6455`, with 79 entries, and the disk
  matches the manifest. Also: `FINDING.md` `9f4c058e…`, `provenance.json` `6981e7eb…`.
- **Not done:** no matrix or row change, no commit, push or GitHub, no canonical promotion.

## 1. Verdict

| scenario | driver | 20 host-side acceptance checks | phone release proof | M-08 creditable |
|---|---|---|---|---|
| **shutdown-1** | PASS | all pass | **PASS** both phones, `target_evidence` | **yes** |
| **port-disconnect-1** | PASS | all pass | **FAIL** (10Pro) | **no** |
| **tab-close-1** | PASS | all pass | **FAIL** (6T) | **no** |

**Concrete blocker, so I stopped:** the reviewed `mac_release_bound.py` check
`release_after_last_exchange` cannot resolve these two scenarios.
- **The race:** the host exits only 77–81 ms after the trace's global last exchange, but the in-band
  anchor is 119–142 ms wide. The outcome is therefore decided by where the interval falls: shutdown
  passed by just +21 ms, port-disconnect failed at −17 ms, tab-close at −28 ms.
- **No handle attribution:** the analyzer can't tell which handle served which phone. The failing
  phone's own link had its last exchange about 500 ms earlier, but that value can't be used.
- **Not retried:** another attempt would just re-roll the same race, and the analyzer is the
  reviewed, pinned version.
- **The other three bound checks** (within exit + 30 s, stop after DIS, stop ≥ bound +
  uncertainty) passed on all six phone records.

**Minimum options for the parent and reviewers (none implemented):**
- (a) Add reviewed handle↔phone attribution to the analyzer. For example: order each phone's 16 B
  proof-notify epoch against the trace's two proof exchanges. This would need its own causal
  argument and tests.
- (b) Add a reviewed, deliberate idle gap in the driver between the last exchange and teardown,
  bigger than the anchor width plus margin (e.g. ≥ 500 ms). This changes the scenario timing.
- (c) An owner/reviewer decision on whether a release proved within exit + 30 s, with the stop
  guard, is enough for M-08 without the "after last exchange" clause.

## 2. Per-scenario measurements

Common to all three runs:
- Chrome: `Chrome/156.0.8078.74`, the plist reads 156.0.8078.74.
- Host binary in `lifecycle.json` meta: `e7c716c5…`. Pinned sources: `scenario_matrix.py f8bbf2d6…`,
  `macos_poc.py e2131ad3…`, with `observer_modules_missing=[]`.
- 6 samples, all `usable=true`. `chrome_visible_top_level=14` and `chrome_cdp_listener_present=true`
  in every sample.
- Host pid at `all_layers=0`, `top_visible=0`, `tcp4=[]`, `pid_alive=true`. Context audit usable
  (`Background`, uid 501, ssh).
- `link_proof`: 2 distinct 16 B PASS. Active scan (`scan_state:started`).
- `lsof-diagnostics.jsonl`: 14 lines (≥ 12 lsof results available in samples), with classes
  `benign` and `clean_stderr` only.
- `cleanup_problems=[]`, survivors `[]`, wrappers `[]`, `trace_released_count=0` (recorded, not
  gated: P0-A).
- Phones: final, `final_after_service_stopped`, clean, not truncated, `peers_dropped=0`, one peer
  `576d2120c83dc839`, 1 connect and 1 disconnect, 10/10 notify API successes and 10 callbacks per
  phone per run. The run id is the same on both phones and different per scenario.
- Hold: fixtures stopped ≥ 65 s after the driver returned and the host was confirmed gone,
  re-checked after the hold. Stop brackets are phone `date +%s%3N` before and after the touch in
  the same `run-as` shell, plus outer adb dates and Mac ms readbacks
  (`phones/stops.raw.txt` → `stops.json`).

### shutdown-1

- **Run id:** `mac-m08-shutdown-20261029T043432Z`.
- **Teardown:** `trace_mechanism=shutdown`; order ends `…disconnected, disconnected, shutdown_complete`;
  exit at 4139 ms, code 0, drained; owned host gone in 0.004 s.
- **Release bound:** PASS. Anchor width 125 ms (±63.5) on both phones. DIS 6T [4080, 4205],
  10Pro [4153, 4278]. Last exchange at 4059.
- **Hashes:**

  | artifact | sha256 |
  |---|---|
  | `shutdown.json` | `bd49b4a2…` |
  | trace | `b81ddd85…` |
  | 6T transport | `74d7f84a…` |
  | 10Pro transport | `0d0a2d75…` |
  | `release-bound.json` | `9cd21693…` |

### port-disconnect-1

- **Run id:** `mac-m08-port-disconnect-20261029T043749Z`.
- **Teardown:** `stdin_closed`, no shutdown frames, exit at 6032 ms, code 0, drained, gone in 0.004 s.
- **Release bound:** FAIL. Width 119 ms. 6T [6037, 6156] passes all checks. 10Pro [5934, 6053]
  is below the last exchange at 5951 (handle c710…).
- **Hashes:**

  | artifact | sha256 |
  |---|---|
  | `port-disconnect.json` | `c825a4e9…` |
  | trace | `45a05e31…` |
  | 6T transport | `619162ae…` |
  | 10Pro transport | `b4c6fa89…` |
  | `release-bound.json` | `0e831e93…` |

### tab-close-1

- **Run id:** `mac-m08-tab-close-20261029T044052Z`.
- **Teardown:** `stdin_closed`, no shutdown frames, exit at 5190 ms, code 0, drained, gone in 0.113 s.
- **Release bound:** FAIL. Width 142 ms. 10Pro [5174, 5316] passes. 6T [5085, 5227] is below the
  last exchange at 5113 (handle 7185…).
- **Hashes:**

  | artifact | sha256 |
  |---|---|
  | `tab-close.json` | `0907b5d4…` |
  | trace | `2d7869e8…` |
  | 6T transport | `834b0e68…` |
  | 10Pro transport | `94744cc0…` |
  | `release-bound.json` | `8d550ae1…` |

Full hashes are in `SHA256SUMS`. Each run's `acceptance-check.json` has the full per-sample table,
from `helpers/retake_check.py`, self-sha `92b1e704…`.

## 3. Listener self-test (step 1, before any Chrome)

`helpers/mac_listener_selftest.py` (`f40e7507…`) ran in a fresh process with its own
`127.0.0.1:0` listener, against the staged `macos_poc.py e2131ad3…`:

- **DIAG set:** returned `[{local_port: 52408, LISTEN}]` while the foreign-nullfs warning was
  present (raw rc 0). One diagnostics line was written, with verbatim stderr and classification
  `benign`. Mount snapshot `328d66e88aec286dd3840630a1d5aaf39dcaf55e67fe6baa818c6a679a3b5ea4`
  (sanitized: `uid≠self`, source paths dropped).
- **DIAG unset (warning still present):** `None`.

The warning had not gone away, so there was no clean-stderr channel to record. No `-w`, no mount
action.

## 4. Staging, identity, resources

**Stage**
- New Mac dir `/Users/raiden/homepc/work/otp23-mac-retake-20261029`. The deterministic
  `stage.tgz` (`a6380d30…`) holds the 13 driver/extension/identity files plus the selftest. It was
  verified on the Mac with `shasum -a 256 -c` against the uploaded expected list.
- `stage/source-provenance.json`: HEAD `9c3ddc54…`, tracked dirty diff `67bec972…`. Only
  `scenario_matrix.py` and `macos_poc.py` differ from HEAD, both with the reviewed bytes.
- The repo sources are unchanged by this phase (`f8bbf2d6`, `e2131ad3`, `a49b3450`, workflow
  `95f78691`).

**Approved app**
- It was reached through the `target/macos-poc` symlink, which resolves to
  `…/otp23-cd971f0/…/VelesOtp23PoC.app`.
- `e7c716c5…`, `me.nagaev.veles.bridge`, Team `7T39M26Z99`, `codesign --verify --strict` OK both
  before (04:31:38Z) and after (04:43:44Z).

**Chrome**
- Driver argv was captured live from `ps`: `--user-data-dir=<tmp>/veles-otp23-macos-profile-*
  --remote-debugging-port=0 --enable-unsafe-extension-debugging --no-first-run
  --no-default-browser-check --disable-sync --window-size=1000,700 about:blank`
  (`<scenario>-1/mac/chrome-argv-samples.txt`).
- `VELES_OTP23_DETACHED_CLOSE` was unset.
- The owner's personal Chrome was never touched.

**Phones**
- 2be02e41 (OnePlus 6T) and 864a3db0 (NE2215).
- App `063ac7ea…677a5e` and test `8ca89381…ede1c` on both phones, read before the runs. Nothing
  was installed or wiped.
- Before starting I checked no Veles UUID was advertising and no fixture process was running.

**Registration**
- Only the own `me.nagaev.veles.otp23.plain.json` was created (`17a196cd…`). It was byte-checked
  before `macos-unregister.sh` removed it.
- The NM directory's file set after the runs equals the pre-state, and no veles/otp23 manifest
  remains in the user or system dir.

**After the runs:**
- no `otp23-host` processes, no own disposable-profile Chrome, 0 leftover profiles
- no fixture app process on either phone, no `mac-m08` adb clients

**Things that predate this session (left untouched):**
- 8 stale local `adb … am instrument` clients from 2026-10-28 01:31–06:51Z
- a 2026-10-28 `otp23-stop-stopstamp` file on the 6T

**Leases:**
- I have no lease tool, so I didn't renew or end either lease.
- The Mac lease (renewed by you about 04:09Z, 90 min) should stay active for the M-11 code-phase
  staging.
- The PC/adb lease can end now: the fixtures are stopped.

**Not run:** `mac_clock_corroborate.py` (optional, corroboration-only). It isn't needed for the
in-band proof.

**Privacy:**
- The published set is clean of the other user's and other apps' names (sweep run).
- Raw NM listings, Chrome stderr noise and adb pid files are kept privately in
  `/tmp/approved-otp23-privacy/mac-retake-2026-10-29/` (`700`), mapped by sha256 in
  `curation/curation-map.json`.

## 5. Signed live baselines for M-11

The three runs are live authorized Bluetooth scans by the approved signed fixture, launched by
Chrome, with active scan and `device_found` events. They are the M-11 comparison baseline. M-11
execution still needs the M11-1 fix and a re-verification (next phase, code only).
