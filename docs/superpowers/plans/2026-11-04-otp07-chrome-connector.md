# OTP-07 Chrome Native-Bridge Connector Implementation Plan (corrected)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement task-by-task. Read the preflight rulings first; they replace the original draft where they differ.

**Goal:** Add one MV3 connector tab that owns the Native Messaging port, performs the v1 handshake, searches/selects phones, maintains isolated per-phone state, routes whole records, and presents actionable startup/search/error UI.

**Architecture:** A service worker launcher focuses or creates a single root `connector.html`. The connector document owns exactly one Native Messaging port, validates every inbound envelope against `docs/protocol/veles-v1.md` section 10, correlates terminal events to outstanding requests, and keeps scans, discovered devices, and connections as separate stores. Trust hints are read-only, peer-scoped inputs from the existing `ChromeTrustRepository`; they never authenticate a peer.

**Tech Stack:** TypeScript, Chrome MV3 (service worker + extension tab, Chrome 102+), Vite multi-page build, Vitest (Node), Playwright packaged-browser test with a simulated transport.

**Authoritative inputs:**
- `docs/rfcs/2026-08-21-otp-external-devices.md`, section `### OTP-07`
- `docs/protocol/veles-v1.md`, section 10 (envelope v1), plus `protocol/v1/envelope.schema.json` and `protocol/v1/limits.json`
- Existing extension: `web-extension/src/{manifest,background}.ts`, `web-extension/src/trust/*`, `web-extension/test/*`, `web-extension/vite.config.ts`
- Existing host behaviour: `native-bridge/host/src/protocol.rs` (read-only reference; do not change it)

## Global constraints

- Manifest permissions are exactly `['storage', 'nativeMessaging']`. No `activeTab`, `tabs`, `host_permissions`, or `web_accessible_resources`.
- Remove `action.default_popup` so `chrome.action.onClicked` fires; retain an action title.
- Emit `connector.html` at the packaged root (alongside `options.html`/`popup.html`), with its entry in `vite.config.ts`.
- Host name is the RFC production name `me.nagaev.veles.bridge`. The existing scaffold constant `app.veles.native_bridge` is not the OTP-25 production registration; do not copy it and do not edit native-bridge manifests here.
- Envelope version `1`; supported API versions `[1]`; select the highest common version. `incompatible_api` fails closed before any BLE request.
- Veles service UUID is `8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51`.
- Handles are `^[0-9a-f]{16}$`; `request_id` is `^[A-Za-z0-9._-]{1,64}$`; `scan_state` uses `status: "started" | "stopped"`, never `started: true`.
- Every post-`ready` message carries `api_version`. `ready` carries `api_version` as its selected value.
- Strict inbound validation: exact field sets, no `null`, no unknown fields, UTF-8 byte bounds, canonical base64 with canonical pad bits, bounded arrays/strings. Correlated events must match an outstanding request and the expected terminal type/handle. Unexpected/duplicate correlated terminals or cross-handle routing fail closed.
- Locally synthesized errors (missing host, port closed, timeout, protocol violation) are connector-only. Never post `synthesized`, `wire`, `request_type`, or any non-schema field.
- Missing host is detected asynchronously from `chrome.runtime.lastError` inside `port.onDisconnect` before `ready`; do not rely on a synchronous `connectNative` throw.
- Scans, discovered devices, and connections are distinct stores. One scan can discover many phones. Operations are queued per connection with count/byte bounds and a global bound; diagnostics are bounded and contain no record bytes, labels, hints, or OTP data.
- Timeouts and disconnects never replay a send, never recreate a handle, and late events never resurrect a closed connection. Port/connection close is idempotent.
- Hints come only from active peers in the existing trust repository. A hint returned by `connected` is provisional and in-memory; do not call `repository.authenticated()`, fabricate proof bytes, or write hints to `chrome.storage` on connect.
- Reconnect is bounded and never runs after user disconnect, intentional port close, shutdown, or tab close.
- Tests use an injected asynchronous mock port, valid 16-lowercase-hex handles, `status: "started"`, `api_version`, and the Veles UUID. Do not read `lastRequestId` before the post has occurred.
- Update the explicit `npm test` file list and the existing manifest/bundle guards. Never commit `dist/`. No commits or pushes are authorized by this plan.
- Packaged-browser simulated transport tests and physical Windows/macOS smoke runs are separate evidence; neither claims the other.

