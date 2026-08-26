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
);
