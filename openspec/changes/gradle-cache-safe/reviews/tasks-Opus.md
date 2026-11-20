# Review record: tasks

- Artifact: tasks (`tasks.md`)
- Reviewer: Opus — Opus-5.5, effort medium
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes:
  - brainstorm.md sha256 08569a637f46e514517ceca16203c206ddbe75b2411cf58281620435d266e8d4
  - proposal.md sha256 25a769528cb93da164101cf41b20b68e06d68bde72a603d303a3fe0b398bfdc4
  - design.md sha256 d5e8bb74414cb10e505f0ec0503af7d4004907b1a0c2aa6fbb2d3bdcfcce6784
  - specs/gradle-build-caching/spec.md sha256 61f399e10d7a86f314334873f30525e815693d12085825d21a814a7703dd867e
  - specs/component-versioning/spec.md sha256 91972bfa3d988df0127e83c127bb7be4463d3c43e3106914f58cdc05e97f7a0e

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | `android-v9.9.9` tags created in worktrees land in the shared repository. | Fixed: tag safety rule (1.2), throwaway clones for every tagged run, tag check before 10.3 and push. |
| medium | 3.6 ran root CC checks before the root fixes. | Fixed: 3.6 runs from `rust/`; root runs in 4.6/8.1. |
| medium | 3.2/4.2 too large. | Fixed: split into 3.1–3.5 and 4.1–4.5. |
| medium | `bridgeBuild` unassigned. | Fixed: in the 4.4 opt-out pipeline. |
| medium | D5 reasons missing for several tasks; no audit. | Fixed: reasons in 2.3/4.1/4.5/5.1; audit 5.4. |
| medium | No way to observe load/store. | Fixed: group 7 preamble. |
| medium | `cc --version` ValueSource on Windows. | Fixed: 3.4. |
| medium | Docs before experiments. | Fixed: docs moved to group 9. |
| low | `RUSTFLAGS`/`CARGO_PROFILE_RELEASE_PANIC` and argument insertion. | Fixed: 3.4. |
| low | No "A problem fails the build" step. | Fixed: 4.6. |
| low | Branch sync to homepc; extension build. | Fixed: 8.3, 10.4. |
| low | JNI not exercised on Android. | Fixed: 10.3. |
| low | Linux-only probe subset. | Fixed: 1.5. |
| low | Harness AGP / not a gate. | Fixed: 2.2. |
| low | Contract-test regex and naming. | Fixed: 6.2. |
| low | CI green. | Fixed: 11.1. |
| low | `.githooks` action. | Fixed: 4.5, 5.1. |

### Round 2 — approve

| Severity | Finding | Disposition |
|---|---|---|
| low | 1.5 points to 9.3 instead of 8.3. | Fixed (non-triggering). |
| low | 7.1/7.2 revert does not reinstall the original tool versions. | Fixed (non-triggering): `rustInstall` after the revert. |
| nit | 8.1 "bridge* check entry points" vague. | Fixed (non-triggering): sets named. |

Approved content: tasks sha256 e99124297163786df4e1345af0797178ecb2ad2f687bc76c151f11b269ede03b (re-pointed after 3 non-triggering fixes from round 2: probe task number, reinstall after revert, named bridge entry points)
