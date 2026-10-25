import { ChromeLocalStore } from './trust/storage';

// Restrict on every worker load, not only installation/update. Repository
// operations independently await restriction before reading or writing trust.
// A denied restriction never exposes a repository; callers fail closed.
export const trustStorageReady =
    typeof chrome !== 'undefined' && chrome.storage?.local
        ? new ChromeLocalStore().restrict().then(
              () => true,
              () => false,
          )
        : Promise.resolve(false);

// MV3 service worker entry.
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
