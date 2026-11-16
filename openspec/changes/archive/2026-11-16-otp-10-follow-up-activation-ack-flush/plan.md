# OTP-10 Activation Ack Flush Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep the Chrome enrollment link open after the client sends `activation_ack` until the phone ends the connection, so the phone verifies the ack in the same enrollment and the first signed reconnect is a FULL session.

**Architecture:** The hold lives in `PairingLink`, which owns the transport handle and the ack's send. It subscribes to a new per-handle close notification from `ConnectorController`, arms a bounded wait once the ack is sent, drops inbound records while held, and finishes either on the phone's close (no disconnect request) or on the bound (one disconnect request). A terminal-active rule stops every teardown path from re-classifying a completed activation as a failure, and the link keeps answering the peer id so revocation still selects it.

**Tech Stack:** TypeScript, the extension's vitest suites, the connector controller and pairing link modules, injected timers, no network, Android or host change.

**Spec:** `openspec/changes/otp-10-follow-up-activation-ack-flush/` — `design.md` (D1-D8), `specs/spake2-enrollment-exchange/spec.md` (`Enrollment completion hold`), plus RFC `docs/rfcs/2026-08-21-otp-external-devices.md` section OTP-10 and `protocol/docs/veles-v1.md` OTP2-STA-010b/010c, OTP2-CRY-019e and OTP2-SID-001.

## Global Constraints

- No protocol, wire, fixture, limit, conformance-inventory, `android/`, `native-bridge/` or packaging change; only `web-extension/` and `docs/` may change.
- Tests never sleep or read the wall clock: inject `setTimeout`/`clearTimeout`/`setInterval`/`clearInterval` and advance them (`.agents/rules/repo-no-time-based-tests.md`).
- Every new extension test file is added to the explicit `npm test` list in `web-extension/package.json`. This plan adds no new test file: the link-level tests live in `test/connector/revocation-integration.test.ts`, which already owns the drive and the trust stack (tasks.md 4.1).
- `./gradlew rustWasm` from the repository root before any extension build, test or typecheck.
- No code, key, hint, installation id or handle is logged; notices carry no identifiers.
- Commit subjects are Conventional Commits with the `otp-10` scope (`.agents/rules/repo-conventional-commits.md`).
- The behavior is verified on a real phone and in real Chrome before the change is called done (`.agents/rules/repo-real-device-testing.md`).

## Review Focus

- A lost ack, where the bound expires → exactly one disconnect request, machine stays `active`, never failed.
- The phone rejects the ack and disconnects → the reject frame is dropped, no failure, the enrollment goes to recovery.
- Revocation, fail-closed or reset inside the hold → the held link closes like any other, the link deletes no record, no failure is reported.
- A tab close or `startPairing` replacement during the hold → immediate close, mirror kept, no second disconnect.
- A slow host where the phone's close arrives before the ack's send resolves → the latch finishes the hold at once when that send succeeds.

---

### Task 1: Per-handle close notification on the controller

**Files:**
- Modify: `web-extension/src/connector/controller.ts`
- Modify: `web-extension/src/connector/sessions.ts:66-78` (`SessionController`)
- Modify: `web-extension/src/connector.ts:186` (`ConnectorControllerLike`)
- Modify: `web-extension/src/pairing/link.ts:28-38` (`PairingTransport`)
- Test: `web-extension/test/connector/controller.test.ts`
- Modify: `web-extension/test/connector/fixtures.ts` (add the `unknown_handle` error fixture)
- Modify: `web-extension/test/pairing/client-machine.test.ts:190`, `:277`; `web-extension/test/connector/close-peer.test.ts:175`; `web-extension/test/connector/revocation-integration.test.ts:414`; `web-extension/test/session/loopback.test.ts` (`LoopbackPairingTransport`)

**Interfaces:**
- Produces: `onConnectionClosed(connectionHandle: string, listener: () => void): () => void` on `ConnectorController`, `SessionController`, `ConnectorControllerLike` and `PairingTransport`.
- Produces: on each fake, `onConnectionClosed(handle, listener)` plus `closeHandle(handle: string): void`, which removes the handle from that fake's snapshot and then fires its listeners. A fake that serves one connection still takes the argument and ignores it.

- [ ] **Step 1: Add the missing fixture** in `fixtures.ts`: `unknownHandleError(overrides)` returning a wire `error` event with `code: 'unknown_handle'` and a `request_id`, built with the same `event()` helper as its neighbours.

