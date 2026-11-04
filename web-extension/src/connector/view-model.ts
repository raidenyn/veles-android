// Pure presentation model for the connector page.
//
// The UI never interprets raw host events or trust records directly. This
// module turns a controller snapshot plus a small amount of UI lifecycle state
// into bounded, already-labelled rows and explicit guidance. No dynamic string
// here is HTML: the DOM renderer always assigns it with `textContent`.

import type { ConnectorFailure } from './errors';
import type { WireEvent } from './protocol';
import type { ConnectionStatus, ConnectorSnapshot } from './state';

export type FailureLike = ConnectorFailure | Extract<WireEvent, { type: 'error' }>;

export type ConnectorPhase =
    | 'starting'
    | 'connecting_host'
    | 'ready'
    | 'failed'
    | 'disconnected'
    | 'shut_down';

export interface ReadySummary {
    apiVersion: number;
    bridgeVersion: string;
    platform: 'windows' | 'macos';
    capabilities: readonly string[];
}

export interface ReconnectView {
    attempts: number;
    maxAttempts: number;
    scheduled: boolean;
    inFlight: boolean;
    suppressed: boolean;
}

export interface ViewModelContext {
    phase?: ConnectorPhase;
    ready?: ReadySummary | null;
    failure?: FailureLike | null;
    reconnect?: ReconnectView | null;
    macPermissionDenied?: boolean;
    shuttingDown?: boolean;
}

export type GuidanceKind =
    | 'missing_host'
    | 'incompatible_api'
    | 'macos_permission'
    | 'port_closed'
    | 'timeout'
    | 'ambiguous_send'
    | 'busy'
    | 'retryable'
    | 'fatal';

export interface Guidance {
    kind: GuidanceKind;
    title: string;
    message: string;
    actionLabel: string | null;
    canRetry: boolean;
    bleControlsEnabled: boolean;
    /** True when there is deliberately no control that re-sends a record. */
    noAutomaticReplay: boolean;
}

export interface ControlState {
    enabled: boolean;
    visible: boolean;
    label: string;
}

export interface ConnectorControls {
    ble: boolean;
    search: ControlState;
    stopScan: ControlState;
    reconnect: ControlState;
    shutdown: ControlState;
}

export interface DeviceRow {
    handle: string;
    label: string;
    platformHint?: string;
    hintLabel: string | null;
    canConnect: boolean;
}

export interface ConnectionRow {
    handle: string;
    label: string;
    status: ConnectionStatus;
    platformHint?: string;
    hintLabel: string | null;
    queuedOperations: number;
    queuedBytes: number;
    canDisconnect: boolean;
}

export interface DiagnosticRow {
    at: number;
    text: string;
}

export interface ConnectorHostView {
    state: 'starting' | 'connecting' | 'ready' | 'failed' | 'closed';
    platform?: 'windows' | 'macos';
    bridgeVersion?: string;
    apiVersion?: number;
    capabilities: readonly string[];
}

export interface ConnectorErrorView {
    message: string;
    code: string;
    stage: string;
    recoverable: boolean;
}

export interface ConnectorViewModel {
    phase: ConnectorPhase;
    statusText: string;
    hostStatusText: string;
    scanStatusText: string;
    reconnectStatusText: string | null;
    host: ConnectorHostView;
    guidance: Guidance | null;
    error: ConnectorErrorView | null;
    controls: ConnectorControls;
    devices: DeviceRow[];
    connections: ConnectionRow[];
    diagnostics: DiagnosticRow[];
}

const UI_DIAGNOSTICS_MAX = 50;
const HINT_DISPLAY_MAX = 80;

interface GuidanceContext {
    platform?: 'windows' | 'macos' | null;
    macPermissionDenied?: boolean;
}

function hintLabel(hint: string | undefined): string | null {
    if (hint === undefined || hint.length === 0) return null;
    const display = hint.length > HINT_DISPLAY_MAX ? `${hint.slice(0, HINT_DISPLAY_MAX)}…` : hint;
    return `Unverified hint: ${display}`;
}