## Preflight rulings

| # | Draft defect | Ruling |
|---|---|---|
| 1 | Draft added `activeTab` and `web_accessible_resources`, kept `default_popup`, and implied extra tab permissions. | Manifest is `['storage','nativeMessaging']`, no WAR/host permissions, no popup; connector is an internal root page opened by the service worker. |
| 2 | Draft used `tabs.query({url})`; without `tabs` permission Chrome ignores URL filters and hides `tab.url`. | Discover the connector by `runtime.sendMessage` ping answered from the page via `tabs.getCurrent()`, plus a `storage.session` tab-id fast path. Never read `tab.url`. Serialize clicks and focus the owning window before activating the tab. |
| 3 | Draft created `src/connector/connector.html`. | Create root `web-extension/connector.html`; Vite input `connector: resolve(__dirname, 'connector.html')` emits `dist/connector.html` + `connector.js`. |
| 4 | Draft used host `me.nagaev.veles.bridge` but the scaffold generator says `app.veles.native_bridge`. | Use the RFC/OTP-25 production name `me.nagaev.veles.bridge`. The scaffold value is a development placeholder; registration changes belong to OTP-25, not this plan. |
| 5 | Draft invented wire shapes: `started: true`, `scan1`/`dev1`/`conn1`, heart-rate UUID, missing `api_version`, `synthesized`/`request_type` on wire. | Validate/serialize only the frozen v1 schema. Valid fixtures use 16-lowercase-hex handles, `status`, `api_version: 1`, and the Veles UUID. Synthesized fields stay local. |
| 6 | Draft treated missing host as a synchronous `connectNative` failure. | Classify `missing_host` from `onDisconnect` + `runtime.lastError` before `ready`; sync throws are local `internal` at `port_create_failed`. |
| 7 | Draft conflated one scan/device/connection per `PhoneState`. | Separate `ScanStore`, `DeviceStore`, `ConnectionStore`; one scan owns many devices; connections are keyed only by `connection_handle`. |
| 8 | Draft wrote hints to `chrome.storage.local` on connect and had no trust integration. | Read hints from `ChromeTrustRepository.activePeers()`; keep `connected` hints provisional in memory; never persist on connect or call `authenticated()` without real proof. |
| 9 | Draft had no inbound schema validation, generation, queue bounds, or idempotent close. | Add strict `validateInbound`, generation-checked pending/state, bounded per-connection/global queues, bounded payload-free diagnostics, and idempotent close. |
| 10 | Draft tests delivered replies synchronously and read `lastRequestId`; ten tasks overlapped and committed `dist/`. | Use an injected async mock port with `nextPosted()`; five cohesive tasks; no commit steps and no `dist/` tracking. |
| 11 | Draft specified no real UI and no browser/physical evidence split. | Add a real startup/status/search/errors/reconnect UI; packaged-browser simulated transport is automated; physical smoke is a separate manual checklist. |

## Review focus

| Failure mode | Expected behaviour | Test |
|---|---|---|
| Native host not installed | Async `missing_host` before `ready`; install guidance; no crash or BLE request | `port.test.ts`, `integration.test.ts`, `view-model.test.ts` |
| Incompatible API | `incompatible_api` fatal; all later BLE calls fail locally; update guidance | `port.test.ts`, `integration.test.ts` |
| Port dies with pending requests | One local `port_closed` per pending request; no duplicates | `port.test.ts` |
| Malformed/cross-handle event | Bounded reject and fail closed; no state mutation | `protocol.test.ts`, `state.test.ts`, `integration.test.ts` |
| Timeout / duplicate terminal | No replay; no handle resurrection; exactly one local terminal | `port.test.ts`, `state.test.ts` |
| One scan, two phones | Both devices listed; independent connections and sends | `state.test.ts`, `integration.test.ts` |
| Intentional close | No reconnect; port/handles stay closed on repeated calls | `port.test.ts`, `hints-reconnect.test.ts` |
| Hint returned by `connected` | Displayed as provisional/unverified; not persisted; no fake auth proof | `hints-reconnect.test.ts` |

## Shared interfaces

