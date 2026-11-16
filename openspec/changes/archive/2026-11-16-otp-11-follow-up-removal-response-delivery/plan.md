# OTP-11 Follow-up: Removal Response Delivery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Chrome responder keep the removal link until the Android requester closes it, or a 5 000 ms bound, so `removal_response` is no longer cut off by the local `sent`-then-disconnect path.

**Architecture:** The session engine marks the reply-then-close terminal effect with `holdForDelivery` only when it answered a `removal_request`. The connector session manager defers only `controller.disconnect` behind an `onConnectionClosed` subscription and an injected timer; the logical teardown, trust deletion, revocation fence and outcome stay immediate. The connector marks the held handle so its disappearance schedules no automatic recovery, and `closeAll`/`closePeer` finish only the holds that existed at their synchronous snapshot.

**Tech Stack:** TypeScript (MV3 extension, vitest, Chrome Native Messaging bridge), Kotlin/Android only as the device peer, `openspec` for the change record.

**Spec:** `openspec/changes/otp-11-follow-up-removal-response-delivery/specs/mutual-peer-removal/spec.md` (design: `design.md` in the same directory)

## Global Constraints

- The change is Chrome-extension only. No `protocol/`, `native-bridge/`, `android/` or fixture change.
- `sent` confirms local OS submission only; never an over-air or application acknowledgement (OTP2-BRD-022.5, OTP2-GAT-013).
- Logical termination, trust deletion, the revocation fence and the reported outcome MUST NOT wait for the hold. Only `controller.disconnect` is deferred.
- Hold bound `REMOVAL_DELIVERY_HOLD_MS = 5_000` on the injected `SessionTimers`, below the requester's 10 000 ms deadline.
- No test sleeps, uses a real wall clock, or uses a time-based wait; fire injected timers and flush promises.
- The response stays the last record; no retry, ack or new wire record is added.
- Android's responder and engine are unchanged; the reverse device direction must stay passing.
- Every commit subject is a Conventional Commit (`type(scope): description`).

## Review Focus

1. **A response send that fails**: the responder has already deleted its record, and a failed send must never create a hold. A wire `send_failed` first makes the connection ambiguous and ends the session through the connection close with no manager disconnect; a local non-ambiguous failure (`send_failed` path) disconnects immediately. Test both in Task 2.
2. **A peer close that arrives before/at hold subscription** (handle already gone): no stray timer, no `disconnect` request, no recovery mark left. Test in Task 2.
3. **A reset/revocation admitted while the handler step is still running**: the snapshot must not include the hold created later by that step; the responder's own revocation finish must not cut its response. Test in Task 3.
4. **The connector recovery mark across a reset**: a hold that survives a reset must still skip recovery when its handle disappears, and a port close must clear the mark. Test in Task 4.
5. **A late/throttled hold timer**: a late expiry may only hold a dead link longer; it must never disconnect before the peer close or create a second request. Test in Task 2.

---

### Task 1: Engine terminal-effect role flag

**Files:**
- Modify: `web-extension/src/session/engine.ts` (the `SessionEffect` union near `:147-160`; the close-after-reply branch near `:1098-1112`)
- Test: `web-extension/test/session/removal.test.ts` (engine half, near `:255-290`)

**Interfaces:**
- Consumes: `RecordType.REMOVAL_REQUEST`, `terminalStage(terminalError, opened.type)`.
- Produces: `SessionEffect`'s `terminated` variant becomes `{ kind: 'terminated'; reason: string; holdForDelivery?: true }`. A successful `removal_request` close sets `holdForDelivery: true`; every other terminal effect leaves it absent.

- [ ] **Step 1: Write the failing engine tests**

In `web-extension/test/session/removal.test.ts`:
- Change the responder assertion at `:276` from
  `expect(effects[1]).toEqual({ kind: 'terminated', reason: 'removal' });`
  to include `holdForDelivery: true`.
