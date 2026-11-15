# OTP-11 Peer Revocation and Fail-Closed Recovery Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let the Android app and the Chrome extension each list, revoke and mutually remove trusted peers and recover fail-closed from lost or corrupt trust state.

**Architecture:** A revocation coordinator per endpoint (Kotlin, TypeScript) sits above the existing trust repository and session layer. It owns a per-peer fence, ordered delete, terminate, sweep revocation, a detached completion job for handler-driven removal that is started when the fence is taken and always releases it, and an asynchronous fail-closed action fed by a store-failure callback. Mutual removal uses the already-specified 0x0205/0x0206 records through a new engine "close after reply" outcome, an engine-level requester guard and a session-host `requestRemoval` owner API. Tasks 4 and 9 define the handler contracts and test them with fakes; the coordinators (Tasks 5 and 10) implement those contracts and do the production wiring.

**Tech Stack:** Kotlin, Room, Android Keystore, kotlinx coroutines, JUnit (android/); TypeScript, Web Crypto, chrome.storage, the repo's `npm test` runner (web-extension/); protocol conformance JSON (protocol/).

**Spec:** `openspec/changes/otp-11-peer-revocation/` — `design.md` (D1-D10), `specs/*/spec.md`, `tasks.md`; RFC `docs/rfcs/2026-08-21-otp-external-devices.md` section OTP-11; contract `protocol/docs/veles-v1.md` OTP2-REC-013.

**Plan refinements of the design (behavior-equivalent):** the detached finish calls `closePeer` for every matching generation including the current one, which is safe because it is a detached job whose termination queues behind the handler's whole step, so no `excluding` parameter exists; fail-closed and reset use the non-destructive `closeAll`, and reset's deletion removes every record anyway.

## Global Constraints

- No protocol, wire or normative fixture change; only conformance inventory entries move from `deferred:OTP-11` to executed.
- Removal frames: `removal_request` 0x0205 and `removal_response` 0x0206, empty body, FULL class only.
- `REMOVAL_RESPONSE_TIMEOUT_MS` = 10 000 ms on an injected clock (design call).
- Tests never sleep or read the wall clock; inject clocks and timers (`.agents/rules/repo-no-time-based-tests.md`).
- Delete first, always terminate: revocation terminates matching sessions even when the delete throws; identity reset terminates everything before deleting.
- Fail-closed deletes nothing: it uses a non-destructive teardown (`closeAll` / `terminate`), never the destructive `cancelPairings` / `cancel`.
- Peer selection uses the authenticated installation id only; never labels, native handles or hints.
- No secret, key, code or hint is logged (no raw `android.util.Log`); peer summaries carry no key material, hint or handle.
- Debug controls exist only in debug builds and the development bundle; strings go into `releaseHookForbidden` and the `bundle.test.ts` FORBIDDEN list.
- Commit subjects are Conventional Commits `type(scope): description` (`.agents/rules/repo-conventional-commits.md`).
- Never regenerate or commit signing keys.
- Commands: Android from `android/` (`cd android && ./gradlew ...`); the extension needs `./gradlew rustWasm` from the repo root once, then `cd web-extension && npm test`; every new extension test file is added to the explicit list in `web-extension/package.json`.

## Review Focus

- Revoke racing an enrollment that has authenticated but not persisted the peer (resurrected record): Task 1, Task 5, Task 7, Task 10.
- Mutual-removal responder that throws while deleting inside the handler (self-deadlock or open session): Task 4, Task 5, Task 9.
- Corrupt stored state first observed in a reconnect lookup while other peers are connected: Task 5 and Task 10 integration tests.
- A silent session after `removal_request` outliving 10 000 ms (Chrome tick is 15 000 ms): Task 4 and Task 9.
- Reset interrupted between the row delete and the key delete, and a reset leaving a stale cached identity: Task 2 and Task 5.

---

## Task 1: Android trust-store failure classification, fence, callback

