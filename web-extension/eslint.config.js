import js from '@eslint/js';
import ts from 'typescript-eslint';
import prettier from 'eslint-config-prettier/flat';

// No vite or react eslint plugins are configured here, intentionally:
//   - The extension uses plain TypeScript (no React/JSX), so there is nothing
//     for eslint-plugin-react-hooks / eslint-plugin-react to lint. Add them
//     (and a JSX-aware parser) if/when React components are introduced.
//   - There is no published `eslint-plugin-vite`; vite.config.ts is a small
//     config file that the standard TS rules already cover. Vite does not
//     require a dedicated eslint plugin for its config files.
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
