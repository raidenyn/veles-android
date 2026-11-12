// A grep-style guard: the pairing code must never reach a logger, a diagnostic ring,
// a broadcast result or a rendered snapshot. It scans both debug source trees so the
// Android receiver and the Chrome panel follow the same rule as the production code.
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const WEB_EXTENSION = join(__dirname, '..', '..');
const ROOTS = [
    join(WEB_EXTENSION, '..', 'app', 'src', 'debug'),
    join(WEB_EXTENSION, 'src', 'debug'),
];

function walk(dir: string, base = ''): string[] {
    return readdirSync(dir).flatMap((entry) => {
        const path = join(dir, entry);
        const rel = base === '' ? entry : `${base}/${entry}`;
        return statSync(path).isDirectory() ? walk(path, rel) : [rel];
    });
}

/** Sinks that could carry the six-digit code outside the on-screen readout. */
const KOTLIN_SINK =
    /(?:VelesLog|logger\.[dweiv]|logger?\b|Log\.[dweiv]|println|print\s*\(|setResultData|\bresult\s*\(|Notification(?:Compat|Manager)?|putExtra)/;
const TYPESCRIPT_SINK =
    /(?:console\.[a-z]+|diagnostics?\.push|DiagnosticRing|textContent\s*=|setResultData|\bresult\s*\(|\blog\s*\()/i;

/** Values that come from the code holder, independent of any type name on the line. */
const CODE_VALUE = /(?:PairingCode|\.code\b|codeBytes|\bdigits\b|PairingDebugState)/;
const GENERIC_CODE = /\b(?:pairingCode|codeInput\.value|code)\b/;

/** Strip line comments so a sentence about "never logged" is not a false positive. */
function code(line: string): string {
    return line.replace(/\/\/.*$/, '').replace(/^\s*\*.*$/, '');
}

/**
 * Strip single/double-quoted text (not template substitutions) before looking for a code
 * expression under a sink, so UI copy such as "Enter a six-digit code" is not a hit.
 */
function expressions(line: string): string {
    return line.replace(/'[^']*'|"[^"]*"/g, "''");
}

/**
 * The pure grep predicate, exported for the negative-control test. A sink combined with a
 * code-bearing value is a leak even when the line never names the `PairingCode` type:
 * `logger.d(tag, state.value.code)` and `textContent = `${peer.code}`` must both hit.
 */
export function leakIn(line: string): boolean {
    const source = expressions(code(line));
    if (source.trim().length === 0) return false;
    const value = CODE_VALUE.test(source) || GENERIC_CODE.test(source);
    if (!value) return false;
    return KOTLIN_SINK.test(source) || TYPESCRIPT_SINK.test(source);
}

describe('pairing code logger guard', () => {
    it('pairing_code_never_reaches_a_logger', () => {
        const hits: string[] = [];
        for (const root of ROOTS) {
            for (const rel of walk(root)) {
                if (!/\.(?:kt|ts)$/.test(rel)) continue;
                const lines = readFileSync(join(root, rel), 'utf8').split('\n');
                lines.forEach((line, index) => {
                    if (leakIn(line)) hits.push(`${root}/${rel}:${index + 1}: ${line.trim()}`);
                });
            }
        }
        expect(hits).toEqual([]);
    });

    it('negative_controls_hit_every_real_leak_shape', () => {
        // Known-leaky lines: these must hit even though none names the PairingCode type.
        const leaks = [
            'logger.d(PAIRING_TAG, PairingDebugHooks.state.value.code.orEmpty())',
            'VelesLog.d(PROBE_TAG, PairingDebugHooks.state.value.codeBytes().toString())',
            'logger.d(PAIRING_TAG, PairingDebugState(open = true, code = text))',
            'result(String(PairingDebugHooks.state.value.codeBytes(), Charsets.US_ASCII))',
            'elements.status.textContent = code;',
            'elements.status.textContent = `${peer.code}`;',
            'console.log("pairing", code);',
            'diagnostics.push({ code });',
        ];
        for (const line of leaks) expect(leakIn(line), line).toBe(true);

        // Safe lines: declarations, machine calls and quoted UI copy are not sinks.
        const safe = [
            'logger.d(PAIRING_TAG, "pairing window open")',
            'elements.status.textContent = "Enter a six-digit code and choose a phone";',
            'elements.status.textContent = `State: ${value.phase}`;',
            'deps.start(deviceHandle, code);',
            'val code = String(PairingDebugHooks.state.value.codeBytes(), Charsets.US_ASCII)',
        ];
        for (const line of safe) expect(leakIn(line), line).toBe(false);
    });
});