**Files:**
- Modify: `android/app/src/main/java/me/nagaev/veles/trust/TrustRepository.kt` (make `TrustException` `open`), `.../trust/TrustDatabase.kt`
- Test: `android/app/src/test/java/me/nagaev/veles/trust/TrustRepositoryTest.kt`

**Interfaces:**
- Produces: `open class TrustException`; `class TrustStoreException : TrustException`; `TrustRepository(..., onStoreFailure: () -> Unit = {})`; on `InstallationTrustRepository`: `suspend fun beginRevocation(peerId: String)`, `suspend fun endRevocation(peerId: String)` (reference-counted per id); `addPending` throws a plain `TrustException` while the id is fenced.

- [ ] **Step 1: Write failing tests** in `TrustRepositoryTest`: `corruptStoredStateIsStoreFailure` (storage throws on decode), `postOperationValidationFailureIsStoreFailure`, `missingPeerIsPeerRejection` (`confirm` of an unknown id throws `TrustException`, not `TrustStoreException`), `failingDurableWriteIsStoreFailure`, `onStoreFailureRunsOncePerEpisode`, `newEpisodeAfterSuccessfulTransaction`, `episodeEndsOnReset`, `addPendingRefusedWhileFenced`, `overlappingFencesReleaseOnlyWhenBothEnd`.
- [ ] **Step 2: Run** `cd android && ./gradlew testDebugUnitTest --tests "me.nagaev.veles.trust.TrustRepositoryTest"`; expected FAIL (types missing).
- [ ] **Step 3: Implement** the exception hierarchy, snapshot-level and post-operation-validation throws in `update()` as `TrustStoreException`, `RoomTrustStorage.transaction` mapping decode, database and write errors to `TrustStoreException` while letting the block's plain `TrustException` through unchanged, the callback at the `update()` choke point (episode runs until the next successful transaction or a reset), and the reference-counted fence checked inside `addPending`'s transaction.
- [ ] **Step 4: Run** the same command, then `./gradlew testDebugUnitTest`; expected PASS.
- [ ] **Step 5: Commit** `feat(android): classify trust-store failures and fence revoking peers`

## Task 2: Android identity reset primitives

**Files:**
- Modify: `.../trust/TrustRepository.kt` (`TrustStorage`, `IdentityKeyStore`, `TrustRepository`), `.../trust/TrustDatabase.kt`, `.../trust/AndroidIdentityKeyStore.kt`; every test fake of those interfaces.
- Test: `TrustRepositoryTest.kt`

**Interfaces:**
- Produces: `TrustStorage.reset()` (suspend, decode-free row delete), `IdentityKeyStore.delete()`, `InstallationTrustRepository.resetIdentity()` (suspend: `storage.reset()` then `keys.delete()`; idempotent; ends the failure episode).

- [ ] **Step 1: Write failing tests**: `resetWorksWithUndecodablePayload`, `interruptionAfterRowDeleteFailsClosedThenResetCompletes`, `interruptionAfterKeyDeleteFailsClosedThenResetCompletes`, `interruptionAfterBothDeletionsInitializesFresh`, `storeFailureNeverTriggersReset`, `nextUseCreatesFreshIdentityAndNoPeers`.
- [ ] **Step 2: Run** the class as in Task 1; expected FAIL.
- [ ] **Step 3: Implement** the three members (a Room query that deletes without decoding; `KeyStore.deleteEntry` for alias `veles.installation.identity.v1`), updating every fake.
- [ ] **Step 4: Run** the class; expected PASS.
- [ ] **Step 5: Commit** `feat(android): add explicit identity reset primitives`

## Task 3: Android session hooks

**Files:**
- Modify: `.../sharing/SessionHost.kt`, `.../pairing/PhonePairingMachine.kt` (and the `PairingConnection` interface), `.../session/SessionEffect.kt` (`ApplicationHandler`), `.../session/PhoneSessionEngine.kt`, `.../session/HandlerComposition.kt`
- Test: `.../sharing/SessionHostTest.kt`, `.../session/` engine and composition tests

