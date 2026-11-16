# Review record: brainstorm

- Artifact: brainstorm.md
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: none (brainstorm is first in the order)

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | Agreed Approach §7 contradicts §5: `terminate()` always calls `machine.onDisconnect()` (`link.ts:378`), so a revocation, reset or port death inside the hold would report a completed enrollment as failed | fixed: the terminal-active rule (Agreed Approach §5, Key Decisions) — after activation the link calls `machine.onDisconnect()` on no path, and `terminate()` only skips that call |
| high | A failed send of the final ack is undefined: the host fails the in-flight request, `PairingLink.send` throws, and the catch calls `machine.onDisconnect()` + `stop()` (`link.ts:311-312`) | fixed: Agreed Approach §7 sends it through `stop()` and leaves the machine `active`; the enrollment goes to the recovery protocol |
| medium | §8 presented the panel's "finished" from the machine state as existing behaviour; it is new connector work (`connector.ts:716-728`) | fixed: now Agreed Approach §10, explicitly new connector work with tests |
| medium | Unverified assumption that the phone's close reaches the client promptly (`SharingGattServer.kt:757-761`, `macos.rs:657-668`) | fixed: stated as a device-confirmed assumption in Open Questions, with its fallback consequence and a matching acceptance criterion |
| medium | The protocol fixture's `close_enrollment_connection` effect was not reconciled with the deferred transport release | fixed: Key Decisions records the reading — the machine still closes logically, the transport release is deferred, and the conformance scenarios observe it through `machine.closed`, so no fixture moves |
| medium | Where the drain waits relative to the frame queue was unsettled (`terminate()` waits on that queue, `link.ts:375-385`) | fixed: Agreed Approach §4 puts the wait outside the queue |
| low | Wrong record state named in the summary | fixed: the record is the `active_activation_pending` mirror; only the machine state is `active` |
| low | A port close is not covered by the new close notification | fixed: Agreed Approach §9 |
| low | "Sub-second ack path" unverified; `PairingLimits.kt` citation path | fixed: wording softened; the path is written out in full |

### Round 2 — approve

| Severity | Finding | Disposition |
| --- | --- | --- |
| medium | Agreed Approach §3 subscribes the close notification at `start()`, but nothing says what the link does when it fires before activation or before the drain is armed | deferred to `design.md` with this rule: the notification only sets a "peer already closed" latch; it never calls `onDisconnect()` or stops the link before activation (that behaviour change is out of scope), and when the drain arms later with the latch set it finishes immediately instead of waiting out the grace |
| low | `cancel`/`dispose` set the machine from `active` to `idle` (`client-machine.ts:865-872`), which stretches "a completed enrollment is never re-classified" | deferred to `design.md`: accepted and stated there — the durable record is untouched (`#pendingPeerId` is already null, `client-machine.ts:787`), no failure is reported, and the phase reads `idle` because the attempt is over |
| low | `terminate()` must also clear the new grace timer and unsubscribe the close notification; `clearTimer()` only handles the tick interval | deferred to `design.md`, which must state the teardown fields the drain owns |

Approved content: brainstorm sha256 88e635325ad8eb2c63f5e752e642dd0cbcb3f32ca22ee22c7ad1407948755d7e
