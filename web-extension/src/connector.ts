// MV3 connector page bootstrap and UI orchestration.
//
// The connector document owns exactly one Native Messaging port (through the
// controller) and presents startup, search, connection, error and shutdown
// state. It answers the launcher ping without opening a port on ping, then
// opens the host and negotiates `hello` on startup.
//
// A transport connect is not success. Every connection the connector opens is
// handed to the session manager with one intended trust-record id and only
// counts (and only marks the UI ready) after the §11.3 authentication gate has
// opened. Trust reads never establish identity; only the session manager's
// stage-bound verifier can write successful-authentication metadata.

import { ConnectorController } from './connector/controller';
import { gateNativePort } from './connector/lifecycle';
import { BootstrapLifecycle, activateGeneration } from './connector/bootstrap-lifecycle';
import {
    synthesizeAuthenticationFailed,
    synthesizeMissingHost,
    synthesizePortClosed,
    synthesizeProtocolViolation,
    type ConnectorFailure,
} from './connector/errors';
import {
    chooseReconnectTarget,
    createChromeTrustHintSource,
    type ReconnectTarget,
    type TrustHintSource,
} from './connector/hints';
import {
    ConnectorOwnership,
    type ConnectorOwnershipApi,
    type OwnershipClaimOutcome,
} from './connector/ownership';
import type { ConnectorClose, ConnectorResult, NativePortLike } from './connector/port';
import type { WireEvent } from './connector/protocol';
import { ReconnectPolicy, type ReconnectAttempt } from './connector/reconnect';
import { SessionManager, type SessionController } from './connector/sessions';
import type { ConnectorSnapshot } from './connector/state';
import {
    buildViewModel,
    type ConnectorPhase,
    type ConnectorViewModel,
    type FailureLike,
    type ReadySummary,
} from './connector/view-model';
import type { ClientEngineDeps } from './session/engine';
import type { DevelopmentProbe } from './debug/probe';
import { ActivationEngine } from './pairing/activation';
import { initPairingCore } from './pairing/core';
import { PairingLink } from './pairing/link';
import { ProofRegistry } from './pairing/proofs';
import { bytes, type TrustedPeer } from './trust/model';
import { normalizeLowS } from './session/ecdsa';
import { IndexedDbIdentityStore } from './trust/identity';
import { ChromeLocalStore, trustLock } from './trust/storage';
import { ChromeTrustRepository, type AuthenticationVerifier } from './trust/repository';

export const CONNECTOR_PING_TYPE = 'veles.connector.ping';
export const CONNECTOR_PONG_TYPE = 'veles.connector.pong';

export interface ConnectorPong {
    type: typeof CONNECTOR_PONG_TYPE;
    tabId: number;
    windowId: number;
}

export interface ConnectorPingSender {
    id?: string;
}

export interface ConnectorCurrentTab {
    id?: number;
    windowId?: number;
}

export type ConnectorPingResponder = (
    message: unknown,
    sender: ConnectorPingSender,
    sendResponse: (response?: unknown) => void,
) => boolean;

export interface ConnectorPingApi {
    runtime: {
        id?: string;
        onMessage: {
            addListener(listener: ConnectorPingResponder): void;
        };
    };
    tabs: {
        getCurrent(): Promise<ConnectorCurrentTab | undefined>;
    };
}

function isConnectorPing(message: unknown): boolean {
    return (
        typeof message === 'object' &&
        message !== null &&
        (message as { type?: unknown }).type === CONNECTOR_PING_TYPE
    );
}

function isLocatableTab(tab: ConnectorCurrentTab | undefined): tab is {
    id: number;
    windowId: number;
} {
    return (
        tab !== undefined &&
        typeof tab.id === 'number' &&
        Number.isInteger(tab.id) &&
        tab.id >= 0 &&
        typeof tab.windowId === 'number' &&
        Number.isInteger(tab.windowId) &&
        tab.windowId >= 0
    );
}

// Only the extension itself may drive the bootstrap responder. Chrome supplies
// sender.id for extension pages; a missing or foreign id is ignored.
function isExtensionSender(sender: ConnectorPingSender, runtimeId: string | undefined): boolean {
    return (
        typeof runtimeId === 'string' &&
        runtimeId.length > 0 &&
        typeof sender.id === 'string' &&
        sender.id === runtimeId
    );
}

export function makeConnectorPingListener(api: ConnectorPingApi): ConnectorPingResponder {
    return (message, sender, sendResponse) => {
        if (!isConnectorPing(message)) return false;
        if (!isExtensionSender(sender, api.runtime.id)) return false;

        void api.tabs.getCurrent().then(
            (tab) => {
                if (!isLocatableTab(tab)) {
                    sendResponse(undefined);
                    return;
                }
                sendResponse({
                    type: CONNECTOR_PONG_TYPE,
                    tabId: tab.id,
                    windowId: tab.windowId,
                });
            },
            () => sendResponse(undefined),
        );
        return true;
    };
}

export function registerConnectorPing(api: ConnectorPingApi): void {
    api.runtime.onMessage.addListener(makeConnectorPingListener(api));
}

// ---------------------------------------------------------------------------
// UI actions and view contract
// ---------------------------------------------------------------------------

export type ConnectorAction =
    | { type: 'search' }
    | { type: 'stop_scan'; handle: string }
    | { type: 'connect'; deviceHandle: string; peerId?: string }
    | { type: 'disconnect'; connectionHandle: string }
    | { type: 'retry' }
    | { type: 'shutdown' };

export interface ConnectorView {
    render(viewModel: ConnectorViewModel): void;
    onAction(listener: (action: ConnectorAction) => void): () => void;
    dispose?(): void;
}

type ReadyEvent = Extract<WireEvent, { type: 'ready' }>;
type ScanStateEvent = Extract<WireEvent, { type: 'scan_state' }>;
type ConnectedEvent = Extract<WireEvent, { type: 'connected' }>;
type ShutdownCompleteEvent = Extract<WireEvent, { type: 'shutdown_complete' }>;

export interface ConnectorControllerLike extends SessionController {
    open(): void;
    hello(): Promise<ConnectorResult<ReadyEvent>>;
    startScan(): Promise<ConnectorResult<ScanStateEvent>>;
    stopScan(scanHandle: string): Promise<ConnectorResult<ScanStateEvent>>;
    connect(
        target: { deviceHandle: string } | { platformHint: string },
    ): Promise<ConnectorResult<ConnectedEvent>>;
    shutdown(): Promise<ConnectorResult<ShutdownCompleteEvent>>;
    close(intent?: 'user' | 'shutdown' | 'protocol'): Promise<void>;
    onFailure(listener: (failure: ConnectorFailure) => void): () => void;
    onWireError(listener: (event: Extract<WireEvent, { type: 'error' }>) => void): () => void;
}

/**
 * The session wiring the connector needs. The repository is created here so its
 * verifier can delegate to the manager's stage-bound verifier.
 */
export interface ConnectorSessionOptions {
    createRepository(
        verifier: AuthenticationVerifier,
        proofs: ProofRegistry,
    ): ChromeTrustRepository;
    createEngineDeps(
        handle: string,
        peerId: string,
        localInstallationId: Uint8Array,
        repository: ChromeTrustRepository,
        proofs: ProofRegistry,
    ): ClientEngineDeps;
}