**Interfaces:**
- Produces: `PairingConnection.boundPeerId(): String?`; `suspend fun SessionHost.closePeer(peerIdHex: String)` (terminates every authenticated generation whose `Live.peerId` matches and every enrollment connection whose `boundPeerId()` matches, and returns only after each matched generation has ended; it takes each generation's own mutex, so it runs behind any in-flight step of that generation, including a handler's reply-send and close effects; never call it from inside a handler); `suspend fun SessionHost.closeAll()` (non-destructive: `teardownLocked` + `transport.close` for every generation, pairing durable records kept; distinct from the destructive `cancelPairings()`); `ApplicationHandler.closeAfterReply(type: Int): Boolean = false`, forwarded by `CompositeApplicationHandler` to the child that handled the type, and honored by the engine's terminal-reply path (replies sealed, teardown, `Terminated` + `Close`, nothing accepted afterwards).

- [ ] **Step 1: Write failing tests**: `closePeerClosesOnlyMatchingGenerations` (including an enrollment connection bound to the peer and two peers with the same label), `closePeerReturnsAfterEnded`, `closePeerWaitsBehindInFlightHandlerStep` (the reply is delivered before the close), `closeAllKeepsDurablePendingRecords`, `closeAfterReplySendsReplyThenCloses`, `compositeForwardsCloseAfterReply`, `transportCloseFailureStillErasesKeys`.
- [ ] **Step 2: Run** `cd android && ./gradlew testDebugUnitTest --tests "me.nagaev.veles.sharing.SessionHostTest" --tests "me.nagaev.veles.session.*"`; expected FAIL.
- [ ] **Step 3: Implement** the members; selection reads `Live.peerId` and `Live.pairing?.boundPeerId()` under `registry`; each match runs `teardownLocked` + `transport.close` under its own mutex and completes a per-`Live` `CompletableDeferred` at the end of teardown; the engine's `terminalError` branch also fires on `handler.closeAfterReply(type)`.
- [ ] **Step 4: Run** the same command; expected PASS.
- [ ] **Step 5: Commit** `feat(android): terminate sessions by peer and close after reply`

## Task 4: Android removal handler, requester owner API, deadline

**Files:**
- Create: `.../session/RemovalHandler.kt`
- Modify: `.../session/PhoneSessionEngine.kt` (removal-pending guard), `.../sharing/SessionHost.kt` (owner API and deadline step)
- Test: `.../session/RemovalHandlerTest.kt`, `.../sharing/SessionHostRemovalTest.kt`

**Interfaces:**
- Consumes: Task 3 `closeAfterReply`.
- Produces: `interface RemovalOperations { suspend fun deleteFenced(peerId: String) }` (takes the revocation fence, starts the coordinator's detached finish job, then deletes; throws `TrustStoreException` on store failure); `class RemovalHandler(ops: RemovalOperations) : ApplicationHandler` whose `forSession(context)` returns a session-scoped instance bound to `context.peerId` (types 0x0205/0x0206; `closeAfterReply` true for the request on the responder and for a valid response on the requester); a new terminal stage `STAGE_REMOVAL` emitted by the engine when a removal ends the session, which the host maps to settle `requestRemoval` with `REMOVED` (any other end of an awaiting session settles `KEPT_NO_RESPONSE`); `enum class RemovalResult { REMOVED, KEPT_NOT_CONNECTED, KEPT_NO_RESPONSE }`; `suspend fun SessionHost.requestRemoval(peerIdHex: String): RemovalResult` (on the serialized queue of the peer's FULL generation: sets the engine removal-pending guard, sends `removal_request`, arms the deadline, settles on `STAGE_REMOVAL`, disconnect or `REMOVAL_RESPONSE_TIMEOUT_MS`; with no FULL session it returns `KEPT_NOT_CONNECTED`); `const val REMOVAL_RESPONSE_TIMEOUT_MS = 10_000L`; engine guard rejecting every inbound record except `removal_response`, heartbeat and `app_error`, and every outbound application send, while pending.

