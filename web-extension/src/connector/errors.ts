// Connector-only terminal failures.
//
// These values are never serialized to the native host. `code` is not limited
// to the wire `error` enum: missing-host, local port closure, and local
// protocol violations have no host-originated representation. A synthesized
// failure follows OTP2-BRD-020 where the wire document fixes its fields (port
// closure is wire code `internal` with stage `port_closed`).

import type { PendingKind, WireErrorCode } from './protocol';

export type ConnectorFailureCode =
    | WireErrorCode
    | 'missing_host'
    | 'port_closed'
    | 'protocol_violation'
    | 'authentication_failed';

export interface ConnectorFailure {
    type: 'error';
    request_id?: string;
    code: ConnectorFailureCode;
    recoverability: 'retryable' | 'fatal';
    stage: string;
    synthesized: true;
}

export function synthesizeMissingHost(
    lastError?: { message?: string } | undefined,
): ConnectorFailure {
    // The browser message is classification input only; it is deliberately
    // not retained in the failure or in diagnostics.
    void lastError;
    return {
        type: 'error',
        code: 'missing_host',
        recoverability: 'fatal',
        stage: 'missing_host',
        synthesized: true,
    };
}

export function synthesizePortClosed(kind: PendingKind): ConnectorFailure {
    // OTP2-BRD-020 fixes the synthesized port-loss fields.
    void kind;
    return {
        type: 'error',
        code: 'internal',
        recoverability: 'fatal',
        stage: 'port_closed',
        synthesized: true,
    };
}

export function synthesizeTimeout(kind: PendingKind): ConnectorFailure {
    void kind;
    return {
        type: 'error',
        code: 'timeout',
        recoverability: 'retryable',
        stage: 'timeout',
        synthesized: true,
    };
}

export function synthesizeIncompatibleApi(): ConnectorFailure {
    return {
        type: 'error',
        code: 'incompatible_api',
        recoverability: 'fatal',
        stage: 'negotiation',
        synthesized: true,
    };
}

export function synthesizeProtocolViolation(stage: string): ConnectorFailure {
    return {
        type: 'error',
        code: 'protocol_violation',
        recoverability: 'fatal',
        stage,
        synthesized: true,
    };
}

export function synthesizeInternal(stage: string): ConnectorFailure {
    return {
        type: 'error',
        code: 'internal',
        recoverability: 'fatal',
        stage,
        synthesized: true,
    };
}

export function synthesizeCapExceeded(): ConnectorFailure {
    return {
        type: 'error',
        code: 'cap_exceeded',
        recoverability: 'retryable',
        stage: 'cap_exceeded',
        synthesized: true,
    };
}

export function synthesizeSendFailed(stage: string): ConnectorFailure {
    return {
        type: 'error',
        code: 'send_failed',
        recoverability: 'fatal',
        stage,
        synthesized: true,
    };
}

export function synthesizeUnknownHandle(stage = 'connection_handle'): ConnectorFailure {
    return {
        type: 'error',
        code: 'unknown_handle',
        recoverability: 'fatal',
        stage,
        synthesized: true,
    };
}

/**
 * The single reason-free client failure for every unsuccessful reconnect
 * authentication (bad signature, bad confirmation, rejected or revoked peer,
 * or no unambiguous intended peer). It deliberately distinguishes none of
 * them: the OTP2-REV-001 path must look identical to any other failure.
 */
export function synthesizeAuthenticationFailed(): ConnectorFailure {
    return {
        type: 'error',
        code: 'authentication_failed',
        recoverability: 'retryable',
        stage: 'authentication',
        synthesized: true,
    };
}