export interface ConnectorAppDeps {
    controller: ConnectorControllerLike;
    view: ConnectorView;
    trustSource?: TrustHintSource;
    session?: ConnectorSessionOptions;
    setTimeout?: (fn: () => void, ms: number) => unknown;
    clearTimeout?: (handle: unknown) => void;
}

interface TrackedConnection {
    status: string;
    deviceHandle?: string;
    platformHint?: string;
}

interface PeerTarget {
    peerId: string;
    hint: string;
}

type RecoveryScope = { kind: 'host' } | { kind: 'associated'; peer: PeerTarget } | { kind: 'link' };

interface Readiness {
    epoch: number;
    promise: Promise<boolean>;
}

const UI_HISTORY_NOTE = 'Latest 50 entries';

function readySummary(event: ReadyEvent): ReadySummary {
    return {
        apiVersion: event.api_version,
        bridgeVersion: event.bridge_version,
        platform: event.platform,
        capabilities: [...event.capabilities],
    };
}

export class ConnectorApp {
    private readonly controller: ConnectorControllerLike;
    private readonly view: ConnectorView;
    private readonly trustSource: TrustHintSource;
    private readonly repository: ChromeTrustRepository | null;
    private readonly proofs: ProofRegistry;
    readonly sessions: SessionManager | null;
    private readonly reconnect: ReconnectPolicy;
    private readonly unsubscribers: (() => void)[] = [];
    private readonly userDisconnectHandles = new Set<string>();
    private readonly sessionFailedHandles = new Set<string>();
    private readonly connectionPeerTargets = new Map<string, PeerTarget>();
    private readonly suppressedPeerIds = new Set<string>();
    private readonly recoveryEpochs = new Map<string, number>();
    private trackedConnections = new Map<string, TrackedConnection>();
    private devices: readonly { handle: string; label: string }[] = [];
    private pairingLink: PairingLink | null = null;
    private pairingEnded = false;
    // Invalidates an enrollment startup still awaiting its transport. A cancel,
    // a tab/host teardown and a replacement start each advance this generation,
    // so a startup that resolves later disposes its link instead of installing a
    // link nobody owns.
    private pairingAttempt = 0;
    // An enrollment connection is not a session: its disappearance must never
    // schedule reconnect recovery.
    private pairingHandles = new Set<string>();
    private userDisconnectSeen = false;
    private localInstallationId: Uint8Array | null = null;

    private phase: ConnectorPhase = 'starting';
    private ready: ReadySummary | null = null;
    private failure: FailureLike | null = null;
    private macPermissionDenied = false;
    private shuttingDown = false;
    private disposed = false;
    private started = false;
    private session = 0;
    private portGeneration = 0;
    private ignoreUserClose = 0;
    private readiness: Readiness | null = null;

    constructor(deps: ConnectorAppDeps) {
        this.controller = deps.controller;
        this.view = deps.view;
        const session = deps.session;
        this.proofs = new ProofRegistry();
        if (session === undefined) {
            this.repository = null;
            this.sessions = null;
            this.trustSource = deps.trustSource ?? createChromeTrustHintSource();
        } else {
            // One repository per connector lifetime. Its verifier forwards to the
            // manager's stage-bound verifier for `phone_active`/`authenticated`, and to
            // the session-bound proof registry for the activation `complete` stage; until
            // the manager exists, except for a pairing proof, every authentication is
            // refused.
            const holder: { verify: AuthenticationVerifier } = { verify: async () => false };
            const proofs = this.proofs;
            const repository = session.createRepository(
                (peer, stage, proof) =>
                    stage === 'complete' && proofs.consume(peer.installationId, stage, proof)
                        ? Promise.resolve(true)
                        : holder.verify(peer, stage, proof),
                proofs,
            );
            const timers =
                deps.setTimeout === undefined && deps.clearTimeout === undefined
                    ? undefined
                    : {
                          ...(deps.setTimeout === undefined ? {} : { setTimeout: deps.setTimeout }),
                          ...(deps.clearTimeout === undefined
                              ? {}
                              : { clearTimeout: deps.clearTimeout }),
                      };
            const manager = new SessionManager(
                deps.controller,
                repository,
                (handle, peerId) => {
                    const identity = this.localInstallationId;
                    if (identity === null) throw new Error('installation identity unavailable');
                    return session.createEngineDeps(handle, peerId, identity, repository, proofs);
                },
                () => deps.controller.portGeneration,
                timers,
                (sessionId) => this.proofs.revoke(sessionId),
            );
            holder.verify = manager.verifier;
            this.repository = repository;
            this.sessions = manager;
            this.trustSource = deps.trustSource ?? createChromeTrustHintSource(repository);
        }
        this.reconnect = new ReconnectPolicy({
            setTimeout: deps.setTimeout,
            clearTimeout: deps.clearTimeout,
            onAttempt: (attempt) => this.attemptRecovery(attempt),
            onChange: () => this.render(),
        });
    }

    async start(): Promise<void> {
        if (this.started || this.disposed) return;
        this.started = true;
        this.unsubscribers.push(
            this.controller.onChange((snapshot) => this.handleChange(snapshot)),
        );
        this.unsubscribers.push(
            this.controller.onFailure((failure) => this.handleFailure(failure)),
        );
        this.unsubscribers.push(
            this.controller.onWireError((event) => this.handleWireError(event)),
        );
        this.unsubscribers.push(this.controller.onClose((close) => this.handleClose(close)));
        this.unsubscribers.push(
            this.view.onAction((action) => {
                void this.handleAction(action);
            }),
        );
        // The installation identity is loaded lazily on the first connect (and
        // before every recovery attempt), so startup never creates trust
        // material for a user who never connects.
        await this.restartHost(false);
    }

    private async loadInstallationIdentity(): Promise<void> {
        const repository = this.repository;
        if (repository === null || this.localInstallationId !== null) return;
        try {
            const identity = await repository.identity();
            this.localInstallationId = bytes(identity.installationId, 16);
        } catch {
            // Trust storage unavailable: every connect fails closed with the
            // generic authentication failure instead of signing with an
            // unknown identity.
        }
    }

    async handleAction(action: ConnectorAction): Promise<void> {
        if (this.disposed) return;
        switch (action.type) {
            case 'search':
                await this.runScan();
                return;
            case 'stop_scan':
                await this.runStopScan(action.handle);
                return;
            case 'connect':
                await this.runConnect(action.deviceHandle, action.peerId);
                return;
            case 'disconnect':
                await this.runDisconnect(action.connectionHandle);
                return;
            case 'retry':
                await this.restartHost(true);
                return;
            case 'shutdown':
                await this.runShutdown();
                return;
        }
    }

