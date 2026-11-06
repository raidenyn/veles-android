import { defineConfig } from 'vitest/config';
import type { Plugin } from 'vite';
import { resolve } from 'node:path';
import { readFileSync } from 'node:fs';
import { buildExtensionManifest, resolveReleaseIdentity } from './src/manifest';

// A release build points VELES_RELEASE_CONFIG at the approved release config (the one the
// host packaging uses) and must pass its production identity; a plain build keeps the
// explicit development default. An unreadable or invalid config fails the build.
function validateReleaseIdentity(): void {
    const path = process.env.VELES_RELEASE_CONFIG;
    if (path === undefined || path === '') return;
    resolveReleaseIdentity(JSON.parse(readFileSync(path, 'utf8')), { requireProduction: true });
}
validateReleaseIdentity();

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
    test: {
        setupFiles: ['./test/setup.ts'],
        environment: 'node',
    },
    build: {
        outDir: 'dist',
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
