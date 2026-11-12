// Typed wrapper over the web-target Rust core (`veles_crypto`).
//
// The package under `rust-wasm/pkg/` is generated, git-ignored output of
// `./gradlew rustWasm`; Vite emits its `.wasm` as an ordinary asset. Only the
// connector document (where the session engine already runs) imports this
// module, so the MV3 service worker never loads the core.
//
// Rust owns the SPAKE2 state machine and the eight-handle registry. This module
// mirrors that registry in JavaScript so a caller (and the test suite) can
// assert that every `PairingExecution` was closed.

import initWasm, { pairing_command } from '../../rust-wasm/pkg/veles_crypto.js';
import bundledWasmUrl from '../../rust-wasm/pkg/veles_crypto_bg.wasm?url';

/** The client is always SPAKE2 party B. */
const ROLE_B = 2;

const OP_START = 1;
const OP_EXCHANGE = 2;
const OP_CONFIRM = 3;
const OP_FINISH = 4;
const OP_CLOSE = 5;

const STATUS_OK = 0;
const STATUS_FAILED = 1;
const STATUS_ENTROPY_UNAVAILABLE = 2;
const STATUS_CAPACITY = 4;

const PUBLIC_POINT_BYTES = 65;
const HANDLE_BYTES = 8;
const CONFIRMATION_BYTES = 32;
const KEY_BYTES = 16;

const PACKAGE_MISSING =
    "Veles pairing core unavailable: build the WASM package with './gradlew rustWasm' " +
    '(output: web-extension/rust-wasm/pkg)';

const openHandles = new Set<string>();

let initialization: Promise<void> | null = null;

/**
 * Loads the core once. Tests pass the package bytes (as
 * `rust/veles-crypto/tests/pairing-wasm.mjs` does); the connector passes no
 * source and gets the bundled URL.
 */
export function initPairingCore(source?: BufferSource | URL): Promise<void> {
    if (initialization !== null) return initialization;
    initialization = initialize(source).catch((cause: unknown) => {
        initialization = null;
        const detail = cause instanceof Error ? cause.message : String(cause);
        throw new Error(`${PACKAGE_MISSING}: ${detail}`);
    });
    return initialization;
}

async function initialize(source: BufferSource | URL | undefined): Promise<void> {
    await initWasm({ module_or_path: source ?? bundledWasmUrl });
}

/** Number of live core handles owned by this module. */
export function openHandleCount(): number {
    return openHandles.size;
}

/** One SPAKE2 execution; `close` is idempotent and safe in a `finally`. */
export class PairingExecution {
    private closed = false;

    private constructor(
        private readonly handle: Uint8Array,
        private readonly key: string,
    ) {}

    /**
     * Starts a pairing attempt for one six-digit code and public context.
     * The caller owns the returned execution and must close it on every exit.
     */
    static start(
        code: string,
        context: Uint8Array,
    ): { execution: PairingExecution; point: Uint8Array } {
        if (code.length !== 6) throw new Error('pairing code must be six digits');
        const codeBytes = Uint8Array.from(code, (character) => character.charCodeAt(0));
        const response = unwrap(
            pairing_command(new Uint8Array([1, OP_START, ROLE_B, ...codeBytes, ...context])),
            'start',
        );
        if (response.length !== HANDLE_BYTES + PUBLIC_POINT_BYTES) {
            throw new Error(`pairing core start returned ${response.length} bytes`);
        }
        const handle = response.slice(0, HANDLE_BYTES);
        const point = response.slice(HANDLE_BYTES);
        const key = handleKey(handle);
        openHandles.add(key);
        return { execution: new PairingExecution(handle, key), point };
    }

    /** Processes the phone's public point. Party B returns no confirmation yet. */
    exchange(peerPoint: Uint8Array): Uint8Array {
        return this.call(OP_EXCHANGE, peerPoint, 0);
    }

    /** Verifies the phone's confirmation and returns this party's confirmation. */
    confirm(peerConfirmation: Uint8Array): Uint8Array {
        return this.call(OP_CONFIRM, peerConfirmation, CONFIRMATION_BYTES);
    }

    /** Transfers the enrollment key once, after confirmation succeeded. */
    finish(): Uint8Array {
        return this.call(OP_FINISH, new Uint8Array(0), KEY_BYTES);
    }

    /** Releases the Rust handle. Calling it again is a no-op. */
    close(): void {
        if (this.closed) return;
        this.closed = true;
        openHandles.delete(this.key);
        // The handle may already be gone (finish consumed it). A close is
        // cleanup, so a rejected or failed command must not mask the caller's
        // real outcome.
        try {
            pairing_command(new Uint8Array([1, OP_CLOSE, ...this.handle]));
        } catch {
            // Ignored on purpose: the entry is gone either way.
        }
    }

    private call(operation: number, body: Uint8Array, expectedBytes: number): Uint8Array {
        if (this.closed) throw new Error('pairing execution is closed');
        const response = unwrap(
            pairing_command(new Uint8Array([1, operation, ...this.handle, ...body])),
            operationName(operation),
        );
        if (response.length !== expectedBytes) {
            throw new Error(
                `pairing core ${operationName(operation)} returned ${response.length} bytes`,
            );
        }
        return response;
    }
}

function handleKey(handle: Uint8Array): string {
    return Array.from(handle, (byte) => byte.toString(16).padStart(2, '0')).join('');
}

function operationName(operation: number): string {
    switch (operation) {
        case OP_EXCHANGE:
            return 'exchange';
        case OP_CONFIRM:
            return 'confirm';
        case OP_FINISH:
            return 'finish';
        default:
            return 'close';
    }
}

/** Drops the status byte and turns every non-zero status into an error. */
function unwrap(response: Uint8Array, operation: string): Uint8Array {
    const status = response[0];
    if (status === STATUS_OK) return response.slice(1);
    if (status === STATUS_ENTROPY_UNAVAILABLE) {
        throw new Error(`pairing core ${operation} failed: entropy unavailable`);
    }
    if (status === STATUS_CAPACITY) {
        throw new Error(`pairing core ${operation} failed: handle capacity reached`);
    }
    if (status === STATUS_FAILED) {
        throw new Error(`pairing core ${operation} failed`);
    }
    throw new Error(`pairing core ${operation} failed with status ${status}`);
}
