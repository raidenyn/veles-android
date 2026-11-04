// Bounded automatic recovery.
//
// Recovery is deliberately small: at most three attempts, with fixed delays of
// 1 s, 2 s and 4 s. Attempt state is per key (a host restart or one peer link),
// so one peer's success or explicit disconnect cannot reset or cancel another
// peer's bounded cycle. A success resets that key to a fresh three-attempt
// allowance. Explicit close/disconnect/shutdown/incompatible/missing-host
// suppression cancels timers; stale timer callbacks are token-checked and do
// nothing.

import type { ReconnectTarget } from './hints';

export const RECONNECT_DELAYS_MS = [1_000, 2_000, 4_000] as const;
export const RECONNECT_MAX_ATTEMPTS = 3;

export type ReconnectSuppressionReason =
    | 'intentional_close'
    | 'user_disconnect'
    | 'shutdown'
    | 'incompatible_api'
    | 'missing_host';

export interface ReconnectAttempt {
    readonly key: string;
    readonly target: ReconnectTarget;
    readonly attempt: number;
    /** Identity of this cycle; used to reject old async continuations. */
    readonly token: number;
}

export type ReconnectAttemptHandler = (attempt: ReconnectAttempt) => boolean | Promise<boolean>;

export interface ReconnectPolicyDeps {
    onAttempt?: ReconnectAttemptHandler;
    onChange?: () => void;
    setTimeout?: (fn: () => void, ms: number) => unknown;
    clearTimeout?: (handle: unknown) => void;
    maxAttempts?: number;
}

type ReconnectState = 'scheduled' | 'inflight' | 'exhausted';

interface ReconnectEntry {
    readonly key: string;
    readonly target: ReconnectTarget;
    attempts: number;
    state: ReconnectState;
    timer?: unknown;
    token: number;
}

const SUPPRESSION_REASONS: ReadonlySet<ReconnectSuppressionReason> = new Set([
    'intentional_close',
    'user_disconnect',
    'shutdown',
    'incompatible_api',
    'missing_host',
]);

function clampAttempts(value: number | undefined): number {
    if (value === undefined || !Number.isFinite(value)) return RECONNECT_MAX_ATTEMPTS;
    return Math.max(1, Math.min(RECONNECT_MAX_ATTEMPTS, Math.trunc(value)));
}

export class ReconnectPolicy {
    readonly maxAttempts: number;
    private readonly entries = new Map<string, ReconnectEntry>();
    private readonly peerSuppression = new Map<string, ReconnectSuppressionReason>();
    private readonly onAttempt: ReconnectAttemptHandler | undefined;
    private readonly onChange: (() => void) | undefined;
    private readonly setTimeoutFn: (fn: () => void, ms: number) => unknown;
    private readonly clearTimeoutFn: (handle: unknown) => void;
    private globalSuppression: ReconnectSuppressionReason | null = null;
    private tokenCounter = 0;

    constructor(deps: ReconnectPolicyDeps = {}) {
        this.maxAttempts = clampAttempts(deps.maxAttempts);
        this.onAttempt = deps.onAttempt;
        this.onChange = deps.onChange;
        this.setTimeoutFn =
            deps.setTimeout ?? ((fn, ms) => globalThis.setTimeout(fn, ms) as unknown);
        this.clearTimeoutFn =
            deps.clearTimeout ??
            ((handle) => globalThis.clearTimeout(handle as ReturnType<typeof setTimeout>));
    }

    get suppressed(): boolean {
        return this.globalSuppression !== null;
    }

    get suppressionReason(): ReconnectSuppressionReason | null {
        return this.globalSuppression;
    }

    get scheduled(): boolean {
        for (const entry of this.entries.values()) {
            if (entry.state === 'scheduled') return true;
        }
        return false;
    }

    get inFlight(): boolean {
        for (const entry of this.entries.values()) {
            if (entry.state === 'inflight') return true;
        }
        return false;
    }

    get active(): boolean {
        return this.scheduled || this.inFlight;
    }

    /** Highest attempt count across keys, for a compact status line. */
    get attempts(): number {
        let attempts = 0;
        for (const entry of this.entries.values()) {
            if (entry.attempts > attempts) attempts = entry.attempts;
        }
        return attempts;
    }

    attemptsFor(key: string): number {
        return this.entries.get(key)?.attempts ?? 0;
    }

    isScheduled(key: string): boolean {
        return this.entries.get(key)?.state === 'scheduled';
    }

    isExhausted(key: string): boolean {
        return this.entries.get(key)?.state === 'exhausted';
    }

    hasCycle(key: string): boolean {
        return this.entries.has(key);
    }

    /** True while this exact in-flight attempt is still the current cycle. */
    isCurrent(attempt: ReconnectAttempt): boolean {
        const entry = this.entries.get(attempt.key);
        return (
            entry !== undefined &&
            entry.token === attempt.token &&
            entry.state === 'inflight' &&
            entry.attempts === attempt.attempt
        );
    }

    pendingKeys(): string[] {
        return [...this.entries.values()]
            .filter((entry) => entry.state !== 'exhausted')
            .map((entry) => entry.key);
    }