```ts
// src/connector/protocol.ts
export const ENVELOPE_VERSION = 1;
export const SUPPORTED_API_VERSIONS = [1] as const;
export const HOST_NAME = 'me.nagaev.veles.bridge';
export const VELES_SERVICE_UUID = '8e400000-8f3c-4d1a-b64a-8e3c9b0d2a51';

export type PendingKind =
    | 'hello' | 'start_scan' | 'stop_scan' | 'connect' | 'disconnect' | 'send' | 'shutdown';

export type WireEvent =
    | { type: 'ready'; envelope_version: 1; api_version: number; request_id: string;
        bridge_version: string; platform: 'windows' | 'macos'; capabilities: string[] }
    | { type: 'device_found'; envelope_version: 1; api_version: number; scan_handle: string;
        device_handle: string; label: string; platform_hint?: string; advertisement?: string }
    | { type: 'scan_state'; envelope_version: 1; api_version: number; scan_handle: string;
        status: 'started' | 'stopped'; request_id?: string }
    | { type: 'connected'; envelope_version: 1; api_version: number; request_id: string;
        connection_handle: string; platform_hint?: string }
    | { type: 'disconnected'; envelope_version: 1; api_version: number;
        connection_handle: string; request_id?: string }
    | { type: 'sent'; envelope_version: 1; api_version: number; request_id: string;
        connection_handle: string }
    | { type: 'message'; envelope_version: 1; api_version: number;
        connection_handle: string; record_base64: string }
    | { type: 'shutdown_complete'; envelope_version: 1; api_version: number; request_id: string }
    | { type: 'error'; envelope_version: 1; api_version?: number; request_id?: string;
        code: WireErrorCode; recoverability: 'retryable' | 'fatal'; stage: string;
        scan_handle?: string; connection_handle?: string };

export interface ValidationContext {
    phase: 'pre_ready' | 'ready';
    apiVersion: number | null;
    pending: ReadonlyMap<string, { kind: PendingKind; handle?: string; generation?: number }>;
    scans: ReadonlyMap<string, { status: 'starting' | 'started' | 'stopping' | 'stopped' }>;
    connections: ReadonlyMap<string, { status: string; generation: number }>;
}

export type Validation =
    | { ok: true; event: WireEvent }
    | { ok: false; code: 'invalid_event' | 'unexpected_correlation'; stage: string };

export function validateInbound(value: unknown, context: ValidationContext): Validation;
```

```ts
// src/connector/port.ts
export interface NativePortLike {
    postMessage(message: unknown): void;
    disconnect(): void;
    onMessage: { addListener(listener: (message: unknown) => void): void };
    onDisconnect: { addListener(listener: () => void): void };
}

export type LocalFailureCode = 'missing_host' | 'port_closed' | 'protocol_violation';
export type ConnectorFailureCode = WireErrorCode | LocalFailureCode;

export type ConnectorFailure = {
    type: 'error';
    request_id?: string;
    code: ConnectorFailureCode;
    recoverability: 'retryable' | 'fatal';
    stage: string;
    synthesized: true; // local only; never serialized to the host
};

export type WireErrorEvent = Extract<WireEvent, { type: 'error' }>;

export type ConnectorResult<T extends WireEvent> =
    | { ok: true; event: T }
    | { ok: false; source: 'wire'; error: WireErrorEvent }
    | { ok: false; source: 'local'; error: ConnectorFailure };

export type ConnectorCloseReason =
    | 'missing_host' | 'port_lost' | 'protocol' | 'user' | 'shutdown';

export interface ConnectorClose {
    generation: number;
    reason: ConnectorCloseReason;
    intentional: boolean;
}

export class NativeConnectorPort {
    constructor(deps: {
        connectNative: (hostName: string) => NativePortLike;
        lastError?: () => { message?: string } | undefined;
        validationContext: () => ValidationContext;
        newRequestId?: () => string;
        now?: () => number;
        setTimeout?: (fn: () => void, ms: number) => unknown;
        clearTimeout?: (handle: unknown) => void;
        requestTimeoutMs?: number; // 30_000
    });
    open(): void;
    request<T extends WireEvent>(kind: PendingKind, fields: Record<string, unknown>): Promise<ConnectorResult<T>>;
    onEvent(listener: (event: WireEvent) => void): () => void;
    onFailure(listener: (failure: ConnectorFailure) => void): () => void;
    onClose(listener: (close: ConnectorClose) => void): () => void;
    close(intent: 'user' | 'shutdown' | 'protocol'): Promise<void>; // idempotent
    isOpen(): boolean;
    isIntentional(): boolean;
}
```

