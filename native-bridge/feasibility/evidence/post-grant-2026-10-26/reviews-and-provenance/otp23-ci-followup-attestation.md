# R-01 CI follow-up attestation (attestation only; decision NO-GO)

- Reviewed commit: `78f1784753ba0274e493ed35a92e27d6311b467a` (range `66519dd..78f1784`: a two-line Kotlin reformat, a test-only Windows launch wrapper in `test_scenario_matrix.py`, and an appended doc section). Production code, the matrix, evidence bytes and checksums were not touched by that commit.
- Reviewer (read from the raw CLI result): session `ceb6e424-1e26-4ea7-972b-efa4ba2dc633`, `modelUsage` `claude-opus-5-5`, `canonicalModel` `claude-opus-5-5`, provider `firstParty`, 0 subagents, `is_error` false. Raw `otp23-ci-opus-review-result.json` sha256 `f0db7b80ad65052d5a059cb8ab3952da4167797cfd5963fc57b0fb5ce0e279c1`; report `otp23-ci-opus-review.md` sha256 `317d18f28fafbca0ad797c7db6291f867e04b233822d3405b265e39de50960b7` (both copied unchanged).
- Verdict: PASS, no CRITICAL or IMPORTANT. The reviewer's Linux probe (non-executable file with a broken shebang) gave wrapper 3 PASS / control 3 FAIL.
- Fix author: `claude-sonnet-5-5`, session `de3b52c3-e10c-4e0d-be2c-0e766f9787c2` (`sonnet-ci-fixes-result.json`, sha256 `4f0426bdd301552cc0db53a43bdea9e306605c587650a9861a1279192a8194ee`).
- Initial CI at `66519dd` had 2 failed jobs and 8 passed jobs (per the parent). Retained failure logs: `signing-and-gates/otp23-ci-66519dd-lint.log` (spotlessKotlinCheck) and `signing-and-gates/otp23-ci-66519dd-windows.log` (3 x WinError 193).
- **Windows CI is PENDING.** The fix is verified on Linux only; no Windows CI pass is claimed. The next CI head is pending.
- Deferred nonblocking: CI-MINOR-2 (assert the wrapper was used) and the process-global patch NIT; the unskipped Windows CI job is the regression gate. MINOR-B, C, D from the earlier verification remain recorded.
- The base attestation (`otp23-postgrant-r01-attestation.*`, `verified_commit` `d72c17798f3e665adec2f2ede1442e36634e1a3a`) is unchanged. Matrix 16 PASS / 13 BLOCKED / 3 NOT RUN; NO-GO. Not an OTP-23 acceptance, not an OTP-24 GO.