- [ ] **Step 1: Write failing tests** with an injected clock and a fake `RemovalOperations`: `responderDeletesRepliesAndCloses`, `responderDeleteFailureEndsSessionAndReportsStoreFailure`, `requesterDeletesOnlyAfterValidResponse`, `lostResponseKeepsRecord`, `disconnectKeepsRecord`, `deadlineAtTenThousandMillisKeepsRecord`, `pendingRequesterRejectsOtherInboundRecords`, `pendingRequesterRejectsOutboundApplicationSend`, `heartbeatAndAppErrorStillAllowedWhilePending`, `unsolicitedResponseIsViolationAndDeletesNothing`, `removalOnNonFullSessionEnds`, `requestRemovalWithoutFullSessionIsNotConnected`, `timerCancelledOnResponseDisconnectAndTeardown`.
- [ ] **Step 2: Run** `cd android && ./gradlew testDebugUnitTest --tests "me.nagaev.veles.session.RemovalHandlerTest" --tests "me.nagaev.veles.sharing.SessionHostRemovalTest"`; expected FAIL.
- [ ] **Step 3: Implement** the handler and engine guard; the deadline is a per-generation step on the host's queue compared with the injected clock on each existing 1 000 ms tick, cancelled on response, disconnect and teardown.
- [ ] **Step 4: Run** the same command; expected PASS.
- [ ] **Step 5: Commit** `feat(android): handle mutual peer removal records`

## Task 5: Android revocation coordinator, status sink and wiring

**Files:**
- Create: `.../trust/PeerRevocationCoordinator.kt`, `.../trust/PeerSummary.kt`
- Modify: `.../sharing/SharingService.kt`
- Test: `.../trust/PeerRevocationCoordinatorTest.kt`, `.../sharing/PeerRevocationIntegrationTest.kt`

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces: `data class PeerSummary(installationId: String, label: String, fingerprint: String, deviceType: Int, state: TrustState, createdAtMillis: Long, lastSeenMillis: Long?, connected: Boolean)`; `enum class TrustStatus { OK, RE_PAIR_REQUIRED }`; `class PeerRevocationCoordinator(trust: InstallationTrustRepository, host: SessionHost, scope: CoroutineScope, onStatus: (TrustStatus) -> Unit = {}, historyCleanup: (String) -> Unit = {}) : RemovalOperations` with `suspend fun peers(): List<PeerSummary>`, `suspend fun revoke(peerId: String)`, `suspend fun removeMutually(peerId: String): RemovalResult` (delegates to `host.requestRemoval`, then local removal only when the user later chooses it), `fun failClosed()`, `suspend fun reset()`; `deleteFenced` takes the fence, immediately launches the detached finish on `scope` with `CoroutineStart.ATOMIC` (the body is guaranteed to start, under `NonCancellable`, even if the scope is already cancelled; after a successful launch only the finish's own unconditional `finally` releases the fence, never a synchronous fallback, so the fence cannot be released early or twice) and then deletes. The finish runs `host.closePeer(peerId)` (it waits behind the handler's whole step, so the reply is delivered first, and returns after every matching generation has ended), then the sweep delete, then `endRevocation`, all in a `try`/`finally` under `NonCancellable`, so a failed delete, a handler or reply failure, a failed sweep (reported as a store failure, then `failClosed`) or a cancellation never leaves the peer fenced.

