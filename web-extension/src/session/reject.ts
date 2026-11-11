import type { AppErrorCode } from './wire';

/**
 * Generic session failure. It deliberately carries no message text; `code` is
 * the safe OTP2-ENC-021 code to send when the failure is reportable as an
 * `app_error`, if any.
 */
export class SessionReject extends Error {
    constructor(readonly code?: AppErrorCode) {
        super('session rejected');
        this.name = 'SessionReject';
    }
}
