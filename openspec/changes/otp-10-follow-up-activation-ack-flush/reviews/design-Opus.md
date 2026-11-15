# Review record: design

- Artifact: design.md
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: brainstorm.md sha256 88e635325ad8eb2c63f5e752e642dd0cbcb3f32ca22ee22c7ad1407948755d7e;
  proposal.md sha256 d6811482c82a688b6c3c13cb18d587093f5a431888d110de7352d3e60e80fde2

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | Revocation cannot reach a held link: `closePeer` selects links by `boundPeerId()` and activation nulls it (`sessions.ts:355-361`, `client-machine.ts:814`, `:948`) | fixed: D4 "Reachability" — the link samples the id and keeps answering with it while the hold is armed; `ClientMachine` untouched |
| medium | The hold's fencing was unspecified: setting `stopped` to drop records would make `terminate()` return early | fixed: a separate `draining` flag, with a table of which path sets and reads each flag |
| medium | Unsubscribing the close notification on every teardown, and its order against the disconnect request, was unstated | fixed: D5 lists every teardown and unsubscribes before disconnecting |
| medium | Timer plumbing stopped at the link; the doubles and existing tests would arm real timers | fixed: D8 names the deps, the surfaces and the doubles; corrected further in rounds 3-4 |
| low | A pending ack send normally fails when the connection closes; its disconnect is answered locally | fixed: D3 states the failed-send path and the local `unknown_handle` reply |
| low | A peer close does not prove the ack was accepted | fixed: Risks states the end state, the protocol backstop and how the device log distinguishes the cases |
| low | D2 notified only on applied `disconnected` events | fixed: D2 diffs the connection set instead |
| low | D7 hid that only a test reads `pairingFinished` | fixed: D7 names the reader and the panel surface |

### Round 2 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| medium | D4 did not say when the link samples the peer id or when it answers with it; a lazy read inside the accessor would leave the held link unreachable | fixed: D4 requires sampling after `onFrame` and after `commit()`, and answering with the remembered id only once `activationCompleted()` |
| medium | D2 claimed the `state.apply` diff covered every removal path, but `closeConnection(…,'loss')` also runs in `finishSend` and the disconnect reply | fixed: D2 diffs inside `emitChange`, which follows every mutation, and lists the paths |
| low | D8 cited non-existent interval deps and a loopback test that does not reach activation | fixed: `ConnectorAppDeps` gains the interval pair; only `enrollment_full` reaches activation |
| low | The implementer list omitted the transport-facing interfaces and `DeferredPairingController` | fixed: D8 lists them plus the remaining doubles |
| low | Only a bad MAC revokes the phone record | fixed: Risks distinguishes `eraseIncompleteFailure` from plain `reject()` |

### Round 3 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| medium | D8 claimed only `enrollment_full` reaches activation; `revocation-integration.test.ts` helpers `driveToActive` and the `pairThroughApp`/`ackDisconnect` path depend on the immediate post-ack disconnect | fixed: D8 names the helpers and requires their fakes to emit the phone's `disconnected` event with injected timers |
| low | D2 described the `controller.ts:296-297` and `:626` citations wrongly | fixed: the send-reply close, the disconnect reply's `unknown_handle` branch and the `state.apply` close are now listed separately |
| low | D8 omitted `FakeTransport` and assumed interval deps that do not exist | fixed: `FakeTransport` listed; `ConnectorAppDeps` gains `setInterval`/`clearInterval` explicitly |

### Round 4 — approve

| Severity | Finding | Disposition |
| --- | --- | --- |
| low | `ackDisconnect` is a shared helper used by paths that do send a client disconnect; D8 reads as if the helper itself changes | deferred to `tasks.md`: only the two post-activation call sites (`:1457`, `:1938`) switch to a phone-side `disconnected` emit; the helper keeps requiring a disconnect post for its other callers |
| low | `:1934-1938` is a separate inline copy of the post-ack sequence, not a use of the helper | deferred to `tasks.md`, which names it as its own edit site |

Approved content: design sha256 b7a0e4acea41c0c49c188c4f6670c89505d7502445b651c20b8d18ffc07ee9ea
