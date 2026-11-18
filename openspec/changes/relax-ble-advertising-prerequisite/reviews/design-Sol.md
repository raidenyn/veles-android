# Review record: design — Sol

- Artifact: design (`design.md`)
- Reviewer: Sol (GPT-6.1-Sol, medium)
- Session preset: `sol` = [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream:
  - brainstorm sha256 0dd5b5ff7c4e5e73ccfa73a45add4b80226a891bdefebe694e8e88e6e53d9b70 — re-pointed from 72734268… after the pass-2 reframe of the disproved premise (2026-11-18), approved by Sol; pass 2 checked the spec, tasks and plan for consistency
  - proposal sha256 b95589b23ea346551478295e40da157ad1d09d6d7d9f69401b7f83dbece010c0 — re-pointed from f029fdb6… after the pass-2 reframe of the disproved premise (2026-11-18), approved by Sol; pass 2 checked the spec, tasks and plan for consistency

## Pass 1

### Round 1 — approve

Reviewed content: design sha256 ab9d89f3effd157ec469510b3c3e3f1e984379281be51998b89328c7350bcf7f

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | low | D4 omits the brainstorm's null-advertiser regression test. | fixed: D4 asserts that a `null` advertiser still reports `Unsupported`. |
| 2 | medium | "Not `Unsupported`" does not prove the prerequisite passed; setup and positive evidence are unspecified. Robolectric 4.14.1 provides the needed shadow APIs. | fixed: D4 specifies the setup and requires `START_STICKY`, `Starting`, foreground promotion and no self-stop. |
| 3 | medium | Stop→Start checks a normal restart, not failure→teardown→fresh Start→success. | fixed: D4 adds a service-level failure/retry test through an extracted `onGattState` sink with unchanged behaviour; D5 states that the hardware run does not observe this sequence. |

Approved content: design sha256 3bf8a0bbeb8b2d544f18e9e17bd6456b7ee514e4c24f2fb9b40fe4884cc1184d — re-pointed from ab9d89f3… after 3 non-triggering minor fixes (findings 1–3 above).

## Pass 2 — reframe after apply

Trigger: the real-device run disproved the tracked issue's premise (`apply.md`, "Finding: the issue's root cause was not reproduced"). With the owner's agreement (2026-11-18) the brainstorm, proposal and design were reframed as a hardening that removes a stale Android-5-era proxy; the code is unchanged. One Sol round reviewed the brainstorm, proposal, design and the tasks label together, checked the AOSP and repository claims, and checked consistency with the spec, tasks and plan.

### Round 1 — approve

Reviewed content: design sha256 a3bccfcaceebc8b5a34a18ebde48bf1c842135468469a077d7a2c191780afae9

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | low | Goals and D5 describe the locked/asleep acceptance without the owner-accepted deviations (no lock screen; `Dozing`). | fixed: the Gate B goal now names the deviations and points to `apply.md`. |

Approved content: design sha256 5ff56fe37158dc3b8dff75ae999429534aaa1fba2d08871241ddd8dd61f77057 — re-pointed from a3bccfca… after 1 non-triggering minor fix (finding 1 above).
