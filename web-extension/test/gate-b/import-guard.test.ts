import { readFileSync, readdirSync, statSync } from 'node:fs';
import { dirname, join, relative, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

import { describe, expect, it } from 'vitest';

// OTP-12 design D8: the scripted client must be a client of the protocol, not a
// connector. This test computes the transitive relative-import closure of every
// TypeScript file in `test/gate-b/` (the `fixtures/` directory is excluded; the
// negative fixture below is scanned explicitly) and fails when the closure reaches
// a forbidden module or when any reached file mentions a forbidden identifier.
//
// It runs the direct-closure form, not the allow-list fallback: `src/session`,
// `src/pairing` (except `link.ts`), `src/trust/model.ts` and `rust-wasm/pkg` all
// resolve cleanly. If a future production edit makes that impossible, the fallback
// documented in design D8 is a direct-import allow-list plus this identifier scan,
// and the divergence must be recorded here and in the apply report.

const HERE = dirname(fileURLToPath(import.meta.url));
const WEB_EXTENSION = resolve(HERE, '../..');
const REPOSITORY = resolve(WEB_EXTENSION, '..');

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

function resolveSpecifier(fromFile: string, specifier: string): string | null {
    if (!specifier.startsWith('.')) return null;
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

function closureOf(entries: string[]): Map<string, ScannedFile> {
    const scanned = new Map<string, ScannedFile>();
    const queue = entries.map((entry) => resolve(entry));
    while (queue.length > 0) {
        const path = queue.pop();
        if (path === undefined || scanned.has(path)) continue;
        const text = readText(path);
        scanned.set(path, { path, text });
        if (text === null) continue;
        for (const specifier of importSpecifiers(text)) {
            const resolved = resolveSpecifier(path, specifier);
            if (resolved !== null && !scanned.has(resolved)) queue.push(resolved);
        }
    }
    return scanned;
}

function violationsOf(scanned: Map<string, ScannedFile>): string[] {
    const violations: string[] = [];
    for (const file of scanned.values()) {
        const inExtension = relative(WEB_EXTENSION, file.path).replaceAll('\\', '/');
        const inRepository = relative(REPOSITORY, file.path).replaceAll('\\', '/');
        const forbiddenPath =
            FORBIDDEN_PREFIXES.some((prefix) => inExtension.startsWith(prefix)) ||
            FORBIDDEN_FILES.includes(inExtension) ||
            inRepository.startsWith('native-bridge/');
        if (forbiddenPath) violations.push(`${inRepository}: forbidden module`);
        if (file.text === null) continue;
        for (const identifier of FORBIDDEN_IDENTIFIERS) {
            if (file.text.includes(identifier)) {
                violations.push(`${inRepository}: mentions ${identifier}`);
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
        const scanned = closureOf(clientEntries());
        const paths = [...scanned.keys()].map((path) =>
            relative(REPOSITORY, path).replaceAll('\\', '/'),
        );
        expect(paths).toContain('web-extension/test/gate-b/transport.ts');
        expect(paths).toContain('web-extension/test/gate-b/trust.ts');
        expect(violationsOf(scanned)).toEqual([]);
    });

    it('negative_fixture_is_detected', () => {
        const scanned = closureOf([join(HERE, 'fixtures/forbidden-import.ts')]);
        const violations = violationsOf(scanned);
        expect(violations.some((line) => line.includes('src/connector'))).toBe(true);
    });
});
