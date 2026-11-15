# Review record: plan

- Artifact: plan.md
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: brainstorm.md sha256 88e635325ad8eb2c63f5e752e642dd0cbcb3f32ca22ee22c7ad1407948755d7e;
  proposal.md sha256 d6811482c82a688b6c3c13cb18d587093f5a431888d110de7352d3e60e80fde2;
  design.md sha256 b7a0e4acea41c0c49c188c4f6670c89505d7502445b651c20b8d18ffc07ee9ea;
  specs/spake2-enrollment-exchange/spec.md sha256 77be0a6e187e67df03d2a5ce420ca8a7125639e24f5a497ccc2e7d35e2f1919f;
  tasks.md sha256 25f42ac48f3c620d98e7388ae2b2fb079168d94b70eb7e04a08f75ace0d933f0 (re-pointed after the
  tasks.md task 4.1 edit; Pass 2 approval recorded in reviews/tasks-Opus.md, consistency round 1 below)

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | The first controller test was broken five ways (subscribed before connect, no handshake, wrong fixture call, unawaited emit, a port method that does not exist) | fixed |
| high | Task 1 did not test the `unknown_handle` send-reply close or `state.reset`, and three of four tests were stubs | fixed |
| high | Task 2 Step 3 dropped the existing `closed || failed` guard | fixed |
| high | Tasks 2 and 3 were in the wrong order and the drain's close behaviour was unstated | fixed |
| high | Existing suites broke between Tasks 2 and 6 | fixed by moving the migration |
| high | Spec scenarios and tasks.md 4.2 items had no test | partly fixed |
| medium | `-t` filters selected the wrong tests | fixed |
| medium | The migration named `closeHandle` at `MockNativePort` sites and left the clock unnamed | fixed |
| medium | Task 2's harness was underspecified; Task 4 named a nonexistent coordinator | fixed |
| low | `finishHold` called `notifyEnded()` before `stop()` | fixed |
| low | The unit-only statement belonged in the PR note | fixed |
| low | "No real timers left pending" was unverifiable | fixed |

### Round 2 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | The red proof removed the hold itself and could not go red | fixed |
| high | Tests were comments rather than code | fixed for most, still open for some |
| medium | `closeListeners` name clash | fixed |
| medium | `driveToActive` migration dropped | fixed |
| medium | Missing tests for the disconnect-reply close and fail-closed cases | partly fixed |
| low | Only a non-null peer id is kept | fixed |
| low | `finishHold` had no early return | fixed |
| low | No plan record existed yet | this record |

### Round 3 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | Task 2's tests depended on Task 3 and then broke under it | fixed by moving the harness changes into Task 2 |
| medium | The tasks.md 4.1 deviation was made only in the plan | fixed: tasks.md 4.1 amended and re-approved in Pass 2 |
| medium | Missing tests for erasure, cancel, the failed-handshake case and two removal paths | partly fixed |
| medium | App-level timer injection was unstated | fixed |
| low | Missing imports | fixed |
| low | `closeHandle` signature differed between tasks | fixed |

### Round 4 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | Task 5's revocation test deadlocked on its own gate | fixed |
| high | Task 4 depended on the panel change and its red proof could not go red | fixed by reordering and asserting the phase |
| medium | The `-t "hold"` filter skipped three tests | fixed |
| medium | No connector-level peer-close, revoked-peer or no-deletion test | partly fixed |
| low | Task 2 left `pairThroughApp` awaiting a dead disconnect | fixed |
| low | The erasure assertion used a property | fixed |
| low | Missing import, wrong fixture reference, no `phone.close()` | fixed |

### Round 5 — changes-required (cap reached)

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | The revoked-peer teardown test in Task 5 needs Task 6's reachability fix | open |
| high | The reset and fail-closed tests await `resetTrust()`, whose `closeAll` awaits a disconnect the mock port never answers, so they time out | open |
| medium | The fail-closed test never runs fail-closed | open |
| medium | The "pending send" tests release the gate before the ack's send is entered | open |
| medium | Several connector assertions are missing (no recovery, no failure notice, no record deleted by the link) | open |
| low | Task 6's `-t "bound peer id"` filter skips the in-flight revocation test | open |
| low | The bound-expiry test does not assert the mirror | open |
| low | Task 4 Step 1's wording does not fit its target test | open |

## Escalation — round 5, cap reached

The pass is capped at five rounds. The reviewer still returns changes-required, so this pass stops
here and the decision goes to the user.

1. **What the plan is trying to do.** Turn the approved design and spec into a step-by-step
   implementation plan for the Chrome extension: a bounded hold after the activation ack, a
   terminal-active rule, revocation reachability, and tests that run without a wall clock.
2. **What is unresolved in ordinary words.** The plan's core hold tests and the controller tests are
   accepted as executable and correct. The remaining trouble is confined to two connector-level
   teardown tests and two "in-flight" tests: their setup can hang or never reach the state they
   claim to test, and a few assertions do not prove what their titles say.
3. **What the reviewer argues.** The reset test awaits an operation that itself waits for a
   disconnect reply the fake port never sends, so it times out instead of failing; the
   revoked-peer teardown test cannot pass before the reachability task adds the remembered peer id;
   the "send pending" tests release their gate before the send actually starts, so they exercise the
   held state rather than the in-flight state.
4. **What the agent argues.** Agreed on all of it; these are mechanical fixes (move one test into
   the reachability task, start the reset without awaiting and answer its disconnect, wait for a
   "send entered" signal before closing or revoking, and add the missing assertions). None of them
   touches the design, the spec or the approved behaviour.