- [ ] **Step 2: Write the failing tests** in `controller.test.ts`, reusing `setup()`, `ready()`, `connect()`. A peer close is unsolicited, so it carries no `request_id`:

```ts
it('notifies a connection-closed listener when the peer disconnects', async () => {
    const context = await ready();
    await connect(context, HANDLE_DEVICE, HANDLE_CONNECTION);
    const seen: string[] = [];
    context.controller.onConnectionClosed(HANDLE_CONNECTION, () => seen.push('closed'));
    await context.mock.emit(
        disconnectedEvent({ request_id: undefined, connection_handle: HANDLE_CONNECTION }),
    );
    expect(seen).toEqual(['closed']);
});

it('notifies a connection-closed listener when a live send is answered unknown_handle', async () => {
    const context = await ready();
    await connect(context, HANDLE_DEVICE, HANDLE_CONNECTION);
    const seen: string[] = [];
    context.controller.onConnectionClosed(HANDLE_CONNECTION, () => seen.push('closed'));
    const send = context.controller.send(HANDLE_CONNECTION, bytes([1, 2, 3]));
    const posted = await context.mock.nextPosted();
    await context.mock.emit(unknownHandleError({ request_id: posted.request_id as string }));
    await send;
    expect(seen).toEqual(['closed']);
});

it('notifies a connection-closed listener for every open handle when the port disconnects', async () => {
    const context = await ready();
    await connect(context, HANDLE_DEVICE, HANDLE_CONNECTION);
    const seen: string[] = [];
    context.controller.onConnectionClosed(HANDLE_CONNECTION, () => seen.push('closed'));
    await context.mock.disconnect();
    expect(seen).toEqual(['closed']);
});

it('fires a connection-closed listener immediately for a handle it does not hold', () => {
    const context = setup();
    let calls = 0;
    const unsubscribe = context.controller.onConnectionClosed(HANDLE_CONNECTION_2, () => {
        calls += 1;
    });
    expect(calls).toBe(1);
    unsubscribe();
    expect(calls).toBe(1);
});

it('stops notifying a connection-closed listener after unsubscribe', async () => {
    const context = await ready();
    await connect(context, HANDLE_DEVICE, HANDLE_CONNECTION);
    const seen: string[] = [];
    const unsubscribe = context.controller.onConnectionClosed(HANDLE_CONNECTION, () =>
        seen.push('closed'),
    );
    unsubscribe();
    await context.mock.emit(
        disconnectedEvent({ request_id: undefined, connection_handle: HANDLE_CONNECTION }),
    );
    expect(seen).toEqual([]);
});
```

- [ ] **Step 3: Run them to verify they fail**

Run: `cd web-extension && npx vitest run test/connector/controller.test.ts -t "connection-closed"`
Expected: FAIL — `controller.onConnectionClosed is not a function`.

- [ ] **Step 4: Implement in `controller.ts`**

Add `private readonly connectionClosedListeners = new Map<string, Set<() => void>>()` (a new name —
`closeListeners` already exists for port-close subscribers, `controller.ts:162`) and
`private lastHandles = new Set<string>()`. In `emitChange()`, before the change listeners run, compute
`const current = new Set(this.state.snapshot().connections.map((connection) => connection.handle))`,
call and delete every listener of a handle present in `lastHandles` but absent from `current`, then
set `this.lastHandles = current`. `onConnectionClosed` registers into the map and returns an
unsubscribe; when `lastHandles` already lacks the handle it calls the listener synchronously and
returns `() => undefined`. Declare the method on the three interfaces and implement it on every fake.

- [ ] **Step 5: Run the tests, then typecheck**

Run: `cd web-extension && npx vitest run test/connector/controller.test.ts && npm run typecheck && npx vitest run test/pairing/client-machine.test.ts test/connector/close-peer.test.ts`
Expected: PASS.

The disconnect reply's `unknown_handle` close and `finishSend`'s send-reply close are the same
`closeConnection(…, 'loss')` statement (`controller.ts:296-297` and `:625-626`), and `state.reset` is
what the port-disconnect test exercises through `handleClose`; say so in the commit body so a
reviewer does not read it as a gap.

- [ ] **Step 6: Commit**

```bash
git add web-extension/src web-extension/test
git commit -m "feat(otp-10): notify the enrollment link when its connection closes"
```

---