- Add a requester test: open a session, call `harness.engine.setRemovalPending(true)`, feed `harness.phoneRecord(RecordType.REMOVAL_RESPONSE, N)`, and assert the terminal effect is exactly `{ kind: 'terminated', reason: 'removal' }` with no `holdForDelivery`.
- Add an `app_error` test: use a stub handler that handles `REMOVAL_REQUEST` and replies with `APP_ERROR`; assert the terminal effect has no `holdForDelivery`.
- Add a `dependency` test: make the handler's deletion throw and assert the terminal effect has no `holdForDelivery`.
- Add an `authorization` test: set the removal guard, feed an inbound `removal_request`, and assert the terminal stage is `authorization` with no `holdForDelivery`.
- Keep the existing non-removal close-after-reply exact assertion at `removal.test.ts:416` green; it covers the unchanged branch.

- [ ] **Step 2: Run the engine tests to verify they fail**

Run: `cd web-extension && npx vitest run test/session/removal.test.ts -t "removal handler"`
Expected: FAIL on the responder assertion (missing `holdForDelivery`), and the new requester/failure cases still pass trivially.

- [ ] **Step 3: Implement the flag**

In `engine.ts`:
```ts
| { kind: 'terminated'; reason: string; holdForDelivery?: true }
```
In the `if (terminalError || closeAfterReply)` branch return:
```ts
{
    kind: 'terminated',
    reason: terminalStage(terminalError, opened.type),
    ...(!terminalError && opened.type === RecordType.REMOVAL_REQUEST
        ? { holdForDelivery: true as const }
        : {}),
}
```
Do not change the `#terminate`, `#terminateUnhandled` or `app_error` literals.

- [ ] **Step 4: Run the engine tests to verify they pass**

Run: `cd web-extension && npx vitest run test/session/removal.test.ts -t "removal handler"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web-extension/src/session/engine.ts web-extension/test/session/removal.test.ts
git commit -m "feat(web-extension): mark removal response deliveries in the terminal effect"
```

---

### Task 2: Session-manager delivery hold

**Files:**
- Modify: `web-extension/src/connector/sessions.ts` (constant near `:55`; `SessionManager` constructor near `:151-190`; `apply` near `:431-490`; `terminate` near `:542-570`; `abortAll` near `:730-735`)
- Test: `web-extension/test/session/removal.test.ts` (the `SessionManager` half and the `RemovalTimers` helper)
- Test: `web-extension/test/connector/close-peer.test.ts` (the responder close-after-reply test near `:545-578`)

**Interfaces:**
- `export const REMOVAL_DELIVERY_HOLD_MS = 5_000;`
- `SessionManager` constructor gains an optional `onRemovalHold?: (handle: string) => void` as the **last** parameter, after the existing `links` parameter. Do not insert it before `links`: existing callers pass `links` positionally, and a `() => readonly PairingLink[]` is assignable to `(handle: string) => void`, so the compiler would not catch the mis-wire.
- `terminate(session, reason, closeLink, holdForDelivery = false)`.
- `private readonly holds = new Map<string, RemovalHold>()`; `RemovalHold = { peerId: string; timer: unknown; unsubscribe: () => void }`.
- `private holdOpen(session: Session): void` and `private finishHold(handle: string, disconnect: boolean): void`.

- [ ] **Step 1: Write the failing manager tests**

