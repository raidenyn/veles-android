# OTP-23 contract alignment (2026-10-26)

Branch `contract/otp23-desktop-alignment`, based on PR #122 head
`fb224e8c54e0241c0d68c3c61687dc692a384102`. This document records what the review
of the two desktop PoCs changed in the canonical contract, the code and the guides,
and what it deliberately did **not** change. Nothing here approves OTP-24.

**Decision: NO-GO for OTP-24** until every row of the
[requirements matrix](otp-23-requirements-matrix.md) is `PASS` or carries the account
waiver below. Fresh evidence was collected on 2026-10-26 (section 10) and it moved the
matrix from all-`NOT RUN` to mostly `BLOCKED`: nothing that needs a phone, a Bluetooth
permission or a signing identity could run. No physical transfer, background or permission row
is `PASS`.

Related work: PR #122 (open prerequisite for this branch), issues #86 (native-central BLE
feasibility) and #87 (bridge), and #120 (OTP-06 hardening follow-ups). None of them is closed or
may be closed on the strength of this document.

**User-approved account waiver (2026-10-26).** Tests that require NEW accounts are skipped
and non-blocking for OTP-23: W-01 (standard non-admin Windows account), M-01 (fresh
standard macOS account) and M-02 (fresh-account TCC prompt owner). They stay `NOT RUN`
with the waiver text, are never `PASS`, and no fresh/standard-account evidence is claimed.
Runs on the existing account must say truthfully whether it is administrator or standard
(the matrix `account` field; a Windows/macOS `PASS` without it is invalid). Nothing else is
relaxed: signing provenance, the final identifier `me.nagaev.veles.bridge`, allow/deny/
recovery with unchanged extension state, the prompt owner as observed on the existing
account, the plain-Rust architecture and strict background delivery all remain required.
The Mac has Apple Development only (no Developer ID) and no final-identifier fixture
exists, so M-03 stays `BLOCKED`.

## 1. Sources read and the pre-edit inventory

Read in full before editing: `desktop-ble-poc.md`, `windows-ble-poc.md`,
`otp-23-windows.md`, `otp-06-transport.md`, `protocol/veles-v1.md` (section 9 and
every cross-reference), the RFC (OTP-23/24/25 and the bridge sections), both PoC
reviews (`windows-ble-poc-opus.md`, `desktop-ble-poc-macos-opus.md`), `protocol/v1/`,
`native-bridge/feasibility/`, the production `native-bridge/src-tauri` and the
Android sharing package. Issue #120 was re-read for M1–M8.

Inventory of everything that mentions or implements fragment sizing, **before** any edit:

| Kind | Item | Fragmentation-relevant? | Result |
|---|---|---|---|
| Specification | `docs/protocol/veles-v1.md` OTP2-GAT-003/010/011 | Yes: `MTU − 3 − 4` with no 512 clamp | **Revised** (profile revision 2) |
| Specification | RFC bridge, OTP-02/06/23/24/25 sections | Yes (Tauri runtime, background wording, packaging) | **Revised** |
| Specification | `otp-06-transport.md` | Yes: "exactly MTU-7" | **Revised** |
| Descriptor | `protocol/v1/limits.json` | Yes | **Revised** (+3 limits) |
| Descriptor | `protocol/v1/binary-layouts.json` (`fragmentation`) | Yes: `payload_budget_expression` | **Revised** |
| Descriptor | `protocol/v1/fixture.schema.json` | `mtu` had a floor but no ceiling | **Revised** (`maximum: 517`) |
| Descriptor | `manifest.json` | Evidence ids are *derived* | **Regenerated** by the new sync tool |
| Descriptor | `envelope.schema.json`, `states.json` | No (opaque records; byte accounting only) | Unchanged |
| Generator | `tools/generate-vectors.mjs`, `crosscheck-vectors/*` | Crypto only | Unchanged |
| Generator | *(none existed for `fragmentation.json`)* | It was hand-maintained | **Added** `generate-fragmentation-vectors.mjs`, `sync-manifest-evidence.mjs` |
| Fixture | `fixtures/fragmentation.json` | Yes: 21 MTU-185 vectors only | **81 vectors**: 21 kept (2 amended), 60 `frag-r2-*` generated |
| Fixture | `crypto/encoding/enrollment/reconnect/records/bridge.json` | No | Unchanged |
| Checker | `checks/binary.mjs` `validateFragmented` | Yes: `min(budget, remaining)` accepted short non-final fragments; no 512 clamp | **Revised** (exact rules) |
| Checker | `checks/contract.test.mjs`, `check.mjs` | Yes | **Extended** |
| Kotlin | `GattTransport.kt`, `SharingGattServer.kt` | Yes: `attributeLimit` flag, `514` literals | **Revised** |
| Kotlin | tests `FragmentFixturesTest`, `GattTransport*Test`, `SharingGattServerTest` | Yes | **Revised/extended** |
| Rust | `feasibility/src/{fragment,core,backend,sim,winrt,macos,macos_map}.rs` + tests | Yes: `ATTRIBUTE_LIMIT`, MTU passed around, mac "inferred MTU 515" | **Revised** |
| Rust | production `native-bridge/src-tauri`, `rust/` | No: 3-byte Native Messaging framing and crypto, no BLE fragments | Unchanged |
| TypeScript | `web-extension/`, `native-bridge/src` | No: complete opaque `record_base64` only | Unchanged |
| Python | `feasibility/scripts/*` drivers | Parse host traces; size lists; the mac trace line changed | **Updated** |
| Acceptance scope | RFC OTP-23, matrix JSON/generator/tests, guides | User-approved account waiver (W-01, M-01, M-02 `NOT RUN`, non-blocking) | **Applied** |
| Guides | `windows-ble-poc.md`, `desktop-ble-poc.md`, `otp-23-windows.md`, feasibility `README.md` | Yes (PoC-only wording, Tauri runtime) | **Revised**, historical results kept |

## 2. The revised canonical contract (OTP2-GAT-010, profile revision 2)

```text
attribute_budget = min(ATT_MTU − 3, 512)     # bytes of one frame, header included
payload_budget   = attribute_budget − 4
```

* **Both directions**, senders and receivers, GATT write and notify.
* **Non-final** fragment: exactly `payload_budget` bytes (and ≤ what remains).
  **Final** fragment: exactly what remains. A frame longer than `attribute_budget`
  is malformed whatever its flags.
* **Canonical sender:** `⌈len / payload_budget⌉` fragments; an exact multiple ends
  in a *full* final fragment; a sender never appends an empty final fragment.
  **Receiver tolerance:** the empty final after an exact multiple is accepted
  (it satisfies both rules and the frozen vector `frag-mtu185-final-zero-payload`
  already pinned it). The previous prose ("the NEXT fragment is the final one",
  "may be as few as 0") was ambiguous; the checker silently accepted short
  non-final fragments that Android and Rust rejected.