```ts
// src/connector/state.ts
export class BoundedQueue<T> {
    constructor(limits: { maxItems: number; maxBytes: number }, sizeOf: (value: T) => number);
    push(value: T): boolean;
    wouldAccept(value: T): boolean;
    peek(): T | undefined;
    shift(): T | undefined;
    remove(value: T): boolean;
    list(): T[];
    get size(): number;
    get bytes(): number;
    clear(): void;
}

// `beginScan`/`beginConnect` return null when the local cap (2 scans / 32
// connections) would be exceeded; the controller maps that to a local
// `cap_exceeded` without posting. Send queue accounting includes the posted
// in-flight operation, matching the host's pending+in-flight send storage.
export class ConnectorState {
    readonly scans: Map<string, ScanRecord>;
    readonly devices: Map<string, DeviceRecord>;
    readonly connections: Map<string, ConnectionRecord>;
    readonly diagnostics: DiagnosticRing;
    beginScan(): { generation: number } | null;
    abortScan(generation: number): boolean;
    acceptScan(handle: string, generation: number): boolean;
    beginStopScan(handle: string): { generation: number } | null;
    // Local expiry marks the scan stopping/draining but does not tombstone it;
    // the observed scan_state: stopped (or port close) is the wire boundary.
    expireScan(handle: string, generation: number): boolean;
    restoreStopScan(handle: string, generation: number): boolean;
    abandonScan(handle: string, generation: number): boolean;
    beginConnect(target: { deviceHandle: string } | { platformHint: string }): {
        generation: number;
    } | null;
    abortConnect(generation: number): boolean;
    acceptConnection(handle: string, generation: number, hint?: string): boolean;
    beginDisconnect(handle: string): { generation: number } | null;
    refuseDisconnect(handle: string, generation: number): boolean;
    apply(event: WireEvent): 'applied' | 'stale' | 'protocol_violation';
    closeConnection(handle: string, reason: 'user' | 'loss' | 'timeout' | 'protocol'): boolean;
    markConnectionAmbiguous(handle: string, generation: number): boolean;
    enqueueSend(handle: string, generation: number, operation: QueuedOperation):
        | 'ok' | 'cap_exceeded' | 'stale' | 'unknown_handle';
    peekSend(handle: string): QueuedOperation | undefined;
    markSendPosted(handle: string, operation: QueuedOperation): boolean;
    completeSend(handle: string, operation: QueuedOperation): boolean;
    discardSend(handle: string, operation: QueuedOperation, reason: SendCancelReason): boolean;
    validationContext(apiVersion: number | null): ValidationContext;
    snapshot(): ConnectorSnapshot;
    reset(reason?: SendCancelReason): void;
}
```

```ts
// src/connector/controller.ts — owns one port plus one ConnectorState.
export class ConnectorController {
    readonly state: ConnectorState;
    constructor(deps: {
        connectNative: (hostName: string) => NativePortLike;
        lastError?: () => { message?: string } | undefined;
        state?: ConnectorState;
        newRequestId?: () => string;
        now?: () => number;
        setTimeout?: (fn: () => void, ms: number) => unknown;
        clearTimeout?: (handle: unknown) => void;
        requestTimeoutMs?: number;
    });
    open(): void;
    hello(): Promise<ConnectorResult<ReadyEvent>>;
    startScan(): Promise<ConnectorResult<ScanStateEvent>>;
    stopScan(scanHandle: string): Promise<ConnectorResult<ScanStateEvent>>;
    connect(target: ConnectTarget): Promise<ConnectorResult<ConnectedEvent>>;
    disconnect(handle: string): Promise<ConnectorResult<DisconnectedEvent>>;
    send(handle: string, record: Uint8Array): Promise<ConnectorResult<SentEvent>>;
    shutdown(): Promise<ConnectorResult<ShutdownCompleteEvent>>;
    close(intent?: 'user' | 'shutdown' | 'protocol'): Promise<void>;
    onRecord(listener: (record: { connectionHandle: string; bytes: Uint8Array }) => void): () => void;
    onChange(listener: (snapshot: ConnectorSnapshot) => void): () => void;
    onFailure(listener: (failure: ConnectorFailure) => void): () => void;
    onClose(listener: (close: ConnectorClose) => void): () => void;
    snapshot(): ConnectorSnapshot;
}
```

