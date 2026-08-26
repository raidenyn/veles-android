// MV3 service worker entry — skeleton for OTP-01 1a.
// Real connector/offscreen lifecycle lands in later OTP tasks.
//
// The chrome-global guard exists so vitest (Node environment) can import this
// module without a browser runtime; the test setup stubs chrome.runtime anyway,
// but defensive coding here prevents a hard ReferenceError if the stub is
// missed in a future test file.
if (typeof chrome !== 'undefined' && chrome.runtime?.onInstalled) {
    chrome.runtime.onInstalled.addListener(() => {
        // no-op placeholder
    });
}

export {};