### Task 2: Hold the link after the ack, and finish it safely

**Files:**
- Modify: `web-extension/src/pairing/link.ts` (timers, flags, subscription, `armHold`, `finishHold`, `stop`, `terminate`, the `receive` catch)
- Modify: `web-extension/src/connector.ts:225-226` (`ConnectorAppDeps`), `:648-670` (link start)
- Test: `web-extension/test/connector/revocation-integration.test.ts` (`FakeTransport` `:414`, `startLink` `:472`, `driveToActive` `:511`, `pairThroughApp` `:1412` and its inline copy at `:1938`)

**Interfaces:**
- Consumes: `onConnectionClosed` from Task 1.
- Produces: `export const ACTIVATION_HOLD_MS = 5_000`; `PairingLinkDeps.setTimeout?: (fn: () => void, ms: number) => unknown` and `clearTimeout?: (handle: unknown) => void`; `ConnectorAppDeps.setInterval` / `clearInterval`.
- Produces: `driveToActive(link, transport, phone, options: { close?: boolean } = {})`, defaulting to `close: true`, and `pairThroughApp(fixture, phone, deviceHandle, options: { close?: boolean } = {})` with the same default.
- Produces (private in `link.ts`): `activationCompleted(): boolean`, `armHold(): void`, `finishHold(disconnect: boolean): Promise<void>`, `draining`, `peerClosed`, and `stop(disconnect = true)`.

- [ ] **Step 1: Extend the harness so the file stays green in this commit**

In `revocation-integration.test.ts`:

```ts
class FakeTransport implements PairingTransport {
    readonly sent: Uint8Array[] = [];
    disconnectCount = 0;
    sendGate: Promise<void> | null = null;
    failNextSend = false;
    private closeListeners = new Set<() => void>();
    private open = true;
    private sendEntered: (() => void) | null = null;
    // connect/send/disconnect as today, plus:
    // send: this.sendEntered?.(); this.sendEntered = null;
    //       if (this.failNextSend) { this.failNextSend = false; throw new Error('send failed'); }
    //       if (this.sendGate !== null) await this.sendGate;
    /** Resolves when the next `send` is entered, before it waits on the gate. */
    nextSendEntered(): Promise<void> {
        return new Promise<void>((resolve) => {
            this.sendEntered = resolve;
        });
    }
    onConnectionClosed(_handle: string, listener: () => void): () => void {
        if (!this.open) {
            listener();
            return () => undefined;
        }
        this.closeListeners.add(listener);
        return () => this.closeListeners.delete(listener);
    }
    closeHandle(_handle: string): void {
        this.open = false;
        for (const listener of [...this.closeListeners]) listener();
        this.closeListeners.clear();
    }
}
```

Also in this step: pass `setTimeout: stack.clock.setTimeout` and `clearTimeout:
stack.clock.clearTimeout` from `startLink` (`:472-481`); add the `close` option to `driveToActive`
(`:511-522`) and to `pairThroughApp` (`:1412-1459`), both defaulting to true and ending with
`transport.closeHandle(handle)` / an unsolicited `port.emit(disconnectedEvent({ request_id:
undefined, connection_handle: PAIR_HANDLE }))` plus `await link.settled()` / `await flush()`; and
replace the two post-ack `await ackDisconnect(port, PAIR_HANDLE)` calls (`:1457` and the inline copy
at `:1938`) with that same unsolicited close. Leave `ackDisconnect` itself unchanged for its other
callers. Add `disconnectedEvent` to the `./fixtures` import.

- [ ] **Step 2: Write the failing tests** in a new `describe('completion hold', …)` block, importing
      `ACTIVATION_HOLD_MS` from `../../src/pairing/link` and `preauthReject` from
      `../../src/pairing/frames`:

