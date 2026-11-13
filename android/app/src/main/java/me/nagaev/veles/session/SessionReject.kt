package me.nagaev.veles.session

/**
 * Generic session failure. It deliberately carries no message text; [code] is the safe
 * OTP2-ENC-021 code to send when the failure is reportable as an `app_error`, if any.
 */
class SessionReject(val code: AppErrorCode? = null) : Exception()