- [ ] **Step 1: Write failing tests** (fakes; ordering recorded in a list): `revokeOrderIsFenceDeleteTerminateAwaitSweepRelease`, `deleteFailureStillTerminatesMatching`, `detachedFinishWaitsBehindTheHandlerStep`, `deleteFailureInHandlerStillReleasesTheFence`, `handlerExceptionStillReleasesTheFence`, `sweepFailureReportsStoreFailureAndReleasesTheFence`, `cancellationStillReleasesTheFence`, `fenceHeldAtHandlerReturnReleasedAfterEnded`, `failClosedIsAsyncNonDestructiveAndReportsRePairRequired`, `handlerOriginatedStoreFailureDoesNotDeadlock`, `peerRejectionTerminatesNothingElse`, `resetTerminatesBeforeDeleting`, `summariesHaveNoSecrets`, `historyCleanupFailureNeverFailsRemoval`, `overlappingRevocationsOfOnePeerKeepTheFence`, `localRevokeReleasesFenceWhenCloseThrows`, `localRevokeReleasesFenceWhenSweepFails`, `localRevokeReleasesFenceOnCancellation`, `detachedFinishStartsEvenWhenScopeAlreadyCancelled` (dispatch paused: the fence stays held until the cleanup body has run, then is released exactly once).
- [ ] **Step 2: Write failing integration tests** in `PeerRevocationIntegrationTest` (real `TrustRepository` over in-memory storage, real `SessionHost`, fake transport, real engines and pairing machine): `corruptStateSeenByReconnectLookupClosesUnrelatedSessions`, `failingDurableWriteClosesUnrelatedSessions`, `revokeBeforeAddPendingLeavesNoRecordOrConnection`, `revokeBetweenAddPendingAndCommitFailsActivation`, `revokeBetweenCommitAndAckFailsAck`, `revokeDuringSignedRecoveryClosesConnection`, `revokeDuringDuplicateCommit`, `revokedPeerReconnectFailsGenerically`, `repairAfterRevokeMatchesFingerprints`, `pairWhileRecordHeldIsRefused`, `resetThenReconnectUsesFreshIdentity`.
- [ ] **Step 3: Run** `cd android && ./gradlew testDebugUnitTest --tests "me.nagaev.veles.trust.PeerRevocationCoordinatorTest" --tests "me.nagaev.veles.sharing.PeerRevocationIntegrationTest"`; expected FAIL.
- [ ] **Step 4: Implement** the coordinator per design D2/D3/D8: `revoke` = `beginRevocation`, then nested unconditional releases under `NonCancellable`: `try { delete } finally { try { host.closePeer } finally { try { sweep } finally { endRevocation } } }`, so a delete failure, a close exception, a failed sweep or a cancellation each still ends the fence; `failClosed` launches on `scope`, calls `host.closeAll()` (non-destructive), emits `RE_PAIR_REQUIRED`, deletes nothing; `reset` = `host.closeAll()`, `storage.reset()`, `keys.delete()`. In `SharingService`: wire `onStoreFailure = coordinator::failClosed`, install `RemovalHandler` in the production handler composition (not only debug), and replace the once-captured `localInstallationId` with a volatile identity cache that the coordinator's `reset()` refreshes from the repository after `resetIdentity()` (the synchronous `engineFactory` keeps reading the cache; a generation created between the termination and the refresh fails authentication and closes, which is fail-closed).
- [ ] **Step 5: Run** the same command and the full `testDebugUnitTest`; expected PASS.
- [ ] **Step 6: Commit** `feat(android): add the peer revocation coordinator`

## Task 6: Android conformance and inventory

**Files:**
- Modify: `protocol/conformance/otp-09-scenarios.json` (two entries), `android/app/src/test/java/me/nagaev/veles/session/ScenarioConformanceTest.kt`, `.../session/RecordCodecTest.kt`

- [ ] **Step 1: Move** `st-rec-removal-mutual-authenticated` and `st-rec-revoke-disconnects-connected-peer` from `deferred:OTP-11` to executed; extend the runner with the needed effect mappings and treat the removal response as an observation-only step (the handler already replied and terminated); do not edit `protocol/v1`.
- [ ] **Step 2: Run** `cd android && ./gradlew testDebugUnitTest --tests "me.nagaev.veles.session.ScenarioConformanceTest" --tests "me.nagaev.veles.session.RecordCodecTest"`, then from the repo root `./gradlew protocolCheck`; expected PASS with both scenarios executed.
- [ ] **Step 3: Commit** `test(android): execute the OTP-11 removal and revoke scenarios`

## Task 7: Chrome trust-store classification, fence, revoke

