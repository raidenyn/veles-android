// OTP-10 Task 8: the enrollment link over BLE, outside `SessionManager`.
//
// The enrollment connection is not a reconnect session: it starts with a
// `pairing_request` and carries §4 pairing frames plus §5 enrollment records.
// This module deliberately depends only on the connector controller's transport
// surface (`connect`/`send`/`disconnect`/`onRecord`), never on
// `SessionManager` or its engines.
//
// A connector tab owns the link. `dispose()` is the tab-teardown path: it closes
// the client machine (including the core handle), deletes this enrollment's
// pending record per the OTP2-STA-010d connector-context rule, and disconnects
// the link.

import { DeviceType } from './enrollment';
import { ClientMachine, type ClientTrustRepository } from './client-machine';
import { PairingExecution, initPairingCore, type PairingCore } from './core';
import type { ProofRegistry } from './proofs';
import type { ConnectorResult } from '../connector/port';
import type { WireEvent } from '../connector/protocol';

const DEFAULT_TICK_MS = 1000;

type ConnectedEvent = Extract<WireEvent, { type: 'connected' }>;
type SentEvent = Extract<WireEvent, { type: 'sent' }>;
type DisconnectedEvent = Extract<WireEvent, { type: 'disconnected' }>;

/** The connector-controller surface the enrollment link needs. */
export interface PairingTransport {
    connect(target: { deviceHandle: string }): Promise<ConnectorResult<ConnectedEvent>>;
    send(
        connectionHandle: string,
        record: Uint8Array,
    ): ConnectorResult<SentEvent> | Promise<ConnectorResult<SentEvent>>;
    disconnect(connectionHandle: string): Promise<ConnectorResult<DisconnectedEvent>>;
    onRecord(
        listener: (record: { connectionHandle: string; bytes: Uint8Array }) => void,
    ): () => void;
}

export interface PairingLinkDeps {
    controller: PairingTransport;
    repo: ClientTrustRepository;
    /** Optional session-bound proof registry for the activation `complete` stage. */
    proofs?: ProofRegistry;
    code: string;
    deviceHandle: string;
    clock?: () => number;
    core?: PairingCore;
    label?: string;
    deviceType?: DeviceType;
    nonce?: () => Uint8Array;
    identityNonce?: () => Uint8Array;
    /** 0 disables the timeout tick (tests drive `machine.onTick()` directly). */
    tickIntervalMs?: number;
    setInterval?: (fn: () => void, ms: number) => unknown;
    clearInterval?: (handle: unknown) => void;
    init?: () => Promise<void>;
    /**
     * Startup cancellation, owned by the connector's attempt generation. `start`
     * checks it at every await boundary, and an inbound record is ignored once it
     * turns true, so a cancelled attempt never releases `pB` or processes a frame.
     */
    isCancelled?: () => boolean;
    /** Called immediately after connect, before the first pairing frame is sent. */
    onConnected?: (connectionHandle: string) => void;
    /** Called once when the link stops, whatever the reason. */
    onEnded?: () => void;
}

/** A startup that observed its attempt cancelled before it could return a live link. */
export class PairingCancelled extends Error {
    constructor() {
        super('pairing startup cancelled');
        this.name = 'PairingCancelled';
    }
}

function defaultClock(): number {
    return typeof performance === 'undefined' ? Date.now() : performance.now();
}

/** One live enrollment attempt over the BLE link. */
export class PairingLink {
    readonly machine: ClientMachine;
    readonly connectionHandle: string;

    private readonly controller: PairingTransport;
    private readonly clearIntervalFn: (handle: unknown) => void;
    private readonly onEnded: (() => void) | undefined;
    private readonly isCancelled: () => boolean;
    private unsubscribe: (() => void) | null = null;
    private timer: unknown = null;
    private disposed = false;
    private stopped = false;
    private ended = false;
    private queue: Promise<unknown> = Promise.resolve();

    private constructor(
        controller: PairingTransport,
        connectionHandle: string,
        machine: ClientMachine,
        clearIntervalFn: (handle: unknown) => void,
        onEnded: (() => void) | undefined,
        isCancelled: () => boolean,
    ) {
        this.controller = controller;
        this.connectionHandle = connectionHandle;
        this.machine = machine;
        this.clearIntervalFn = clearIntervalFn;
        this.onEnded = onEnded;
        this.isCancelled = isCancelled;
    }