```ts
describe('completion hold', () => {
    it('finishes without a disconnect when the phone closes after the ack', async () => {
        const stack = await createStack();
        const { link, transport } = await startLink(stack);
        const phone = await PhonePeer.create({ code: CODE });
        try {
            await driveToActive(link, transport, phone, { close: false });
            expect(transport.disconnectCount).toBe(0);
            transport.closeHandle(link.connectionHandle);
            await link.settled();
            expect(transport.disconnectCount).toBe(0);
            expect(link.machine.state).toBe('active');
            expect(link.machine.failed).toBe(false);
            expect(stack.clock.pendingTimers).toBe(0);
        } finally {
            phone.close();
        }
    });

    it('issues exactly one disconnect when the hold bound expires', async () => {
        const stack = await createStack();
        const { link, transport } = await startLink(stack);
        const phone = await PhonePeer.create({ code: CODE });
        try {
            await driveToActive(link, transport, phone, { close: false });
            expect(transport.disconnectCount).toBe(0);
            stack.clock.advance(ACTIVATION_HOLD_MS);
            await link.settled();
            expect(transport.disconnectCount).toBe(1);
            expect(link.machine.state).toBe('active');
            expect(link.machine.failed).toBe(false);
            // The link deleted nothing: the activated record is still stored.
            expect((await stack.repository.peers()).length).toBe(1);
            expect(link.machine.fingerprint()).toBeDefined();
        } finally {
            phone.close();
        }
    });

    it('drops a reject record that arrives during the hold', async () => {
        const stack = await createStack();
        const { link, transport } = await startLink(stack);
        const phone = await PhonePeer.create({ code: CODE });
        try {
            await driveToActive(link, transport, phone, { close: false });
            const written = transport.sent.length;
            await link.receive(preauthReject());
            expect(link.machine.state).toBe('active');
            expect(link.machine.failed).toBe(false);
            expect(transport.sent.length).toBe(written);
        } finally {
            phone.close();
        }
    });

    it('has already erased the enrollment material when the hold starts', async () => {
        const stack = await createStack();
        const { link, transport } = await startLink(stack);
        const phone = await PhonePeer.create({ code: CODE });
        try {
            await driveToActive(link, transport, phone, { close: false });
            expect(link.machine.enrollmentKeysForTest()).toBeNull();
            expect(link.machine.closed).toBe(true);
        } finally {
            phone.close();
        }
    });

    it('finishes at once when the close was seen before the ack send resolved', async () => {
        const stack = await createStack();
        const { link, transport } = await startLink(stack);
        const phone = await PhonePeer.create({ code: CODE });
        try {
            await driveToPending(link, transport, phone);
            await link.receive(await phone.prepareFrame());
            let release!: () => void;
            transport.sendGate = new Promise<void>((resolve) => {
                release = resolve;
            });
            const entered = transport.nextSendEntered();
            const acking = link.receive(await phone.completeFrame());
            await entered; // the ack's send is in flight, parked on the gate
            transport.closeHandle(link.connectionHandle);
            release();
            await acking;
            await link.settled();
            expect(transport.disconnectCount).toBe(0);
            expect(stack.clock.pendingTimers).toBe(0);
        } finally {
            phone.close();
        }
    });

    it('leaves the machine active when the ack send fails', async () => {
        const stack = await createStack();
        const { link, transport } = await startLink(stack);
        const phone = await PhonePeer.create({ code: CODE });
        try {
            await driveToPending(link, transport, phone);
            await link.receive(await phone.prepareFrame());
            transport.failNextSend = true;
            await link.receive(await phone.completeFrame());
            expect(link.machine.state).toBe('active');
            expect(link.machine.failed).toBe(false);
            expect(transport.disconnectCount).toBe(1);
        } finally {
            phone.close();
        }
    });

    it('closes on terminate during the hold without failing the machine', async () => {
        const stack = await createStack();
        const { link, transport } = await startLink(stack);
        const phone = await PhonePeer.create({ code: CODE });
        try {
            await driveToActive(link, transport, phone, { close: false });
            await link.terminate();
            expect(link.machine.state).toBe('active');
            expect(link.machine.failed).toBe(false);
            expect(transport.disconnectCount).toBe(1);
        } finally {
            phone.close();
        }
    });

    it('cancel and dispose during the hold end idle with the record kept', async () => {
        for (const finish of ['cancel', 'dispose'] as const) {
            const stack = await createStack();
            const { link, transport } = await startLink(stack);
            const phone = await PhonePeer.create({ code: CODE });
            try {
                await driveToActive(link, transport, phone, { close: false });
                if (finish === 'cancel') await link.cancel();
                else await link.dispose();
                expect(link.machine.state).toBe('idle');
                expect(link.machine.failed).toBe(false);
                expect(transport.disconnectCount).toBe(1);
                expect((await stack.repository.peers()).length).toBe(1);
            } finally {
                phone.close();
            }
        }
    });
});
```

- [ ] **Step 3: Run them to verify they fail**

