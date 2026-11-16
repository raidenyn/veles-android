# Review record: plan

- **Artifact**: `plan.md`
- **Reviewer**: Opus (Opus-5.5, medium)
- **Session preset**: [Opus]
- **Cap**: 5 rounds
- **Upstream**: brainstorm.md sha256 `d95c221184f9475e95e94a369245fecc303d15fd7067234105fdfd7f859c1e09`; proposal.md sha256 `8c447272e7419f88407e4f868d382f8b1f8950d082bbb0b57d788016ff0205e8`; design.md sha256 `04b8de0aca9d48d41568abb80d8fc7424e0dd4ab21d87b9c9f9d7a773b6b6e6c`; specs/mutual-peer-removal/spec.md sha256 `00150e06aaf91d1ffc0cb9d577cdd2c02dbc5cee147de0aba15bb05328b3dcd2`; tasks.md sha256 `e6d0f143f5c5b0e55b2c5a4da655ff2c1c13bd225dcf67cb243a44f5adf07f09`

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | H1: `holdOpen` armed the timer and subscribed before the map entry, so an immediate close listener left a stale timer and mark. | fixed — the entry is inserted first, the timer is armed, then the subscription; an immediate finish clears everything and the real unsubscribe is called after the check. |
| high | H2: `onRemovalHold` was inserted before `links`, silently mis-wiring positional callers because a links function is assignable to a handle callback. | fixed — the callback is the last constructor parameter, after `links`. |
| medium | M1: the already-gone and port-closed tests removed the handle or closed the port before the branch, so they proved nothing. | fixed — they are named defensive guards and are tested through a direct `holdOpen` seam or a stub controller. |
| medium | M2: `closeSelected` left the release placement open and the snippet used an awaiting variant. | fixed — `releaseHold` is fire-and-forget and runs before the `allSettled` await. |
| medium | M3: the fail-closed test was ambiguous between `ConnectorApp.failClosed()` and the manager's `closeAll`. | fixed — the manager test covers `closeAll`; Task 4 covers `app.failClosed()` with a flush. |
| medium | M4: the reset action and the suspended-handler seam were unnamed. | fixed — `app.resetTrust()` is named; the in-flight case stays at the manager level with `SuspendedOperations`. |
| medium | M5: the device run omitted re-pairing, extension loading and host build/registration. | fixed — re-pair each attempt, load `dist` unpacked, and build/register the host per `docs/otp-10-physical-runs.md`. |
| low | L1: the `send_failed` fixture was vague. | fixed — `wireErrorEvent` is named. |
| low | L2: the `app_error` test named no handler. | fixed — a stub handler that replies `APP_ERROR`. |
| low | L3: the non-removal close-after-reply assertion was not noted. | fixed — `removal.test.ts:416` is named and kept green. |
| low | L4: the final `git add -A` used a test subject. | fixed — explicit paths and a `docs(openspec)` subject. |

### Round 2 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | H1: the concurrent-peer-request test ordered `requestRemoval` before the phone's request and expected one sent request, which is the opposite case. | fixed — the inbound request is suspended first, `requestRemoval` runs while the handler is in flight, zero `REMOVAL_REQUEST` records are sent and the last record is `REMOVAL_RESPONSE`. |
| medium | M1: the `send_failed` test expected a manager disconnect that a wire failure does not produce (the connection becomes ambiguous first). | fixed — the plan asserts the ambiguous path's no-disconnect outcome, and adds a local non-ambiguous failure test for the `send_failed` branch. |
| medium | M2: awaiting `releaseHold` made a reset wait for the host's disconnect reply. | fixed — `releaseHold` is fire-and-forget. |
| medium | M3: no connector-level test for a hold that survives a reset. | fixed — Task 4 spies on the first `repository.revoke` call, runs `resetTrust` before the hold exists, then closes the handle and asserts no recovery. |
| medium | M4: "user/host disconnect" conflated a host event with a user request. | fixed — the two cases are named separately and the hold's extra-request rule is asserted. |
| low | L1: the both-on-the-air setup was unnamed. | fixed — `sendRequest` is used and `kept-no-response` is asserted. |
| low | L2: the revocation-finish fake could deadlock if it awaited `closePeer`. | fixed — it starts `closePeer` without awaiting. |
| low | L3: the responder-holds test did not assert immediate logical termination. | fixed — `ops.deleted`, `terminations === ['removal']` and `authenticatedSessions()` are asserted before release. |
| low | L4: some expected-fail claims were wrong. | fixed — the trivially passing tests are named. |

### Round 3 — approve

| Severity | Finding | Disposition |
|---|---|---|
| medium | M-A: Review Focus 1 and the `send_failed` test contradicted the task bullet about an immediate disconnect. | fixed — Review Focus distinguishes the wire ambiguous path (connection close, no manager disconnect) from the local `send_failed` path (one immediate disconnect), and 2.1 adds the stub-controller local-failure test. |
| low | L-A: the hold-survives-reset test did not ack the response's `sent` and suspended every `revoke` call. | fixed — only the first `revoke` is suspended and the response is acked before the handle close. |
| low | L-B: two expected-fail claims were inaccurate. | fixed — the trivially passing tests are named. |
| low | L-C: `abortAll` was listed under Task 3 but lands in Task 2. | fixed — removed from Task 3's file list. |

### Round 4 — cosmetic confirmation, approve

The round-3 edits only added a test and clarified expected-fail wording; no interface, constant or
implementation step changed. The round-3 approval stands. One optional wording note remains
(Review Focus 1's "`send_failed` path" means the manager's termination reason), which does not
block approval.

Reviewed hash: `a1a4fef3c56e276816524464e8f6e14349abe428ccf55ff58b47220b102b0cfa`.

Approved content: plan sha256 a1a4fef3c56e276816524464e8f6e14349abe428ccf55ff58b47220b102b0cfa
