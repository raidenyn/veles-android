# Review record: proposal

- Artifact: proposal.md
- Reviewer: Sol (GPT-6.1-Sol, medium per the roster; executed at the environment's reviewer pin, high effort)
- Session preset: [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: brainstorm.md sha256 c7826d38283fb95d1e5a1f2d8e6ac45c7e316a27c5582eb64eb54aa95e517903

## Pass 1

### Round 1 — approve

| Severity | Finding | Disposition |
| --- | --- | --- |
| low | The current baseline was overstated: the canonical test-bundle script already signs with Apple Development, so the problem is inconsistent operational practice, not missing support | fixed: the Why now distinguishes the canonical script from runs that use ad-hoc bundles or bare executables |
| low | The "recorded Mac experiment" had no traceable citation | fixed: Impact now links `evidence/macos-tcc-reuse-2026-11-16.md`, which holds the raw probe JSONs and the real-host transcript |

No critical or high findings.

### Round 2 — approve

| Severity | Finding | Disposition |
| --- | --- | --- |
| low | The "From" line still said every session uses ad-hoc signing or a bare executable | fixed: qualified to "a run may build or use ..." |

### Round 3 — approve (wording confirmation)

No findings. The reviewer confirmed the remaining low finding resolved and no new critical or high
problem.

Approved content: proposal sha256 699bd8c725342240c701b64678db3ebea98c03696eadefe958024d484104229c

## Pass 2 — rename amendment

### Round 1 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | The "non-breaking" claim and the edit list omitted the acceptance driver (`accdrv/macid.py`), which rejects the new app name | fixed: the specific edits and Impact now include the acceptance-driver compatibility change and its test |
| medium | The Impact still said "Docs and agent guidance only" and "No code" | fixed: it now says "no host implementation" and lists the two scripts, the acceptance driver and the tests |

### Round 2 — approve

No findings. The acceptance-driver compatibility is explicit and the scope no longer claims
docs-only.

Approved content: proposal sha256 612b4e8b99c7f73d262c0cd1278f9f3a7a563c6380e1e966a93513c8012edded