Run: `cd web-extension && npx vitest run test/connector/revocation-integration.test.ts -t "completion hold"`
Expected: FAIL — `ACTIVATION_HOLD_MS` is not exported and the first test sees `disconnectCount === 1`.
The erased-material, reject-drop and terminate-during-hold tests are characterization tests: they may already pass and are
not part of the red proof.

- [ ] **Step 4: Implement the hold and the finish in `link.ts`**

Add the timer deps with global defaults, `ACTIVATION_HOLD_MS = 5_000`, `private holdTimer: unknown`,
`private unsubscribeClose: (() => void) | null`, `private draining = false` and
`private peerClosed = false`. Rename `clearTimer()` to `clearTimers()`, clearing the tick and the
hold. In `start()`, right after `connect` resolves and before the first `send`, subscribe
`onConnectionClosed(handle, () => { link.peerClosed = true; if (link.draining) void link.finishHold(false); })`,
and unsubscribe on every startup failure exit. In the queued `receive` handler, return early when
`this.draining`, and keep the existing guard, changing only its action:

```ts
if (this.machine.closed || this.machine.failed) {
    if (this.activationCompleted()) this.armHold();
    else await this.stop();
}
```

`activationCompleted()` is `this.machine.closed && this.machine.state === 'active' && !this.machine.failed`.
`armHold()` returns early when `stopped || disposed`; otherwise it sets `draining`, unsubscribes
`onRecord`, and either calls `finishHold(false)` at once when `peerClosed` or starts `holdTimer` with
`ACTIVATION_HOLD_MS` calling `finishHold(true)`. `finishHold(disconnect)` returns early when
`stopped || disposed`, otherwise sets `stopped`, clears both timers, unsubscribes the close listener
and `await this.stop(disconnect)` (no await before `stop` reaches `controller.disconnect`, so a
test that advances the clock sees the request at once); it does not call `notifyEnded()` itself. `stop(disconnect = true)`
sets `stopped`, unsubscribes both listeners before the request, calls `notifyEnded()` once after it,
and skips `controller.disconnect` when `disconnect === false`. In `terminate()` and in the `receive`
catch, call `machine.onDisconnect()` only when `!activationCompleted()`. In `connector.ts`, add
`setInterval`/`clearInterval` to `ConnectorAppDeps` and pass the app's existing
`setTimeout`/`clearTimeout` together with that new interval pair to `PairingLink.start` — the app
fixture already injects the first two (`revocation-integration.test.ts:1306-1307`).

- [ ] **Step 5: Run the tests to verify they pass**

Run: `cd web-extension && npx vitest run test/connector/revocation-integration.test.ts -t "completion hold"`
Expected: PASS.

- [ ] **Step 6: Run the whole file and the neighbouring suites**

Run: `cd web-extension && npx vitest run test/connector`
Expected: PASS — the harness changes in Step 1 keep the existing app-level flows green. The
loopback suite is not run here: its `enrollment_full` would leave a real 5 s timer pending until
Task 3 migrates it, so land Tasks 2 and 3 together and run `sessionLoopbackCheck` only after Task 3.

- [ ] **Step 7: Commit**

```bash
git add web-extension/src/pairing/link.ts web-extension/src/connector.ts web-extension/test/connector/revocation-integration.test.ts
git commit -m "feat(otp-10): hold the enrollment link until the phone closes it"
```

---

### Task 3: Migrate the loopback suite

**Files:**
- Modify: `web-extension/test/session/loopback.test.ts` (`enrollment_full` `:1143`)

**Interfaces:**
- Consumes: `ACTIVATION_HOLD_MS` and the close notification from Tasks 1-2.

- [ ] **Step 1: Update `enrollment_full`**: give `LoopbackPairingTransport` the same
      `onConnectionClosed`/`closeHandle` pair as the other fakes, start the link with the test's
      `SessionClock` `setTimeout`/`clearTimeout` (so the hold never uses a real timer), and, after the ack,
      reuse the existing `await phonePairingState(phone)` `'ACTIVE'` check at `:1198` and then call
      `closeHandle(handle)`. The loopback `closeHandle` must also set `this.running = false`, so its read
      loop stops taking the phone's frames before the reconnect. Assert `clock.pendingTimers === 0`
      right after `closeHandle`, before `reconnectClient(...)` reuses the clock. `interrupted_activation_resumes` (`:1228`) needs no change.
- [ ] **Step 2: Run the loopback suite through Gradle** (it is not in the `npm test` list):