    /**
     * Starts the OTP-10 enrollment flow over the BLE link. The flow is not a
     * `SessionManager` session: it owns its own `PairingLink`, and the connector
     * tab teardown (`dispose`) is what closes the core handle and deletes this
     * enrollment's pending record (OTP2-STA-010d).
     */
    async startPairing(deviceHandle: string, code: string): Promise<boolean> {
        if (this.disposed || this.shuttingDown) return false;
        const repository = this.repository;
        if (repository === null) {
            this.applyFailure(synthesizeAuthenticationFailed());
            return false;
        }
        // Own this attempt before the teardown await: a cancel, teardown or
        // replacement that runs during it must make this startup stale.
        const attempt = this.nextPairingAttempt();
        await this.teardownPairing();
        if (!this.isPairingAttemptLive(attempt)) {
            if (attempt === this.pairingAttempt) this.pairingEnded = true;
            return false;
        }
        this.pairingEnded = false;
        try {
            const link = await PairingLink.start({
                controller: this.controller,
                repo: repository,
                proofs: this.proofs,
                code,
                deviceHandle,
                // Every await inside `start` re-checks this: a cancel, teardown or
                // replacement during startup must stop it before it processes an
                // offer or releases `pB`.
                isCancelled: () => !this.isPairingAttemptLive(attempt),
                // Register the handle from the connected event, before the first
                // send: a link lost during start must never look like a session.
                onConnected: (connectionHandle) => {
                    this.pairingHandles.add(connectionHandle);
                },
                onEnded: () => {
                    // A superseded attempt must not end the current one.
                    if (attempt === this.pairingAttempt) this.pairingEnded = true;
                },
            });
            if (!this.isPairingAttemptLive(attempt)) {
                // Cancelled, torn down or replaced while starting: never install
                // a link for an attempt the connector no longer owns.
                if (attempt === this.pairingAttempt) this.pairingEnded = true;
                await link.dispose();
                // A disconnect that already left the controller snapshot was
                // consumed by `handleChange`; otherwise keep the registration so a
                // later removal of an ambiguous link never schedules recovery.
                if (
                    !this.controller
                        .snapshot()
                        .connections.some(
                            (connection) => connection.handle === link.connectionHandle,
                        )
                ) {
                    this.pairingHandles.delete(link.connectionHandle);
                }
                return false;
            }
            this.pairingLink = link;
            return true;
        } catch {
            if (this.isPairingAttemptLive(attempt)) {
                this.pairingEnded = true;
                this.applyFailure(synthesizeAuthenticationFailed());
            } else if (attempt === this.pairingAttempt) {
                this.pairingEnded = true;
            }
            return false;
        }
    }

    /** Starts a new enrollment generation, invalidating every pending startup. */
    private nextPairingAttempt(): number {
        this.pairingAttempt += 1;
        return this.pairingAttempt;
    }

    private isPairingAttemptLive(attempt: number): boolean {
        return attempt === this.pairingAttempt && !this.disposed && !this.shuttingDown;
    }

    /** True once the current enrollment link stopped (success, failure or cancel). */
    get pairingFinished(): boolean {
        return this.pairingEnded;
    }

    /** The debug panel's state readout; never contains the six-digit code. */
    get pairingState(): { phase: string; finished: boolean; fingerprint?: string } {
        if (this.pairingLink === null) return { phase: 'idle', finished: this.pairingEnded };
        const machine = this.pairingLink.machine;
        const fingerprint = machine.fingerprint();
        return {
            phase: machine.state,
            finished: this.pairingEnded,
            ...(fingerprint === undefined ? {} : { fingerprint }),
        };
    }

    /** Discovered phones for the debug panel; the extension-owned search fills this. */
    get pairingDevices(): readonly { handle: string; label: string }[] {
        return this.devices;
    }

    /** Every trust record, for the debug Clear control (design D5). */
    async pairingPeers(): Promise<readonly TrustedPeer[]> {
        return this.repository === null ? [] : this.repository.peers();
    }

    /** Clear a stranded or injected record with `repository.revoke`; no revocation protocol. */
    async clearPairingPeer(peerId: string): Promise<void> {
        const repository = this.repository;
        if (repository === null) throw new Error('trust store unavailable');
        await repository.revoke(peerId);
    }

    /** User cancel of the current enrollment attempt; false when its cleanup failed. */
    async cancelPairing(): Promise<boolean> {
        // Cancel also invalidates a startup that has not installed its link yet.
        this.nextPairingAttempt();
        const link = this.pairingLink;
        this.pairingLink = null;
        this.pairingEnded = true;
        return link === null ? true : link.cancel();
    }

    private teardownPairing(): Promise<void> {
        const link = this.pairingLink;
        this.pairingLink = null;
        this.pairingEnded = true;
        return link === null ? Promise.resolve() : link.dispose();
    }

    private abandonPairing(): Promise<void> {
        // A port death also invalidates a startup still awaiting its transport.
        this.nextPairingAttempt();
        // Keep the link: a later real tab teardown must still delete the pending
        // record this dead link could not remove.
        const link = this.pairingLink;
        this.pairingEnded = true;
        return link === null ? Promise.resolve() : link.abandon();
    }

    dispose(): void {
        if (this.disposed) return;
        this.disposed = true;
        this.nextPairingAttempt();
        this.session += 1;
        this.reconnect.dispose();
        void this.teardownPairing();
        for (const unsubscribe of this.unsubscribers) unsubscribe();
        this.unsubscribers.length = 0;
        this.view.dispose?.();
        if (this.controller.isOpen()) void this.controller.close('user');
    }

    private async restartHost(resetPolicy: boolean): Promise<void> {
        const token = ++this.session;
        if (resetPolicy) this.reconnect.reset();
        this.failure = null;
        this.ready = null;
        this.phase = 'connecting_host';
        this.shuttingDown = false;
        this.render();

        if (this.controller.isOpen()) {
            this.ignoreUserClose += 1;
            await this.controller.close('user');
            if (token !== this.session || this.disposed) return;
        }

        const epoch = this.portGeneration;
        const ready = await this.ensureHostReady(epoch);
        if (token !== this.session || this.disposed) return;
        if (!ready) return;

        // A same-Mac Retry is a real access probe: keep the permission
        // guidance sticky, but perform a bounded scan so a granted permission
        // can clear it without requiring a host restart cycle.
        if (resetPolicy && this.macPermissionDenied && this.currentPlatform() === 'macos') {
            await this.runScan();
        }
    }

    private currentPlatform(): 'windows' | 'macos' | null {
        return this.ready?.platform ?? null;
    }

    private ensureHostReady(epoch: number): Promise<boolean> {
        if (this.ready !== null) return Promise.resolve(true);
        if (this.readiness?.epoch === epoch) return this.readiness.promise;

        const readiness: Readiness = { epoch, promise: this.negotiateHost(epoch) };
        this.readiness = readiness;
        const clear = (): void => {
            if (this.readiness === readiness) this.readiness = null;
        };
        void readiness.promise.then(clear, clear);
        return readiness.promise;
    }

    private async negotiateHost(epoch: number): Promise<boolean> {
        if (this.disposed || epoch !== this.portGeneration) return false;
        if (!this.controller.isOpen()) this.controller.open();
        const result = await this.controller.hello();
        if (this.disposed) return false;
        if (!result.ok) {
            // Missing-host and incompatible-API are the outcome of this exact
            // hello even though the host/protocol close already advanced the
            // port generation. Keep their actionable guidance; other stale
            // port-closure failures were already applied by handleClose.
            const terminalHelloFailure =
                (result.source === 'local' &&
                    (result.error.code === 'missing_host' ||
                        result.error.code === 'incompatible_api')) ||
                (result.source === 'wire' && result.error.code === 'incompatible_api');
            if (terminalHelloFailure) {
                this.applyFailure(result.error);
                return false;
            }
            if (epoch === this.portGeneration) this.applyFailure(result.error);
            return false;
        }
        if (epoch !== this.portGeneration) return false;
        this.acceptReady(result.event);
        return true;
    }

