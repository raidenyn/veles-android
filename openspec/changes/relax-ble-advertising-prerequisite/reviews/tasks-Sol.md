# Review record: tasks — Sol

- Artifact: tasks (`tasks.md`)
- Reviewer: Sol (GPT-6.1-Sol, medium)
- Session preset: `sol` = [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream:
  - brainstorm sha256 727342681a009204f8dd7209d1e77024bb4616e82c26b33eb40f07c943cdd040
  - proposal sha256 f029fdb6639ca9d4eb3f03b0c1db725957d3c580806b3ae84f681caf46040a8b
  - design sha256 3bf8a0bbeb8b2d544f18e9e17bd6456b7ee514e4c24f2fb9b40fe4884cc1184d
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
