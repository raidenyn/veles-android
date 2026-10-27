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

## Follow-up from the same session: ingress admission (IMPORTANT, fixed in the working tree)

Raw: `evidence/fresh-2026-10-26/opus-ingress-followup.json` (session `f3746c7a-184d-4343-933a-12093f203b6a`, `claude-opus-5-5`, no edits/builds/devices). The reviewer withdrew "I-1 fully resolved": `postValue` dropped silently on `ingress.acquire` failure, so a malformed frame of 512 bytes or less (183 B at MTU 185, or under 4 B) could be lost while a valid partial later completed. Fix: any admission failure uses the same bounded `failClosed` (one queued disconnect per peer; later frames refused; no queue-size change; no payload copy). Tests: MTU 185 and MTU 517 saturation + control, peer isolation, one-disconnect/reconnect; restoring the silent drop fails 3 (`ingress-final/mutation-silent-postvalue.log`). Gates: `ingress-final/` (321 tests, same-shell Gradle/JDK identity, verbatim guard). The earlier review chain is preserved: both previous reviews had judged I-1 fixed and were wrong about its completeness. See alignment section 13.

## Final verification (committed `e19c8ca`)

Raw: `native-bridge/feasibility/evidence/fresh-2026-10-26/opus-final-ingress-verification.json`. Actual CLI metadata:
`claude-opus-5-5`, firstParty, session `f3746c7a-184d-4343-933a-12093f203b6a`, 0 subagents, 0 repository edits; reviewed
`b1f3d7c..e19c8ca` (verified record-fix commit `b1f3d7c3b2d5ecff9fab973055a2eb8a27e3c2f9`). Verdict: the ingress gap is **fully resolved**; **no
remaining CRITICAL or IMPORTANT** findings; the review may be recorded as successful NO-GO contract/docs/harness work. Matrix row R-01 is
`PASS` on that basis, with the metadata above as its evidence. Everything earlier in this document (including the sections that say
"pending", "uncommitted" or "working tree") is the historical record of the phase in which it was written and was not rewritten.

**Decision: NO-GO for OTP-24** (matrix D-02 records it). OTP-23 is not claimed satisfied, OTP-24 GO is not claimed, and no issue is
closed. See alignment section 14.

## Portability follow-up after the final review (CI run 40291094274)

PR #123's `macos-26` job failed `bash scripts/test_macos_package_lib.sh` with `FAIL: invalid id ünï.code` (1 of 39 checks); Linux passed all 39.
macOS Bash's locale collation lets the `[A-Za-z0-9]` bracket ranges in `validate_bundle_id` match accented letters. The Windows job was
**cancelled** by the Mac failure (matrix fail-fast), not an actual Windows failure. Fix (packaging helper and workflow only; application
code remains frozen at `e19c8ca`): `validate_bundle_id` now declares a function-local `LC_ALL=C` (Bash 3.2 compatible), validation rules
unchanged and not weakened; the shell test adds a locale regression (non-ASCII ids rejected and the valid id accepted under C, POSIX and the
available UTF-8 locales; 69 checks now) and the feasibility workflow sets `fail-fast: false` so each OS reports independently (every matrix job
must still pass). Locally only the C, C.UTF-8 and POSIX locales exist, none with accented collation, so the macOS failure itself was **not**
reproduced here; the real confirmation is the `macos-26` job. No evidence is relabelled.

Focused read-only verification completed in the same actual `claude-opus-5-5` / firstParty session
`f3746c7a-184d-4343-933a-12093f203b6a` (raw `opus-ci-portability-review.json`): **no CRITICAL or IMPORTANT findings**,
ASCII rules unchanged, Bash 3.2 compatible, no acceptance criterion weakened. Mergeability is conditional on the real Mac and Windows
CI jobs passing. The mandatory local Gradle gates were rerun successfully after this helper-only change. The NO-GO decision is unchanged.

## Post-grant phase: interim Opus reviews and manual adjudication (NOT the final review)

Everything above is the preserved record of the earlier phases. This section records the reviews made around the post-grant harness (`0d75bb2`, `15f57a4`, `3dbe3db`, `db5d4db`).