    private isAttemptLive(attempt: ReconnectAttempt, epoch: number, token: number): boolean {
        return (
            !this.disposed &&
            !this.shuttingDown &&
            epoch === this.portGeneration &&
            token === this.session &&
            this.reconnect.isCurrent(attempt)
        );
    }

    private async attemptRecovery(attempt: ReconnectAttempt): Promise<boolean> {
        if (this.disposed || this.shuttingDown || this.reconnect.suppressed) return false;
        const hostAttempt = attempt.key === 'host';
        // A link-recovery cycle belongs to the port that carried that link.
        // It must never open a replacement host on its own; the host cycle
        // owns replacement after an unexpected port loss. A cycle scheduled
        // before that port generation changed is invalid even if a timer fires
        // after the replacement is open.
        if (!hostAttempt) {
            const scheduledEpoch = this.recoveryEpochs.get(attempt.key);
            if (
                scheduledEpoch === undefined ||
                scheduledEpoch !== this.portGeneration ||
                !this.controller.isOpen()
            ) {
                return false;
            }
        }

        const epoch = this.portGeneration;
        const token = this.session;
        if (!this.isAttemptLive(attempt, epoch, token)) return false;
        if (!(await this.ensureHostReady(epoch))) return false;
        if (!this.isAttemptLive(attempt, epoch, token)) return false;

        if (attempt.target.kind === 'hint') {
            await this.loadInstallationIdentity();
            // Cancellation or port replacement during the identity read must be
            // observed before any transport connect is issued.
            if (!this.isAttemptLive(attempt, epoch, token)) return false;
            // A host restart is always negotiated first, then the hint is used
            // only as an opaque OS search candidate. It never authenticates:
            // the attempt succeeds, and renews the peer's allowance, only after
            // the authentication gate opens and the class is fixed.
            const target = attempt.target;
            const result = await this.controller.connect({ platformHint: target.hint });
            if (!this.isAttemptLive(attempt, epoch, token)) {
                if (result.ok) {
                    // A successful transport connect that no longer belongs to
                    // this attempt must not outlive it unowned.
                    await this.controller
                        .disconnect(result.event.connection_handle)
                        .catch(() => undefined);
                }
                return false;
            }
            if (!result.ok) {
                this.applyFailure(result.error);
                return false;
            }
            const authenticated = await this.authenticateConnection(
                result.event,
                target.peerId,
                token,
                target.hint,
            );
            if (!this.isAttemptLive(attempt, epoch, token)) return false;
            // A failed authentication is a consumed attempt: the policy arms
            // the next try or stops at its bound.
            if (!authenticated) return false;
            this.recoveryEpochs.delete(attempt.key);
            // A confirmed reconnect renews this peer's future allowance even
            // if an obsolete cycle from an older port generation still
            // existed; no user-disconnect suppression is created.
            this.reconnect.discard(`peer:${target.peerId}`);
            this.recoveryEpochs.delete(`peer:${target.peerId}`);
            this.markReady();
            return true;
        }

        const snapshot = this.controller.snapshot();
        if (
            snapshot.scans.some(
                (scan) =>
                    scan.status === 'starting' ||
                    scan.status === 'started' ||
                    scan.status === 'stopping',
            )
        ) {
            this.markReady();
            return true;
        }

        const result = await this.controller.startScan();
        if (!this.isAttemptLive(attempt, epoch, token)) return false;
        if (!result.ok) {
            this.applyFailure(result.error);
            return false;
        }
        this.recoveryEpochs.delete(attempt.key);
        this.markReady();
        return true;
    }

    private async scheduleAutomaticRecovery(key: string, scope: RecoveryScope): Promise<void> {
        if (this.disposed || this.shuttingDown || this.reconnect.suppressed) return;
        const epoch = this.portGeneration;
        const token = this.session;
        let target: ReconnectTarget;
        try {
            const peers = await this.trustSource.activePeers();
            if (scope.kind === 'associated') {
                const match = peers.find(
                    (peer) => peer.peerId === scope.peer.peerId && peer.hint === scope.peer.hint,
                );
                target =
                    match === undefined || this.suppressedPeerIds.has(match.peerId)
                        ? { kind: 'search', reason: 'none' }
                        : { kind: 'hint', peerId: match.peerId, hint: match.hint };
            } else if (scope.kind === 'host') {
                const chosen = this.userDisconnectSeen
                    ? ({ kind: 'search', reason: 'none' } as const)
                    : chooseReconnectTarget(peers);
                target =
                    chosen.kind === 'hint' && this.suppressedPeerIds.has(chosen.peerId)
                        ? { kind: 'search', reason: 'none' }
                        : chosen;
            } else {
                // A link with no defensible peer association must not select
                // an unrelated active peer's hint. Fall back to search.
                target = { kind: 'search', reason: 'none' };
            }
        } catch {
            // A denied/unavailable trust read is not authentication. Recover
            // through explicit search instead of treating it as a hint.
            target =
                scope.kind === 'host'
                    ? { kind: 'search', reason: 'invalid' }
                    : { kind: 'search', reason: 'none' };
        }
        if (
            this.disposed ||
            this.shuttingDown ||
            epoch !== this.portGeneration ||
            token !== this.session ||
            this.reconnect.suppressed
        ) {
            return;
        }
        if (this.reconnect.schedule(key, target)) {
            this.recoveryEpochs.set(key, epoch);
        }
        this.render();
    }

    private markReady(): void {
        this.failure = null;
        this.phase = 'ready';
        this.macPermissionDenied = false;
        this.render();
    }

    private canRunBleAction(): boolean {
        if (this.disposed || !this.controller.isOpen()) return false;
        if (this.phase === 'ready') return true;
        return this.phase === 'failed' && this.failure?.recoverability === 'retryable';
    }

    private async runScan(): Promise<void> {
        if (!this.canRunBleAction()) return;
        const token = this.session;
        const result = await this.controller.startScan();
        if (token !== this.session || this.disposed) return;
        if (result.ok) {
            this.markReady();
            return;
        }
        this.applyFailure(result.error);
        this.render();
    }

    private async runStopScan(handle: string): Promise<void> {
        if (!this.canRunBleAction()) return;
        const token = this.session;
        const result = await this.controller.stopScan(handle);
        if (token !== this.session || this.disposed) return;
        if (result.ok) {
            this.markReady();
            return;
        }
        this.applyFailure(result.error);
        this.render();
    }

    private async runConnect(deviceHandle: string, peerId?: string): Promise<void> {
        if (!this.canRunBleAction()) return;
        const token = this.session;
        await this.loadInstallationIdentity();
        if (token !== this.session || this.disposed) return;
        const intended = await this.intendedPeer(peerId);
        if (token !== this.session || this.disposed) return;
        if (intended === undefined) {
            // Zero or several active-family phones and no explicit id: fail
            // closed before any transport connect.
            this.applyFailure(synthesizeAuthenticationFailed());
            return;
        }
        const result = await this.controller.connect({ deviceHandle });
        if (token !== this.session || this.disposed) {
            // The connect succeeded for an attempt this connector no longer
            // owns; close it instead of leaving an unauthenticated link open.
            if (result.ok) {
                void this.controller
                    .disconnect(result.event.connection_handle)
                    .catch(() => undefined);
            }
            return;
        }
        if (!result.ok) {
            this.applyFailure(result.error);
            return;
        }
        if (!(await this.authenticateConnection(result.event, intended, token))) return;
        this.markReady();
    }

