import { vi } from 'vitest';

// MV3 APIs are browser-globals. Stub the minimum surface background.ts uses
// so the module can be imported under vitest's Node environment.
vi.stubGlobal('chrome', {
    runtime: {
        onInstalled: { addListener: vi.fn() },
        getManifest: vi.fn(),
    },
});