    /** Every key with a policy entry, including exhausted cycles. */
    keys(): string[] {
        return [...this.entries.keys()];
    }

    /**
     * Drop one cycle without recording user-disconnect suppression. Used when
     * a port generation ends: old link work must not become a permanent
     * barrier to a later valid cycle under the same peer key.
     */
    discard(key: string): void {
        const entry = this.entries.get(key);
        if (entry === undefined) return;
        this.clearEntryTimer(entry);
        this.entries.delete(key);
        this.notify();
    }

    isSuppressed(key?: string): boolean {
        if (this.globalSuppression !== null) return true;
        return key === undefined ? this.peerSuppression.size > 0 : this.peerSuppression.has(key);
    }

    suppressionFor(key: string): ReconnectSuppressionReason | null {
        return this.peerSuppression.get(key) ?? this.globalSuppression;
    }

    /**
     * Start (or restart) one bounded cycle for `key`. Returns false when the
     * policy is globally/per-peer suppressed or this key already has a timer.
     */
    schedule(key: string, target: ReconnectTarget): boolean {
        if (this.globalSuppression !== null || this.peerSuppression.has(key)) return false;
        // One cycle per key. A fresh loss notification while that cycle is
        // scheduled, in flight, or exhausted must not renew the allowance;
        // only a confirmed success or explicit reset() may do that.
        if (this.entries.has(key)) return false;

        const entry: ReconnectEntry = {
            key,
            target,
            attempts: 0,
            state: 'scheduled',
            token: this.nextToken(),
        };
        this.entries.set(key, entry);
        this.arm(entry, RECONNECT_DELAYS_MS[0] as number);
        this.notify();
        return true;
    }

    /** A confirmed recovery resets the key's attempts and clears its suppression. */
    noteSuccess(key?: string): void {
        if (key === undefined) {
            this.clearTimers();
            this.entries.clear();
            this.peerSuppression.clear();
            this.notify();
            return;
        }
        const entry = this.entries.get(key);
        if (entry !== undefined) {
            this.clearEntryTimer(entry);
            this.entries.delete(key);
        }
        this.peerSuppression.delete(key);
        this.notify();
    }

    /** Explicit user disconnect cancels only that peer's pending cycle. */
    cancel(key: string, reason: ReconnectSuppressionReason = 'user_disconnect'): void {
        const entry = this.entries.get(key);
        if (entry !== undefined) {
            this.clearEntryTimer(entry);
            this.entries.delete(key);
        }
        this.peerSuppression.set(key, reason);
        this.notify();
    }

    /**
     * Global suppression for closing/disposing the connector, shutdown, or a
     * missing/incompatible host. Cancels every pending timer.
     */
    suppress(reason: ReconnectSuppressionReason): void {
        if (!SUPPRESSION_REASONS.has(reason)) return;
        this.globalSuppression = reason;
        this.clearTimers();
        this.entries.clear();
        this.peerSuppression.clear();
        this.notify();
    }

    /** Explicit Retry lifts earlier suppression and starts a fresh allowance. */
    reset(): void {
        this.globalSuppression = null;
        this.clearTimers();
        this.entries.clear();
        this.peerSuppression.clear();
        this.notify();
    }

    dispose(): void {
        this.suppress('intentional_close');
    }

    private notify(): void {
        try {
            this.onChange?.();
        } catch {
            // A presentation subscriber must not break recovery state.
        }
    }

    private nextToken(): number {
        this.tokenCounter += 1;
        return this.tokenCounter;
    }

    private arm(entry: ReconnectEntry, delayMs: number): void {
        entry.state = 'scheduled';
        const token = entry.token;
        entry.timer = this.setTimeoutFn(() => {
            void this.fire(entry, token);
        }, delayMs);
    }

    private async fire(entry: ReconnectEntry, token: number): Promise<void> {
        if (entry.token !== token) return;
        if (this.entries.get(entry.key) !== entry) return;
        entry.timer = undefined;
        entry.state = 'inflight';
        entry.attempts += 1;
        this.notify();
        const attempt: ReconnectAttempt = {
            key: entry.key,
            target: entry.target,
            attempt: entry.attempts,
            token: entry.token,
        };

        let success = false;
        try {
            const outcome = this.onAttempt === undefined ? true : await this.onAttempt(attempt);
            success = outcome !== false;
        } catch {
            success = false;
        }

        // A cancel/suppress/success during the async handler invalidates this
        // entry. A late callback must never arm another timer.
        if (this.entries.get(entry.key) !== entry || entry.token !== token) return;

        if (success) {
            this.noteSuccess(entry.key);
            return;
        }
        if (entry.attempts < this.maxAttempts) {
            this.arm(entry, RECONNECT_DELAYS_MS[entry.attempts] as number);
        } else {
            entry.state = 'exhausted';
        }
        this.notify();
    }

    private clearEntryTimer(entry: ReconnectEntry): void {
        if (entry.timer === undefined) return;
        this.clearTimeoutFn(entry.timer);
        entry.timer = undefined;
    }

    private clearTimers(): void {
        for (const entry of this.entries.values()) this.clearEntryTimer(entry);
    }
}
