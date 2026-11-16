# Review record: tasks

- **Artifact**: `tasks.md`
- **Reviewer**: Opus (Opus-5.5, medium)
- **Session preset**: [Opus]
- **Cap**: 5 rounds
- **Upstream**: brainstorm.md sha256 `d95c221184f9475e95e94a369245fecc303d15fd7067234105fdfd7f859c1e09`; proposal.md sha256 `8c447272e7419f88407e4f868d382f8b1f8950d082bbb0b57d788016ff0205e8`; design.md sha256 `04b8de0aca9d48d41568abb80d8fc7424e0dd4ab21d87b9c9f9d7a773b6b6e6c`; specs/mutual-peer-removal/spec.md sha256 `00150e06aaf91d1ffc0cb9d577cdd2c02dbc5cee147de0aba15bb05328b3dcd2`

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| medium | M1: no test checked the "no delivery claim" rule on the close and expiry paths. | fixed — 2.1 asserts the outcome stays `removed`, no failure/diagnostic is added, and nothing records delivery. |
| medium | M2: the connector recovery mark had no lifecycle tests. | fixed — 3.2 covers port close, prune, reset-during-hold and a hold surviving a reset. |
| medium | M3: the responder's own `closePeer`-from-handler race was not named in the manager tests. | fixed — 2.3 names it. |
| medium | M4: the existing immediate-disconnect responder assertions were updated after the implementation, leaving the suite red between tasks. | fixed — both updates moved into 2.1, before 2.2. |
| low | User/host disconnect on a held handle was untested. | fixed — added to 2.1. |
| low | The authorization termination's no-mark case was untested. | fixed — added to 1.1. |
| low | Fail-closed was tested only at the `SessionManager` level. | fixed — 2.3 asserts it through `ConnectorApp.failClosed()` or states the `closeAll` stand-in. |
| low | The expiry-after-handle-gone race was untested. | fixed — added to 2.1. |
| low | 5.1 missed `format:check` and the build prerequisites. | fixed — 5.1 lists them. |
| low | The device PASS criteria and failure/fallback rules were not explicit. | fixed — 4.2-4.4 state availability, both directions, fallback vs PASS, and the unit-only fallback. |

### Round 2 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| medium | M-A: the exact-effect assertion update moved after 1.2, so the suite would be red between tasks. | fixed — the update is in 1.1, before 1.2. |
| medium | M-B: no connector-app test for no recovery after a reset during a hold, or for a hold surviving a reset; the mark-clearing order relative to the snapshot was unstated. | fixed — 3.2 covers both and 3.3 states the mark is cleared only on a port close; an identity reset does not clear an in-flight hold's mark. |
| low | L1: the close-peer test path was ambiguous. | fixed — full path used. |
| low | L2: no test for no hold when the port is already closed. | fixed — added to 2.1. |
| low | L3: no test for `send_failed` and second-attempt paths. | fixed — added to 2.1. |
| low | L4: the concurrent case did not assert no second request. | fixed — added to 2.1. |
| low | L5: the both-on-the-air case was not tested at the manager level. | fixed — added to 2.1. |

### Round 3 — approve

| Severity | Finding | Disposition |
|---|---|---|
| medium | M1: 4.3 applied the Android → Chrome native-trace rule to both directions, although Chrome is the requester in Chrome → Android and has no hold. | fixed — the trace rule is Android → Chrome only; Chrome → Android requires both screens and both empty lists, with Chrome's disconnect after the response. |
| low | L1: tasks and design D4 described the mark reset differently. | accepted — D4's "full reset" means the port-close connector reset; the design needs no change. |
| low | L2: some spec THEN clauses lacked explicit assertions. | fixed — engine-erased, reset/fail-closed not-delayed and revocation-trust-unaffected assertions added to 2.1/2.3. |
| low | L3: no test for a hold created after the handle already left while the port is open. | fixed — added to 2.1. |
| low | L4: no `dependency` no-mark case. | fixed — added to 1.1. |

### Round 4 — cosmetic confirmation, approve

The round-3 edits only added test coverage and clarified the device criteria. The confirmation found
no implementation-plan change and the round-3 approval stands. Two low notes are deferred to task
4.1: the fallback sentence should say it applies only to Android → Chrome, and the doc should state
the Chrome → Android PASS criterion that design D8 lists only for the other direction.

Reviewed hash: `e6d0f143f5c5b0e55b2c5a4da655ff2c1c13bd225dcf67cb243a44f5adf07f09`.

Approved content: tasks sha256 e6d0f143f5c5b0e55b2c5a4da655ff2c1c13bd225dcf67cb243a44f5adf07f09