Add helpers to `removal.test.ts`:
```ts
function postedDisconnects(port: MockNativePort, handle: string): Record<string, unknown>[] {
    return port.posted.filter(
        (message): message is Record<string, unknown> =>
            typeof message === 'object' &&
            message !== null &&
            (message as { type?: unknown }).type === 'disconnect' &&
            (message as { connection_handle?: unknown }).connection_handle === handle,
    );
}
```
Add tests using the existing `createFixture`, `openSession`, `emitRecord`, `nextSendFor`, `ackSend`, `flush`, `disconnectedEvent`, `RemovalTimers`:
- **Responder holds:** emit a `removal_request`, ack the response's `sent`, `await flush()`, assert `postedDisconnects(port, HANDLE_CONNECTION)` is empty, the hold timer (`timers.tasks.find(t => t.delay === REMOVAL_DELIVERY_HOLD_MS)`) is pending, and already before the hold is released: `ops.deleted` contains the peer, `terminations(...) === ['removal']`, and `authenticatedSessions()` is empty.
- **Peer close:** fire `disconnectedEvent({ connection_handle: HANDLE_CONNECTION })`, `await flush()`, assert the hold task is cancelled, no disconnect was posted, and `terminations(...) === ['removal']`.
- **Host-reported or user-initiated close:** for a host-originated `disconnected` event, assert the hold posts no additional disconnect; for a user-initiated `controller.disconnect` request, ack it and assert the hold adds no second request.
- **Expiry:** call the hold task's `run()`, `await flush()`, assert exactly one disconnect request.
- **Expiry after the handle is gone:** first emit the peer close, then run the hold task; assert no additional disconnect.
- **Requester immediate:** keep the existing `settles removed only after a valid removal_response` test; assert the request's response still posts a disconnect immediately and no hold timer exists.
- **Concurrent peer request first:** create the fixture with `SuspendedOperations`; emit the phone's `removal_request` (the handler suspends), call `fixture.manager.requestRemoval(peerId)` while it is in flight, release the handler, ack the response's `sent`; assert the hold is created, `removal` resolves `removed`, `sentTypes(port, HANDLE_CONNECTION).filter(t => t === REMOVAL_REQUEST)` is empty, and the last sent is `REMOVAL_RESPONSE`.
- **Both requests already on the air:** `const { removal } = await sendRequest(fixture)` (the request is sent and acked), then emit the phone's `removal_request`; assert the terminal reason is `authorization`, one disconnect is posted, `ops.deleted` is empty, and `removal` resolves `kept-no-response`.
- **`send_failed` / ambiguous send:** emit the wire-error fixture `wireErrorEvent({ code: 'send_failed', request_id: reply.requestId })` (see `web-extension/test/connector/fixtures.ts:160`); assert no hold timer, the termination is `connection_closed`/`link_lost` rather than `removal` (a wire `send_failed` becomes an ambiguous connection first), and the manager posts no disconnect.
- **Local non-ambiguous send failure:** use a stub `SessionController` whose `send` resolves a local non-ambiguous failure (for example `cap_exceeded`) for the response; assert `apply` terminates with `send_failed`, posts exactly one disconnect and creates no hold.
- **Second removal attempt:** after the responder hold exists, `requestRemoval` resolves `kept-not-connected`.
- **Port closed (defensive guard):** construct a `SessionManager` with a stub `SessionController` whose `isOpen()` returns false while `isCurrent` holds (or call `holdOpen` directly through a test-only cast with a stub session); assert no hold entry, no timer and no disconnect. If no reachable seam is added, record in the PR note that the guard is defensive and not integration-tested.
- **Handle already gone (defensive guard):** call `holdOpen` directly through a test-only cast with a stub controller whose `onConnectionClosed` invokes the listener immediately and whose `isOpen()` is true; assert the map entry and timer are cleared and no disconnect is posted.
- **Held link state:** after the response, assert `fixture.manager.authenticatedSessions()` is empty, the engine is closed, and an inbound record on the handle produces no send and no new diagnostic.
- **No delivery claim:** on both peer close and expiry, assert the removal outcome is `removed`, no failure diagnostic is added, and no code path records delivery.

