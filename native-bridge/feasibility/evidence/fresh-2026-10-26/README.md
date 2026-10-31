# Fresh OTP-23 evidence — 2026-10-26

**Decision: NO-GO for OTP-24.** These are new measurements against the source
snapshot in `identity.json`. They do not replace or relabel the earlier no-go or
later successful PoC runs elsewhere in `evidence/`.

## What ran

- Both physical desktop systems built, tested and linted the actual native
  backends: `cargo test --locked`,
  `cargo clippy --locked --all-targets -- -D warnings`, and
  `cargo build --release --locked --bin otp23-host`.
- Windows stable Chrome ran `scripts/chrome_validate.py --flavour plain
  --medium --out evidence/fresh-windows-chrome.json` in the existing interactive
  administrator account's filtered medium-integrity token. All 12 reported cases
  passed. These were **scan-only** measurements: zero Veles phones were
  advertising. They prove neither real-link teardown nor record transfer.
- macOS packaged the new binary with `scripts/macos-package.sh --bundle-id
  me.nagaev.veles.bridge` in Aqua using the existing Apple Development identity.
  `codesign --verify --strict` succeeded. Stable Chrome launched it headlessly,
  negotiated `ready`, then scan failed with `access_denied` and CoreBluetooth
  authorization `denied`. See the JSON and native trace. The signing class is not
  CI/distribution-signing provenance, and no prompt owner or recovery is proved.
- Linux built the isolated Android APKs. After the user approved replacing only
  the old disposable `.otp23` and `.otp23.test` fixtures (debug-key mismatch),
  both phones installed the fresh APKs. Both instrumentation attempts failed the
  Nearby Devices prerequisite **before advertising**. An explicit adb grant was
  rejected by Android with `GRANT_RUNTIME_PERMISSIONS`; no permission bypass was
  used. The user could not approve the phone or Mac Bluetooth permissions now.
- `linux-required-gates.log` records successful
  `spotlessCheck detekt lintDebug testDebugUnitTest protocolCheck` on the normal
  build. Both raw Log guards from `ci.yml` also returned exit 0, with no matches.

The source archive intentionally lacks `.git`. `identity.json` links its verified
SHA-256 and selected source hashes on both machines to the exact local commit;
the driver's explicit git-unavailable result is retained rather than edited.

The initial Android APK was built before the snapshot commit: its actual
git-derived version is
`0.0.3-25-fb224e8-contract_otp23-desktop-alignment-dirty`. Its aligned source
edits were subsequently committed in `4e9c685`; this is **not** presented as a
clean committed Android build. Later malformed-ingress fixes are unit-tested
but have no physical transfer evidence. The failed prerequisite attempts remain
tied to their original APK hashes and version rather than being relabelled.

## What remains blocked

Fresh two-phone record I/O, maximum-record repetitions, setup-order observation,
disconnect isolation, link-active EOF/port-close/shutdown, both strict background
scenarios, two centrals on one phone (#120 M6), and macOS allow/deny ownership and
same-extension-state recovery remain unresolved. No background measurement was
started after the permission prerequisites failed.

The user's approved waiver skips tests requiring **new accounts** as non-blocking
for OTP-23; it is not a PASS and does not waive other permission, signing,
transport or lifecycle criteria. Current-account evidence is labeled as such.

## Cleanup

Both fixture applications were stopped. The owned Native Messaging registrations
were removed, the temporary Windows build/smoke tasks were removed, no host
remained, and both `homepc_session_end` calls completed. Personal Chrome profiles,
the normal Veles application's data, and existing TCC grants were preserved.

## Artifact interpretation

- `fresh-windows-chrome.json`: unchanged driver result; case-level PASS values are
  confined to their stated scan-only scope.
- `fresh-macos-final-id-scan.json` / `.trace.log`: unchanged denied scan, including
  Chrome ancestry and no observed on-screen host windows.
- `fresh-*-host-manifest.json`: copies of the actual exact-origin registrations
  before removal, not installer outputs.
- `fresh-*-native-*.log`, `fresh-windows-cargo-test.log`,
  `fresh-windows-clippy.log`, `fresh-windows-build.log`: actual target checks.
  Ignored radio tests are not counted as passes.
- `phone-*-prerequisite.log`: actual failing instrumentation results, not BLE
  transfer failures or passing fixture holds.
- `model-provenance.json`: actual implementation model identity from CLI metadata;
  a quota-limited review probe is explicitly not a completed final review.

## Post-review addendum

`post-review/` holds the full outputs of the checks re-run after the initial Opus 5.5 review fixes
(see `docs/protocol/reviews/otp-23-contract-alignment-opus.md`): Gradle required gates, the verbatim
`ci.yml` Log-guard step and exit, JDK, unit counts (316/0), harness/cargo/protocol logs and the mutation
run. `linux-required-gates.log` and `linux-final-required-gates.log` and `release-inspection.json` describe
earlier trees and are unchanged history.

## Submitted-source refresh and second review

`submitted-cd971f0/` (see its own `README.md`) holds the clean-source refresh. The second actual Opus 5.5
review is `opus-review-cd971f0.json`. `post-review/log-guards.log` has only a header and exit marker and
`post-review/jdk.txt` is PATH's Ubuntu Java; both are preserved but are not proof of the verbatim guard step or
of the Gradle runtime (use `submitted-cd971f0/ci-log-guards-verbatim.log` and `linux-audited-gates.log`).

## Ingress-admission fix (`ingress-final/`)

After the second Opus review's follow-up (`opus-ingress-followup.json`), `postValue` fails the peer closed on any
ingress admission failure. `ingress-final/` holds the same-shell Gradle version + required gates log, the
source-equal CI guard script and its `bash -v` execution, JUnit-derived counts (321/0) and the mutation log. Earlier
directories keep their own source identities (`cd971f0` and older) and are not proof of this behavior.

## Final checkpoint (`final-e19c8ca/`)

Committed `e19c8ca` is the frozen application source; `final-e19c8ca/` holds its clean gates, the verbatim guard run, the
isolated APK build, identity and the single-phone refresh. `opus-final-ingress-verification.json` is the actual final review
(`claude-opus-5-5`, no remaining CRITICAL/IMPORTANT). Earlier reviews and directories are unmodified history. Decision: NO-GO.