function macPermissionGuidance(): Guidance {
    return {
        kind: 'macos_permission',
        title: 'Allow Bluetooth access on this Mac',
        message:
            'macOS blocked Bluetooth access for the Veles Native Bridge. In System Settings > Privacy & Security > Bluetooth, allow Bluetooth access for the Veles Native Bridge on this Mac, then choose Retry.',
        actionLabel: 'Retry',
        canRetry: true,
        // Retry performs a real scan/access probe, and Search can be used as
        // an explicit probe while this guidance remains sticky.
        bleControlsEnabled: true,
        noAutomaticReplay: false,
    };
}

function missingHostGuidance(): Guidance {
    return {
        kind: 'missing_host',
        title: 'Native bridge not installed',
        message:
            'The Veles Native Bridge host is missing or not registered. Install the compatible Veles Native Bridge package for this computer, then choose Retry.',
        actionLabel: 'Retry',
        canRetry: true,
        bleControlsEnabled: false,
        noAutomaticReplay: false,
    };
}

function incompatibleGuidance(): Guidance {
    return {
        kind: 'incompatible_api',
        title: 'Update required',
        message:
            'The installed Veles Native Bridge and this extension do not share a supported API version. Update the Veles Native Bridge and/or the extension, then choose Retry.',
        actionLabel: 'Retry',
        canRetry: true,
        bleControlsEnabled: false,
        noAutomaticReplay: false,
    };
}

export function guidanceFor(
    failure: FailureLike | null | undefined,
    context: GuidanceContext = {},
): Guidance | null {
    if (failure?.code === 'missing_host') return missingHostGuidance();
    if (failure?.code === 'incompatible_api') return incompatibleGuidance();

    const macDenied =
        context.macPermissionDenied === true ||
        (failure !== null &&
            failure !== undefined &&
            (failure.stage === 'access_denied' || failure.stage === 'permission_pending'));
    if (context.platform === 'macos' && macDenied) return macPermissionGuidance();

    if (failure === null || failure === undefined) return null;

    if (
        failure.code === 'port_closed' ||
        (failure.code === 'internal' && failure.stage === 'port_closed')
    ) {
        return {
            kind: 'port_closed',
            title: 'Native bridge disconnected',
            message:
                'The Native Messaging connection closed. Choose Reconnect to start the local bridge again. No queued record is replayed.',
            actionLabel: 'Reconnect',
            canRetry: true,
            bleControlsEnabled: false,
            noAutomaticReplay: true,
        };
    }
    if (
        failure.code === 'timeout' ||
        (failure.code === 'send_failed' && failure.stage === 'timeout') ||
        (failure.code === 'send_failed' && failure.stage === 'ambiguous_send')
    ) {
        return {
            kind: failure.code === 'timeout' ? 'timeout' : 'ambiguous_send',
            title: 'Delivery state unknown',
            message:
                'The bridge did not confirm this operation. The request was not replayed automatically. Choose Reconnect or search again; do not assume delivery.',
            actionLabel: 'Reconnect',
            canRetry: true,
            bleControlsEnabled: false,
            noAutomaticReplay: true,
        };
    }
    if (failure.code === 'cap_exceeded' || failure.code === 'busy') {
        return {
            kind: 'busy',
            title: 'Bridge is busy',
            message:
                'The bridge temporarily has no room for another operation. Wait a moment, then try again.',
            actionLabel: 'Retry',
            canRetry: true,
            bleControlsEnabled: true,
            noAutomaticReplay: false,
        };
    }
    if (failure.recoverability === 'retryable') {
        return {
            kind: 'retryable',
            title: 'Operation failed',
            message: `The bridge reported ${failure.code}. The request was not replayed automatically. Try the action again.`,
            actionLabel: 'Retry',
            canRetry: true,
            bleControlsEnabled: true,
            noAutomaticReplay: true,
        };
    }
    return {
        kind: 'fatal',
        title: 'Bridge error',
        message: `The bridge reported ${failure.code}. Choose Retry to restart the local bridge.`,
        actionLabel: 'Retry',
        canRetry: true,
        bleControlsEnabled: false,
        noAutomaticReplay: true,
    };
}

