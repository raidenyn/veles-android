# Review record: tasks — Sol

- Artifact: tasks (`tasks.md`)
- Reviewer: Sol (GPT-6.1-Sol, medium)
- Session preset: `sol` = [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream:
  - brainstorm sha256 0dd5b5ff7c4e5e73ccfa73a45add4b80226a891bdefebe694e8e88e6e53d9b70 — re-pointed from 72734268… after the pass-2 reframe of the disproved premise (2026-11-18), approved by Sol; pass 2 checked the spec, tasks and plan for consistency
  - proposal sha256 b95589b23ea346551478295e40da157ad1d09d6d7d9f69401b7f83dbece010c0 — re-pointed from f029fdb6… after the pass-2 reframe of the disproved premise (2026-11-18), approved by Sol; pass 2 checked the spec, tasks and plan for consistency
  - design sha256 5ff56fe37158dc3b8dff75ae999429534aaa1fba2d08871241ddd8dd61f77057 — re-pointed from 3bf8a0bb… after the pass-2 reframe of the disproved premise (2026-11-18), approved by Sol; pass 2 checked the spec, tasks and plan for consistency
  - specs/ble-sharing-availability/spec.md sha256 79cbd6a500ce33e9da30d2339bd32fb35775284e5b2422334e2f1aef55995e27

## Pass 1

### Round 1 — changes-required

Reviewed content: tasks sha256 09f3942207985ed536c4dceb87fb7f238b2fef1f46ef64306532f9f00be24b9d

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | high | The gate allowed unlocking after 15 minutes and did not require the phone asleep, with the task removed, when the push arrives. | fixed: 4.2 keeps the phone locked and asleep, with the task removed, through `fired` and the connector receipt; it samples at arrival, and unlocking before arrival is FAIL. |
| 2 | medium | A callback after `close()` must cover both success and failure. | fixed: 2.2 asserts both after draining the main looper. |
| 3 | medium | The service test had no deterministic GATT startup and no cleanup. | fixed: 3.2 checks the Robolectric shadow first, falls back to an internal factory seam, and adds `@After` cleanup. |
| 4 | medium | The Gate B steps lacked executable commands. | fixed: 4.2 references the runbook and spells out the commands, package, receiver, log capture and pull. |
| 5 | low | The raw Log guard must run from the repository root. | fixed: 3.4 says so. |

### Round 2 — approve

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1–5 | — | Every round-1 finding is resolved; no new findings. | no action |

Approved content: tasks sha256 d27e46e84e13530cc1941ccf65fcf71532e01c1f739901d5d2129c1f9f290a8c

## Pass 2 — reframe after apply

Trigger: the real-device run disproved the tracked issue's premise (`apply.md`, "Finding: the issue's root cause was not reproduced"). With the owner's agreement (2026-11-18) the brainstorm, proposal and design were reframed as a hardening that removes a stale Android-5-era proxy; the code is unchanged. One Sol round reviewed the brainstorm, proposal, design and the tasks label together, checked the AOSP and repository claims, and checked consistency with the spec, tasks and plan.

### Round 1 — approve

Reviewed content: tasks sha256 b12a61d3cecab1da68433875e1d3315893282cec38bfee3ea173d80facb8a2a5

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | medium | Task 4.2 step 1 "Confirm the card leaves `Unsupported`" still assumes the disproved starting state. | fixed: step 1 confirms the card reaches `Advertising`, whatever it showed before Start. |

Approved content: tasks sha256 60a90f67ed2debce0b5703de0263fdd310b15128c6bca3594434fa1b180813e5 — re-pointed from b12a61d3… after 1 non-triggering minor fix (finding 1 above).