Update the existing responder test at `removal.test.ts:493` to assert the hold instead of `nextDisconnectFor`, and `close-peer.test.ts:561` likewise (import `REMOVAL_DELIVERY_HOLD_MS` from `../../src/connector/sessions`).

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web-extension && npx vitest run test/session/removal.test.ts test/connector/close-peer.test.ts`
Expected: FAIL on the hold assertions (no hold timer, immediate disconnect). The already-gone guard and the requester/no-hold assertions may pass trivially before the code; the responder, peer-close and expiry tests are the failing ones.

- [ ] **Step 3: Implement the hold**

In `sessions.ts`:
- Export `REMOVAL_DELIVERY_HOLD_MS = 5_000`.
- Add the `RemovalHold` interface and `holds` map.
- Add `onRemovalHold?: (handle: string) => void` to the constructor as the last parameter, after `links`.
- In `apply`'s `case 'terminated'`: `this.terminate(session, effect.reason, true, effect.holdForDelivery === true); return;`.
- In `terminate`, add the fourth parameter. In the `closeLink` branch:
```ts
if (closeLink && this.controller.isOpen()) {
    if (holdForDelivery && reason === STAGE_REMOVAL) {
        this.holdOpen(session);
    } else {
        void this.controller.disconnect(session.handle).catch(() => undefined);
    }
}
```
- `holdOpen`: insert the map entry with a placeholder unsubscribe **before** arming the timer and subscribing, because `onConnectionClosed` answers synchronously for an already-gone handle:
```ts
const handle = session.handle;
const hold: RemovalHold = {
    peerId: session.peerId,
    timer: undefined,
    unsubscribe: () => undefined,
};
this.holds.set(handle, hold);
hold.timer = this.timers.setTimeout(() => this.finishHold(handle, true), REMOVAL_DELIVERY_HOLD_MS);
const unsubscribe = this.controller.onConnectionClosed(handle, () => this.finishHold(handle, false));
if (!this.holds.has(handle)) {
    // The immediate listener already finished the hold: keep no timer or mark.
    unsubscribe();
    return;
}
hold.unsubscribe = unsubscribe;
this.onRemovalHold?.(handle);
```
If the handle is already gone, `onConnectionClosed` calls the listener synchronously; `finishHold` clears the timer and the map entry.
- `finishHold`: idempotent delete, `clearTimeout`, `unsubscribe`, and only `disconnect === true && controller.isOpen()` → `void this.controller.disconnect(handle).catch(() => undefined)`.
- `abortAll`: after terminating sessions, `for (const handle of [...this.holds.keys()]) this.finishHold(handle, false);`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd web-extension && npx vitest run test/session/removal.test.ts test/connector/close-peer.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web-extension/src/connector/sessions.ts web-extension/test/session/removal.test.ts web-extension/test/connector/close-peer.test.ts
git commit -m "feat(web-extension): hold the removal link until the requester closes"
```

---

### Task 3: Cleanup snapshots for held links

**Files:**
- Modify: `web-extension/src/connector/sessions.ts` (`closePeer`/`closeAll`/`closeSelected` near `:355-410`)
- Test: `web-extension/test/session/removal.test.ts`

**Interfaces:**
- `private takeHolds(predicate: (hold: RemovalHold) => boolean): string[]` snapshots and deletes the selected map entries synchronously, clears their timers and unsubscribes; the returned handles are released by the caller.
- `private releaseHold(handle: string): void` calls `controller.disconnect(handle)` fire-and-forget when the port is open and swallows failures, exactly like `terminate`'s close.
- `closeSelected(..., held: readonly string[])` calls `releaseHold` for every held handle **before** awaiting `Promise.allSettled`, so a failed link close cannot skip them and the reset/revocation does not wait for the host's disconnect reply. Each held handle gets exactly one disconnect when the port is open.
- `closePeer(peerId)` passes a predicate matching `hold.peerId === peerId`; `closeAll()` passes all.

- [ ] **Step 1: Write the failing cleanup tests**

- **Reset:** hold a responder link, call `fixture.manager.closeAll()`, flush; assert exactly one disconnect for the held handle and the reset's own trust work is not delayed (the test's `closeAll` resolves).
- **Fail-closed:** the shared path is `closeAll`; assert it at the manager level here. The connector-app `app.failClosed()` test in Task 4 flushes one microtask (the coordinator defers its work) and asserts the same end state plus the termination of every session and pairing link.
- **Same-peer revocation:** hold a responder link, call `manager.closePeer(peerId)`; assert exactly one disconnect and the revocation's trust write is unaffected (the fake coordinator/ops still records).
- **Other-peer revocation:** hold peer A, call `closePeer(peerB)`; assert A's hold is untouched and no disconnect for A.
- **Responder's own revocation finish:** use a `RemovalOperations` fake whose `deleteFenced` starts `manager.closePeer(peerId)` **without awaiting it** (as `revocation.ts:197-199` does; awaiting would deadlock behind the handler's own step) and then resolves; assert the responder still holds and posts no disconnect before the peer close. This is the snapshot regression.
- **In-flight removal after the snapshot:** start a removal whose handler suspends, call `closeAll`, then let the handler finish; assert the new hold survives the snapshot, ends at the peer close with no disconnect or at the bound with one, and the reset completed.
- **Port close:** `port.disconnect()` while held; assert the timers/marks are cleared and no disconnect is posted.
- **Snapshot point:** a hold created after `closeAll`'s snapshot is not selected.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web-extension && npx vitest run test/session/removal.test.ts`
Expected: FAIL on the reset/revocation cleanup assertions. The port-close guard, responder-finish snapshot, in-flight and snapshot-point tests pass trivially before this task and pin the existing behavior; the reset and same-peer revocation tests are the failing ones.

