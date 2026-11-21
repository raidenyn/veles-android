# Review record: tasks — Opus

- Artifact: `tasks.md`
- Reviewer: Opus (Opus-5.5, medium)
- Preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes:
  - `brainstorm.md` sha256 4df8162115fc5e4d9607694fe07ac6ce69ba2d394c7769fb043b9bd1321b01b0
  - `proposal.md` sha256 3462e1cb71ae8c6b94d7211639ece96148f504012ac1930b0142f6519f8b9a55
  - `design.md` sha256 e1ec96f8fb13b7d9b9e2e79a788f41ec3384fa8acf291c0c548200d8cfe61e35
  - `specs/affected-node-verification/spec.md` sha256 6b66da19a740e6f14966e01428c889946194f6ed5e2995fcfbd439f75ca623b4
  - `specs/agent-guidance-layout/spec.md` sha256 32598cf686a97288fdb9ca1ca064edf7d80a22be593eddb42b2e9c03128a5b39
  - `specs/hooked-rule-delivery/spec.md` sha256 294fbcc72241ec14343a6ae141f97280167c05bff937d5d21e8a0b5f9d2b97ff
  - `specs/verification-stop-hook/spec.md` sha256 a6894be8484832c8b0088cc1ad9665637fc0174b4ca1b88548a205c65c3551e7

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| medium | 7.1 drops the absent-tool and linked-worktree acceptance runs the spec still requires. | fixed: both in 7.1; the worktree case in 7.2. |
| low | The three "Pre-push rule triggers" scenarios are not named. | fixed: named in 3.3. |
| low | 6.1 omits the SIGKILLed-launcher limit and the contention workaround. | fixed: all three limits with workarounds. |
| low | 5.2 hides a fixture migration across five test files. | fixed: named, same commit. |
| low | 7.4 needs a clean tree after the 7.3 results. | fixed: commit `spike.md` first. |

### Round 2 — approve

No new findings.

Approved content: tasks sha256 6da11b4dca207c37b905621417203d8ded1b1798e63c447cbeeb0ac458c1799a