function errorMessage(failure: FailureLike): string {
    switch (failure.code) {
        case 'missing_host':
            return 'The Veles Native Bridge host is not installed or registered.';
        case 'incompatible_api':
            return 'The Veles Native Bridge API version is incompatible with this extension.';
        case 'port_closed':
            return 'The Native Messaging port closed.';
        case 'timeout':
            return 'The bridge did not answer before the request timed out.';
        case 'send_failed':
            return failure.stage === 'ambiguous_send'
                ? 'Delivery could not be confirmed; the request was not replayed.'
                : 'The bridge could not send the operation.';
        case 'scan_failed':
            return failure.stage === 'access_denied' || failure.stage === 'permission_pending'
                ? 'Bluetooth access was not granted, so the phone search stopped.'
                : 'The bridge could not complete the phone search.';
        case 'connect_failed':
            return 'The bridge could not connect to the selected phone.';
        case 'cap_exceeded':
            return 'The bridge or connector is at its operation limit.';
        case 'unknown_handle':
            return 'The selected phone is no longer connected.';
        case 'protocol_violation':
            return 'The bridge sent an unexpected or correlated-invalid message.';
        case 'internal':
            return failure.stage === 'port_closed'
                ? 'The Native Messaging port closed.'
                : 'The connector encountered an internal bridge error.';
        default:
            return `The bridge reported ${failure.code} during ${failure.stage}.`;
    }
}

function formatDiagnostic(at: number, direction: string, type: string, extra: string): string {
    const stamp = Number.isFinite(at) ? new Date(at).toISOString().slice(11, 19) : '--:--:--';
    return extra.length > 0
        ? `${stamp} ${direction} ${type} ${extra}`
        : `${stamp} ${direction} ${type}`;
}

function diagnosticText(entry: ConnectorSnapshot['diagnostics'][number]): string {
    const parts: string[] = [];
    if (entry.code !== undefined) parts.push(`code=${entry.code}`);
    if (entry.stage !== undefined) parts.push(`stage=${entry.stage}`);
    if (entry.scan_handle !== undefined) parts.push(`scan=${entry.scan_handle}`);
    if (entry.device_handle !== undefined) parts.push(`device=${entry.device_handle}`);
    if (entry.connection_handle !== undefined) parts.push(`connection=${entry.connection_handle}`);
    if (entry.bytes !== undefined) parts.push(`bytes=${entry.bytes}`);
    return formatDiagnostic(entry.at, entry.direction, entry.type, parts.join(' '));
}

function derivePhase(context: ViewModelContext, ready: ReadySummary | null): ConnectorPhase {
    if (context.phase !== undefined) return context.phase;
    if (context.shuttingDown === true) return 'shut_down';
    if (ready !== null) return 'ready';
    return context.failure === null || context.failure === undefined ? 'starting' : 'failed';
}

function hostText(
    phase: ConnectorPhase,
    ready: ReadySummary | null,
    failure: FailureLike | null,
): string {
    if (ready !== null) {
        return `Bridge ${ready.bridgeVersion} · ${ready.platform === 'macos' ? 'macOS' : 'Windows'} · API ${ready.apiVersion}`;
    }
    if (failure?.code === 'missing_host') return 'Native bridge not installed';
    if (failure?.code === 'incompatible_api') return 'Native bridge incompatible';
    if (phase === 'connecting_host') return 'Connecting to the local bridge…';
    if (phase === 'shut_down') return 'Bridge shut down';
    if (phase === 'disconnected' || (failure !== null && failure !== undefined)) {
        return 'Bridge disconnected';
    }
    return 'Starting connector…';
}

