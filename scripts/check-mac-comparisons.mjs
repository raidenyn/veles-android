#!/usr/bin/env node
// OTP-10 task 4.6: fail on a direct comparison over MAC material.
//
// Both endpoints must compare authenticators with a constant-time helper:
// Kotlin `java.security.MessageDigest.isEqual` and TypeScript
// `equalConstantTime` (`web-extension/src/session/engine.ts`). A direct `==`,
// `===`, `!=`, `!==`, `contentEquals` or `equals` over a MAC leaks through
// timing, so this grep-style rule rejects it in production sources. Fingerprint
// comparisons in the enrollment code also use the constant-time helper, but the
// rule is MAC-scoped: the OTP-05 trust repository compares stored hex
// fingerprints directly and is outside this task. Test sources are outside the
// scanned roots on purpose: negative vectors must be able to write the
// forbidden form.
//
// Usage:
//   node scripts/check-mac-comparisons.mjs [--root DIR]
//   node scripts/check-mac-comparisons.mjs --file FILE [--file FILE ...]
//
// Exit 0 when clean, 1 with `path:line: text` lines on violations.

import { readdirSync, readFileSync, statSync } from 'node:fs';
import { join, relative, resolve } from 'node:path';

const DEFAULT_ROOTS = ['app/src/main', 'web-extension/src'];
const SKIPPED_DIRECTORIES = new Set(['.git', 'build', 'coverage', 'dist', 'node_modules']);
const SOURCE_EXTENSIONS = new Set(['.kt', '.ts']);

// A comparison is safe only when one of these helpers drives it.
const SAFE_HELPER = /(?:MessageDigest\.isEqual|equalConstantTime|crypto\.timingSafeEqual)\s*\(/;

// MAC identifiers: `mac`, `expectedMac`, `received_mac`, `macBytes`, `mac_hex`,
// `MAC_BYTES`, any casing. The identifier must end in the token (with only the
// narrow byte/hex suffix allowed), so an unrelated `macaroon`,
// `macPermissionDenied` or the `MAC_BODY_BYTES` length constant is not a MAC.
const MAC_IDENTIFIER = String.raw`(?:[A-Za-z0-9_]*[Mm]ac(?:Bytes|Hex|_hex)?|[A-Za-z0-9_]*MAC(?:_BYTES|_HEX)?)`;
const COMPARISON_BEFORE = new RegExp(
    String.raw`(?:^|[^A-Za-z0-9_])${MAC_IDENTIFIER}\s*(?:===?|!==?|\.contentEquals\s*\(|\.equals\s*\()`,
);
const COMPARISON_AFTER = new RegExp(
    String.raw`(?:===?|!==?)\s*${MAC_IDENTIFIER}(?![A-Za-z0-9_])`,
);

function isComment(text) {
    const trimmed = text.trimStart();
    return trimmed.startsWith('//') || trimmed.startsWith('*') || trimmed.startsWith('/*');
}

/** Returns `{ path, line, text }` for every direct comparison in `text`. */
function scanSource(path, text) {
    const violations = [];
    const lines = text.split('\n');
    for (let index = 0; index < lines.length; index += 1) {
        const line = lines[index];
        if (isComment(line)) continue;
        if (SAFE_HELPER.test(line)) continue;
        if (COMPARISON_BEFORE.test(line) || COMPARISON_AFTER.test(line)) {
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
    const violations = fileSet(paths).flatMap((path) =>
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