    private async intendedPeer(explicit?: string): Promise<string | undefined> {
        if (explicit !== undefined && explicit.length > 0) return explicit;
        const repository = this.repository;
        if (repository === null) return undefined;
        try {
            return SessionManager.intendedPeer(await repository.peers());
        } catch {
            // A denied/unavailable trust read never selects a peer.
            return undefined;
        }
    }

    /**
     * Runs the authenticated session for one transport connect. Only an open
     * gate fixes the connection's peer target; a transport success or a failed
     * authentication leaves it unset and reports the generic failure.
     */
    private async authenticateConnection(
        event: ConnectedEvent,
        peerId: string,
        token: number,
        fallbackHint?: string,
    ): Promise<boolean> {
        const handle = event.connection_handle;
        const sessions = this.sessions;
        const opened =
            sessions === null ? { result: 'failed' as const } : await sessions.open(handle, peerId);
        if (token !== this.session || this.disposed) return false;
        if (opened.result !== 'authenticated') {
            this.sessionFailedHandles.add(handle);
            this.applyFailure(synthesizeAuthenticationFailed());
            return false;
        }
        this.connectionPeerTargets.set(handle, {
            peerId: opened.peerId,
            hint: event.platform_hint ?? fallbackHint ?? '',
        });
        this.sessionFailedHandles.delete(handle);
        return true;
    }

    private async runDisconnect(connectionHandle: string): Promise<void> {
        if (this.disposed) return;
        const token = this.session;
        const peer = this.connectionPeerTargets.get(connectionHandle);
        if (peer !== undefined) this.suppressedPeerIds.add(peer.peerId);
        this.userDisconnectSeen = true;
        this.userDisconnectHandles.add(connectionHandle);
        this.reconnect.cancel(
            peer === undefined ? `connection:${connectionHandle}` : `peer:${peer.peerId}`,
            'user_disconnect',
        );
        this.recoveryEpochs.delete(
            peer === undefined ? `connection:${connectionHandle}` : `peer:${peer.peerId}`,
        );
        if (peer !== undefined) {
            this.reconnect.cancel(`connection:${connectionHandle}`, 'user_disconnect');
            this.recoveryEpochs.delete(`connection:${connectionHandle}`);
        }

        const result = await this.controller.disconnect(connectionHandle);
        if (token !== this.session || this.disposed) return;
        if (!result.ok) {
            if (
                !this.controller.snapshot().connections.some((c) => c.handle === connectionHandle)
            ) {
                this.userDisconnectHandles.delete(connectionHandle);
                this.connectionPeerTargets.delete(connectionHandle);
            }
            this.applyFailure(result.error);
            this.render();
            return;
        }
        this.connectionPeerTargets.delete(connectionHandle);
        this.failure = null;
        this.phase = 'ready';
        this.render();
    }

    private async runShutdown(): Promise<void> {
        if (this.disposed || this.shuttingDown) return;
        const token = ++this.session;
        this.shuttingDown = true;
        this.reconnect.suppress('shutdown');
        this.render();

        const result = await this.controller.shutdown();
        if (token !== this.session || this.disposed) return;
        if (!result.ok) {
            this.applyFailure(result.error);
            return;
        }
        this.phase = 'shut_down';
        this.ready = null;
        this.failure = null;
        this.render();
    }

    private acceptReady(event: ReadyEvent): void {
        this.ready = readySummary(event);
        this.failure = null;
        this.phase = 'ready';
        this.shuttingDown = false;
        if (event.platform !== 'macos') this.macPermissionDenied = false;
        this.render();
    }

    private applyFailure(failure: FailureLike, phase: ConnectorPhase = 'failed'): void {
        this.failure = failure;
        this.phase = phase;
        if (failure.code === 'missing_host') this.reconnect.suppress('missing_host');
        if (failure.code === 'incompatible_api') this.reconnect.suppress('incompatible_api');
        if (
            this.ready?.platform === 'macos' &&
            (failure.stage === 'access_denied' || failure.stage === 'permission_pending')
        ) {
            this.macPermissionDenied = true;
        }
        this.render();
    }

    private handleFailure(failure: ConnectorFailure): void {
        if (this.disposed || this.shuttingDown) return;
        this.applyFailure(failure);
    }

    private handleWireError(failure: Extract<WireEvent, { type: 'error' }>): void {
        if (this.disposed || this.shuttingDown) return;
        // Unsolicited host failures are real, actionable bridge outcomes. They
        // are never correlated to a request here and never close a healthy
        // port or its other connections.
        this.applyFailure(failure);
    }

    private handleClose(close: ConnectorClose): void {
        if (this.disposed) return;
        this.devices = [];
        // A native-host port death is not connector-context loss: OTP2-STA-010d
        // deletes a genuinely-pending client record only when the document/tab
        // ends. Close the core handle and keep the durable pending record so a
        // later recovery session can resume; the real tab teardown still deletes it.
        void this.abandonPairing();
        // Every port death/replacement invalidates pending readiness and
        // recovery reads from the previous generation, including an ignored
        // user close used to restart the host.
        this.portGeneration += 1;
        this.readiness = null;
        this.connectionPeerTargets.clear();
        this.sessionFailedHandles.clear();
        this.pairingHandles.clear();
        for (const key of this.reconnect.keys()) {
            if (key === 'host') continue;
            this.reconnect.discard(key);
            this.recoveryEpochs.delete(key);
        }
        if (close.reason === 'user' && this.ignoreUserClose > 0) {
            this.ignoreUserClose -= 1;
            return;
        }
        if (close.reason === 'user') {
            this.reconnect.suppress('intentional_close');
            this.shuttingDown = false;
            this.phase = 'disconnected';
            this.ready = null;
            this.failure = null;
            this.trackedConnections = new Map();
            this.render();
            return;
        }
        if (close.reason === 'shutdown') {
            this.reconnect.suppress('shutdown');
            this.shuttingDown = false;
            this.phase = 'shut_down';
            this.ready = null;
            this.failure = null;
            this.trackedConnections = new Map();
            this.render();
            return;
        }
        if (close.reason === 'missing_host') {
            this.ready = null;
            this.trackedConnections = new Map();
            this.applyFailure(synthesizeMissingHost());
            return;
        }
        if (close.reason === 'protocol') {
            this.reconnect.suppress('intentional_close');
            this.ready = null;
            this.trackedConnections = new Map();
            if (this.failure === null) {
                this.applyFailure(synthesizeProtocolViolation('protocol_close'));
            } else {
                this.phase = 'failed';
                this.render();
            }
            return;
        }

        // Unexpected port loss. This is the only automatic host-recovery path.
        this.ready = null;
        this.trackedConnections = new Map();
        this.applyFailure(synthesizePortClosed('hello'), 'disconnected');
        if (!this.shuttingDown) void this.scheduleAutomaticRecovery('host', { kind: 'host' });
    }