5. **Options.**
   - **A — authorise one more planning pass (recommended).** Apply the eight fixes and run a fresh
     five-round pass. Cost: a few more review rounds; risk: low, the changes are local to the plan.
   - **B — accept the plan as it stands.** The implementer would hit the two hanging tests and the
     ordering problem during apply and fix them there. Cost: a broken red/green sequence; risk:
     medium, the fix would land without a plan-level review.
   - **C — drop the connector-level teardown tests from this change.** Keep the link-level tests
     only and record the connector coverage as a follow-up. Cost: less coverage of D4/D6; risk:
     medium, the spec's teardown scenario would ship with link-level evidence only.
6. **Decision needed.** Which option, and if A, whether I may run the extra pass now.

### User decision on the round-5 escalation

The user chose option A and authorised up to three more rounds (rounds 6 to 8) in this pass, "if needed".
The eight round-5 findings were applied to `plan.md` as mechanical edits (the revoked-peer test moved into
Task 6; the reset and fail-closed tests start the teardown unawaited and answer its disconnect; a
`nextSendEntered()` signal gates the two in-flight tests; the missing no-recovery, no-failure and
no-deletion assertions, the bound-expiry mirror assertion, the Task 6 filter and the Task 4 wording).

### Round 6 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| critical | Task 5 `pairingState` assertions used `toEqual` and ignored `fingerprint` | fixed (`toMatchObject`) |
| high | `fixture.controller.snapshot()` does not exist | fixed (`fixture.controller.controller.snapshot()`) |
| medium | The no-recovery assertion could not go red; no failure-notice check | fixed |
| low | Loopback red between Tasks 2 and 3; phone not closed in Task 5; port-death title unasserted; characterization tests not marked | fixed |

### Round 7 — approve

| Severity | Finding | Disposition |
| --- | --- | --- |
| medium | Task 3 does not say what fires the loopback close, and uses a real timer | fixed |
| low | Task 6 revocation test releases the gate before the revoke selects the link | fixed (`microFlush()`) |
| low | `finishHold` must not await before `stop` | fixed |
| low | Task 5 try/finally; terminate-during-hold is a characterization test | fixed |

The round-7 approval was not recorded as binding because the fixes above edited the plan afterwards.

### Round 8 — changes-required (extra-round limit reached)

| Severity | Finding | Disposition |
| --- | --- | --- |
| medium | The loopback transport's read loop keeps running after the phone's close and steals the reconnect frames | fixed in the plan after the round (`closeHandle` sets `running = false`); not re-reviewed |
| low | Task 3 wording: `await phonePairingState`, reuse the `:1198` check, assert the timer count right after `closeHandle` | fixed after the round; not re-reviewed |

No critical or high finding is open. There is no `Approved content` line: round 8 returned changes-required and the plan
was edited afterwards.

## Escalation — rounds 6 to 8 used

The three rounds the user authorised are spent. The last round found one medium issue in Task 3 only; it is fixed in
the plan but no reviewer has confirmed it. A ninth round, outside the user's authorisation, is needed for a binding approval.

### Round 9 — approve (user-authorised extra round)

| Severity | Finding | Disposition |
| --- | --- | --- |
| — | No open finding. The round-8 medium (loopback read loop) and low (Task 3 wording) were verified fixed against `loopback.test.ts` | confirmed |

Upstream hashes the reviewer read in this round: brainstorm.md sha256 88e635325ad8eb2c63f5e752e642dd0cbcb3f32ca22ee22c7ad1407948755d7e;
proposal.md sha256 d6811482c82a688b6c3c13cb18d587093f5a431888d110de7352d3e60e80fde2;
design.md sha256 b7a0e4acea41c0c49c188c4f6670c89505d7502445b651c20b8d18ffc07ee9ea;
specs/spake2-enrollment-exchange/spec.md sha256 77be0a6e187e67df03d2a5ce420ca8a7125639e24f5a497ccc2e7d35e2f1919f;
tasks.md sha256 25f42ac48f3c620d98e7388ae2b2fb079168d94b70eb7e04a08f75ace0d933f0 (differs from the header's value and from reviews/tasks-Opus.md; flagged to the user, not rewritten).

Approved content: plan sha256 f56dceae78e657edf28a40830d554bee6b2770780a44e7b4ebca10bdf134c149

## Consistency revalidation (upstream changed after the approval)

### Round 1 — approve

tasks.md task 4.1 was edited after the plan's header hash was written: the link-level tests live in
`test/connector/revocation-integration.test.ts` (no new file, no `npm test` entry) and 4.1 gained
`cancel()` coverage. The reviewer confirmed the change is cosmetic for the plan: the plan already
names that file in every task, adds no new test file, covers `cancel()` in the "cancel and dispose"
test, and has no stale `link-hold` reference. `plan.md` itself still hashes to
`f56dceae78e657edf28a40830d554bee6b2770780a44e7b4ebca10bdf134c149`, the approved content.

Upstream hashes re-pointed in the header, judged against this round:

- brainstorm.md sha256 88e635325ad8eb2c63f5e752e642dd0cbcb3f32ca22ee22c7ad1407948755d7e
- proposal.md sha256 d6811482c82a688b6c3c13cb18d587093f5a431888d110de7352d3e60e80fde2
- design.md sha256 b7a0e4acea41c0c49c188c4f6670c89505d7502445b651c20b8d18ffc07ee9ea
- specs/spake2-enrollment-exchange/spec.md sha256 77be0a6e187e67df03d2a5ce420ca8a7125639e24f5a497ccc2e7d35e2f1919f
- tasks.md sha256 25f42ac48f3c620d98e7388ae2b2fb079168d94b70eb7e04a08f75ace0d933f0

| Severity | Finding | Disposition |
| --- | --- | --- |
| low | The old tasks hash remained in the record header | fixed: re-pointed, citing this round |

Approved content: plan sha256 f56dceae78e657edf28a40830d554bee6b2770780a44e7b4ebca10bdf134c149