**Files:**
- Modify: `web-extension/src/trust/model.ts`, `web-extension/src/trust/repository.ts`
- Test: `web-extension/test/trust/revocation.test.ts` (new)

**Interfaces:**
- Produces: `class TrustStoreError extends TrustError`; `ChromeTrustRepository` option `onStoreFailure?: () => void`; `beginRevocation(peerId: string): void`, `endRevocation(peerId: string): void` (reference-counted); `revoke(peerId)` unconditional for any state and clears the hint; hint writes for a removed id rejected; `revokePending` unchanged; `reset()` ends the failure episode.

- [ ] **Step 1: Write failing tests**: `invalidIdentityIsStoreError`, `keysReadFailureIsStoreError`, `failingLocalWriteIsStoreError`, `postOperationValidationFailureIsStoreError`, `missingPeerIsTrustError`, `onStoreFailureOncePerEpisode`, `fencedAddPendingRefusedBeforeDurableWrite`, `overlappingFencesRefCounted`, `revokeWorksInEveryState`, `revokeRemovesHintAndRejectsLaterHintWrite`, `cancelPendingDoesNotDeleteRecoveredActiveRecord` (regression for `ClientMachine.#cancel`).
- [ ] **Step 2: Run** `./gradlew rustWasm` (root, once) then `cd web-extension && npm test`; expected FAIL on the new file.
- [ ] **Step 3: Implement**: `update()` classifies everything outside the `operation` call (read, decode, validation, post-operation validation, `local.write`) as `TrustStoreError`, keeping `TrustError` for the operation's checks and the fences; extend the generation fence to check the revocation mark immediately before the write; add the new test file to `package.json`.
- [ ] **Step 4: Run** `npm test`; expected PASS.
- [ ] **Step 5: Commit** `feat(web-extension): classify trust-store failures and fence revoking peers`

## Task 8: Chrome session hooks

**Files:**
- Modify: `web-extension/src/pairing/link.ts`, `web-extension/src/connector/sessions.ts`, `web-extension/src/session/engine.ts`, `web-extension/src/pairing/activation.ts` (`composeHandlers`)
- Test: `web-extension/test/connector/close-peer.test.ts` (new)

**Interfaces:**
- Produces: `PairingLink.boundPeerId(): string | undefined`; `PairingLink.terminate(): Promise<void>` (non-destructive: closes the handle, erases secrets, keeps durable records; distinct from the existing `cancel(): Promise<boolean>` and `dispose()` that delete pending); `SessionManager.closePeer(peerId: string): Promise<void>` (selects every entry of `byHandle`, handshaking or authenticated, whose `peerId` matches, plus matching enrollment links via an injected `links: () => readonly PairingLink[]`; each termination is enqueued on that session's serialized queue behind its in-flight step, so a handler's buffered reply is delivered before the close; resolves when each has closed; handles are strings); `SessionManager.closeAll(): Promise<void>` (non-destructive); `ApplicationHandler.closeAfterReply?(type: number): boolean` in `engine.ts`, honored by the engine's terminal-reply path and forwarded by `composeHandlers`.

- [ ] **Step 1: Write failing tests**: closes only matching handles including a handshaking one, other phones continue, enrollment link terminated without deleting a durable pending record, `closePeer` resolves after teardown, a removal reply is delivered before the close through the real manager and composed handler, close-after-reply sends the reply then closes, `composeHandlers` forwards the hook.
- [ ] **Step 2: Run** `cd web-extension && npm test`; expected FAIL.
- [ ] **Step 3: Implement** by reusing the existing terminate path; add the new test file to `package.json`.
- [ ] **Step 4: Run** `npm test`; expected PASS.
- [ ] **Step 5: Commit** `feat(web-extension): terminate sessions by peer and close after reply`

## Task 9: Chrome removal handler, requester owner API, deadline

**Files:**
- Create: `web-extension/src/session/removal.ts`
- Modify: `web-extension/src/session/engine.ts` (removal-pending guard), `web-extension/src/connector/sessions.ts` (owner API, timer via `SessionTimers`)
- Test: `web-extension/test/session/removal.test.ts` (new)