    private handleChange(snapshot: ConnectorSnapshot): void {
        if (this.disposed) return;
        this.devices = snapshot.devices.map((device) => ({
            handle: device.handle,
            label: device.label,
        }));
        const next = new Map(
            snapshot.connections.map((connection) => [
                connection.handle,
                {
                    status: connection.status,
                    ...(connection.deviceHandle === undefined
                        ? {}
                        : { deviceHandle: connection.deviceHandle }),
                    ...(connection.platformHint === undefined
                        ? {}
                        : { platformHint: connection.platformHint }),
                } satisfies TrackedConnection,
            ]),
        );

        // Recovery is keyed to negotiated transport readiness, not the
        // presentation phase: an unrelated retryable scan/connect failure may
        // show an error banner while the host and other links stay valid.
        if (this.ready !== null && this.controller.isOpen() && !this.shuttingDown) {
            for (const [handle, previous] of this.trackedConnections) {
                if (next.has(handle)) continue;
                if (this.pairingHandles.delete(handle)) continue;
                if (this.userDisconnectHandles.delete(handle)) continue;

                const peer = this.connectionPeerTargets.get(handle);
                if (peer !== undefined && this.suppressedPeerIds.has(peer.peerId)) continue;
                if (this.sessionFailedHandles.delete(handle)) continue;
                const key = peer === undefined ? `connection:${handle}` : `peer:${peer.peerId}`;
                if (this.reconnect.isSuppressed(key)) continue;
                if (previous.status !== 'connected' && previous.status !== 'ambiguous') continue;
                void this.scheduleAutomaticRecovery(
                    key,
                    peer === undefined ? { kind: 'link' } : { kind: 'associated', peer },
                );
            }
        }
        for (const handle of [...this.userDisconnectHandles]) {
            if (!next.has(handle)) this.userDisconnectHandles.delete(handle);
        }
        for (const handle of [...this.sessionFailedHandles]) {
            if (!next.has(handle)) this.sessionFailedHandles.delete(handle);
        }
        for (const handle of [...this.connectionPeerTargets.keys()]) {
            if (!next.has(handle)) this.connectionPeerTargets.delete(handle);
        }
        this.trackedConnections = next;
        this.render();
    }

    private render(): void {
        if (this.disposed) return;
        this.view.render(
            buildViewModel(this.controller.snapshot(), {
                phase: this.phase,
                ready: this.ready,
                failure: this.failure,
                macPermissionDenied: this.macPermissionDenied,
                shuttingDown: this.shuttingDown,
                reconnect: {
                    attempts: this.reconnect.attempts,
                    maxAttempts: this.reconnect.maxAttempts,
                    scheduled: this.reconnect.scheduled,
                    inFlight: this.reconnect.inFlight,
                    suppressed: this.reconnect.suppressed,
                },
            }),
        );
    }
}

// ---------------------------------------------------------------------------
// Production DOM view
// ---------------------------------------------------------------------------

function handleFocusToken(action: string, handle: string | undefined): FocusToken {
    return handle === undefined ? { action } : { action, handle };
}

function requireElement(document: Document, id: string): HTMLElement {
    const element = document.getElementById(id);
    if (element === null) throw new Error(`connector page is missing #${id}`);
    return element;
}

function actionButton(
    document: Document,
    action: ConnectorAction['type'],
    label: string,
    handle?: string,
): HTMLButtonElement {
    const button = document.createElement('button');
    button.type = 'button';
    button.textContent = label;
    button.dataset.action = action;
    if (handle !== undefined) {
        button.dataset.handle = handle;
        button.setAttribute('aria-label', `${label} ${handle}`);
    }
    return button;
}

interface FocusToken {
    action: string;
    handle?: string;
}

interface ConnectorElements {
    status: HTMLElement;
    hostStatus: HTMLElement;
    hostGuidance: HTMLElement;
    search: HTMLButtonElement;
    stopScan: HTMLButtonElement;
    scanStatus: HTMLElement;
    deviceList: HTMLElement;
    connectionList: HTMLElement;
    errorBanner: HTMLElement;
    reconnect: HTMLButtonElement;
    reconnectStatus: HTMLElement;
    shutdown: HTMLButtonElement;
    diagnostics: HTMLElement;
    diagnosticsList: HTMLElement;
}

export class DocumentConnectorView implements ConnectorView {
    private readonly elements: ConnectorElements;
    private actionListener: ((action: ConnectorAction) => void) | null = null;
    private readonly clickHandler = (event: Event): void => {
        const target = event.target;
        if (!(target instanceof Element)) return;
        const button = target.closest('button[data-action]');
        if (!(button instanceof HTMLButtonElement)) return;
        const action = button.dataset.action;
        const handle = button.dataset.handle;
        if (action === 'search' || action === 'retry' || action === 'shutdown') {
            this.emit({ type: action });
            return;
        }
        if (handle === undefined) return;
        if (action === 'stop_scan') this.emit({ type: 'stop_scan', handle });
        if (action === 'connect') this.emit({ type: 'connect', deviceHandle: handle });
        if (action === 'disconnect') this.emit({ type: 'disconnect', connectionHandle: handle });
    };

    constructor(private readonly document: Document) {
        this.elements = {
            status: requireElement(document, 'status'),
            hostStatus: requireElement(document, 'host-status'),
            hostGuidance: requireElement(document, 'host-guidance'),
            search: requireElement(document, 'search-button') as HTMLButtonElement,
            stopScan: requireElement(document, 'stop-scan-button') as HTMLButtonElement,
            scanStatus: requireElement(document, 'scan-status'),
            deviceList: requireElement(document, 'device-list'),
            connectionList: requireElement(document, 'connection-list'),
            errorBanner: requireElement(document, 'error-banner'),
            reconnect: requireElement(document, 'reconnect-button') as HTMLButtonElement,
            reconnectStatus: requireElement(document, 'reconnect-status'),
            shutdown: requireElement(document, 'shutdown-button') as HTMLButtonElement,
            diagnostics: requireElement(document, 'diagnostics'),
            diagnosticsList: requireElement(document, 'diagnostics-list'),
        };
        this.elements.search.dataset.action = 'search';
        this.elements.stopScan.dataset.action = 'stop_scan';
        this.elements.reconnect.dataset.action = 'retry';
        this.elements.shutdown.dataset.action = 'shutdown';
        document.addEventListener('click', this.clickHandler);
    }

    onAction(listener: (action: ConnectorAction) => void): () => void {
        this.actionListener = listener;
        return () => {
            if (this.actionListener === listener) this.actionListener = null;
        };
    }

    render(view: ConnectorViewModel): void {
        this.elements.status.textContent = view.statusText;
        this.elements.status.dataset.phase = view.phase;
        this.elements.hostStatus.textContent = view.hostStatusText;
        this.elements.hostGuidance.textContent = view.guidance?.message ?? '';
        this.elements.hostGuidance.hidden = view.guidance === null;
        this.elements.scanStatus.textContent = view.scanStatusText;
        this.elements.reconnectStatus.textContent = view.reconnectStatusText ?? '';
        this.elements.reconnectStatus.hidden = view.reconnectStatusText === null;

        this.elements.search.textContent = view.controls.search.label;
        this.elements.search.disabled = !view.controls.search.enabled;
        this.elements.stopScan.textContent = view.controls.stopScan.label;
        this.elements.stopScan.hidden = !view.controls.stopScan.visible;
        this.elements.stopScan.disabled = !view.controls.stopScan.enabled;

        this.elements.reconnect.textContent = view.controls.reconnect.label;
        this.elements.reconnect.disabled = !view.controls.reconnect.enabled;
        this.elements.shutdown.textContent = view.controls.shutdown.label;
        this.elements.shutdown.disabled = !view.controls.shutdown.enabled;

        this.elements.errorBanner.hidden = view.error === null;
        this.elements.errorBanner.textContent = view.error?.message ?? '';

        const focus = this.captureListFocus();
        this.renderDevices(view);
        this.renderConnections(view);
        this.renderDiagnostics(view);
        this.restoreListFocus(focus);
    }