**Reviewer metadata (actual, from raw Claude Code CLI results, copied unchanged under `native-bridge/feasibility/evidence/post-grant-2026-10-26/`).** One session,
`97410d6b-c523-444a-86e8-f934f3da6b5c`, `modelUsage` key `claude-opus-5-5`, provider `firstParty`, 0 subagents, read-only (no repository edits, no builds, no devices). Four raw results:

| Raw | Outcome | Disposition |
|---|---|---|
| `reviews-and-provenance/otp23-resume-opus-preflight.json` | **eight IMPORTANT** harness defects before measurement: ST-13 flag scan empty on elevated Windows, weak ST-08 phone check, wrong distinct-phone field, unmeasured M6 clock offset, no real M6 barrier, M6 "same phone" resting on a typed label, a path that could mark M6 discharged, a canned recovery human note | fixed by the `claude-sonnet-5-5` harness waves before measurement |
| `reviews-and-provenance/otp23-resume-opus-fixes-review.json` | "not approved yet": six of eight fixes correct; **IMPORTANT** final phone snapshot could miss late callback failures and look clean; lifecycle wrapper hygiene check dropped; should-fix text (a note not to claim `SyntheticTransportFixture` vanishes from release without inspecting the release APK) | fixed (service-stop wait, `final_after_service_stopped`, `pending_unresolved`/`pending_dropped`, wrapper hygiene restored) |
| `reviews-and-provenance/otp23-resume-opus-last-preflight.json` | **approved for physical measurement**; no remaining CRITICAL or IMPORTANT | measurements then ran on `3dbe3db`/`db5d4db` |
| `m6/otp23-resume-opus-m6-adjudication.json` | **A-02 (M6) PASS by independent manual adjudication**; the machine FAIL is retained; six residuals | recorded in matrix row A-02 and alignment section 15 |

The M6 adjudication states, and the matrix preserves: the machine adjudicator failed `same_phone_identity` and `real_io_intervals_overlap` (uncertainty +/-3154.105 ms, not shrunk); the PASS rests on the phone's single
elapsed-realtime clock (16 overlapping record pairs, 1505 ms summed; 100 records per peer fragmented 8x512+36 bytes; 900 notify returns all SUCCESS and 900 callbacks all GATT_SUCCESS per peer; 71 survivor records after the other peer disconnected).
Residuals: operator-asserted physical phone, inferred peer-to-central mapping, orderly (not fault-injected) isolation, loose barrier (2.8 s), synthetic fixture with no Veles trust, key use read from the installer readback. It is **not** an OTP-24 GO.

**Implementation sessions (actual `claude-sonnet-5-5` modelUsage key, 0 subagents):** strict `6e198e91-fd1d-4e54-a2fc-fa9024059b42`, lifecycle `1aedf9f2-a8e8-437f-a321-8afcacf4d7d1`,
permission `37885501-841f-4246-9c90-9a90bdd4ed79`, phone `93fb7008-5c51-4ade-9ff8-082723b3dd3b` (`reviews-and-provenance/sonnet-*-result.json`, `post-grant-model-provenance.json`).

**Status of `R-01`.** See the final-review section below: the review ran and its corrections are made but unverified; matrix row `R-01` is `BLOCKED`/PENDING for this phase until the parent independently verifies them. The earlier `R-01` PASS
(session `f3746c7a-184d-4343-933a-12093f203b6a`, verified commit `e19c8ca555b3275313cd325a12e15791a2cf894d`) is preserved and scoped to the source before this harness and telemetry code. None of the four interim raws above replaces it. The NO-GO decision is unchanged.

## Post-grant final review (session `ceb6e424-1e26-4ea7-972b-efa4ba2dc633`) and corrections

**Reviewer metadata (actual).** Claude Code CLI result `reviews-and-provenance/otp23-postgrant-final-opus-result.json` (copied unchanged; report `otp23-postgrant-final-opus-review.md`): `modelUsage` key `claude-opus-5-5`, 0 subagents, read-only, scope `7afd164..db5d4db` plus the uncommitted working-tree integration.
The earlier integration pass was `claude-sonnet-5-5`, session `fe9d1222-792e-4494-a2af-3e5468bb4d6a` (`sonnet-postgrant-integration-result.json`).

**Result.** 0 CRITICAL, 2 IMPORTANT, 7 MINOR; no acceptance criterion rewritten; NO-GO unchanged.