Run: `./gradlew sessionLoopbackCheck`
Expected: PASS with `enrollment_full` included.

- [ ] **Step 3: Commit**

```bash
git add web-extension/test/session/loopback.test.ts
git commit -m "test(otp-10): fire the phone close in the enrollment loopback"
```

---

### Task 4: Report the enrollment finished while the link is held

**Files:**
- Modify: `web-extension/src/connector.ts:716-728`
- Test: `web-extension/test/connector/revocation-integration.test.ts` (inside the inline post-ack flow at `:1934-1938`)

**Interfaces:**
- Produces: `pairingState.finished === pairingEnded || machine.closed`; `pairingFinished` unchanged.

- [ ] **Step 1: Write the failing assertions** in that test's inline post-ack flow (it drives the
      link by hand and does not go through `pairThroughApp`): after the ack's `ackSend`, and before
      the unsolicited peer close Task 2 Step 1 put there, add:

```ts
expect(fixture.app.pairingState.finished).toBe(true);
expect(fixture.app.pairingFinished).toBe(false);
```

- [ ] **Step 2: Run it to verify it fails**

Run: `cd web-extension && npx vitest run test/connector/revocation-integration.test.ts -t "waits for a reset in flight"`
Expected: FAIL — `finished` is `false` during the hold.

- [ ] **Step 3: Implement** the `finished` expression in `pairingState`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd web-extension && npx vitest run test/connector/revocation-integration.test.ts test/debug/pairing-panel.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web-extension/src/connector.ts web-extension/test/connector/revocation-integration.test.ts
git commit -m "fix(otp-10): show the enrollment finished while the link is held"
```

---

### Task 5: Teardown of a held link through the connector

**Files:**
- Test: `web-extension/test/connector/revocation-integration.test.ts`

**Interfaces:**
- Consumes: `pairThroughApp(..., { close: false })`, `createAppFixture`, `startApp`, `fixture.app.resetTrust()`, `fixture.controller.currentPort().disconnect()`, `fixture.app.failClosed()`, `ackDisconnect`, `postsOf`.

- [ ] **Step 1: Write the failing tests**, all in the `completion hold` describe block (wrap each
      body after `PhonePeer.create` in `try { … } finally { phone.close(); }` as the other tests do). Closing a
      held link posts a disconnect and `resetTrust()`/`failClosed()` wait for its reply, so each test
      starts the teardown without awaiting it, answers the disconnect, then awaits (as the existing
      reset tests do with `ackDisconnect`). The peer-revocation case lives in Task 6, which adds the
      remembered peer id it needs.

```ts
it('closes a held link on a reset and leaves the machine active', async () => {
    const fixture = createAppFixture();
    await startApp(fixture);
    const port = fixture.controller.currentPort();
    const phone = await PhonePeer.create({ code: CODE });
    await pairThroughApp(fixture, phone, PAIR_DEVICE, { close: false });
    const resetting = fixture.app.resetTrust();
    await ackDisconnect(port, PAIR_HANDLE);
    await resetting;
    await flush();
    expect(postsOf(port, 'disconnect')).toHaveLength(1);
    expect(fixture.app.pairingState).toMatchObject({ phase: 'active', finished: true });
    phone.close();
});

it('closes a held link when the port dies, with no disconnect request', async () => {
    const fixture = createAppFixture();
    await startApp(fixture);
    const port = fixture.controller.currentPort();
    const phone = await PhonePeer.create({ code: CODE });
    await pairThroughApp(fixture, phone, PAIR_DEVICE, { close: false });
    await port.disconnect();
    await flush();
    expect(fixture.app.pairingState).toMatchObject({ phase: 'active', finished: true });
    expect(postsOf(port, 'disconnect')).toHaveLength(0);
    expect((await requireRepository(fixture).peers()).length).toBe(1);
    phone.close();
});

it('closes a held link during a fail-closed recovery', async () => {
    const fixture = createAppFixture();
    await startApp(fixture);
    const port = fixture.controller.currentPort();
    const phone = await PhonePeer.create({ code: CODE });
    await pairThroughApp(fixture, phone, PAIR_DEVICE, { close: false });
    // The public entry the store-failure sink calls (`ConnectorApp.failClosed`, connector.ts:770).
    fixture.app.failClosed();
    await ackDisconnect(port, PAIR_HANDLE);
    await flush();
    expect(postsOf(port, 'disconnect')).toHaveLength(1);
    expect(fixture.app.pairingState).toMatchObject({ phase: 'active', finished: true });
    // Fail-closed is non-destructive: the link deleted no record.
    expect((await requireRepository(fixture).peers()).length).toBe(1);
    phone.close();
});

