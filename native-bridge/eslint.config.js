import js from '@eslint/js';
import ts from 'typescript-eslint';
import prettier from 'eslint-config-prettier/flat';

// No vite or react plugins: the native-bridge ships no browser UI. Scripts
// are plain Node ESM (.mjs); src/ is a small TypeScript manifest generator.
export default ts.config(
    js.configs.recommended,
    ...ts.configs.strict,
    ...ts.configs.stylistic,
    prettier,
    {
        ignores: ['node_modules/**', 'src-tauri/target/**', 'dist/**'],
    },
    {
        files: ['scripts/**/*.mjs'],
        languageOptions: {
            globals: {
                console: 'readonly',
                process: 'readonly',
                Buffer: 'readonly',
                URL: 'readonly',
            },
        },
    },
);