```ts
// src/connector/hints.ts
export interface PeerHint { peerId: string; label: string; hint: string }
export interface TrustHintSource { activePeers(): Promise<readonly PeerHint[]> }
export type ReconnectTarget =
    | { kind: 'hint'; peerId: string; hint: string }
    | { kind: 'search'; reason: 'none' | 'ambiguous' | 'invalid' };
export function hintsFromPeers(peers: readonly PeerHint[]): PeerHint[];
export function chooseReconnectTarget(peers: readonly PeerHint[]): ReconnectTarget;
```

---

### Task 1: Manifest, launcher, and packaged connector entry

**Files:** `web-extension/src/manifest.ts`, `web-extension/src/background.ts`, `web-extension/connector.html` (new, root), `web-extension/src/connector.ts` (new bootstrap/ping), `web-extension/vite.config.ts`, `web-extension/package.json`; tests `test/manifest-guard.test.ts`, `test/smoke.test.ts`, `test/launcher.test.ts` (new).

**Interfaces:** `buildExtensionManifest()` returns `permissions: ['storage','nativeMessaging']`, `action: { default_title: 'Veles OTP' }`, and no `default_popup`, `web_accessible_resources`, or `host_permissions`. `openOrFocusConnector(api: LauncherChrome): Promise<void>` and `setupLauncher(api: LauncherChrome): void` in `background.ts`; `CONNECTOR_TAB_KEY = 'veles.connector.tabId'` in `storage.session`.

- [ ] **Step 1:** Change the manifest tests first: exact permissions, no popup, no WAR, no host permissions, CSP unchanged. Update `smoke.test.ts`'s `background`/`action`/`options_ui` assertions to the corrected shape.
- [ ] **Step 2:** Run `npm test` and confirm the manifest tests fail for the old manifest.
- [ ] **Step 3:** Update `manifest.ts`, add root `connector.html` with the required static elements (`status`, `host-guidance`, `search-button`, `scan-status`, `device-list`, `connection-list`, `error-banner`, `reconnect-button`, `shutdown-button`, `diagnostics`), and add `connector: resolve(__dirname, 'connector.html')` to Vite input. Add `src/connector.ts` with a `runtime.onMessage` ping responder (`veles.connector.ping` → `{ type: 'veles.connector.pong', tabId, windowId }` via `chrome.tabs.getCurrent()`); it must not open a port until the UI acts.
- [ ] **Step 4:** Implement the launcher with an injected API: a module-level promise chain serializes clicks. Fast path: read `storage.session[CONNECTOR_TAB_KEY]`, ping via `runtime.sendMessage`; if the pong's tab id matches, call `windows.update(windowId, { focused: true })` then `tabs.update(tabId, { active: true })`. Otherwise broadcast ping; use the pong's tab id/windowId, store it, and focus. If no pong (or stale/mismatched id), `tabs.create({ url: runtime.getURL('connector.html') })`, store the returned id, and focus. Never call `tabs.query({ url })` or read `tab.url`.
- [ ] **Step 5:** Write `launcher.test.ts` with a fake Chrome API and deferred responses: two concurrent clicks create exactly one tab; an existing pong focuses the window then activates the tab; a rejected ping creates a tab and replaces the stored id; no `tabs`/`activeTab` assumptions. Assert `setupLauncher` registers `action.onClicked`.
- [ ] **Step 6:** Update the explicit `npm test` file list in `package.json` to include `test/launcher.test.ts` (and later task files as they land). Do not add the browser test to `npm test`.
- [ ] **Step 7:** Run `npm test`, `npm run typecheck`, and `npm run lint`.

### Task 2: Strict wire validation, port lifecycle, and local errors

**Files:** `web-extension/src/connector/protocol.ts`, `web-extension/src/connector/errors.ts`, `web-extension/src/connector/port.ts` (new); tests `test/connector/protocol.test.ts`, `test/connector/port.test.ts`, `test/connector/mock-port.ts`, `test/connector/fixtures.ts` (new).

**Interfaces:** `validateInbound` as above; `synthesizeMissingHost(lastError)`, `synthesizePortClosed(kind)`, `synthesizeTimeout(kind)`, `synthesizeIncompatibleApi()`, `synthesizeProtocolViolation(stage)` returning `ConnectorFailure`; `MockNativePort` with `posted: unknown[]`, `nextPosted(): Promise<Record<string, unknown>>`, `emit(value)`, and `disconnect(lastErrorMessage?)`.

