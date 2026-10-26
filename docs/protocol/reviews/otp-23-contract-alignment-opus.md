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

## Final verification: PENDING

A fresh read-only Opus 5.5 re-review of the fixed tree has **not** run (matrix row R-01 `NOT RUN`). No
issue is closed and OTP-23 is not claimed complete.