- [ ] **Step 3: Implement the snapshots**

In `closePeer`:
```ts
const held = this.takeHolds((hold) => hold.peerId === peerId);
await this.closeSelected(sessions, links, 'revoked', held);
```
In `closeAll`:
```ts
const held = this.takeHolds(() => true);
await this.closeSelected(sessions, links, 'closed', held);
```
In `closeSelected`, release the held handles first, fire-and-forget:
```ts
for (const handle of held) this.releaseHold(handle);
const results = await Promise.allSettled([
    ...sessions.map((session) => this.closeBehind(session, reason)),
    ...links.map((link) => link.terminate()),
]);
```
`releaseHold(handle)` calls `controller.disconnect` if the port is open and swallows failures; it never makes `closeSelected` wait for the host's reply. `takeHolds` must clear the timer and unsubscribe exactly once.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd web-extension && npx vitest run test/session/removal.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web-extension/src/connector/sessions.ts web-extension/test/session/removal.test.ts
git commit -m "fix(web-extension): finish only the removal holds a cleanup selects"
```

---

### Task 4: Connector recovery skip for a held handle

**Files:**
- Modify: `web-extension/src/connector.ts` (field near `:407-427`; `SessionManager` construction near `:500-540`; `handleClose` reset near `:1390-1400`; `handleChange` near `:1465-1505`)
- Test: `web-extension/test/connector/connector-app.test.ts` (new tests near the recovery regressions at `:485`)

**Interfaces:**
- `private readonly removalHoldHandles = new Set<string>();`
- Pass `onRemovalHold: (handle) => this.removalHoldHandles.add(handle)` to `new SessionManager` as the last argument, after the existing `links` argument.
- In `handleChange`'s disappearance loop, start with `if (this.removalHoldHandles.delete(handle)) continue;`.
- At the bottom cleanup loops, delete any `removalHoldHandles` entry not in `next`.
- In `handleClose`, `this.removalHoldHandles.clear()` next to the other sets.

- [ ] **Step 1: Write the failing connector-app tests**

- **No premature disconnect, no recovery:** establish a session, send a phone `removal_request`, ack the response's `sent`, assert no `disconnect` is posted; then emit the phone's `disconnected`, advance the connector clock, flush, and assert no `start_scan` or `connect` is posted.
- **Reset during the hold:** start the same flow, call `context.app.resetTrust()` while held, flush, then close the handle; assert no recovery and that the reset completed.
- **Hold survives reset:** spy on `context.trust.repository.revoke` to suspend only its first call (the handler's delete; the later `sweep` call must proceed), emit the phone's `removal_request`, call `context.app.resetTrust()` (its `closeAll` snapshot runs before the hold exists), release the spy, ack the response's `sent`, flush, then close the handle; assert no `start_scan`/`connect` and that the hold was created after the reset's snapshot.
- **Fail-closed during the hold:** start the same flow, call `context.app.failClosed()`, flush one microtask, assert the hold is finished with exactly one disconnect and the other sessions/pairing links are terminated.
- **Port close clears the mark:** hold, `port.disconnect()`, assert the set is empty and no request is posted.
- **Late mark prune:** add a mark after the handle already left `trackedConnections`; trigger a `handleChange`; assert the mark is gone.

Use `answerHandshake`, `applicationFrame(RecordType.REMOVAL_REQUEST, 1n)`, `nextSendFor`, `ackSend`, `flush` and `context.clock.advance` from the existing harness. The in-flight removal that outlives a reset snapshot is covered at the `SessionManager` level in Task 3 with the existing `SuspendedOperations` fake; the connector-app harness needs no suspended handler for the mark tests.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `cd web-extension && npx vitest run test/connector/connector-app.test.ts`
Expected: FAIL on the recovery assertions. The no-premature-disconnect assertion passes after Task 2 and pins the hold behavior.

- [ ] **Step 3: Implement the recovery skip**

Add the field, the constructor callback, the `handleChange` skip/prune and the `handleClose` clear as listed in Interfaces. The callback is start-only; do not remove the mark in the hold's end path.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd web-extension && npx vitest run test/connector/connector-app.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web-extension/src/connector.ts web-extension/test/connector/connector-app.test.ts
git commit -m "fix(web-extension): skip recovery for a held removal link"
```