* **Preserved unchanged:** MTU floor 185 (budget 182/178, 24 fragments for 4096),
  record range 1..4096, transaction/index/reserved-byte rules, the 128-fragment
  ceiling, reassembly (2 / 8 KiB per peer, 16 / 64 KiB global), 5 s inactivity,
  60 s conflict window, queues (16 msg / 16 KiB per peer, 128 / 128 KiB global),
  Native Messaging limits, and all crypto. Added: maximum accepted ATT MTU 517
  (what Android's `requestMtu` allows and what the code already enforced).
* **macOS measured value is not the MTU.** CoreBluetooth reports only the maximum
  write-without-response length `W` (512 on both measured Macs). `W` is a
  backend write limit, at most `MTU − 3` and possibly platform-capped, so equality with
  `MTU − 3` is **not** assumed. A common budget is established only for `W ≥ 512`
  (then `MTU − 3 ≥ 512`, the clamp binds, attribute 512); for `W < 512` it cannot be
  established without guessing and the host refuses the link (`budget_undetermined`,
  fail closed). The earlier host derived
  "effective MTU 515" and fed that into the MTU formula; the same bytes resulted,
  but it asserted an observation that never happened. The host now passes
  `LinkCapacity::MaxWriteWithoutResponse(W)` and nothing invents an MTU.
* **One budget per implementation, no hardcoded MTUs or chunk sizes.**
  Kotlin: `GattTransport.attributeBudget/payloadBudget`. Rust:
  `fragment::LinkBudget::from_capacity(LinkCapacity)`; Windows reports
  `AttMtu(MaxPduSize)`, macOS `MaxWriteWithoutResponse(W)`, the simulator a named
  floor capacity; the core derives the single budget and refuses out-of-range
  links (`mtu_range`). Node: `payloadBudget` in the vector generator and the
  descriptor expression checked by the contract test.

### Compatibility, versioning and rollout

Revision 1 said "non-final payload = `MTU − 7`" with no clamp. It is
**byte-identical** to revision 2 for every negotiated MTU ≤ 515 and for every record
whose frames fit 512 bytes. It differs only at MTU 516/517, where revision 1 demanded
513/514-byte attribute values. Android cannot send them
(`notifyCharacteristicChanged` throws, the physical failure that began this work),
so **no interoperable revision-1 behavior existed at those MTUs to preserve**.

Why a pre-release correction rather than a version bump or negotiation:

1. Nothing has shipped that exchanges protected records: release builds discard every
   completed record (`SharingService` calls the synthetic fixture only under
   `BuildConfig.DEBUG`), so there is no deployed population to protect.
2. The record `protocol_version` stays 1 because fragmentation is not covered by the
   record header or its AAD; the GATT profile (UUIDs/properties) is unchanged.
3. The committed MTU-185 vectors are unchanged and still pass in all consumers.
4. The GATT profile has no capability channel, and adding one now would add
   attack surface and a downgrade choice to fix a defect that has no working
   old-behavior counterpart.

**Fail-closed behavior across a mismatch** (both directions, covered by tests):
a revision-1 sender at MTU 516/517 is rejected by a revision-2 receiver (frame above
the 512 attribute budget → partial state erased, nothing delivered); a revision-2
sender at MTU 516/517 is rejected by a revision-1 receiver (non-final payload 508 ≠
`MTU − 7`). Both ends discard the transaction as transport-only loss — never a
mis-framed record — and the record layer's AEAD sequence rules still apply. Fixtures:
`frag-r2-mtu517-legacy-r1-frames-rejected` and the `frag-r2-*-over-budget` family.

When this must change again: after the first release, this profile is frozen. Any
further change needs an explicit profile-version mechanism (for example a new
service UUID), not a silent revision. The earlier text that left the rule to
"the builder's choice" and claimed an unchanged frozen compatibility is withdrawn.

### Test and vector coverage

`protocol/tools/generate-fragmentation-vectors.mjs` (reference model transcribed
from the spec; it does not call the checker) produces the 60 `frag-r2-*` vectors:
MTUs 185, 247, 514, 515, 516, 517 around one- and two-fragment edges and 4096
(plus 4095 at 517), the exact multiples 1016 and the tolerated empty final at 517/516,
and negatives (legacy r1 frames, 513-byte non-final/final, 512-byte frame at MTU 514,
non-final short/exceeding remaining, final short/long, empty final with bytes left,
total 0 and 4097, index gap, extra after final). Two revision-1 vectors were
*amended* (`frag-reserved-nonzero`, `frag-live-transaction-duplicate-ignored`): both
used a short non-final first fragment, so they were rejected for a reason other than
the rule they claimed to pin. This is recorded openly, not hidden.

Independent agreement: the Node checker, the Kotlin production receiver *and sender*
(`FragmentFixturesTest` regenerates every canonical accepted vector byte-for-byte),
and the Rust receiver *and sender* (`r2_sender_reproduces_every_canonical_accepted_fixture_byte_for_byte`)
consume the same file. Each has a literal budget table test for every MTU 185..517
and a round-trip at 1, 2, `p−1`, `p`, `p+1`, `2p±1`, 4095, 4096 for every MTU.
A deliberate mutation (drop the 512 clamp in Kotlin) fails 9 tests.

## 3. Android

* The cap is no longer an opt-in `OTP23_POC` flag: `GattTransport` has one formula,
  `SharingGattServer` bounds inbound write values to 512, and `app/build.gradle.kts`
  no longer defines the field. The isolated init script now only adds the `.otp23`
  application-id suffix to the **debug** build type (its test also asserts it never
  touches release or defines build-config fields).
* Synthetic fixtures stay debug-only: the echo reply and the new unsolicited-push
  hook are reachable only under `BuildConfig.DEBUG`, so R8 removes them from release.
  Release inspection: section 7 (historical) and section 11 (current).
* New, debug-only and opt-in (`-e otp23_unsolicited_after_s N` on
  `SharingFeasibilityTest`): N seconds after a central subscribes, push one
  deterministic type-3 4096-byte record to it; hash-only evidence goes to
  `files/otp23-unsolicited.json`. This exists for the strict background run.
* **#120 M3 (duplicate connection callbacks): found and fixed.** A repeated
  `STATE_CONNECTED` for an admitted address used to fall into the capacity-refusal
  branch and `cancelConnection` the healthy link. It is now ignored, with
  regression tests (duplicate keeps the link and its MTU/subscription state; the
  ninth client is still refused).
* **#120 M4 (CCCD/MTU ordering): no defect found.** Windows (`d5b6f2d`) and
  macOS (`fb224e8`) both completed MTU/measurement before the CCCD write and
  interoperated; the existing behavior (CCCD before MTU rejected, record I/O gated on
  MTU ≥ 185 and subscription) is now covered by an explicit test. Supporting
  CCCD-first clients is not adopted without a client that needs it.
* **#120 M1, M2, M5, M7, M8 remain deferred** — nothing in this work proved or
  required them. **M6 is not discharged:** two *phones* on one *central* (the PoCs)
  is not two *centrals* on one *phone*; matrix row A-02 stays `NOT RUN`.

## 4. Runtime decision: plain Rust; Tauri is a packaging-only question

The RFC, the OTP-23/24 guides and the harness README now state a **plain Rust
runtime**. Kept strictly apart:

* **Historical no-go evidence, `1a34191`:** Tauri 2.6.0 showed a visible
  `Tao Thread Event Target` window at `(0,0,13,13)` on the interactive desktop and needed a
  forced exit after `AppHandle::exit`; the frozen MTU rule crashed Android at 4095 bytes.
  Preserved unmodified.
* **Historical later successes, `d5b6f2d` (Windows) and `fb224e8` (macOS):** plain Rust,
  capped fragments, PoC liveness. Preserved unmodified.
* **Current evidence** (new): [`otp-23-tauri-packaging-evaluation.md`](otp-23-tauri-packaging-evaluation.md).
  It reports dependency graphs plus current native plain-versus-dependency-linked builds and
  SBOMs (Windows 717,312 vs 758,272 B, 22 vs 276 components; macOS 1,721,088 vs 1,744,368 B, 46 vs
  275), a cost proxy only. It does **not** assert that retaining Tauri for packaging is justified:
  no bundler/installer proof exists (that belongs to OTP-25, whose installers were not implemented)
  and matrix row D-01 records completion of the evaluation with the decision that retaining Tauri
  solely for packaging is **not approved and not justified** (OTP-25 picks another pipeline or brings a
  reviewed justification).

## 5. macOS documentation

See [`desktop-ble-poc.md`](desktop-ble-poc.md), "macOS identity and permissions", for the
full text. In short: the host runs from a **stable signed app bundle with usage
descriptions**; the historical Apple Development identity and bundle id
`me.nagaev.veles.otp23.poc` are **not** evidence for the production identity; signing works
from the Aqua session but failed over SSH key access (`errSecInternalComponent`); a disposable
Chrome profile carries its own Native Messaging manifest; the denied → `allowed_always`
transition happened after a **human** grant and which Settings entry owned it is **unknown**;
no TCC reset was used. No standard-account, fresh-account or distribution result is claimed.

## 6. Verification harness changes (for the physical runs)

* **Strict background validation** (`scripts/strict_background.py`, verdict logic in
  `strict_background_core.py`, 27 tests): after another ordinary tab is focused and the
  connector is verified hidden, the driver closes *every* DevTools connection (target
  session and browser-level) and holds no CDP object until the idle interval ends; there is
  no connector, host or phone traffic, no keepalive, no audio/WebRTC (an exemption scan of the
  loaded extension files is part of the verdict). Delivery is an **unsolicited** phone
  notification on a preconfigured delay after subscription. Evidence afterwards: the DevTools
  HTTP target list (page title counter, no session), the page's own receipt timestamps
  (wall clock, visibility, focus — added to `connector.js`), the host trace placed before
  re-attach, and the phones' hash-only files. **Cold** idle (nothing ever sent) and
  **prewarm** (one byte-verified exchange first) are separate runs with a hard check that the
  cold host trace contains no `send`. If the architecture needs a Native Messaging exemption,
  this reports it; any acceptance revision must be proposed separately with that evidence —
  the criterion is not weakened here.
* The old `poc_background.py` and its evidence stay as they were: browser-level CDP attached,
  desktop-scheduled timer, warmed links. They are liveness evidence, not this test.
* **Requirements matrix** (`acceptance/otp23-matrix.json`, `scripts/matrix.py`, 14 tests;
  rendered to [`otp-23-requirements-matrix.md`](otp-23-requirements-matrix.md)).
* **Current dependency graph** collector `scripts/dependency_cost.py`.
* Provenance for the fresh runs (source and binary/APK hashes, Rust/Xcode/NDK/signing
  identities) uses the existing `verify-identity.py`/`macos-poc-build-identity.json`
  pattern; each run must record a new identity file rather than reuse a historical one.

## 7. Verification performed on this branch — SUPERSEDED (historical local run)

> **Superseded.** This is the first local run (310 unit tests, `SharingGattServerTest` 11, `test_matrix` 14,
> release hash `20b1ca56…`). It is kept as history and is not current. The current counts, full logs and
> guard output are in section 11 and `native-bridge/feasibility/evidence/fresh-2026-10-26/post-review/`.

Run on this Linux host (JDK 21, Rust 1.98.0, Node 26.12.1, Python 3.14); the numbers are from the
actual command output of the final tree. **Windows and macOS code was not executed here**, and no
physical device was used by this task.

| Check | Result |
|---|---|
| `./gradlew spotlessCheck detekt lintDebug` | PASS (after `spotlessApply` formatting and two detekt fixes) |
| `./gradlew testDebugUnitTest` | PASS: 310 tests, 0 failures, 0 skipped (sharing package: 44, incl. `GattTransportBudgetTest` 9, `SharingGattServerTest` 11, `FragmentFixturesTest` 2) |
| `./gradlew protocolCheck` | PASS (contract checks 165 Node tests, Rust, web-extension, bridge and JVM fixture consumers) |
| Raw `android.util.Log` guards, both greps copied verbatim from `ci.yml` `lint-check` | PASS (exit 0, no matches) |
| `node protocol/checks/check.mjs` | PASS: 165 tests; `check-contract-text` 156 manifest entries; generator `--check` and manifest sync `--check` clean |
| `cargo fmt --check`, `cargo test --locked` (feasibility, Linux) | PASS: 165 passed, 0 failed, 4 real-radio tests ignored by default |
| `cargo clippy --all-targets -D warnings` for Linux, `aarch64-apple-darwin`, `x86_64-pc-windows-msvc` | PASS (the two foreign targets are type-checked and linted, **not built, linked or run**) |
| `./gradlew bridgeInstall bridgeFormat bridgeLint bridgeTypecheck bridgeTest` | PASS (production bridge is unaffected by the change; run as a regression guard) |
| Node `connector-api` + `isolated-device` tests | PASS (4) |
| Python `trace-parser`, `test_poc_background` (26), `test_macos_poc` (36), `test_macos_background_support` (9), `test_strict_background` (27), `test_matrix` (14); `matrix.py check` | PASS; decision printed `NO-GO` |
| Mutation checks | Removing the 512 clamp fails 9 Kotlin tests and 10+ Rust tests |
| `./gradlew :app:assembleRelease` + inspection of the built APK | PASS: `mapping.txt` and the dex strings contain no `SyntheticTransportFixture`, `scheduleSyntheticPush` or `OTP23_POC`; `BuildConfig.DEBUG = false`; application id `me.nagaev.veles`; permissions are the existing set plus only `BLUETOOTH_ADVERTISE`, `BLUETOOTH_CONNECT`, `FOREGROUND_SERVICE(_CONNECTED_DEVICE)` for sharing, no scan/location. APK sha256 `20b1ca56e5d48eec42fea935121dc6de4ac4b82d428b83e4fff364b3229eee54` (unsigned, this tree) |

Not run: instrumented Android tests (no device), `web-extension` npm lint/format (unchanged
sources; the extension fixture consumers ran inside `protocolCheck`), `rustFormat/rustLint/rustTest`
for the shared `rust/` crate (unchanged).

Implementation model: Claude Code, model `claude-sonnet-5-5`; no subagents were used.

## 8. Hardware harness commands (for the coordinating agent)

Build the isolated APKs on Linux exactly as `windows-ble-poc.md` describes, then start the
instrumentation on **each** phone with the strict-run delay (must equal the driver's
`--phone-delay-s`, default 690):

```sh
adb -s SERIAL shell am instrument -w -e class me.nagaev.veles.sharing.SharingFeasibilityTest \
  -e otp23 true -e otp23_unsolicited_after_s 690 \
  me.nagaev.veles.otp23.test/androidx.test.runner.AndroidJUnitRunner
```

On the desktop (Windows PowerShell via `scripts/invoke-poc.ps1 -Script strict_background.py`,
or macOS shell) from `native-bridge/feasibility`:

```sh
python3 scripts/strict_background.py --scenario cold    --out evidence/strict-cold.json    --adb SERIAL_A --adb SERIAL_B
python3 scripts/strict_background.py --scenario prewarm --out evidence/strict-prewarm.json --adb SERIAL_A --adb SERIAL_B
# no adb on the desktop (macOS): pull afterwards on the PC the phones are USB-attached to
adb -s SERIAL shell run-as me.nagaev.veles.otp23 cat files/otp23-unsolicited.json > phone-A.json
python3 scripts/strict_background.py ... --phone-evidence phone-A.json --phone-evidence phone-B.json
```

Restart the instrumentation before each run (the push fires once per subscription). Update the
matrix JSON from the produced evidence (never from memory), `python3 scripts/matrix.py render --write`,
and keep `python3 scripts/matrix.py decision` as the only go/no-go.

## 9. Outstanding

* Every hardware row in the matrix. Windows/macOS Rust is type-checked and linted on this Linux
  host for both targets but **not executed or physically verified** here.
* macOS signing identity (Developer ID / final-identifier fixture) and the allow/deny/recovery
  rows need credentials and a human on the existing account. The new-account rows are waived.
* Any future Tauri bundler proof is OTP-25 scope (retention is not approved; nothing implemented here).
* #120 M1, M2, M5, M6, M7, M8.
* The protocol document's status stays "specified, awaiting review": this revision has not had
  the independent protocol review that OTP-02 requires for acceptance.

## 10. Fresh findings and final integration (2026-10-26)

All files are under `native-bridge/feasibility/evidence/fresh-2026-10-26/` (see its `README.md`);
the artifacts are unmodified outputs of the runs, and every earlier evidence file is untouched.

**Provenance.** Source snapshot `4e9c685855d68c0c97eacc21108b7565c49bc186`, archive
`git archive` sha256 `79f86123…05a6ae8` verified on both desktops (the archive has no `.git`, so
the drivers' explicit git-unavailable result is kept). Implementation model: Claude Code 2.1.310,
session `7fda8a4e-f413-455a-9895-698edf5ce7a9`, `claude-sonnet-5-5` per CLI result metadata, no
subagents (`model-provenance.json`). **Final Opus 5.5 review: NOT RUN** — an availability probe hit a
session quota; that probe is not a review, and matrix row R-01 stays `NOT RUN` until a real one
completes.

**Source changed after the physical attempt.** The Android APKs the phones received (sha256
`46982666…3c128`, test APK `09280790…e0d2`) were built from a **dirty working tree on top of
`fb224e8`** (installed version `0.0.3-25-fb224e8-contract_otp23-desktop-alignment-dirty`, version
code 3) *before* the same edits were committed as `4e9c685`. They are therefore **not** a clean
`4e9c685` Android build; the native desktop archive hashes are the clean exact ones. The disclosure
is in `identity.json` and the evidence README. Two source changes came
after that attempt: the `SharingGattServer` malformed-attribute fix and the packaging-helper fix
below (plus comments/docs). **No fresh Android transfer PASS exists for either version**, and
every physical transfer criterion stays `BLOCKED`.

| Finding | Status | Evidence | What it does *not* show |
|---|---|---|---|
| Windows stable Chrome, plain host, 12 scan-only cases (CH-01 to CH-05, CH-07 to CH-13) | cases PASS at their stated scope | `fresh-windows-chrome.json`, `identity.json` | zero phones advertised: no link cleanup, no two-phone exchange. Account is the existing **administrator** at medium integrity, not a standard user. CH-03 measured the host with 0 top-level windows and no endpoints; the Chrome-created `cmd` wrapper had 0 visible and **1 hidden** window, so no claim that the wrapper has no hidden windows is made |
| Windows host binary identity | installed sha256 `09713016…ff430` (717,312 B) is what Chrome ran; an earlier build hash differed | `identity.json` | no reproducible-binary claim |
| Native `cargo test`/`clippy -D warnings`/`build --release` on both real targets | exit 0 | logs in the directory | ignored radio tests are not passes |
| macOS final-identifier fixture (`me.nagaev.veles.bridge`) signed in Aqua | signed, strict-verified | `identity.json`, `fresh-macos-final-id-scan.*` | **Apple Development**, not Developer ID, notarization or CI provenance. Chrome launched it, `ready` was negotiated, no on-screen host window was observed, then the scan returned `access_denied` / CoreBluetooth `denied`. No prompt owner, allow or recovery |
| Phone fixtures | blocked | `phone-*-prerequisite.log` | both failed `PermissionRequired` before advertising; adb grant refused (`GRANT_RUNTIME_PERMISSIONS`); the user cannot approve Bluetooth permissions now. Only the disposable `.otp23`/`.otp23.test` packages were reinstalled (user-approved); the normal Veles app and data were not touched |
| Required gates (normal build) | PASS | `linux-required-gates.log` | — |

Scope bookkeeping: the new-account waiver covers only W-01, M-01 and M-02. The original RFC also
requires the **unsigned dev-registered host** to be tested; only its fresh-account variant is
waived, so a separate blocking existing-account row, **M-11**, was added. **No test was run for
it.** Signing provenance (M-03), allow/deny/recovery (M-04…M-06) and the phone prerequisite (A-03)
stay separate blocking rows. D-01 is `PASS` only as completion of the evaluation and its negative retention decision:
current dependency graphs, native sizes and SBOMs (a proxy) do not justify retaining Tauri, so it is
**not approved**. It is not approval of Tauri, packaging readiness or OTP-24. No OTP-25 installer work is
an OTP-23 requirement.

### Fixes made in this integration

1. **`macos-package-lib.sh`**: `check_signature_authority` matched `Identifier=…` with a regex, so
   dots were wildcards and `meXnagaevXvelesXbridge` passed for `me.nagaev.veles.bridge`. It now uses a
   literal whole-line match. `test_macos_package_lib.sh` (33 checks: bundle-id validation, ad-hoc,
   missing/self-signed authority, no Apple root, wrong/lookalike/prefix/suffix ids, correct chains)
   fails 2 checks against the old matcher. This validates packaging after signing; it changes no
   host binary or transport.
2. **`SharingGattServer.onCharacteristicWriteRequest` (a real contract defect).** A value above 512
   bytes returned before `GattTransport.receive`, so — unlike the direct receiver tests — it left the
   peer's partial transaction alive and a later final fragment could complete a record across a
   malformed frame, contradicting OTP2-GAT-011 (malformed input discards the partials). The value is
   still never copied; it now posts a data-less, per-peer-bounded discard (the existing 16-callback
   ingress budget) that runs in arrival order. *(Superseded by section 11/I1: when the budget is saturated the
   peer now fails closed instead of the discard being dropped.)*
   Regression tests: partial → over-budget → final delivers nothing; the control without the
   malformed frame delivers; ordering and per-peer isolation under a 500-frame flood. Removing the
   call fails the regression. This does not expand into #120 M2.
3. **Docs/comments:** the production scaffold entry `native-bridge/src-tauri/src/main.rs` no longer
   promises Tauri runtime wiring; `CLAUDE.md` and `reproducible-builds.md` label the Tauri
   build/packaging commands as existing tooling, not the approved production architecture.

### Remaining harness limitations (not implemented; physical runs are blocked)

* The strict driver does not yet instrument **visibility/focus continuity** (no
  `visibilitychange`/`focus`/`blur` history for the connector and ordinary tab); it proves the state
  only before the DevTools clients close and from the page's receipt record.
* macOS needs **offline adjudication**: phone evidence is pulled on the PC after the run, but there is
  no tool yet that recomputes the verdict from the raw result plus phone files without Chrome.
* `invoke-poc.ps1`'s script allowlist still excludes `strict_background.py`; run it directly.
* `macos-package.sh --bundle-id` and its authority check exist and are tested by shell helpers;
  no real signing is performed by tests.

### Verification after the final integration (this tree, after snapshot `4e9c685`) — SUPERSEDED

> Superseded history: the 313-test / `SharingGattServerTest` 14 run below predates the I1 fix. Current
> results are in section 12.

Run on Linux after the three fixes above: `./gradlew spotlessCheck detekt lintDebug testDebugUnitTest
protocolCheck` PASS (313 unit tests, 0 failures; `SharingGattServerTest` now 14), both raw Log guards
from `ci.yml` exit 0, `bash scripts/test_macos_package_lib.sh` 33 checks PASS (and 2 fail against the
old regex matcher), `bash -n` on the packaging scripts, `matrix.py check` (NO-GO), `test_matrix` 16,
`test_strict_background` 27, `test_poc_background` 26, `test_macos_poc` 36,
`test_macos_background_support` 9, Node connector/isolated tests 4, `node protocol/checks/check.mjs`
165, `cargo fmt --check` for the production bridge scaffold. No Rust sources changed in this step, so
the native Windows/macOS results in `fresh-2026-10-26/` still describe the Rust code; the Android
change after `4e9c685` has had no physical run. The parent's earlier `rustFormat rustLint rustTest` and
extension npm checks (see the evidence README) predate nothing that changed here.

## 11. Review fixes and current verification (after the initial Opus 5.5 review)

The first actual review (`claude-opus-5-5`, session `1b23a799-1730-4b2d-905a-bd912cde7ef3`, raw copy
`evidence/fresh-2026-10-26/opus-initial-review.json`) found no CRITICAL issue and four IMPORTANT ones;
the disposition is [`protocol/reviews/otp-23-contract-alignment-opus.md`](protocol/reviews/otp-23-contract-alignment-opus.md).
Implementation model for the fixes: `claude-sonnet-5-5`. *(Historical status at that phase: the review was not yet passed; the later outcome is in section 14.)*

* **I1 (Android) — CORRECTED by section 13:** the text below covered only writes above 512 bytes; the
  same silent-drop gap existed for malformed frames of 512 bytes or less and is closed in section 13.
  Original text: if the peer's 16-slot ingress is saturated, an over-budget write can no longer be
  silently dropped. The peer fails closed with at most one queued disconnect (a per-peer `overflowed`
  set, cleared on removal, reconnect and close), queued ahead of every later accepted frame, which
  are refused at arrival. No payload is copied and there is no unbounded metadata queue. The earlier
  prose "dropped like any flooded callback" no longer applies to malformed frames. An over-budget
  value on an *invalid* operation (wrong characteristic, prepared write, response requested, non-zero
  offset) is answered `GATT_REQUEST_NOT_SUPPORTED` through the same bounded ingress and does not touch
  the partial, consistent with normal invalid operations.
* **I2 (corrected by the second review):** the first fix wave cited `evidence/fresh-2026-10-26/post-review/`
  and described its Log-guard file as the verbatim `ci.yml` step and `jdk.txt` as a Zulu JDK. Both claims were
  wrong: `post-review/log-guards.log` contains only a header line and an exit marker, and `post-review/jdk.txt`
  is Ubuntu's PATH `java`, not the Gradle runtime. Those files are **preserved as history and no longer cited as
  proof**. `G-01` now cites `evidence/fresh-2026-10-26/submitted-cd971f0/`: `ci-log-guards.sh` (text-equal to
  the `ci.yml` block), `ci-log-guards-verbatim.log` (`bash -v` run: both greps shown, no matches, exit 0),
  `gradle-version.txt` and `linux-audited-gates.log` (exported `JAVA_HOME=/home/dev/.jdk/zulu21`, Gradle
  launcher Azul 21.0.12.1, daemon JVM `/home/dev/.jdk/zulu21`, gates exit 0). Section 7 is superseded
  history.
* **I3:** `W-13` (existing administrator account, filtered medium integrity, stable Chrome launches the
  plain host on demand, `hello → ready`, CH-02) is a narrow non-waived `PASS`; it proves nothing about
  phones, transfer or link cleanup. `W-01` (standard account) stays waived and `NOT RUN`.
* **I4:** `D-01` no longer demands OTP-25 installer work. It records the evaluation and its decision
  (retaining Tauri solely for packaging is not approved or justified; OTP-25 picks another pipeline or
  brings a separately reviewed justification). It is not approval of Tauri, packaging or OTP-24.
* **Minor:** stale `macos.rs` header rewritten (code unchanged); Tauri evaluation arithmetic and
  references corrected; `macos-package.sh` now also runs `codesign --verify --strict -R='anchor apple
  generic'` (a text helper cannot establish trust, a self-signed Apple-named chain passes the text
  checks and must fail this cryptographic anchor; the helper test uses a mock `codesign`); A-01, A-02
  and M-11 are `BLOCKED` with their prerequisites.

Results of the first fix wave (superseded by section 12 for Linux gates and Log guards; the Zulu and verbatim wording originally used here was inaccurate, see I2 above): required Gradle gates PASS;
**316** unit tests, 0 failures (`SharingGattServerTest` 17); Log guards exit 0; protocol Node check 165;
cargo 165 passed (Linux; macOS/Windows targets clippy-clean, not executed); harness tests: matrix 18,
strict driver 27, background 26, mac support 9, mac poc OK, shell package helper 39 checks, Node 4.
Restoring the pre-I1 silent drop fails 3 `SharingGattServerTest` tests (`mutation-pre-i1.log`).
The release APK was **not** rebuilt here; `release-inspection.json` describes an older tree and is not
relabelled. The later Android changes have had no physical run: every phone criterion stays `BLOCKED`.

Strict-driver follow-ups (not implemented, physical runs blocked, no formal PASS is possible from the
driver): visibility/focus continuity history is not instrumented; Chrome's launch flags are not recorded
and rejected if they carry throttling exemptions (current flags are clean); the remote-debugging port stays
listening, without a client, during the idle interval; macOS offline adjudication and the `invoke-poc.ps1`
allowlist remain as listed in section 10.

## 12. Clean submitted-source refresh (`cd971f0`) and the second Opus review

Functional code is frozen at commit `cd971f0aaba9f3e49e669d63b06405a02f6cd088`; the desktop source archive
(sha256 `e34d279b…c667cb`, verified on both systems) comes from it. All artifacts are under
`native-bridge/feasibility/evidence/fresh-2026-10-26/submitted-cd971f0/` (read its `README.md` and
`identity.json`). Earlier dirty-Android and `4e9c685` results stay untouched and are not relabelled.

**Second review.** `claude-opus-5-5`, firstParty, session `f3746c7a-184d-4343-933a-12093f203b6a`, no
subagents (raw: `opus-review-cd971f0.json`): no CRITICAL; independently verified I1, I3 and I4; only the I2
record-keeping (section 11) remained IMPORTANT, now corrected above. **The matrix row R-01 stays `NOT RUN`
until the parent's focused Opus verification of these last records; nothing here marks it `PASS`, and
OTP-23 is not claimed done.**

| Area | Current fact | Not shown |
|---|---|---|
| Linux gates | exit 0; 316 tests, 0 failures/errors/skipped; Log-guard script run verbatim, exit 0; Gradle launcher Azul 21.0.12.1, daemon `/home/dev/.jdk/zulu21` | — |
| Native builds | `cargo test`/`clippy -D warnings`/`build --release` exit 0 on real Windows and macOS; Windows host `6cf72ed0…` (717,312 B), macOS raw host `c36894dc…` | no reproducibility claim |
| Windows Chrome | 12 scan-only cases PASS (existing **administrator**, filtered medium token); CH-03 wrapper still 0 visible and 1 hidden window | not a standard account; no link cleanup |
| Windows mixed `--phone` run | CH-05 saw one device; **PH-01 FAIL** (no phone in a later 30 s scan). Retained; cause **unproven** | — |
| Windows isolated single phone | one host, one scan, one phone (OnePlus 6T), byte-exact 4, 508, 509, 1016, 1017, 4095, 4096 at measured ATT MTU 517, 7 sends/7 replies, disconnect + shutdown, no surviving host | **supporting only**: not two-phone concurrency, repeated maxima, M6 or background |
| macOS | final-identifier fixture re-signed in Aqua; `codesign --verify --strict` and the `anchor apple generic` check passed; signed `e7c716c5…`; Chrome launch, `ready`, then `access_denied` | Apple Development, not CI/distribution provenance; prompt owner unknown; no allow/recovery; no TCC reset/bypass |
| Android | clean `cd971f0` debug APK `6382bf28…` (0.0.3-27-cd971f0-contract_otp23-desktop-alignment, code 3) installed as an update on both phones (readback matches; no `-g`, no data clearing); test APK `09280790…` unchanged | 10 Pro: Nearby Devices denied, `PermissionRequired`. 6T: granted at observation (mechanism unobserved), fixture held and ended `OK (1 test)` |
| Clean release APK | `a424caf7…`, unsigned; all DEX and the R8 mapping lack `SyntheticTransportFixture`, `scheduleSyntheticPush`, `OTP23_POC`; no scan/location permission; not debug | the older `release-inspection.json` (`75babe…`) is a different tree, retained |

Matrix consequences: `G-01`/`G-02` re-point to the clean evidence; `W-13` re-run; `W-03` is a narrow
`PASS` (one device event and stop); `W-04`/`W-05`/`W-06` stay `BLOCKED` with the single-phone run recorded
as supporting only; `A-03` now says the **10 Pro** is denied while the **6T** is granted (never that both
are denied); `A-01` records the Windows ordering observation but macOS is unobserved; `M-03` records the new
anchor-checked fixture hashes; everything else stays as before. Account waiver unchanged: only `W-01`,
`M-01`, `M-02` are `NOT RUN` and non-blocking. The decision remains **NO-GO**.

## 13. Ingress-admission fix for malformed frames of 512 bytes or less

The second Opus 5.5 session (`f3746c7a-184d-4343-933a-12093f203b6a`, raw follow-up:
`evidence/fresh-2026-10-26/opus-ingress-followup.json`) confirmed one more IMPORTANT gap and withdrew its
earlier statement that I1 was fully resolved. `postValue` still returned silently when `ingress.acquire`
failed, so a malformed frame that is not over 512 bytes — 183 bytes at MTU 185 (budget 182), or fewer than 4
bytes at any MTU — could be dropped while the peer's valid partial later completed. No normative rule permits
dropping a frame before parsing when the drop changes the outcome (the OTP2-ENC-004 allowance covers only
drops whose effect is to forfeit a record).

**Change (historical wording: uncommitted when written, after the `cd971f0` snapshot; committed as `e19c8ca`):** any `ingress.acquire`
failure in `postValue` now calls the same bounded `failClosed` used for over-512 writes: at most one queued
disconnect per peer (`overflowed`), queued ahead of every later frame, later frames refused at arrival, flag
cleared on removal/reconnect/close. Queue sizes (16 per peer, 8 peers) are unchanged, no payload is copied for
a refused admission, and `postUnsupported` stays a lost-response-only path. **Behavior change:** overflow of
*valid* frames is now a disconnect instead of silent loss; that loss already forfeited the record and the
session (OTP2-ENC-004), so only the silence is removed. This is not #120 M2 cleanup or OTP-20 hardening.

**Tests (independent, `SharingGattServerTest`, now 22):** MTU 185 partial (total 179, payload 178) + 15 ignored
stray continuations fill the 16 slots, a 183-byte frame is refused, drain, an otherwise-valid final must not
deliver (its control without the malformed frame delivers); MTU 517 partial (509/508) + 15 strays + a 3-byte
frame, same assertions; peer `b` stays healthy and only `a` is cancelled; many refused admissions queue exactly
one disconnect and a reconnect is clean. Restoring the silent drop fails 3 of them
(`ingress-final/mutation-silent-postvalue.log`).

**Current verification (Linux, same-shell `JAVA_HOME=/home/dev/.jdk/zulu21`, launcher Azul 21.0.12.1, daemon
`/home/dev/.jdk/zulu21`; `ingress-final/`):** `spotlessCheck detekt lintDebug testDebugUnitTest protocolCheck`
BUILD SUCCESSFUL, exit 0; **321** unit tests, 0 failures/errors/skipped (JUnit XML); the source-equal `ci.yml`
guard body run with `bash -v`, both greps shown, no matches, exit 0.

**What this does not change.** The `cd971f0` evidence (316 tests, clean APKs `6382bf28…` and release `a424caf7…`,
native Windows/macOS results, the single-phone diagnostic) keeps its own source identity: it was valid for that
code and is **not** physical proof of the overflow behavior. The APKs must be rebuilt and the release
re-inspected after this fix is committed (done at `e19c8ca`, section 14); no device work was done here. At that
phase `R-01` was `NOT RUN` pending a focused re-review (completed, section 14). NO-GO is unchanged; the account waiver still covers only W-01, M-01, M-02; every
two-phone, background and macOS permission criterion remains blocked.

## 14. FINAL record (committed `e19c8ca`): review passed as NO-GO work; decision NO-GO

**Frozen source.** Application code: `e19c8ca555b3275313cd325a12e15791a2cf894d` (sharing server sha256
`9850629b…61517`, debug APK version `0.0.3-29-e19c8ca-contract_otp23-desktop-alignment`, no dirty suffix). Verified
record-fix commit before it: `b1f3d7c3b2d5ecff9fab973055a2eb8a27e3c2f9`. Artifacts: `evidence/fresh-2026-10-26/final-e19c8ca/`
(`README.md`, `identity.json`, `linux-clean-gates.log`, `ci-log-guards-verbatim.log`, `isolated-build.log`, `windows/`).

**Review.** Actual CLI metadata: `claude-opus-5-5`, firstParty, session `f3746c7a-184d-4343-933a-12093f203b6a`, 0 subagents,
0 repository edits (raw `opus-final-ingress-verification.json`). It verified `e19c8ca`: the ingress gap is **fully resolved**,
**no remaining CRITICAL or IMPORTANT** finding, and the review may be recorded as successful NO-GO work. Matrix row `R-01` is
therefore `PASS` — as a review of NO-GO contract/docs/harness work only. The earlier reviews (`opus-initial-review.json`,
`opus-review-cd971f0.json`, the withdrawn "I-1 fully resolved" and the IMPORTANT `opus-ingress-followup.json`) are unmodified
history, and any "pending" or "uncommitted" wording in the sections above is historical for that phase.

**Current verification.** Clean rebuild after the commit, same shell: exported `JAVA_HOME=/home/dev/.jdk/zulu21`, Gradle launcher
Azul 21.0.12.1, daemon `/home/dev/.jdk/zulu21`; `spotlessCheck detekt lintDebug testDebugUnitTest protocolCheck` and `assembleRelease`
exit 0; **321** JVM tests, 0 failures/errors/skipped (`SharingGattServerTest` 22); the source-equal `ci.yml` guard body run with
`bash -v`, exit 0. Unsigned release APK `810aef8d…` inspected across all DEX and the R8 mapping (no `SyntheticTransportFixture`,
`scheduleSyntheticPush`, `OTP23_POC`; no scan/location permission; not debuggable). Debug APK `1e0ae023…` (instrumentation APK
`09280790…` unchanged) was installed as an update on both phones (readback matches, no `-g`, no data clearing). The unchanged
native Windows host (`6cf72ed0…`) ran the existing one-host diagnostic against it: 4, 508, 509, 1016, 1017, 4095 and 4096 bytes
byte-exact at measured MTU 517, own host gone, registration and task removed. That is **supporting single-phone healthy-path
evidence only**; no malformed-overload injection was run on hardware (covered by the independent regressions and the mutation
log), and it is not two-phone, survivor, M6 or background proof. The 10 Pro still denies Nearby Devices; the 6T is granted.

**Decision.** `D-02` records **NO-GO for OTP-24** from the per-row dispositions; its PASS means the decision was recorded, not that
OTP-24 is eligible. GO eligibility requires every row `PASS` except the three account-waived non-blocking rows (W-01, M-01, M-02).
Still blocking: two-phone concurrency and failure isolation, link-active cleanup modes, M6, both strict background scenarios,
macOS permission/signing provenance/allow/deny/recovery, the unsigned-host row M-11, the 10 Pro permission prerequisite, and the other
`BLOCKED` rows. OTP-23 is **not** claimed satisfied and no issue is closed.

**Remaining strict-driver limitations (unchanged, honest):** no visibility/focus continuity history, Chrome launch flags not recorded
or rejected, the remote-debugging port stays listening during the idle interval, no macOS offline adjudication tool, and the
`invoke-poc.ps1` allowlist excludes the strict driver. The `native-bridge/src-tauri/src/main.rs` header comment minor remains
dispositioned as before (not edited to avoid a code-file change after freeze).

## 15. POST-GRANT phase (sources `0d75bb2`, `15f57a4`, `3dbe3db`, `db5d4db`): permissions granted, physical evidence integrated; decision NO-GO

This is a NEW phase. Sections 1-14 (including the 321-test `e19c8ca` record and the `R-01` PASS scoped to `e19c8ca`) are the preserved
record of earlier phases and were not rewritten. Index, layout and data caveats: `native-bridge/feasibility/evidence/post-grant-2026-10-26/INTEGRATION.md`;
authoritative rows: `docs/otp-23-requirements-matrix.md`.

**Identities (read from real output, nothing new claimed).** Debug APK `063ac7ea…77a5e` (instrumentation `8ca89381…ede1c`, unsigned release `5d770ede…e3f10`), installed as an
update (`-r`, no `-g`, no uninstall/data clearing) on both phones; the apksigner certificate SHA-256 is `2081d17da5ec1a400e710720eba4a16939db774da54cdce936be30f4a848506e`,
the previously approved debug key. macOS: the same signed host `e7c716c5…eb91c`, bundle `me.nagaev.veles.bridge`, Team `7T39M26Z99`, Apple Development (not Developer ID, not notarized,
no CI provenance). Windows host: the same `6cf72ed0…fcda`; the Rust sources are unchanged since `cd971f0`. Clean `3dbe3db` gates: **334** JVM tests, 0 failures/errors/skipped (JUnit XML);
the DEX search of the release APK (sha256 `5d770ede…`) found no `TransportTelemetry`, `SyntheticTransportFixture`, `scheduleSyntheticPush` or `OTP23_POC` (output re-read and saved as `signing-and-gates/otp23-3dbe3db-release-dex-inspection.txt`; the original inspection output was not preserved), and the per-class JUnit summary is `signing-and-gates/junit-summary-334.txt`. `db5d4db` changed only the strict harness, its tests,
docs and the feasibility workflow. No new signing-CI provenance is claimed.

**Physical results (actual).**

- Two-phone sweeps: Windows 25/25 and macOS 26/26 cases PASS - independent connects, concurrent byte-exact records at 4, 177-180, 505-511, 1015-1017, 4095, 4096 (+3 repeats), stale handle, survivor and shutdown.
  Windows measured ATT MTU 517; macOS exposes only max write-without-response 512 (attribute 512, payload 508), not an MTU of 515. The earlier narrower sweeps have no preserved raw and are not cited.
- macOS lifecycle 6/6 PASS (scan active + two links). Windows lifecycle: the first run was mostly BLOCKED (`access_denied`); the scan-active rerun passed shutdown, tab-close, truncated EOF and failure-isolation
  but `LC-port-disconnect` and direct `LC-stdin-eof` are BLOCKED again. The intermittent Windows AccessDenied is **unproven** (not shown to be a phone permission); direct EOF is not Chrome provenance. All failures retained.
- Strict background: macOS cold and prewarm observational PASS (all CDP clients closed ~720 s, unsolicited 4096-byte records from both phones to a hidden connector, ordinary visible-focused tab uninterrupted, flags absent), raws ended BLOCKED
  for a missing phone file and were finalized OFFLINE with the strong validator, explicit sources and a measured clock (by the pre-`db5d4db` evaluator, an operator-inferred provenance; the HEAD finalizer yields BLOCKED for these legacy raws because ST-15 has no foreground evidence - see "Final review corrections" below). Windows: two setup FAILs, two ordinary-tab blur FAILs (retained), then the `db5d4db` OS-foreground-gated retries passed all 15 checks (cold and prewarm).
  **Native Messaging's intrinsic exemption is not excluded and `formal_otp23_acceptance` is false**, so the literal RFC "no exemption of any kind" wording is not satisfied; the matrix keeps W-11/W-12/M-09/M-10 `BLOCKED` and does not waive or rewrite the RFC.
- M6: 100 x 4096-byte exchanges against each of two centrals on the sole 6T; the machine adjudication is FAIL (label hashes differ across platforms; +/-3154 ms clock uncertainty cannot prove per-exchange overlap) and is retained. An actual
  `claude-opus-5-5` manual adjudication (session `97410d6b-c523-444a-86e8-f934f3da6b5c`) found A-02 PASS on the phone's single clock (16 overlapping pairs, 900 notify returns/callbacks per peer all success, 71 survivor records after the other peer left)
  with six recorded residuals (operator-asserted phone identity, inferred peer mapping, orderly not fault-injected isolation, loose barrier, synthetic fixture, key readback outside these files).
- macOS permission: a controlled retry (same Chrome pid, one extension load, same sentinel and signed binary) went baseline started -> `access_denied` (user toggled VelesOtp23PoC OFF) -> started again (ON): M-05 and M-06 PASS.
  The Settings toggle is the user's report, not TCC-database proof; the prompt is `unknown`; M-04 stays BLOCKED (guidance text and prompt owner not recorded). The first timed-out attempt is a retained FAIL.

**Still unmet (all keep NO-GO):** M-03 CI/distribution signing provenance; M-11 unsigned dev-host matrix; W-10 radio-off/adapter-absent; W-07/W-08 Chrome-provenance port-disconnect/stdin-EOF with links; W-02/M-08 connected-phase window
measurement; M-04; A-01; the Native Messaging exemption wording; `R-01` for this phase.

**Proposed, NOT approved - for owner review.** Amend the strict-background criterion to allow the architecture-required Native Messaging port while still forbidding artificial keepalive, audio, WebRTC, any other exemption and any CDP client,
and to require actual unsolicited delivery to a hidden connector with an ordinary tab focused for >= 600 s. Until the owner decides, nothing is rewritten and the rows stay BLOCKED.

**Review state.** `R-01` for this phase is **PENDING** until the parent's final fresh `claude-opus-5-5` review of the committed post-grant source and verification of the data fixes. Interim actual Opus sessions (`97410d6b-…`: preflight,
fixes review, last preflight, M6 adjudication) and the four `claude-sonnet-5-5` implementation sessions (`6e198e91-…`, `1aedf9f2-…`, `37885501-…`, `93fb7008-…`) are recorded from raw CLI metadata in `post-grant-model-provenance.json`; none is the final review.

**Final review corrections (actual `claude-opus-5-5` session `ceb6e424-1e26-4ea7-972b-efa4ba2dc633`: 0 CRITICAL, 2 IMPORTANT, 7 MINOR).** Raw result and report are copied unchanged under `reviews-and-provenance/`.

- IMPORTANT-1: the committed Mac strict adjudications (ST-01..ST-14 PASS) came from the pre-`db5d4db` evaluator; that is operator-inferred, not recorded in the files. The current finalizer returns BLOCKED for the same raw and phone files (ST-15 has no foreground evidence);
  the re-evaluations are saved as new files in `phone-evidence/mac/reevaluation-at-head/`, the originals are untouched, there is no legacy bypass and M-09/M-10 stay BLOCKED. Every new finalizer output records the evaluator git revision, scoped dirty status and SHA256 of the evaluator files.
- IMPORTANT-2: M-11 now states that no unsigned dev-registered host was built or run on the existing account, the grant covers only the signed bundle, no result exists, and it is not waived.
- MINOR: the A-02 overlap figures are reproduced by a committed deterministic algorithm; DEX and JUnit outputs are saved; W-05/M-07 fragment bounds are marked derived; the Windows strict run-to-phone-file mapping, the unpreserved first prewarm phone files and the proof residuals
  (operator `SERIAL=PATH` binding, operator-identified harness revision, Mac page-focus only, fix-wave metadata not captured) are in `INTEGRATION.md`; the final gate logs are copied as separate logs; AppleDouble files are git-ignored and not committed.

**`R-01` stays PENDING** until the parent independently verifies these corrections and a PASS row cites a review bound to the committed tree.

**R-01 ATTESTATION (current; supersedes every earlier "PENDING" / "review not done" statement in this section and in sections 1-14, which stay as history of their time).** R-01 is PASS for the reviewed NO-GO work of this phase, bound to the byte-preserved commit `d72c17798f3e665adec2f2ede1442e36634e1a3a`.
Review and fix verification: actual `claude-opus-5-5` (`canonicalModel` `claude-opus-5-5`, `firstParty`, 0 subagents, `is_error` false), session `ceb6e424-1e26-4ea7-972b-efa4ba2dc633`; no CRITICAL or IMPORTANT finding remains. Opus reviewed the working tree that became `5f3de250192a4f8b6d8f800531444a6b59d2143e` and did not itself re-open `d72c17798f3e665adec2f2ede1442e36634e1a3a`; the parent verified that `git diff 5f3de250192a4f8b6d8f800531444a6b59d2143e..d72c17798f3e665adec2f2ede1442e36634e1a3a` over `app/`, the feasibility scripts and the feasibility workflow is empty, so only evidence attributes, raw byte storage and metadata wording changed, and that `sha256sum -c SHA256SUMS` passes all 186 entries in a clean `git archive` of `d72c17798f3e665adec2f2ede1442e36634e1a3a` (`signing-and-gates/otp23-clean-export-d72c177-checksums.log`). MINOR-A is fixed; MINOR-B, C and D are deferred as nonblocking (residuals and owners in `INTEGRATION.md`). Attestation: `reviews-and-provenance/otp23-postgrant-r01-attestation.json` and `.md`.
The matrix is now 16 PASS / 13 BLOCKED / 3 NOT RUN (waived). **The decision stays NO-GO for OTP-24**; no other row changed and no CI result is claimed here.

**Decision: NO-GO for OTP-24.** OTP-23 is not claimed satisfied, OTP-24 is not implemented and no issue is closed. The account waiver still covers only W-01, M-01, M-02.
