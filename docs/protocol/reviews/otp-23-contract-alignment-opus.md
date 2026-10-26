# OTP-23 contract alignment — Opus 5.5 review disposition

## Reviewer metadata (actual)

Claude Code read-only review, `canonicalModel` **`claude-opus-5-5`**, provider `firstParty`, session
`1b23a799-1730-4b2d-905a-bd912cde7ef3`, no subagents, no repository edits, no device or network use. Raw CLI
result: `native-bridge/feasibility/evidence/fresh-2026-10-26/opus-initial-review.json` (unchanged).
Scope: `fb224e8..4e9c685` plus the uncommitted working tree and the fresh evidence. Implementation of the
fixes below: `claude-sonnet-5-5`, no subagents.

## Verdict of the initial review

OTP-24 **NO-GO** (correct). No CRITICAL findings. Mergeable as a NO-GO contract/harness/evidence change
once four IMPORTANT findings were fixed. **This is not a pass of the fixed tree.**

## Findings and fixes

| ID | Finding | Fix | Verification |
|---|---|---|---|
| I-1 | Over-budget write's discard silently dropped when the peer's 16 ingress slots are full; a final fragment then completes across the malformed frame | `SharingGattServer`: saturated ingress fails the peer closed with at most one queued disconnect (`overflowed` set; cleared on remove/reconnect/close; later frames refused at arrival). No payload copy, no unbounded queue. Invalid-operation oversize writes get `GATT_REQUEST_NOT_SUPPORTED` and leave the partial alone (minor 4) | `saturatedIngressFailsTheMalformedPeerClosedInsteadOfDroppingTheDiscard` (partial + 15 valid strays fill the budget, oversize cannot acquire, drain, otherwise-valid final must not deliver; peer b unaffected), `overflowDisconnectIsQueuedAtMostOncePerPeerAndClearedOnReconnect`, `oversizedValueOnAnInvalidOperationIsRejectedWithoutTouchingThePartial`; restoring the old drop fails 3 tests (`post-review/mutation-pre-i1.log`) |
| I-2 | G-01 PASS cited stale logs without Log-guard output; alignment section 7 stale | G-01 re-pointed to `post-review/` full logs, guard output and counts; section 7 marked SUPERSEDED; section references fixed | `post-review/gradle-required-gates.log` (BUILD SUCCESSFUL), `log-guards.log`, `unit-test-counts.txt` (316/0) |
| I-3 | Windows existing-account on-demand launch had no non-waived row | New `W-13` (admin filtered medium, CH-02, narrow) PASS; W-01 stays waived NOT RUN | matrix tests; evidence `fresh-windows-chrome.json` |
| I-4 | D-01 required OTP-25 installer work (circular) | D-01 records the evaluation and the decision (retention not approved / not justified); OTP-25 chooses another pipeline or re-justifies | `docs/otp-23-tauri-packaging-evaluation.md`, matrix tests |
| M-1 | stale `macos.rs` header (inferred MTU, W = M-3, 515) | comment rewritten to `LinkCapacity` semantics (code unchanged) | cargo fmt/clippy incl. macOS target |
| M-2 | Tauri doc arithmetic / stale sentences / reference | 254, current-evidence row, "previous section" | review of doc |
| M-3 | self-signed Apple-named chain passes text checks | `codesign --verify --strict -R='anchor apple generic'` added and mock-tested; docs state text helpers cannot prove trust. A team-OU pin was *not* added (no hardcoded team) | `test_macos_package_lib.sh` 39 checks |
| M-4 | looser oversize invalid-operation path | handled with I-1 | server tests |
| M-5 | strict driver does not record/reject Chrome launch flags or focus history | recorded as explicit follow-up; physical runs blocked and driver never claims formal PASS | alignment section 11 |
| M-6 | A-01, A-02, M-11 bare `NOT RUN` | `BLOCKED` with prerequisite blockers; no waiver expanded | matrix tests |

## Outstanding acceptance blockers (unchanged; NO-GO)

No fresh two-phone transfer, edge-size, link-cleanup, strict background (cold/prewarm) or #120 M6 result
exists; the phone fixture lacks Nearby Devices permission and the user cannot approve Bluetooth permissions
now; the macOS fixture is Apple Development-signed with CoreBluetooth `denied` (no Developer ID/CI
provenance, prompt owner, allow or recovery); the unsigned existing-account host (M-11) was not run; the
final review is pending. The installed Android APKs came from a dirty `fb224e8` tree, and later Android
changes have never had a physical run.

## Second review (clean `cd971f0`)

`claude-opus-5-5`, firstParty, session `f3746c7a-184d-4343-933a-12093f203b6a`, no subagents, no
repository edits (raw: `native-bridge/feasibility/evidence/fresh-2026-10-26/opus-review-cd971f0.json`,
unchanged). Verdict: no CRITICAL; **I-1, I-3 and I-4 independently verified as fixed**; I-2 only partly
fixed. Minor: D-01 still described as open in `CLAUDE.md`/`reproducible-builds.md`/`src-tauri/main.rs`;
stale "flood excess dropped" wording and an unmarked 313-test section in the alignment document; stale
`fixed_in_working_tree` provenance line.

| Item | Finding | Disposition |
|---|---|---|
| I-2a | the saved "verbatim" Log-guard file has only a header and exit marker | Withdrawn as proof and **preserved** (`post-review/log-guards.log`). Replaced by `submitted-cd971f0/ci-log-guards.sh` (text-equal to `ci.yml`) and `ci-log-guards-verbatim.log` (`bash -v`, both greps shown, no matches, exit 0) |
| I-2b | "Zulu JDK 21" cited `jdk.txt`, which is Ubuntu's PATH Java | `post-review/jdk.txt` preserved but no longer cited. `linux-audited-gates.log` + `gradle-version.txt`: exported `JAVA_HOME=/home/dev/.jdk/zulu21`, launcher Azul 21.0.12.1, daemon JVM `/home/dev/.jdk/zulu21`, exit 0, 316 tests |
| Minor 1 | D-01 "open" wording | `CLAUDE.md` and `docs/reproducible-builds.md` now state the negative retention decision. The `native-bridge/src-tauri/src/main.rs` header comment is left untouched on purpose (a comment-only edit would change a code file after the freeze) |
| Minor 2 | stale paragraphs | the "flood excess dropped" paragraph and the 313-test section are marked SUPERSEDED; section 11's claims are corrected, not silently relabelled |
| Minor 3 | stale provenance line | `model-provenance.json` records the second review and drops the "uncommitted" wording |

Also recorded from the same refresh: the earlier mixed Chrome+phone run failed `PH-01` (cause unproven; an
isolated one-host run passed and is supporting single-phone evidence only); the 10 Pro remains permission
denied while the 6T is granted; the clean release APK `a424caf7…` was rebuilt and inspected.

## Final verification: PENDING

The parent's focused Opus verification of these last record corrections has **not** run. Matrix row R-01 is
`NOT RUN`. No issue is closed, OTP-23 is not claimed complete, and OTP-24 remains NO-GO.