it('schedules no recovery and shows no failure when the phone closes a held link', async () => {
    const fixture = createAppFixture();
    await startApp(fixture);
    const port = fixture.controller.currentPort();
    const phone = await PhonePeer.create({ code: CODE });
    await pairThroughApp(fixture, phone, PAIR_DEVICE, { close: false });
    await port.emit(disconnectedEvent({ request_id: undefined, connection_handle: PAIR_HANDLE }));
    await flush();
    expect(fixture.app.pairingState).toMatchObject({ phase: 'active', finished: true });
    expect(fixture.controller.controller.snapshot().connections).toEqual([]);
    // No client disconnect, no timer left pending, and no reconnect even after the clock moves
    // far past any reconnect delay. The machine staying `active` is the failure-notice proof:
    // a reported failure would end it `idle` with `failed: true`.
    expect(postsOf(port, 'disconnect')).toHaveLength(0);
    expect(fixture.controller.clock.pendingTimers).toBe(0);
    fixture.controller.clock.advance(60_000);
    await flush();
    expect(postsOf(port, 'connect')).toHaveLength(1);
    expect((await requireRepository(fixture).peers()).length).toBe(1);
    phone.close();
});
```

- [ ] **Step 2: Prove the reset, port-death and fail-closed cases are red without the terminal-active rule**

By hand, replace the two guards Task 2 introduced — in `terminate()` and in the `receive` catch —
turning `if (!this.activationCompleted()) await this.machine.onDisconnect();` into
`if (true) await this.machine.onDisconnect();`. Do **not** change `activationCompleted()` itself:
that would remove the hold and these tests would never reach a held link.

Run: `cd web-extension && npx vitest run test/connector/revocation-integration.test.ts -t "held link"`
Expected: FAIL on the reset, port-death and fail-closed cases — the machine ends `idle` with
`failed: true`, so `phase` is `idle`. Restore both guard lines before continuing.

- [ ] **Step 3: Confirm the wiring they expose** — `ConnectorApp` must pass its injected timers plus
      the interval pair into the link (Task 2 Step 4), and `handleClose` must end the attempt through
      `abandonPairing()` as it already does. Do not add a second teardown path.

- [ ] **Step 4: Run them to verify they pass**

Run: `cd web-extension && npx vitest run test/connector/revocation-integration.test.ts`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web-extension/test/connector/revocation-integration.test.ts
git commit -m "test(otp-10): cover teardown of a held enrollment link"
```

---

### Task 6: Keep a held link reachable by revocation while the ack is in flight

**Files:**
- Modify: `web-extension/src/pairing/link.ts` (`boundPeerId`, `rememberPeerId`, `receive`)
- Test: `web-extension/test/connector/revocation-integration.test.ts`

**Interfaces:**
- Produces: `boundPeerId()` answers `machine.boundPeerId() ?? rememberedPeerId` from
  `activationCompleted()` until `stopped`; the id is sampled after every `onFrame` return and after
  `commit()`, keeping only a non-null value.

- [ ] **Step 1: Write the failing tests** in the `completion hold` block:

