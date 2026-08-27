import { defineConfig } from 'vitest/config';
import type { Plugin } from 'vite';
import { resolve } from 'node:path';
import { buildExtensionManifest } from './src/manifest';

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
            input: {
                background: resolve(__dirname, 'src/background.ts'),
                content: resolve(__dirname, 'src/content.ts'),
                options: resolve(__dirname, 'options.html'),
                popup: resolve(__dirname, 'popup.html'),
            },
            output: {
                entryFileNames: '[name].js',
                chunkFileNames: 'chunks/[name]-[hash].js',
                assetFileNames: 'assets/[name]-[hash][extname]',
            },
        },
    },
});