---

### Task 5: Device run and documentation

**Files:**
- Modify: `docs/otp-11-debug-controls.md`
- No code changes.

- [ ] **Step 1: Update the debug-controls procedure**

Add a follow-up run section:
- Preconditions: OnePlus 6T debug APK, development-channel extension, macOS host from the same commit; a FULL session (disconnect/reconnect first if the row says recovery-only).
- Android → Chrome: press `Mutual remove` on the phone; PASS = phone and Chrome both `Peer removed on both devices`, both peer lists empty, native trace shows the response's `sent`, then the phone's `disconnected`, no client `disconnect` in between.
- Chrome → Android: press `Mutual remove` in the Chrome panel; PASS = both screens `Peer removed on both devices`, both lists empty, Chrome's disconnect after the received response (requester path, no hold).
- Record a 5 s fallback as a fallback, not a PASS; record failures rather than retrying them away.
- No ids, keys, fingerprints or transcripts.

- [ ] **Step 2: Build and run the device acceptance**

If the device or browser is unavailable, say so and ask; do not record a run as tested.

```bash
./gradlew rustWasm
cd web-extension && npm ci && npm run build
cd .. && (cd android && ./gradlew assembleDebug)
```

Install with `adb install -r` after comparing the signing certificate (see `android/AGENTS.md`), start Bluetooth sharing, open the debug pairing screen, and load `web-extension/dist` unpacked in the dedicated Chrome profile. Build and register the development native host from the same commit as described in `docs/otp-10-physical-runs.md` (the host registration detail is the node's `native-bridge/host/AGENTS.md`). Every successful mutual removal deletes both records, so re-pair and open a FULL session before each attempt. Run at least ten attempts in each direction and record the outcomes and the trace facts in the new section.

- [ ] **Step 3: Commit the documentation**

```bash
git add docs/otp-11-debug-controls.md
git commit -m "docs(otp-11): record the removal response delivery run"
```

- [ ] **Step 4: Record the PR note**

State the devices/extension/host used, the attempt counts, and that the 5 s expiry fallback is unit-tested only unless the device exercised it. If the pre-fix loss cannot be reproduced, say the unit test is the regression proof.

---

### Task 6: Verification and finalize

**Files:**
- No source changes; run the checks and the OpenSpec hand-off.

- [ ] **Step 1: Run the extension checks**

```bash
./gradlew rustWasm
cd web-extension
npm ci
npm run format:check
npm run lint
npm run typecheck
npm test
npm run build
npm run test:bundle
```

- [ ] **Step 2: Run the cross-package checks**

```bash
cd ..
./gradlew sessionLoopbackCheck
python3 docs/checks/check_agent_files.py --root .
python3 -m unittest discover -s docs/checks -p 'test_*.py' -v
python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)
```
Run the checks listed in the `AGENTS.md` of every selected node, including `docs` and `web-extension`.

- [ ] **Step 3: Run the OpenSpec checks**

```bash
openspec validate --all
openspec status --change otp-11-follow-up-removal-response-delivery
```
Audit every planning file against `reviews/`: an Opus approval with the current hash, the full upstream set (brainstorm, proposal, design, specs, tasks for the plan), and no open critical/high finding.

- [ ] **Step 4: Commit the finished work and finalize**

Each task commits its own code and docs. If any planning file remains uncommitted, commit it explicitly with a docs subject; never `git add -A` under a test subject:

```bash
git add openspec/changes/otp-11-follow-up-removal-response-delivery
git commit -m "docs(openspec): record the OTP-11 removal response follow-up"
```
Then run the schema's finalize hand-off (commit, push, open the pull request, record it in `tracker.md`, set the card In Review only after the open PR is confirmed). Do not start apply in this planning session; the plan is the last artifact.
