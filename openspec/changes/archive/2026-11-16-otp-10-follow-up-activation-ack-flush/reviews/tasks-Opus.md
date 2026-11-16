# Review record: tasks

- Artifact: tasks.md
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: brainstorm.md sha256 88e635325ad8eb2c63f5e752e642dd0cbcb3f32ca22ee22c7ad1407948755d7e;
  proposal.md sha256 d6811482c82a688b6c3c13cb18d587093f5a431888d110de7352d3e60e80fde2;
  design.md sha256 b7a0e4acea41c0c49c188c4f6670c89505d7502445b651c20b8d18ffc07ee9ea;
  specs/spake2-enrollment-exchange/spec.md sha256 77be0a6e187e67df03d2a5ce420ca8a7125639e24f5a497ccc2e7d35e2f1919f

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | Verification never ran the loopback suite the change migrates: it is absent from the `npm test` list and skips without its classpath | fixed: a task now runs `./gradlew sessionLoopbackCheck` (included in `protocolCheck`) |
| medium | The terminal-active guard missed the `receive` catch that calls `machine.onDisconnect()` (`link.ts:311`) | fixed: the catch is a named guard site |
| medium | Finish paths and the peer-id answer window were underspecified, so a later revocation could re-select an ended link | fixed: every finish sets `stopped` and the answer window is bounded |
| medium | The timer plumbing for the migrated tests was dropped | fixed: the connector forwards the full timer set and `startLink` injects timers |
| medium | No task covered the spec's teardown paths for fail-closed, reset and port death | fixed: connector-level tests added |
| medium | The docs-node checks ran conditionally and before the doc edit, and `components.py changed` was never invoked | fixed, then corrected again in rounds 2-3 |
| low | The panel test does not reach activation | fixed: the assertion moved to a flow that does |
| low | The new behaviour tests named no file | fixed: a new `test/pairing/link-hold.test.ts`, added to the `npm test` list |
| low | The subscription site was ambiguous | fixed |
| low | No tests for "Erasure before the hold" and "No OTP data" | fixed |
| low | Verification wording (`npm ci`, the device-unavailable rule, the commit scope) | fixed |

### Round 2 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| medium | The remembered peer id was answerable only "while the hold is armed", so a revocation during the ack send would miss the link | fixed: the window now runs from `activationCompleted()` until the link finishes |
| low | The peer-close finish read as if it skipped `notifyEnded()` | fixed |
| low | The docs checks still preceded the doc edit | fixed: a dedicated step runs them after it |

### Round 3 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| medium | The D7 assertion was anchored to a test that never activates, and any assertion after the close would pass without the change | fixed: the assertion now runs during the hold with `pairingFinished === false` |
| low | The revocation's own trust-store write was not asserted | fixed |

### Round 4 — approve

| Severity | Finding | Disposition |
| --- | --- | --- |
| low | 3.1 names `pairThroughApp` and the inline `:1934-1938` sequence as one site; either works, but the sentence should say which | deferred: either site yields a valid during-hold assertion, so the builder may choose; the inline copy avoids adding a pause hook to a shared helper |

Approved content: tasks sha256 0f47309b99dff8983937622ace1eab1468b5838ffeac22d2dc8a8b43a30c95a9

## Pass 2 (after task 4.1 was edited)

Task 4.1 changed: the link-level behaviour tests now live in
`test/connector/revocation-integration.test.ts` instead of a new `test/pairing/link-hold.test.ts`
(with its `npm test` entry), and 4.1 gained explicit `cancel()` coverage. Nothing else changed.

### Round 1 — approve

| Severity | Finding | Disposition |
| --- | --- | --- |
| low | 4.1 sits before 4.3, which carries the `startLink` timer injection and the post-ack site changes | noted: the plan's Task 2 Step 1 does the harness work in the same commit as the hold, so a builder working task by task is not blocked |
| low | The pre-edit file is not recoverable (the change directory is untracked), so "unchanged elsewhere" was checked against the earlier dispositions rather than a byte diff | noted |
| low (pre-existing) | 4.3 says to keep `ackDisconnect` unchanged while D8 describes it as waiting for "that event or the timer" | noted: 4.3's wording is the tighter one and the plan follows it |

The edit keeps every spec scenario covered, matches D8's seams (the harness, `startLink` and
`FakeTransport` live in that file) and contradicts no other task.

Approved content: tasks sha256 25f42ac48f3c620d98e7388ae2b2fb079168d94b70eb7e04a08f75ace0d933f0
