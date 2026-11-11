import { defineConfig } from 'vitest/config';
import type { Plugin } from 'vite';
import { resolve } from 'node:path';
import { readFileSync } from 'node:fs';
import {
    buildExtensionManifest,
    guardProductionIdentity,
    resolveReleaseIdentity,
    type ReleaseIdentity,
} from './src/manifest';

const GUARD_BUILD = process.env.VELES_GUARD_BUILD === '1';

// A release build points VELES_RELEASE_CONFIG at the approved release config (the one the
// host packaging uses) and must pass its production identity; a plain build keeps the
// explicit development default. An unreadable or invalid config fails the build.
//
// The guard build (`VELES_GUARD_BUILD=1`) is a production-channel build with a fixed,
// test-only identity from manifest.ts. It never reads VELES_RELEASE_CONFIG and writes to
// ../build/web-extension-guard/ so the development dist/ used for physical verification is
// untouched.
function buildIdentity(): ReleaseIdentity {
    if (GUARD_BUILD) return guardProductionIdentity();
    const path = process.env.VELES_RELEASE_CONFIG;
    if (path === undefined || path === '') return resolveReleaseIdentity(undefined);
    return resolveReleaseIdentity(JSON.parse(readFileSync(path, 'utf8')), {
        requireProduction: true,
    });
}
const identity = buildIdentity();

function emitManifest(): Plugin {
    return {
        name: 'veles-emit-manifest',
        generateBundle() {
            this.emitFile({
                type: 'asset',
                fileName: 'manifest.json',
                source: JSON.stringify(buildExtensionManifest(), null, 2) + '\n',
            });
        },
    };
}

export default defineConfig({
    plugins: [emitManifest()],
    // A build-time constant, never a runtime lookup: a production build folds the
    // development guard and tree-shakes the whole debug/probe chunk.
    define: {
        __VELES_CHANNEL__: JSON.stringify(identity.channel),
    },
    test: {
        setupFiles: ['./test/setup.ts'],
        environment: 'node',
    },
    build: {
        outDir: GUARD_BUILD ? '../build/web-extension-guard' : 'dist',
        emptyOutDir: true,
        sourcemap: false,
        minify: false,
        rollupOptions: {
            preserveEntrySignatures: 'strict',
            input: {
                background: resolve(__dirname, 'src/background.ts'),
                connector: resolve(__dirname, 'connector.html'),
                offscreen: resolve(__dirname, 'offscreen.html'),
                content: resolve(__dirname, 'src/content.ts'),
                options: resolve(__dirname, 'options.html'),
                popup: resolve(__dirname, 'popup.html'),
                trust: resolve(__dirname, 'src/trust/repository.ts'),
            },
            output: {
                entryFileNames: '[name].js',
                chunkFileNames: 'chunks/[name]-[hash].js',
                assetFileNames: 'assets/[name]-[hash][extname]',
            },
        },
    },
});
