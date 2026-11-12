#!/usr/bin/env node
// OTP-10 task 4.6: fail on a direct comparison over MAC material.
//
// Both endpoints must compare authenticators with a constant-time helper:
// Kotlin `java.security.MessageDigest.isEqual` and TypeScript
// `equalConstantTime` (`web-extension/src/session/engine.ts`). A direct `==`,
// `===`, `!=`, `!==`, `contentEquals` (call or infix), `Arrays.equals` or
// `equals` over a MAC leaks through timing, so this grep-style rule rejects it
// in production sources. Fingerprint comparisons in the enrollment code also
// use the constant-time helper, but the rule is MAC-scoped: the OTP-05 trust
// repository compares stored hex fingerprints directly and is outside this
// task. Test sources are outside the scanned roots on purpose: negative
// vectors must be able to write the forbidden form.
//
// Usage:
//   node scripts/check-mac-comparisons.mjs [--root DIR]
//   node scripts/check-mac-comparisons.mjs --file FILE [--file FILE ...]
//
// Exit 0 when clean, 1 with `path:line: text` lines on violations.

import { existsSync, readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';

const DEFAULT_ROOTS = ['app/src/main', 'web-extension/src'];
const SKIPPED_DIRECTORIES = new Set(['.git', 'build', 'coverage', 'dist', 'node_modules']);
const SOURCE_EXTENSIONS = new Set(['.kt', '.ts']);

// Constant-time helpers: a call to one of these is exempt, but only the call
// itself. `MessageDigest.isEqual(a, b) && mac == other` still fails.
const HELPER_CALL = /(?:MessageDigest\.isEqual|equalConstantTime|crypto\.timingSafeEqual|equalBytes)\s*\(/g;

// Authenticator identifiers: `mac`, `expectedMac`, `received_mac`, `macBytes`,
// `mac_hex`, `MAC_BYTES`, `activationProof`, `tag`, `confirmation`, any casing.
// The identifier must end in the token (with only the narrow byte/hex suffix
// allowed), so an unrelated `macaroon`, `macPermissionDenied` or the
// `MAC_BODY_BYTES` length constant is not an authenticator.
const AUTH_IDENTIFIER = String.raw`(?:[A-Za-z0-9_]*[Mm]ac(?:Bytes|Hex|_hex)?|[A-Za-z0-9_]*MAC(?:_BYTES|_HEX)?|[A-Za-z0-9_]*[Pp]roof|[A-Za-z0-9_]*[Tt]ag|[A-Za-z0-9_]*[Cc]onfirmation)`;
const AUTH_TOKEN = new RegExp(String.raw`(?:^|[^A-Za-z0-9_])${AUTH_IDENTIFIER}(?![A-Za-z0-9_])`);
const EQUALITY_BEFORE = new RegExp(
    String.raw`(?:^|[^A-Za-z0-9_])${AUTH_IDENTIFIER}\s*(?:===?|!==?)`,
);
const EQUALITY_AFTER = new RegExp(String.raw`(?:===?|!==?)\s*${AUTH_IDENTIFIER}(?![A-Za-z0-9_])`);
const NULL_EQUALITY = [
    new RegExp(
        String.raw`(?:^|[^A-Za-z0-9_])${AUTH_IDENTIFIER}\s*(?:===?|!==?)\s*(?:null|undefined)(?![A-Za-z0-9_])`,
        'g',
    ),
    new RegExp(
        String.raw`(?:null|undefined)\s*(?:===?|!==?)\s*${AUTH_IDENTIFIER}(?![A-Za-z0-9_])`,
        'g',
    ),
];
const ARRAY_COMPARISON = /(?:\bcontentEquals\b|\bArrays\s*\.\s*equals\s*\(|\.\s*equals\s*\()/;

function isComment(text) {
    const trimmed = text.trimStart();
    return trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.startsWith('/*');
}

/** Removes each constant-time helper call (balanced parentheses), leaving the rest. */
function stripHelperCalls(line) {
    let text = line;
    let searchFrom = 0;
    for (;;) {
        HELPER_CALL.lastIndex = searchFrom;
        const match = HELPER_CALL.exec(text);
        if (match === null) return text;
        const start = match.index;
        let depth = 0;
        let index = start + match[0].length - 1;
        for (; index < text.length; index += 1) {
            if (text[index] === '(') depth += 1;
            else if (text[index] === ')') {
                depth -= 1;
                if (depth === 0) {
                    index += 1;
                    break;
                }
            }
        }
        text = `${text.slice(0, start)} ${text.slice(index)}`;
        searchFrom = start + 1;
    }
}

/** Drops null/undefined checks: they compare presence, not authenticator bytes. */
function stripNullChecks(line) {
    return NULL_EQUALITY.reduce((text, pattern) => text.replace(pattern, ' '), line);
}

/** Returns `{ path, line, text }` for every direct comparison in `text`. */
function scanSource(path, text) {
    const violations = [];
    const lines = text.split('\n');
    for (let index = 0; index < lines.length; index += 1) {
        const line = lines[index];
        if (isComment(line)) continue;
        const candidate = stripNullChecks(stripHelperCalls(line));
        const directEquality = EQUALITY_BEFORE.test(candidate) || EQUALITY_AFTER.test(candidate);
        const arrayComparison = ARRAY_COMPARISON.test(candidate) && AUTH_TOKEN.test(candidate);
        if (directEquality || arrayComparison) {
            violations.push({ path, line: index + 1, text: line.trim() });
        }
    }
    return violations;
}

function walk(directory) {
    const files = [];
    for (const entry of readdirSync(directory, { withFileTypes: true })) {
        const path = join(directory, entry.name);
        if (entry.isDirectory()) {
            if (!SKIPPED_DIRECTORIES.has(entry.name)) files.push(...walk(path));
            continue;
        }
        if (SOURCE_EXTENSIONS.has(entry.name.slice(entry.name.lastIndexOf('.')))) {
            files.push(path);
        }
    }
    return files;
}

function fileSet(paths) {
    const files = [];
    for (const path of paths) {
        const stats = statSync(path);
        if (stats.isDirectory()) files.push(...walk(path));
        else files.push(path);
    }
    return files;
}

function display(path) {
    const local = relative(process.cwd(), path);
    return local.startsWith('..') ? path : local;
}

function main() {
    const args = process.argv.slice(2);
    let root = process.cwd();
    const explicit = [];
    for (let index = 0; index < args.length; index += 1) {
        if (args[index] === '--root') {
            root = resolve(args[index + 1] ?? '.');
            index += 1;
        } else if (args[index] === '--file') {
            explicit.push(resolve(args[index + 1] ?? '.'));
            index += 1;
        } else {
            console.error(`check-mac-comparisons: unknown argument ${args[index]}`);
            process.exit(2);
        }
    }

    const paths = explicit.length > 0 ? explicit : DEFAULT_ROOTS.map((path) => join(root, path));
    const existing = [];
    for (const path of paths) {
        if (existsSync(path)) {
            existing.push(path);
            continue;
        }
        if (explicit.length > 0) {
            console.error(`check-mac-comparisons: no such file or directory ${path}`);
            process.exit(2);
        }
    }
    const violations = fileSet(existing).flatMap((path) =>
        scanSource(path, readFileSync(path, 'utf8')),
    );
    for (const violation of violations) {
        console.error(`${display(violation.path)}:${violation.line}: ${violation.text}`);
    }
    if (violations.length > 0) {
        console.error(
            `check-mac-comparisons: ${violations.length} direct MAC comparison(s); ` +
                'use MessageDigest.isEqual / equalConstantTime',
        );
        process.exit(1);
    }
}

main();