    /**
     * Connects the selected phone, starts the client machine and sends the one
     * `pairing_request`. A failed connect or send leaves no handle or listener.
     * Cancellation is checked across every await, so a startup the connector no
     * longer owns releases the handle and the core instead of becoming live.
     */
    static async start(deps: PairingLinkDeps): Promise<PairingLink> {
        const isCancelled = deps.isCancelled ?? (() => false);
        if (isCancelled()) throw new PairingCancelled();
        await (deps.init ?? initPairingCore)();
        if (isCancelled()) throw new PairingCancelled();
        // Construct (and validate the code/caps) before opening a link: a bad
        // request can never leave a connected BLE link without an owner.
        const machine = new ClientMachine({
            core: deps.core ?? PairingExecution,
            repo: deps.repo,
            clock: deps.clock ?? defaultClock,
            code: deps.code,
            ...(deps.proofs === undefined ? {} : { proofs: deps.proofs }),
            ...(deps.label === undefined ? {} : { label: deps.label }),
            ...(deps.deviceType === undefined ? {} : { deviceType: deps.deviceType }),
            ...(deps.nonce === undefined ? {} : { nonce: deps.nonce }),
            ...(deps.identityNonce === undefined ? {} : { identityNonce: deps.identityNonce }),
        });
        const connected = await deps.controller.connect({ deviceHandle: deps.deviceHandle });
        if (!connected.ok) throw new Error('pairing connect failed');
        const handle = connected.event.connection_handle;
        // The connector owns the recovery ledger. Register the enrollment handle
        // before the first send, so a link that disappears during start can never
        // be mistaken for a lost session.
        deps.onConnected?.(handle);
        const link = new PairingLink(
            deps.controller,
            handle,
            machine,
            deps.clearInterval ?? ((timer) => globalThis.clearInterval(timer as never)),
            deps.onEnded,
            isCancelled,
        );
        if (isCancelled()) {
            // The transport is already connected: close it and erase the machine
            // before anyone can mistake this attempt for an installed link.
            await link.abandon();
            throw new PairingCancelled();
        }
        link.unsubscribe = deps.controller.onRecord((record) => {
            if (record.connectionHandle !== handle) return;
            // `receive` drops the record and tears the link down when the attempt
            // was cancelled while this listener was live.
            void link.receive(record.bytes);
        });
        try {
            if (isCancelled()) throw new PairingCancelled();
            await link.send(machine.start());
        } catch (error) {
            await link.dispose();
            throw error;
        }
        if (isCancelled()) {
            // A cancel that landed while the initial send was pending: `receive`
            // may already have torn the link down, so dispose and never install a
            // timer or return an unowned link.
            await link.dispose();
            throw new PairingCancelled();
        }
        const tick = deps.tickIntervalMs ?? DEFAULT_TICK_MS;
        if (tick > 0) {
            const setIntervalFn = deps.setInterval ?? ((fn, ms) => globalThis.setInterval(fn, ms));
            link.timer = setIntervalFn(() => {
                void link.machine.onTick().then((abandoned) => {
                    if (abandoned) void link.stop();
                });
            }, tick);
        }
        return link;
    }

    /** Processes one record or pairing frame from this link's connection. */
    receive(bytes: Uint8Array): Promise<void> {
        const next = this.queue.then(async () => {
            if (this.disposed || this.stopped) return;
            if (this.isCancelled()) {
                // A cancelled attempt must not process an offer or release `pB`
                // (or a later confirmation): erase the machine and drop the link,
                // including this enrollment's pending record.
                await this.dispose();
                return;
            }
            try {
                const frames = await this.machine.onFrame(bytes);
                for (const frame of frames) await this.send(frame);
                // A verified prepare authorizes the activation commit, and the link
                // owns that step: `onFrame` deliberately returns no frame for it.
                if (this.machine.state === 'verified_prepare_ready') {
                    await this.send(await this.machine.commit());
                }
                if (this.machine.closed || this.machine.failed) await this.stop();
            } catch {
                // A transport failure tears the link down; erasure keeps any durable
                // pending record for the recovery path, exactly like link loss.
                await this.machine.onDisconnect();
                await this.stop();
            }
        });
        this.queue = next.then(
            () => undefined,
            () => undefined,
        );
        return next;
    }

    /** User cancel: deletes the pending record and disconnects. False means cleanup failed. */
    async cancel(): Promise<boolean> {
        if (this.disposed) return true;
        this.stopped = true;
        const cleaned = await this.machine.cancel();
        await this.stop();
        return cleaned;
    }

    /**
     * Connector-context loss (tab teardown): close the handle, delete this
     * enrollment's pending record per OTP2-STA-010d, and disconnect.
     */
    async dispose(): Promise<void> {
        if (this.disposed) return;
        this.disposed = true;
        this.stopped = true;
        this.clearTimer();
        this.unsubscribe?.();
        this.unsubscribe = null;
        // Even after `abandon()` closed a dead link, tab teardown still deletes the
        // connector-context pending record.
        await this.machine.cancel();
        await this.stop();
    }

    /**
     * Connection loss while the connector document still lives (for example a
     * native-host port death). OTP2-STA-010d ties pending-record deletion to
     * connector-context loss, so this closes the handle and erases live material
     * but keeps the durable pending record for the recovery protocol.
     */
    async abandon(): Promise<void> {
        if (this.disposed || this.stopped) return;
        this.stopped = true;
        this.clearTimer();
        this.unsubscribe?.();
        this.unsubscribe = null;
        await this.machine.onDisconnect();
        await this.stop();
    }

    /** Waits for every inbound frame queued so far (tests and teardown sequencing). */
    async settled(): Promise<void> {
        await this.queue;
    }

    private async send(frame: Uint8Array): Promise<void> {
        const result = await this.controller.send(this.connectionHandle, frame);
        if (!result.ok) throw new Error('pairing send failed');
    }

    private async stop(): Promise<void> {
        this.clearTimer();
        this.unsubscribe?.();
        this.unsubscribe = null;
        try {
            await this.controller.disconnect(this.connectionHandle);
        } catch {
            // A dead port has already ended the link; nothing is left to close.
        }
        this.notifyEnded();
    }

    private notifyEnded(): void {
        if (this.ended) return;
        this.ended = true;
        try {
            this.onEnded?.();
        } catch {
            // A completion subscriber must not break teardown.
        }
    }

    private clearTimer(): void {
        if (this.timer === null) return;
        this.clearIntervalFn(this.timer);
        this.timer = null;
    }
}