export function buildViewModel(
    snapshot: ConnectorSnapshot,
    context: ViewModelContext = {},
): ConnectorViewModel {
    const ready = context.ready ?? null;
    const failure = context.failure ?? null;
    const phase = derivePhase(context, ready);
    const guidance = guidanceFor(failure, {
        platform: ready?.platform ?? null,
        macPermissionDenied: context.macPermissionDenied === true,
    });

    const bleAllowed =
        (phase === 'ready' || (phase === 'failed' && guidance?.bleControlsEnabled === true)) &&
        guidance?.bleControlsEnabled !== false &&
        context.shuttingDown !== true;

    const activeScan = snapshot.scans.find(
        (scan) => scan.status === 'started' || scan.status === 'stopping',
    );
    const hasRunningScan = snapshot.scans.some(
        (scan) => scan.status === 'starting' || scan.status === 'started',
    );

    const connectedDevices = new Set(
        snapshot.connections
            .map((connection) => connection.deviceHandle)
            .filter((handle): handle is string => handle !== undefined),
    );

    const devices = snapshot.devices.map((device) => ({
        handle: device.handle,
        label: device.label,
        ...(device.platformHint === undefined ? {} : { platformHint: device.platformHint }),
        hintLabel: hintLabel(device.platformHint),
        canConnect: bleAllowed && !connectedDevices.has(device.handle),
    }));

    const devicesByHandle = new Map(snapshot.devices.map((device) => [device.handle, device]));
    const connections = snapshot.connections.map((connection) => {
        const device =
            connection.deviceHandle === undefined
                ? undefined
                : devicesByHandle.get(connection.deviceHandle);
        return {
            handle: connection.handle,
            label: device?.label ?? 'Paired phone',
            status: connection.status,
            ...(connection.platformHint === undefined
                ? {}
                : { platformHint: connection.platformHint }),
            hintLabel: hintLabel(connection.platformHint),
            queuedOperations: connection.queuedOperations,
            queuedBytes: connection.queuedBytes,
            canDisconnect: bleAllowed && connection.status !== 'disconnecting',
        };
    });

    let scanStatusText = 'Not searching.';
    if (activeScan !== undefined) {
        const label = activeScan.status === 'stopping' ? 'Stopping search' : 'Searching';
        scanStatusText = `${label} · ${activeScan.deviceCount} phone${activeScan.deviceCount === 1 ? '' : 's'} found.`;
    } else if (snapshot.scans.some((scan) => scan.status === 'starting')) {
        scanStatusText = 'Starting search…';
    }

    const reconnect = context.reconnect ?? null;
    const reconnectStatusText =
        reconnect === null
            ? null
            : reconnect.inFlight
              ? `Reconnect attempt ${Math.min(reconnect.attempts, reconnect.maxAttempts)} of ${reconnect.maxAttempts} in progress.`
              : reconnect.scheduled
                ? `Reconnect attempt ${Math.min(reconnect.attempts + 1, reconnect.maxAttempts)} of ${reconnect.maxAttempts} pending.`
                : reconnect.attempts > 0
                  ? `Reconnect stopped after ${reconnect.attempts} of ${reconnect.maxAttempts} attempts.`
                  : null;

    const actionLabel =
        guidance?.kind === 'missing_host' ||
        guidance?.kind === 'incompatible_api' ||
        guidance?.kind === 'macos_permission'
            ? 'Retry'
            : 'Reconnect';

    const statusText =
        guidance?.title ??
        (phase === 'ready'
            ? 'Connector ready.'
            : phase === 'connecting_host'
              ? 'Connecting to the Veles Native Bridge…'
              : phase === 'shut_down'
                ? 'Native bridge shut down.'
                : 'Starting connector…');

    return {
        phase,
        statusText,
        hostStatusText: hostText(phase, ready, failure),
        scanStatusText,
        reconnectStatusText,
        host: {
            state:
                phase === 'ready'
                    ? 'ready'
                    : phase === 'connecting_host'
                      ? 'connecting'
                      : phase === 'starting'
                        ? 'starting'
                        : phase === 'failed'
                          ? 'failed'
                          : 'closed',
            ...(ready === null
                ? {}
                : {
                      platform: ready.platform,
                      bridgeVersion: ready.bridgeVersion,
                      apiVersion: ready.apiVersion,
                  }),
            capabilities: ready?.capabilities ?? [],
        },
        guidance,
        error:
            failure === null
                ? null
                : {
                      message: errorMessage(failure),
                      code: failure.code,
                      stage: failure.stage,
                      recoverable: failure.recoverability === 'retryable',
                  },
        controls: {
            ble: bleAllowed,
            search: {
                enabled: bleAllowed && !hasRunningScan,
                visible: true,
                label: hasRunningScan ? 'Search running' : 'Search for phones',
            },
            stopScan: {
                enabled:
                    bleAllowed &&
                    activeScan !== undefined &&
                    activeScan.status === 'started' &&
                    !activeScan.locallyExpired,
                visible: activeScan !== undefined,
                label: 'Stop search',
            },
            reconnect: {
                enabled: context.shuttingDown !== true,
                visible: true,
                label: actionLabel,
            },
            shutdown: {
                enabled: context.shuttingDown !== true && phase !== 'shut_down',
                visible: true,
                label: 'Shut down',
            },
        },
        devices,
        connections,
        diagnostics: snapshot.diagnostics
            .slice(-UI_DIAGNOSTICS_MAX)
            .map((entry) => ({ at: entry.at, text: diagnosticText(entry) })),
    };
}