- [ ] **Step 1:** Write `protocol.test.ts` first: accepts a valid `ready` (`envelope_version: 1`, `api_version: 1`, `request_id`, `bridge_version`, `platform`, `capabilities: ['veles_record_v1']`); rejects `started: true` and missing `status`; rejects short/non-hex handles (`scan1`, `dev1`); rejects non-canonical base64, records over 4096 decoded bytes for `message`, unknown fields, `null`, wrong `api_version`, and capability strings other than `veles_record_v1`.
- [ ] **Step 2:** Add correlation tests: a correlated terminal for an unknown/duplicate `request_id` is `unexpected_correlation`; `device_found`/`message` with `request_id` reject; correlated `scan_state.started` must match a pending `start_scan`, `stopped` a pending `stop_scan`; unsolicited `stopped` must name a known scan; correlated `sent` must match the pending send's connection handle and captured generation; `error` carries at most one handle and a valid `[a-z0-9_]{1,32}` stage.
- [ ] **Step 3:** Run the focused test and confirm failure, then implement `protocol.ts` with exact field sets, UTF-8 byte-length checks, canonical RFC 4648 base64 (padding and pad-bit round trip), and the context correlation rules. Use the Veles service UUID only where a UUID is accepted; do not invent request/event fields.
- [ ] **Step 4:** Write `port.test.ts` first: `hello()` posts exactly `{envelope_version:1,type:'hello',request_id,api_versions:[1]}` (no `api_version` pre-ready) and resolves only on `ready`; post-ready requests include the negotiated `api_version`; local synthesized failures never appear in `posted`.
- [ ] **Step 5:** Add lifecycle tests: `onDisconnect` before `ready` with `lastError.message = 'Specified native messaging host not found.'` resolves pending `hello` with `missing_host` fatal and does not throw; no `lastError` resolves `port_closed`; disconnect with two pending requests yields exactly one `port_closed` each and a second disconnect adds none; `close()` twice is idempotent and suppresses reconnect intent; an intentional close does not synthesize `port_closed` for already-terminal work.
- [ ] **Step 6:** Add timeout/ambiguity tests with injected timers: a non-scan request resolves `timeout` exactly once after 30 s, is removed from `pending`, and a late matching `sent` is ignored without resolving again or recreating the handle; scan requests have no 30 s terminal timeout and are closed by `scan_state`/port closure. Duplicate terminal after success fails closed with one `protocol_violation` local failure.
- [ ] **Step 7:** Implement `errors.ts` and `port.ts`. Read `runtime.lastError` synchronously in `onDisconnect`; distinguish `missing_host` only before `ready`. Keep `synthesized` local. Run the focused tests.

> **Review ruling (2026-11-04, Task 2 round 1):** the authoritative
> OTP2-BRD-020/030 scan exemption overrides the earlier suggestion of a
> connector-side `start_scan` startup timeout. Both `start_scan` and
> `stop_scan` have no 30-second request timer; scan lifecycle events or port
> closure terminalize them. Any separate startup watchdog requires an approved
> contract change, not an implementation-report ruling.

### Task 3: Separate scan/device/connection state and bounded routing

**Files:** `web-extension/src/connector/limits.ts`, `web-extension/src/connector/state.ts`, `web-extension/src/connector/diagnostics.ts`, `web-extension/src/connector/controller.ts` (new); tests `test/connector/state.test.ts`, `test/connector/limits.test.ts`, `test/connector/controller.test.ts` (new). The controller is the Task 3 port/state adapter Task 4 consumes; it is not UI.

**Interfaces:** `BoundedQueue` and `ConnectorState` as above; `ScanRecord` holds `handle`, `status`, `devices: Set<string>`, `generation`; `DeviceRecord` holds `handle`, `label`, optional provisional `platformHint`, `advertisementBytes`, `scanHandle`, `generation`; `ConnectionRecord` holds `handle`, optional `deviceHandle`, optional provisional `platformHint`, `status`, `generation`, and a `BoundedQueue<QueuedOperation>`. `DiagnosticRing` stores `{ at, direction, type, code?, stage?, scan_handle?, device_handle?, connection_handle?, bytes? }` only.