    dispose(): void {
        this.document.removeEventListener('click', this.clickHandler);
        this.actionListener = null;
    }

    private emit(action: ConnectorAction): void {
        this.actionListener?.(action);
    }

    private captureListFocus(): FocusToken | null {
        const active = this.document.activeElement;
        if (!(active instanceof HTMLButtonElement)) return null;
        if (!active.closest('#device-list, #connection-list')) return null;
        const action = active.dataset.action;
        if (action === undefined) return null;
        return handleFocusToken(action, active.dataset.handle);
    }

    private restoreListFocus(token: FocusToken | null): void {
        if (token === null) return;
        for (const list of [this.elements.deviceList, this.elements.connectionList]) {
            for (const element of list.querySelectorAll('button[data-action]')) {
                if (!(element instanceof HTMLButtonElement)) continue;
                if (
                    element.dataset.action === token.action &&
                    element.dataset.handle === token.handle &&
                    !element.disabled
                ) {
                    element.focus();
                    return;
                }
            }
        }
        // The initiating row may disappear after a successful action. Move
        // focus to a meaningful surviving control rather than dropping it.
        if (!this.elements.search.disabled) {
            this.elements.search.focus();
        } else if (!this.elements.reconnect.disabled) {
            this.elements.reconnect.focus();
        }
    }

    private renderDevices(view: ConnectorViewModel): void {
        const rows = view.devices.map((device) => {
            const item = this.document.createElement('li');
            const label = this.document.createElement('span');
            label.textContent = device.label;
            item.append(label);
            if (device.hintLabel !== null) {
                const hint = this.document.createElement('span');
                hint.textContent = device.hintLabel;
                hint.dataset.role = 'hint';
                item.append(hint);
            }
            const button = actionButton(this.document, 'connect', 'Connect', device.handle);
            button.disabled = !device.canConnect;
            button.setAttribute('aria-label', `Connect to ${device.label}`);
            item.append(button);
            return item;
        });
        this.elements.deviceList.replaceChildren(...rows);
    }

    private renderConnections(view: ConnectorViewModel): void {
        const rows = view.connections.map((connection) => {
            const item = this.document.createElement('li');
            const label = this.document.createElement('span');
            label.textContent = `${connection.label} · ${connection.status}`;
            item.append(label);
            if (connection.hintLabel !== null) {
                const hint = this.document.createElement('span');
                hint.textContent = connection.hintLabel;
                hint.dataset.role = 'hint';
                item.append(hint);
            }
            if (connection.queuedOperations > 0) {
                const queued = this.document.createElement('span');
                queued.textContent = `${connection.queuedOperations} queued operation${connection.queuedOperations === 1 ? '' : 's'}`;
                item.append(queued);
            }
            const button = actionButton(
                this.document,
                'disconnect',
                'Disconnect',
                connection.handle,
            );
            button.disabled = !connection.canDisconnect;
            button.setAttribute('aria-label', `Disconnect ${connection.label}`);
            item.append(button);
            return item;
        });
        this.elements.connectionList.replaceChildren(...rows);
    }

    private renderDiagnostics(view: ConnectorViewModel): void {
        const summary = this.elements.diagnostics.querySelector('summary');
        if (summary !== null) summary.textContent = `Diagnostics (${UI_HISTORY_NOTE})`;
        const rows = view.diagnostics.map((entry) => {
            const item = this.document.createElement('li');
            item.textContent = entry.text;
            return item;
        });
        this.elements.diagnosticsList.replaceChildren(...rows);
    }
}

// ---------------------------------------------------------------------------
// Production bootstrap
// ---------------------------------------------------------------------------

let productionApp: ConnectorApp | null = null;
let bootstrapInFlight: Promise<ConnectorApp | null> | null = null;

function createChromeConnectorOwnership(): ConnectorOwnership {
    return new ConnectorOwnership({
        runtime: {
            sendMessage: (message) => chrome.runtime.sendMessage(message),
            onMessage: chrome.runtime
                .onMessage as unknown as ConnectorOwnershipApi['runtime']['onMessage'],
        },
    });
}

/**
 * The connector's single trust repository, identity and session engine wiring.
 * The repository's verifier is supplied by the app and forwards to the session
 * manager's stage-bound verifier; lookup and mirror upgrade use the same store,
 * so there is no second, permissive verifier anywhere.
 */
function productionSessionOptions(): ConnectorSessionOptions {
    const keys = new IndexedDbIdentityStore();
    return {
        createRepository(verifier: AuthenticationVerifier): ChromeTrustRepository {
            return new ChromeTrustRepository(keys, new ChromeLocalStore(), trustLock, verifier);
        },
        createEngineDeps(
            _handle: string,
            peerId: string,
            localInstallationId: Uint8Array,
            repository: ChromeTrustRepository,
            proofs: ProofRegistry,
        ): ClientEngineDeps {
            return {
                localInstallationId,
                expectedPeerId: peerId,
                isCurrent: () => false,
                handler: new ActivationEngine({ trust: repository, proofs }),
                lookupPhone: async (id) => {
                    const peers = await repository.peers();
                    const peer = peers.find((candidate) => candidate.installationId === id);
                    if (peer === undefined) return undefined;
                    return {
                        publicKey: bytes(peer.publicKey, 65),
                        state: peer.state,
                        // OTP2-CRY-019f: the public `ET` persisted with the pending,
                        // committed or active record binds the recovery keys.
                        et: bytes(peer.enrollmentTranscript, 32),
                    };
                },
                upgradeMirror: (id, proof) => repository.confirm(id, 'phone_active', proof),
                now: () => performance.now(),
                sign: async (message) => {
                    const identity = await keys.read();
                    if (identity === undefined) throw new Error('identity unavailable');
                    return normalizeLowS(
                        new Uint8Array(
                            await crypto.subtle.sign(
                                { name: 'ECDSA', hash: 'SHA-256' },
                                identity.privateKey,
                                message,
                            ),
                        ),
                    );
                },
            };
        },
    };
}

function renderSecondaryPage(
    document: Document,
    outcome: Extract<OwnershipClaimOutcome, { owner: false }>,
): void {
    const status = document.getElementById('status');
    if (status !== null) {
        status.dataset.phase = 'secondary';
        status.textContent =
            outcome.reason === 'claim-failed' || outcome.reason === 'storage'
                ? 'Could not verify connector ownership.'
                : 'Connector is already open in another tab.';
    }
    const hostStatus = document.getElementById('host-status');
    if (hostStatus !== null) {
        hostStatus.textContent = 'This tab is not connected to the Native Bridge.';
    }
    const guidance = document.getElementById('host-guidance');
    if (guidance !== null) {
        guidance.textContent =
            outcome.ownerTab === undefined
                ? 'Only one connector tab may own the Native Bridge. Close this tab and use the existing connector.'
                : 'Only one connector tab may own the Native Bridge. The existing connector tab was focused.';
        guidance.hidden = false;
    }
    for (const id of ['search-button', 'stop-scan-button', 'reconnect-button', 'shutdown-button']) {
        const element = document.getElementById(id);
        if (element instanceof HTMLButtonElement) element.disabled = true;
    }
}