| Finding | Disposition |
|---|---|
| IMPORTANT-1: HEAD finalizer cannot reproduce the committed Mac PASS adjudications; evaluator not recorded | Option (b): documented as pre-`db5d4db` evaluator, operator-inferred; new HEAD re-evaluations (BLOCKED, ST-15) saved as separate files; finalizer outputs now record evaluator revision, scoped dirty flag and SHA256 of `strict_background_core.py`, `strict_phone_validation.py` and itself; tests re-run the finalizer on both committed Mac raws; no legacy bypass, no status promotion |
| IMPORTANT-2: M-11 blocker stale | Rewritten: not executed, no unsigned host built or run, grant covers only the signed bundle, no result, not waived; guard test added |
| MINOR-3..9 | A-02 algorithm committed with output; DEX and JUnit outputs saved and tested; fragment bounds marked derived; Windows run-to-phone mapping and unpreserved files stated; proof residuals listed; separate gate logs copied; AppleDouble reworded (git-ignored, not committed) |

**`R-01` stays `BLOCKED`/PENDING.** These corrections are not yet independently verified by the parent, and the integration is uncommitted. The review PASS row may be written only after verification and a commit, citing this review bound to that commit.

## Post-grant R-01 attestation (current; supersedes the earlier PENDING statements above, which are preserved as history)

R-01 is PASS for reviewed NO-GO work, bound to byte-preserved commit `d72c17798f3e665adec2f2ede1442e36634e1a3a`. Reviewer metadata from the raw CLI results: session `ceb6e424-1e26-4ea7-972b-efa4ba2dc633`, `claude-opus-5-5`, `firstParty`, 0 subagents, `is_error` false
(final review raw sha256 `8990eb9b…`, fix verification raw sha256 `1599974b…`, both unchanged under `native-bridge/feasibility/evidence/post-grant-2026-10-26/reviews-and-provenance/`). No CRITICAL or IMPORTANT finding remains; MINOR-A fixed; MINOR-B/C/D deferred as nonblocking.
Opus reviewed the tree that became `5f3de250192a4f8b6d8f800531444a6b59d2143e` and did not itself re-open `d72c17798f3e665adec2f2ede1442e36634e1a3a`; the parent verified an empty `app/`, feasibility-scripts and workflow diff `5f3de250192a4f8b6d8f800531444a6b59d2143e..d72c17798f3e665adec2f2ede1442e36634e1a3a` and a clean `git archive` SHA256SUMS pass (186/186).
Matrix: 16 PASS / 13 BLOCKED / 3 NOT RUN (waived). The decision stays **NO-GO for OTP-24**; this is not an OTP-23 acceptance and no CI result is claimed.

## CI failure disposition after `66519dd` (appended; earlier review and evidence text above is unchanged)

Pushed `66519dd0f5d8057941c71ddc8f07b2948502f146` failed two CI jobs. Both are test-side; no production code, raw evidence, metadata or checksum file changed, and no hardware was used.

| CI failure | Cause | Fix |
|---|---|---|
| `spotlessKotlinCheck`: `FragmentFixturesTest.kt` line 55 | ktlint wants the `transactions.size == 1 && committed.last()...` continuation split onto two lines. The earlier local `spotlessCheck` reported UP-TO-DATE, so the formatter did not run. The reason the local cache missed the change is **not determined**; it was not investigated beyond forcing a real run with `spotlessApply --rerun-tasks` | the single two-line reformat; nothing else changed |
| Windows `test_scenario_matrix.py` `DirectStdin` (3 tests, `WinError 193`) | the fake host is a shebang script, which Windows cannot execute directly | test-only launch boundary in `test_scenario_matrix.py`: `Popen` is wrapped so the fake host runs as `sys.executable <script>` on every OS. Still a real process with real pipes, so the stdin-EOF and cleanup assertions are unchanged; no test skipped; production `DirectHost` untouched |

The Windows fix is verified on Linux only. It has not been run on Windows; the next Windows CI run is the evidence.

**`R-01` scope.** The current R-01 PASS covers the reviewed `66519dd` source and the `d72c177` snapshot as attested above. The two test changes in this section are **new, uncommitted and not reviewed by Opus**; they are pending focused Opus verification by the parent, who will attest the new commit hash afterwards. No fresh review of them is claimed. Matrix stays 16 PASS / 13 BLOCKED / 3 NOT RUN; the decision stays **NO-GO for OTP-24**.
