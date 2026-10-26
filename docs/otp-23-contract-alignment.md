# OTP-23 contract alignment (2026-10-26)

Branch `contract/otp23-desktop-alignment`, based on PR #122 head
`fb224e8c54e0241c0d68c3c61687dc692a384102`. This document records what the review
of the two desktop PoCs changed in the canonical contract, the code and the guides,
and what it deliberately did **not** change. Nothing here approves OTP-24.

**Decision: NO-GO for OTP-24** until every row of the
[requirements matrix](otp-23-requirements-matrix.md) is `PASS` or carries the account
waiver below. The matrix starts fresh: every hardware row is `NOT RUN`; the signing
identity and allow/deny/recovery rows are `BLOCKED` on credentials and a human.

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
  Release inspection is recorded in section 7.
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
  It reports dependency graphs measured today and explicitly **does not** assert that
  retaining Tauri for packaging is justified; binary size, SBOM and a packaging proof are
  open (matrix row D-01 is `BLOCKED`). No installer was implemented.

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

## 7. Verification performed on this branch

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
* Current binary size/SBOM/packaging proof for the Tauri question (D-01).
* #120 M1, M2, M5, M6, M7, M8.
* The protocol document's status stays "specified, awaiting review": this revision has not had
  the independent protocol review that OTP-02 requires for acceptance.