async function bootstrapOwnedConnector(): Promise<ConnectorApp | null> {
    if (typeof document === 'undefined' || typeof window === 'undefined') return null;
    if (typeof chrome === 'undefined' || typeof chrome.runtime?.connectNative !== 'function') {
        return null;
    }

    const guard = new BootstrapLifecycle(
        (name, callback) => {
            if (name === 'freeze' || name === 'resume') document.addEventListener(name, callback);
            else window.addEventListener(name, callback);
        },
        () => window.location.reload(),
    );
    window.addEventListener('pageshow', (event) => {
        if (event.persisted && guard.ended) window.location.reload();
    });
    const ownership = createChromeConnectorOwnership();
    const ownershipResult = await ownership.claim();
    guard.cleanup(() => {
        void ownership.release();
    });
    if (guard.ended) return null;
    if (!ownershipResult.owner) {
        renderSecondaryPage(document, ownershipResult);
        return null;
    }

    // OTP-10: the connector tab owns the bundled WASM pairing core; the MV3
    // service worker never loads it. Warm the module once, off the startup
    // path; a failure stays local to the pairing flow and never blocks the
    // host connection.
    void initPairingCore().catch(() => undefined);

    // Only the owner answers the launcher ping. Secondary/restored documents
    // therefore cannot be mistaken for the document that owns the host.
    if (
        typeof chrome.runtime.onMessage?.addListener === 'function' &&
        typeof chrome.tabs?.getCurrent === 'function'
    ) {
        registerConnectorPing({
            runtime: chrome.runtime as unknown as ConnectorPingApi['runtime'],
            tabs: chrome.tabs as unknown as ConnectorPingApi['tabs'],
        });
    }

    const token = await activateGeneration(
        () => chrome.runtime.sendMessage({ type: 'veles.lifecycle.activate' }),
        () => ownership.release(),
    );
    if (guard.ended) return null;
    if (typeof token !== 'string') {
        await ownership.release();
        const status = document.getElementById('status');
        if (status) status.textContent = 'Connector lifecycle unavailable. Reload to retry.';
        document
            .getElementById('reconnect-button')
            ?.addEventListener('click', () => window.location.reload());
        return null;
    }
    let ended = false;
    let port: NativePortLike | null = null;
    const controller = new ConnectorController({
        connectNative: (hostName: string) => {
            if (ended) throw new Error('Connector generation ended');
            port = gateNativePort(
                chrome.runtime.connectNative(hostName) as unknown as NativePortLike,
                async () => {
                    if (ended) return false;
                    const result: unknown = await chrome.runtime.sendMessage({
                        type: 'veles.lifecycle.check',
                        token,
                    });
                    return (
                        !ended &&
                        typeof result === 'object' &&
                        result !== null &&
                        (result as { ok?: unknown }).ok === true
                    );
                },
                () => chrome.runtime.lastError,
            );
            return port;
        },
        lastError: () => chrome.runtime.lastError,
    });
    const sessionOptions = productionSessionOptions();
    let probe: DevelopmentProbe | null = null;
    if (__VELES_CHANNEL__ === 'development') {
        const { createDevelopmentProbe } = await import('./debug/probe');
        probe = createDevelopmentProbe(document);
    }
    const app = new ConnectorApp({
        controller,
        view: new DocumentConnectorView(document),
        session: probe === null ? sessionOptions : probe.sessionOptions(sessionOptions),
    });
    if (probe !== null && app.sessions !== null) {
        probe.install(app, app.sessions, controller);
    }
    if (__VELES_CHANNEL__ === 'development') {
        // The second compile-time-guarded dynamic import keeps the pairing panel in its own
        // development-only chunk; a production build folds both guards and emits neither.
        const { mountPairingPanel } = await import('./debug/pairing-panel');
        mountPairingPanel(document.body, {
            devices: () => app.pairingDevices,
            status: () => app.pairingState,
            trust: {
                peers: () => app.pairingPeers(),
                revoke: (peerId: string) => app.clearPairingPeer(peerId),
            },
            start: (deviceHandle: string, code: string) => app.startPairing(deviceHandle, code),
            cancel: () => app.cancelPairing(),
            onChange: (listener: () => void) => controller.onChange(() => listener()),
        });
    }
    const copyStatus = document.getElementById('synthetic-copy-status');
    const copyButton = document.getElementById('synthetic-copy-button');
    const armButton = document.getElementById('synthetic-copy-arm');
    let armed = false;
    let copying = false;
    const copy = async (): Promise<void> => {
        if (ended || copying) return;
        copying = true;
        if (copyStatus) copyStatus.textContent = 'Copying synthetic test code…';
        try {
            const result: unknown = await chrome.runtime.sendMessage({
                type: 'veles.clipboard.copy',
                token,
                code: '012345',
            });
            if (!ended && copyStatus)
                copyStatus.textContent =
                    typeof result === 'object' &&
                    result !== null &&
                    (result as { ok?: unknown }).ok === true
                        ? 'Synthetic code copied.'
                        : 'Copy failed. Transport remains connected; retry explicitly.';
        } catch {
            if (!ended && copyStatus)
                copyStatus.textContent =
                    'Copy failed. Transport remains connected; retry explicitly.';
        } finally {
            copying = false;
        }
    };
    copyButton?.addEventListener('click', () => void copy());
    armButton?.addEventListener('click', () => {
        if (ended) return;
        armed = true;
        if (copyStatus)
            copyStatus.textContent = 'Armed: next raw message copies synthetic code 012345 once.';
    });
    const stopRecords = controller.onRecord(() => {
        // Diagnostic only: never interpret or copy raw record bytes as OTP data.
        if (!armed || ended) return;
        armed = false;
        void copy();
    });
    const stopStatus = controller.onChange((snapshot) => {
        if (!ended)
            void chrome.runtime
                .sendMessage({
                    type: 'veles.lifecycle.status',
                    token,
                    connected: snapshot.connections.some(
                        (connection) => connection.status === 'connected',
                    ),
                })
                .catch(() => undefined);
    });
    const onPageHide = (): void => {
        if (ended) return;
        ended = true;
        armed = false;
        // Invalidate synchronously before awaiting worker I/O or processing callbacks.
        port?.disconnect();
        stopRecords();
        stopStatus();
        probe?.dispose();
        app.dispose();
        productionApp = null;
        void chrome.runtime
            .sendMessage({ type: 'veles.lifecycle.end', token })
            .catch(() => undefined);
        void ownership.release();
    };
    guard.cleanup(onPageHide);
    productionApp = app;
    void app.start();
    return app;
}

export function bootstrapConnector(): Promise<ConnectorApp | null> {
    if (productionApp !== null) return Promise.resolve(productionApp);
    if (bootstrapInFlight !== null) return bootstrapInFlight;
    bootstrapInFlight = bootstrapOwnedConnector().finally(() => {
        bootstrapInFlight = null;
    });
    return bootstrapInFlight;
}

if (typeof document !== 'undefined' && typeof window !== 'undefined') {
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', () => void bootstrapConnector(), {
            once: true,
        });
    } else {
        void bootstrapConnector();
    }
}