- [ ] **Step 1:** Write `limits.test.ts` first asserting the exported constants equal `protocol/v1/limits.json`: outstanding requests 64, open scans 2, open connections 32, request timeout 30 s, scan cap 300 s, discovery rate 64/s and 1024/scan, discovered cache 1024, output queue 128 messages/262144 bytes, send storage per link 16/16384 and global 128/131072, platform hint 256, advertisement 256, error stage 32, bridge record admission 5120, Veles record max 4096.
- [ ] **Step 2:** Write `state.test.ts` first: one `start_scan` followed by two `device_found` events with valid distinct handles yields one scan and two devices; two connections can be created from those devices; a `sent` for connection A never resolves a pending send for B (cross-handle is `protocol_violation`).
- [ ] **Step 3:** Add bound/idempotence tests: a third open scan and 33rd connection fail locally with `cap_exceeded` without posting; the 1025th discovered device FIFO-evicts the oldest non-connected device while connected devices survive; per-connection queue rejects over 16 operations or 16 KiB; global queue rejects over 128 operations or 128 KiB; `closeConnection` twice returns true once and false after, drops queued unsent work, and late events for the closed handle return `stale` without re-adding it.
- [ ] **Step 4:** Implement `limits.ts`, `diagnostics.ts` (bounded ring, no payload/label/hint fields), and `state.ts`. Every queued operation captures its connection generation; stale generation is dropped before posting. `apply()` validates unsolicited handles against the stores and rejects unknowns; late events with a tombstoned handle are stale, never resurrected.
- [ ] **Step 5:** Wire `ConnectorState.validationContext()` into `NativeConnectorPort` and make the UI snapshot derivable from state. Run `state.test.ts`, `limits.test.ts`, and `port.test.ts`.

### Task 4: Trust-scoped hints, bounded reconnect, and real connector UI

**Files:** `web-extension/src/connector/hints.ts`, `web-extension/src/connector/reconnect.ts`, `web-extension/src/connector/view-model.ts`, `web-extension/src/connector.ts`, `web-extension/connector.html` (new); tests `test/connector/hints-reconnect.test.ts`, `test/connector/view-model.test.ts`.

**Interfaces:** `TrustHintSource` and `chooseReconnectTarget` as above; `ReconnectPolicy` with `attempts` capped at 3, delays `[1000, 2000, 4000]` ms, and `suppressed` when `intentionalClose || userDisconnect || shutdown || incompatibleApi || missingHost`; `buildViewModel(snapshot): ConnectorViewModel` and `guidanceFor(failure): Guidance | null`.

- [ ] **Step 1:** Write `hints-reconnect.test.ts` first with a fake `TrustHintSource`: no active hints → `{ kind:'search', reason:'none' }`; one active peer hint → `{ kind:'hint', peerId, hint }`; two different hints → `{ kind:'search', reason:'ambiguous' }`; invalid/empty hints are ignored. Assert the connector never writes `chrome.storage`, never calls `repository.authenticated`, and never constructs proof bytes on `connected`; the returned `platform_hint` stays `provisional` in state.
- [ ] **Step 2:** Add reconnect tests with fake timers: unsolicited link loss triggers at most 3 attempts with the declared delays; successful reconnect resets the counter; user `disconnectDevice`, `closePort`, `shutdown`, `incompatible_api`, and `missing_host` suppress reconnect; late timers after intentional close do nothing.
- [ ] **Step 3:** Implement `hints.ts` over `ChromeTrustRepository.activePeers()` (read-only), `reconnect.ts` with the bounded policy, and `view-model.ts` with startup/ready/scanning/devices/connections/errors/guidance states. `connected` hints render as “unverified hint”.
- [ ] **Step 4:** Write `view-model.test.ts` first: `missing_host` → install guidance and no BLE controls; `incompatible_api` → host/extension update guidance and disabled BLE controls; `scan_state.started` → scanning status and device list; `device_found` → searchable device row; `connected` → connection row with unverified hint; `timeout`/ambiguous send → explicit error with no automatic replay control. Read `connector.html` and assert the required element IDs exist.
- [ ] **Step 5:** Implement the actual UI in root `connector.html` and `src/connector.ts`: startup status, host/version status, search/stop scan, discovered device list with connect, connection list with disconnect, error banner/guidance, reconnect, shutdown, and a bounded diagnostics disclosure. No console logging of labels, hints, requests, or records.