```ts
it('answers the bound peer id during the hold after the machine erased it', async () => {
    const stack = await createStack();
    const { link, transport } = await startLink(stack);
    const phone = await PhonePeer.create({ code: CODE });
    try {
        await driveToActive(link, transport, phone, { close: false });
        expect(link.machine.boundPeerId()).toBeUndefined();
        expect(link.boundPeerId()).toBe(hexOf(phone.installationId));
    } finally {
        phone.close();
    }
});

it('stops answering the bound peer id after the held link finishes', async () => {
    const stack = await createStack();
    const { link, transport } = await startLink(stack);
    const phone = await PhonePeer.create({ code: CODE });
    try {
        await driveToActive(link, transport, phone, { close: false });
        transport.closeHandle(link.connectionHandle);
        await link.settled();
        expect(link.boundPeerId()).toBeUndefined();
    } finally {
        phone.close();
    }
});

it('closes a held link on peer revocation and deletes only through the revocation', async () => {
    const stack = await createStack();
    const { link, transport } = await startLink(stack);
    const phone = await PhonePeer.create({ code: CODE });
    try {
        await driveToActive(link, transport, phone, { close: false });
        expect((await stack.repository.peers()).length).toBe(1);
        await stack.coordinator.revoke(hexOf(phone.installationId));
        expect(transport.disconnectCount).toBe(1);
        expect((await stack.repository.peers()).length).toBe(0);
    } finally {
        phone.close();
    }
});

it('lets a revocation select the link while the ack send is pending', async () => {
    const stack = await createStack();
    const { link, transport } = await startLink(stack);
    const phone = await PhonePeer.create({ code: CODE });
    try {
        await driveToPending(link, transport, phone);
        await link.receive(await phone.prepareFrame());
        let release!: () => void;
        transport.sendGate = new Promise<void>((resolve) => {
            release = resolve;
        });
        const entered = transport.nextSendEntered();
        const acking = link.receive(await phone.completeFrame());
        await entered; // the ack's send is in flight, parked on the gate
        // Not awaited yet: `terminate()` waits on the link queue, which is blocked on the gate.
        const revoking = stack.coordinator.revoke(hexOf(phone.installationId));
        // Let the revocation reach the link selection while the send is still parked.
        await microFlush();
        release();
        await acking;
        await revoking;
        await link.settled();
        expect(transport.disconnectCount).toBe(1);
        expect((await stack.repository.peers()).length).toBe(0);
    } finally {
        phone.close();
    }
});
```

- [ ] **Step 2: Run them to verify they fail**

Run: `cd web-extension && npx vitest run test/connector/revocation-integration.test.ts -t "completion hold"`
Expected: FAIL — `boundPeerId()` returns `undefined` after activation, so the two revocation tests
find no link to close and see `disconnectCount === 0`.

- [ ] **Step 3: Implement** `rememberPeerId()` (called after each `machine.onFrame(...)` return and
      after `machine.commit()`, storing only a non-null id) and the accessor rule above.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `cd web-extension && npx vitest run test/connector/revocation-integration.test.ts -t "completion hold"`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add web-extension/src/pairing/link.ts web-extension/test/connector/revocation-integration.test.ts
git commit -m "fix(otp-10): keep a held enrollment link reachable by revocation"
```

---

### Task 7: Verification, device run and docs

**Files:**
- Modify: `docs/otp-11-debug-controls.md` (observed values of the enrollment run)

- [ ] **Step 1: Run the extension checks**

```bash
./gradlew rustWasm rustHostJni
cd web-extension && npm ci && npm run format:check && npm run lint && npm run typecheck && npm test && npm run build && npm run test:bundle
```

Expected: all pass.

- [ ] **Step 2: Run the loopback suite**

Run: `./gradlew sessionLoopbackCheck`
Expected: PASS.

- [ ] **Step 3: Derive and run the changed-node checks**

```bash
python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)
openspec validate --all --strict
```

Expected: the extension and docs nodes run their checks and the change validates.

- [ ] **Step 4: Real-device acceptance run**

On the phone install the debug APK; in Chrome load the development bundle with the Native Messaging
host built from the same commit. Enroll at least ten consecutive times and take the first reconnect
after each. For every run record: the phone log line showing the record `ACTIVE` before the link
closes, the native-port trace showing the phone's `disconnected` event and no client `disconnect`
request, the first reconnect's class (FULL), an OTP arriving over it, and mutual removal accepted
with no extra disconnect. Exercise cancel, tab close and a revocation during the hold where they can
be triggered by hand. If a device or browser is unavailable, stop and ask; never report the change as
tested.

- [ ] **Step 5: Record the evidence and the escape hatch** in `docs/otp-11-debug-controls.md`: observed
      screens and trace facts only, never identifiers, keys, hints or codes, plus one sentence naming
      the paths that were unit-tested only (the bound-expiry path and any edge path not triggerable by
      hand). Carry the same sentence into the pull-request note at finalize; note it in the change's
      `finalize.md` so it is not lost.

- [ ] **Step 6: Re-run the docs node checks after that edit**

```bash
python3 docs/checks/check_agent_files.py --root .
python3 -m unittest discover -s docs/checks -p 'test_*.py' -v
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add docs/otp-11-debug-controls.md openspec/changes/otp-10-follow-up-activation-ack-flush/finalize.md
git commit -m "docs(otp-10): record the activation ack flush device run"
```