**Interfaces:**
- Consumes: Task 8 `closeAfterReply`.
- Produces: `interface RemovalOperations { deleteFenced(peerId: string): Promise<void> }` (same contract as Android: takes the fence, starts the detached finish, then deletes); `createRemovalHandler(ops: RemovalOperations): ApplicationHandler` whose `forSession(context)` binds `context.peerId`; a terminal engine stage `removal` that `SessionManager` maps to settle `requestRemoval`; `type RemovalResult = 'removed' | 'kept-not-connected' | 'kept-no-response'`; `SessionManager.requestRemoval(peerId: string): Promise<RemovalResult>` (on the serialized queue of the peer's full session: sets the engine guard, sends `removal_request`, arms the deadline, settles on the `removal` stage, disconnect or timeout); `export const REMOVAL_RESPONSE_TIMEOUT_MS = 10_000`; engine `setRemovalPending(on: boolean)` guard with the same allowed set as Android.

- [ ] **Step 1: Write failing tests** mirroring Task 4 (responder, store-failure on delete, requester after valid response, lost response, disconnect, deadline at 10 000 ms driven by injected `SessionTimers` on the production `SessionManager`, inbound and outbound guard, unsolicited response, non-full rejection, not-connected result, timer cancelled on response/disconnect/teardown).
- [ ] **Step 2: Run** `cd web-extension && npm test`; expected FAIL.
- [ ] **Step 3: Implement** with a cancellable timer owned by `SessionManager` through its injected `SessionTimers`, run through the serialized session queue (the 15 000 ms engine tick cannot enforce 10 000 ms); add the new test file to `package.json`.
- [ ] **Step 4: Run** `npm test`; expected PASS.
- [ ] **Step 5: Commit** `feat(web-extension): handle mutual peer removal records`

## Task 10: Chrome revocation coordinator, status sink and wiring

**Files:**
- Create: `web-extension/src/connector/revocation.ts`
- Modify: `web-extension/src/connector.ts`
- Test: `web-extension/test/connector/revocation.test.ts`, `web-extension/test/connector/revocation-integration.test.ts` (new)

**Interfaces:**
- Produces: `interface PeerSummary { installationId: string; label: string; fingerprint: string; deviceType: number; state: string; createdAtMillis: number; lastSeenMillis: number | null; connected: boolean }`; `type TrustStatus = 'ok' | 're-pair-required'`; `createRevocationCoordinator(deps: { repo: ChromeTrustRepository; sessions: SessionManager; onStatus?: (s: TrustStatus) => void; historyCleanup?: (peerId: string) => void }): RevocationCoordinator & RemovalOperations` with `peers()`, `revoke(id)`, `removeMutually(id): Promise<RemovalResult>`, `failClosed()`, `reset()`; `deleteFenced` as in Task 5 (fence, launch the failure-safe detached finish: `sessions.closePeer(id)` behind the in-flight step, sweep, `endRevocation`, in a `finally`); `abortPairingStartup(): void` on the connector with its own non-destructive abort signal, distinct from user or context cancellation: in `PairingLink.start`, `receive` and the connector's stale-start branch the abort path calls `terminate()` (release handle and core, keep durable records) while cancellation keeps calling `dispose()`; a pairing whose `start()` (connect or initial send) is still pending installs no link and deletes no record.

- [ ] **Step 1: Write failing tests** mirroring Task 5's coordinator list with Chrome equivalents.
- [ ] **Step 2: Write failing integration tests** (real repository over fake storage, real `SessionManager` and enrollment link, fake transport): invalid identity seen by the reconnect lookup closes unrelated sessions, failing `local.write` closes unrelated sessions, revoke before `addPending`, between persist and commit, between commit and ack, during signed recovery, during a duplicate commit, generic rejection of a revoked peer's reconnect, fail-closed and reset during a pending pairing connect and initial send (no surviving link, no deleted pending record on fail-closed, no late write after reset), reset then re-pair then reconnect through the production `ConnectorApp` (its cached `localInstallationId` is invalidated and reloaded per engine creation), re-pairing after revoke with matching fingerprints, pairing while a record is held refused, hint removed and later hint write rejected, listing without secrets or hints.
- [ ] **Step 3: Run** `cd web-extension && npm test`; expected FAIL.
- [ ] **Step 4: Implement** per design D2/D3/D8 and in `connector.ts` wire `onStoreFailure` to `failClosed`, replace the cached `ConnectorApp.localInstallationId` with a cache that `reset()` refreshes from the repository before it resolves, with new `open()` calls gated on that refresh (the synchronous `deps(handle, peerId)` keeps reading the cache), install the removal handler through `composeHandlers` in production, and use non-destructive `closeAll`/`terminate` for fail-closed and reset; add both test files to `package.json`.
- [ ] **Step 5: Run** `npm test`; expected PASS.
- [ ] **Step 6: Commit** `feat(web-extension): add the peer revocation coordinator`

## Task 11: Chrome conformance

**Files:**
- Modify: `web-extension/test/session/scenarios.test.ts`, the record-codec test for the empty-body removal encodings

- [ ] **Step 1: Make** the two OTP-11 scenarios execute: handle `role: phone` for the removal scenario, treat the response step as observation-only, update inventory expectations; leave `protocol/v1` untouched.
- [ ] **Step 2: Run** `cd web-extension && npm test`; expected PASS with both scenarios executed.
- [ ] **Step 3: Commit** `test(web-extension): execute the OTP-11 removal and revoke scenarios`

## Task 12: Debug-only controls and release guards

**Files:**
- Modify: `android/app/src/debug/java/me/nagaev/veles/debugpairing/PairingDebugScreen.kt` (and its hooks), `android/app/build.gradle.kts` (`releaseHookForbidden`), `web-extension/src/debug/pairing-panel.ts`, `web-extension/test/bundle.test.ts`
- Test: `android/app/src/testDebug/java/me/nagaev/veles/debugpairing/`, `web-extension/test/debug/pairing-panel.test.ts`

- [ ] **Step 1: Write failing tests**: peer list renders; Revoke removes and closes; Mutual remove without a full session reports local-only and deletes nothing; Reset needs confirmation and is endpoint-wide; Clear routes through the coordinator and terminates an open session instead of refusing; the re-pair-required status is shown; release and bundle guard tests list every new string.
- [ ] **Step 2: Run** `cd android && ./gradlew testDebugUnitTest` and `cd web-extension && npm test`; expected FAIL.
- [ ] **Step 3: Implement** the controls in the debug source set and the dynamic-import panel only; add the strings to `releaseHookForbidden` and the FORBIDDEN list.
- [ ] **Step 4: Run** the tests plus `cd android && ./gradlew :app:releaseHookGuard` and `cd web-extension && npm run test:bundle`; expected PASS.
- [ ] **Step 5: Commit** `feat(android): add debug peer management controls` and `feat(web-extension): add debug peer management controls` (two commits).

## Task 13: Verification and docs

**Files:**
- Create/Modify: a short `docs/` note on exercising revoke, mutual removal and reset with the debug controls (observed values only after a run)

- [ ] **Step 1: Run** `python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)` and the checks each selected node's `AGENTS.md` lists: Android `cd android && ./gradlew spotlessCheck detekt lintDebug testDebugUnitTest assembleRelease :app:releaseHookGuard` plus the raw `android.util.Log` guard from `.github/workflows/check-android.yml`; extension `npm run format:check`, `lint`, `typecheck`, `test`, `test:bundle` after `./gradlew rustWasm`; root `./gradlew protocolCheck`; docs `python3 docs/checks/check_agent_files.py --root .` and `python3 -m unittest discover -s docs/checks -p 'test_*.py'`; expected all pass.
- [ ] **Step 2: Run** `openspec validate --all --strict`; expected valid.
- [ ] **Step 3: Commit** `docs: describe exercising peer revocation with the debug controls`