### Task 5: Integration, packaged-browser simulation, and separate physical evidence

**Files:** `web-extension/test/connector/integration.test.ts`, `web-extension/test/connector/browser-connector.test.ts`, `web-extension/scripts/test-connector-browser.mjs` (new); `web-extension/test/bundle.test.ts`, `web-extension/package.json`; optional `docs/superpowers/plans/` progress only.

**Interfaces:** Integration uses `MockNativePort`, `ConnectorState`, `NativeConnectorPort`, and a fake `TrustHintSource`. The packaged-browser fixture imports the connector controller with a simulated `NativePortLike`; the production `connector.ts` still uses `chrome.runtime.connectNative`.

- [ ] **Step 1:** Write `integration.test.ts` first for: `hello`/`ready` with `api_version: 1`; `incompatible_api` fails closed with no BLE post; one scan → two valid devices → two connections → two sends routed to the correct handles; `message` delivered to the correct connection; async missing host via `onDisconnect` + `lastError`; port death synthesizes one `port_closed` per pending request; send timeout does not replay and a late `sent` does not resurrect the connection; duplicate terminal fails closed; intentional close suppresses reconnect.
- [ ] **Step 2:** Run the focused integration test; fix only integration wiring (not wire shapes). Confirm every posted message validates against the protocol schema and every test handle is 16 lowercase hex.
- [ ] **Step 3:** Add a Playwright test helper (`test/connector/browser-connector.test.ts` or script invoked by `test-connector-browser.mjs`) that loads the built extension in Chromium, injects a test-only connector fixture with a simulated transport, and asserts startup → scan → two devices → connect → message → error guidance through the real packaged HTML/UI. Add `npm run test:connector-browser`; keep it out of `npm test` and do not commit `dist/`.
- [ ] **Step 4:** Update `test/bundle.test.ts` for the actual post-build file set (including root `connector.html`/`connector.js` and any shared chunks) and the exact manifest round-trip. Update `package.json`'s explicit `test` list with every new Node test file. Do not add generated `dist/` files to git.
- [ ] **Step 5:** Write the physical smoke checklist as a separate manual evidence section (not an automated claim): on an actual Windows and macOS host, register the OTP-25 dev host, open the connector, verify one tab, search, connect two phones, send a synthetic whole record, observe `sent`, disconnect, close the tab, reopen by hint or search. Record OS, host build hash, extension build hash, device labels, and pass/fail per platform; do not call this covered by the simulated-browser test.
- [ ] **Step 6:** Verify with `npm ci --ignore-scripts`, `npm test`, `npm run test:bundle` (after `npm run build`), `npm run format:check`, `npm run lint`, `npm run typecheck`, `npm run build`, and inspect `dist/` for `manifest.json`, `connector.html`, `connector.js`, and no unexpected permissions. Run `npm run test:connector-browser` where Chromium is available; record any unavailable prerequisite rather than claiming success.

## Execution order

1. Task 1 establishes the manifest, root connector page, launcher, and build/test wiring.
2. Task 2 adds the strict wire/port core with the injected mock harness.
3. Task 3 adds separate scan/device/connection stores, bounds, generation, and idempotent close.
4. Task 4 adds trust-scoped hints, bounded reconnect, and the real UI.
5. Task 5 proves the full flow with Node integration, packaged-browser simulation, and a distinct physical smoke checklist.

Each task is test-first and independently reviewable. Do not start a later task until the earlier focused tests and the existing `npm test` list pass.

## Completion criteria

- Repeated action clicks focus one connector tab; the launcher is serialized and focuses the owning window.
- Manifest is exactly `storage` + `nativeMessaging`, no popup, no WAR/activeTab/tabs.
- Strict v1 validation and correlation reject malformed/cross-handle/duplicate terminals; no wire-only fields are invented.
- One scan lists multiple phones; scans, devices, and connections remain isolated; queues are bounded per phone and globally.
- Missing host, incompatible API, port closure, timeout ambiguity, and intentional close have the specified visible/local behaviour.
- Hints are peer-scoped and provisional; no fake authentication and no durable hint save on `connected`.
- `npm test` passes with the updated explicit list; manifest/bundle guards pass; packaged-browser simulation and physical smoke evidence are reported separately.
