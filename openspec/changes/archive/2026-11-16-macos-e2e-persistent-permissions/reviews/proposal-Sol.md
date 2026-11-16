# Review record: proposal

- Artifact: proposal.md
- Reviewer: Sol (GPT-6.1-Sol, medium per the roster; executed at the environment's reviewer pin, high effort)
- Session preset: [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: brainstorm.md sha256 01670f07cf77f34f048604305d5e26ec96b4867d92a67cdb9f017ef472da6c7d

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

## Pass 3 — identifier migration to me.nagaev.veles.debug

### Round 1 — approve

The proposal states the retirement of `me.nagaev.veles.otp23.poc`, the one fresh owner-approved grant
for `me.nagaev.veles.debug` and the migration scope. No findings.

### Round 2 — approve (consistency)

The brainstorm clarification changed its upstream hash but not the proposal's claims; the consistency
check found no issue.

Approved content: proposal sha256 9e5baea97f5cfed8ce9dfee7d96a3be679c440a109e6b69df6884d0157d52910
