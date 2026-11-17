import { mkdtempSync, readFileSync, readdirSync, rmSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

// OTP-12 design D8: the scripted client must be a client of the protocol, not a
// connector. This test computes the transitive import closure of every TypeScript
// file in `test/gate-b/` (the `fixtures/` directory is excluded; the negative
// fixture below is scanned explicitly) and fails when the closure reaches a
// forbidden module, when any reached file mentions a forbidden identifier, when a
// relative import cannot be resolved, or when an unknown package is imported.
//
// DIVERGENCE RECORDED (design D8 fallback): package specifiers are a closed
// allow-list (`vitest` and `node:*`) and their internals are not scanned; every
// relative import in the closure is resolved and an unresolved one fails closed.
// This is the direct-import allow-list fallback for bare packages, which the spec
// permits when the full closure cannot be made clean without a production edit.
// No production edit was needed for the relative closure (`src/session`,
// `src/pairing` except `link.ts`, `src/trust/model.ts`, `rust-wasm/pkg`).

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB_EXTENSION = resolve(HERE, '../..');
const REPOSITORY = resolve(WEB_EXTENSION, '..');

const ALLOWED_PACKAGES = ['vitest'];

const FORBIDDEN_PREFIXES = [
    'src/connector/',
    'src/background',
    'src/offscreen',
    'src/content',
    'src/debug/',
];

const FORBIDDEN_FILES = ['src/pairing/link.ts', 'src/trust/storage.ts', 'src/trust/repository.ts'];

// Built by concatenation so this guard file does not itself trip the scan it performs.
const FORBIDDEN_IDENTIFIERS = ['chrome' + '.', 'device' + 'Handle', 'connection' + 'Handle'];

interface ScannedFile {
    path: string;
    text: string | null;
}

interface Closure {
    files: Map<string, ScannedFile>;
    violations: string[];
}

function isTextFile(path: string): boolean {
    return path.endsWith('.ts') || path.endsWith('.js') || path.endsWith('.mjs');
}

function readText(path: string): string | null {
    if (!isTextFile(path)) return null;
    try {
        return readFileSync(path, 'utf8');
    } catch {
        return null;
    }
}

function importSpecifiers(source: string): string[] {
    const specifiers: string[] = [];
    const staticImport = /(?:^|\n)\s*(?:import|export)\s+[^;]*?from\s*['"]([^'"]+)['"]/g;
    const sideEffectImport = /(?:^|\n)\s*import\s*['"]([^'"]+)['"]/g;
    const dynamicImport = /import\s*\(\s*['"]([^'"]+)['"]\s*\)/g;
    for (const pattern of [staticImport, sideEffectImport, dynamicImport]) {
        for (const match of source.matchAll(pattern)) {
            if (match[1] !== undefined) specifiers.push(match[1]);
        }
    }
    return specifiers;
}

/** Resolves one relative import to an existing file, or null when it does not resolve. */
function resolveRelativeSpecifier(fromFile: string, specifier: string): string | null {
    const base = resolve(dirname(fromFile), specifier.split('?')[0] ?? specifier);
    const candidates = [base, `${base}.ts`, `${base}.js`, `${base}.mjs`, join(base, 'index.ts')];
    for (const candidate of candidates) {
        try {
            if (statSync(candidate).isFile()) return candidate;
        } catch {
            // Try the next candidate.
        }
    }
    return null;
}

function isAllowedPackage(specifier: string): boolean {
    return ALLOWED_PACKAGES.includes(specifier) || specifier.startsWith('node:');
}

function inRepository(path: string): string {
    return relative(REPOSITORY, path).replaceAll('\\', '/');
}

function closureOf(entries: string[]): Closure {
    const files = new Map<string, ScannedFile>();
    const violations: string[] = [];
    const queue = entries.map((entry) => resolve(entry));
    while (queue.length > 0) {
        const path = queue.pop();
        if (path === undefined || files.has(path)) continue;
        const text = readText(path);
        files.set(path, { path, text });
        if (text === null) continue;
        for (const specifier of importSpecifiers(text)) {
            if (specifier.startsWith('.')) {
                const resolved = resolveRelativeSpecifier(path, specifier);
                if (resolved === null) {
                    violations.push(`${inRepository(path)}: unresolved import '${specifier}'`);
                } else if (!files.has(resolved)) {
                    queue.push(resolved);
                }
                continue;
            }
            if (!isAllowedPackage(specifier)) {
                violations.push(`${inRepository(path)}: unknown package '${specifier}'`);
            }
        }
    }
    return { files, violations };
}

function violationsOf(closure: Closure): string[] {
    const violations = [...closure.violations];
    for (const file of closure.files.values()) {
        const inExtension = relative(WEB_EXTENSION, file.path).replaceAll('\\', '/');
        const repositoryPath = inRepository(file.path);
        const forbiddenPath =
            FORBIDDEN_PREFIXES.some((prefix) => inExtension.startsWith(prefix)) ||
            FORBIDDEN_FILES.includes(inExtension) ||
            repositoryPath.startsWith('native-bridge/');
        if (forbiddenPath) violations.push(`${repositoryPath}: forbidden module`);
        if (file.text === null) continue;
        for (const identifier of FORBIDDEN_IDENTIFIERS) {
            if (file.text.includes(identifier)) {
                violations.push(`${repositoryPath}: mentions ${identifier}`);
            }
        }
    }
    return violations;
}

function clientEntries(): string[] {
    return readdirSync(HERE)
        .filter((name) => name.endsWith('.ts'))
        .map((name) => join(HERE, name));
}

describe('gate-b scripted-client import guard', () => {
    it('closure_of_client_files_avoids_forbidden_modules', () => {
        const closure = closureOf(clientEntries());
        const paths = [...closure.files.keys()].map((path) => inRepository(path));
        expect(paths).toContain('web-extension/test/gate-b/transport.ts');
        expect(paths).toContain('web-extension/test/gate-b/trust.ts');
        expect(paths).toContain('web-extension/src/session/engine.ts');
        expect(violationsOf(closure)).toEqual([]);
    });

    it('negative_fixture_is_detected', () => {
        const closure = closureOf([join(HERE, 'fixtures/forbidden-import.ts')]);
        const violations = violationsOf(closure);
        expect(violations.some((line) => line.includes('src/connector'))).toBe(true);
    });

    it('unresolved_relative_import_is_detected', () => {
        const directory = mkdtempSync(join(tmpdir(), 'gate-b-guard-'));
        try {
            const entry = join(directory, 'entry.ts');
            writeFileSync(
                entry,
                "import { missing } from './missing';\nexport const value = missing;\n",
            );
            const closure = closureOf([entry]);
            expect(
                closure.violations.some((line) => line.includes("unresolved import './missing'")),
            ).toBe(true);
        } finally {
            rmSync(directory, { recursive: true, force: true });
        }
    });
});
