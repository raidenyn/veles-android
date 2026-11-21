# Review record: specs — Opus

- Artifact: `specs/` (four delta specs)
- Reviewer: Opus (Opus-5.5, medium)
- Preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes:
  - `brainstorm.md` sha256 4df8162115fc5e4d9607694fe07ac6ce69ba2d394c7769fb043b9bd1321b01b0
  - `proposal.md` sha256 3462e1cb71ae8c6b94d7211639ece96148f504012ac1930b0142f6519f8b9a55
  - `design.md` sha256 e1ec96f8fb13b7d9b9e2e79a788f41ec3384fa8acf291c0c548200d8cfe61e35

## Pass 1

### Round 1 — changes-required

#### specs/affected-node-verification/spec.md

| Severity | Finding | Disposition |
|---|---|---|
| medium | "Delete before it starts" contradicts D2 (after the run lock); the Incomplete-run scenario requires a contended run to erase a pass. | fixed: delete after taking the lock; scenario limited to runs that took the lock; "Contended run keeps a pass" added. |
| medium | The duplicate check reads only Build & Test or Commands sections; the design reads every shell fence. | fixed: any shell fenced block. |
| low | Gradle flags such as `-q` would make a duplicate "targeted". | fixed: value-less Gradle options ignored for task-only lines (design D1 aligned). |

#### specs/agent-guidance-layout/spec.md

| Severity | Finding | Disposition |
|---|---|---|
| low | "The agent-file check enforces the layout" still names stop predicates only and the drift check. | fixed: MODIFIED block names proof names, `requires`/`exempt`, the duplicate check and the new negative cases. |

#### specs/hooked-rule-delivery/spec.md

| Severity | Finding | Disposition |
|---|---|---|
| low | `exempt` validity stricter than D3. | fixed: design D3 aligned to the spec. |
| low | Host adapters cell holds two sentences. | rebutted: one cell, no pipe inside; renders correctly (accepted in round 2). |

#### specs/verification-stop-hook/spec.md

| Severity | Finding | Disposition |
|---|---|---|
| low | "Documented known limits" lacks the new limits. | fixed: SIGKILLed launcher, step groups, contention added. |
| low | "Remembered passes" reads as contradicting the branch-pass record. | fixed: names the remembered-pass record and the separate branch-pass file. |

### Round 2 — approve

| Severity | Finding | Disposition |
|---|---|---|
| low | The contention limit has no workaround. | fixed after approval: "stop again after the branch run finishes". |

#### specs/affected-node-verification/spec.md

Approved content: specs/affected-node-verification/spec.md sha256 6b66da19a740e6f14966e01428c889946194f6ed5e2995fcfbd439f75ca623b4

#### specs/agent-guidance-layout/spec.md

Approved content: specs/agent-guidance-layout/spec.md sha256 32598cf686a97288fdb9ca1ca064edf7d80a22be593eddb42b2e9c03128a5b39

#### specs/hooked-rule-delivery/spec.md

Approved content: specs/hooked-rule-delivery/spec.md sha256 294fbcc72241ec14343a6ae141f97280167c05bff937d5d21e8a0b5f9d2b97ff

#### specs/verification-stop-hook/spec.md

Approved content: specs/verification-stop-hook/spec.md sha256 a6894be8484832c8b0088cc1ad9665637fc0174b4ca1b88548a205c65c3551e7 (re-pointed after one non-triggering low fix made after the round-2 approval: the contention limit's workaround)
