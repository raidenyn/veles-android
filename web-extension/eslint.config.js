import js from '@eslint/js';
import ts from 'typescript-eslint';
import prettier from 'eslint-config-prettier/flat';

export default ts.config(
    js.configs.recommended,
    ...ts.configs.strict,
    ...ts.configs.stylistic,
    prettier,
    {
        ignores: ['dist/**', 'node_modules/**', 'coverage/**'],
    },
    // Node ES-module scripts (scripts/*.mjs) use Node globals (console,
    // process, Buffer, URL) that the default espree parser does not know
    // about. Declare the small set the packaging script touches rather than
    // pulling in the `globals` package.
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
