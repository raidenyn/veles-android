# Review record: design — Opus

- Artifact: design
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: opus = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream:
  - brainstorm.md sha256 490e0eb287dc4ef2ae56fad35981b90928aedf37321deaf1384f75a4aeac9705
  - proposal.md sha256 3526ef09aa9b5255a6dd1671f1c846592ed925dfed6e68906e3e6db44c1bbfc5

## Pass 1

### Round 1 — changes-required

Reviewed content sha256 cc4916b1052a18d291d5c6ad8a6756b4a053db71507e72288fb179922414378f.

| Severity | Finding | Disposition |
|---|---|---|
| medium | Without `--verbose` gitleaks prints only a count, so the block message cannot name the finding and the "stderr names the finding" assertion fails. | fixed: D4 adds `--verbose`, both streams to stderr; D7 asserts rule ID and file in combined output. |
| low | Test runs `.githooks/pre-commit` relative to the temp repo, and does not isolate `HOME`/`PATH`. | fixed: absolute hook path, isolated environment. |
| low | D5 "full scan of the current tree" vs D6 full-history scan. | fixed: D5 refers to the D6 `gitleaks git` scan. |
| low | Two `build.gradle.kts` strings would go stale. | fixed: listed in D8. |
| low | Exit-code statement omits 126 (unknown flag). | fixed. |
| low | Risks cite "config-loading tests" that do not exist. | fixed: explains the refused case catches a broken config. |
| low | Archive layout and version output left to apply. | fixed: apply records both observed values. |

Seven minor fixes since the last completed review: triggering, so round 2.

### Round 2 — approve

Reviewed content sha256 bd6c33cd356ff0d161a5b02a4f2679ae997a06e0a0b52c60f56915cdf3642dba.

| Severity | Finding | Disposition |
|---|---|---|
| low | "The key text" ambiguous for an ID/secret pair. | fixed: only an access key ID line is staged; it must be absent from both streams; expected rule named. |
| low | `VELES_GITLEAKS` set but not executable: fallback unclear. | fixed: set means that path only. |
| low | "coreutils it calls" vague under a minimal `PATH`. | fixed: hook uses builtins, `PATH` holds only `git`. |
| low | Stub needs shebang and executable bit; Windows wording contradicts direct execution. | fixed: executable stub; tool-error case always skips on Windows. |
| low | Stream split of gitleaks output unconfirmed. | fixed: confirmed on the pinned binary at apply. |

Approved content: design sha256 f795f8177cc4fb9864301661b45846faf700d34045d413625120fb3cbb40ba31 — re-pointed from bd6c33cd… after five non-triggering minor fixes from round 2 (key layout, `VELES_GITLEAKS` no fallback, builtins-only `PATH`, executable stub and Windows skip, stream split confirmed at apply).

## Re-point after a non-triggering edit

Approved content: design sha256 da883c49065127429e1ef475b25b33b2bd626e55a3f83bca6b374d7d0b82bf6d — re-pointed from f795f817… after two non-triggering minor fixes raised by the specs review (round 2): D1/D9 state that a declined push protection sends the no-CI-job decision back to the user before the PR leaves draft, recorded in the PR; the D7 "missing" row places a leak-code stub at the build path to prove there is no fallback.

The re-point above was the sixth and seventh minor fix since the round-2 approval, which crosses the
five-fix threshold; it is therefore triggering, and pass 2 below re-reviews the design.

## Pass 2

### Round 1 — changes-required

Reviewed content sha256 da883c49065127429e1ef475b25b33b2bd626e55a3f83bca6b374d7d0b82bf6d.

| Severity | Finding | Disposition |
|---|---|---|
| medium | D7 refused row still said "key and secret" after the ID-only layout edit. | fixed: "an AWS access key ID built at runtime". |
| low | D4 step 2 "set" vs spec "set and non-empty". | fixed. |
| low | D9 silent on where a user-chosen CI job is planned. | fixed: added to this change's plan and re-reviewed first. |
| low | Missing-row stub must be executable for the no-fallback proof. | fixed: executable bit set and checked. |

### Round 2 — approve

Reviewed content sha256 33875a3a5eeab54503d221be4a62abe9689a9bfbae6fa6d0b6bcd83be057c37b.

| Severity | Finding | Disposition |
|---|---|---|
| low | Refused row does not check the block message's `--no-verify` advice. | fixed: asserted. |
| low | D8 `CONTRIBUTING.md` bullet omits "only warns until installed" which the spec requires. | fixed. |

Approved content: design sha256 0eafbff0ece7e06778f6768499513d052421fa5d3201a388ea244fc65661e2b3 — re-pointed from 33875a3a… after two non-triggering minor fixes from pass 2 round 2 (`--no-verify` assertion, `CONTRIBUTING.md` warn note).
