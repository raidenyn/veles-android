// A grep-style guard: the pairing code must never reach a logger, a diagnostic ring,
// a broadcast result or a rendered snapshot. It scans both debug source trees so the
// Android receiver and the Chrome panel follow the same rule as the production code.
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { describe, expect, it } from 'vitest';

const WEB_EXTENSION = join(__dirname, '..', '..');
const ROOTS = [
    join(WEB_EXTENSION, '..', 'android', 'app', 'src', 'debug'),
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
    /(?:VelesLog|logger\.[dweiv]|\blogger?\b|Log\.[dweiv]|println|print\s*\(|setResultData|\bresult\s*\(|Notification(?:Compat|Manager)?|putExtra)/;
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
 * Strip quoted UI copy before looking for a code expression under a sink, so
 * "Enter a six-digit code" is not a hit. Kotlin double-quoted strings can interpolate with
 * `$`, so they are never stripped for `.kt` lines; TypeScript double quotes cannot.
 */
function expressions(line: string, kotlin: boolean): string {
    return kotlin ? line.replace(/'[^']*'/g, "''") : line.replace(/'[^']*'|"[^"]*"/g, "''");
}

/**
 * The pure grep predicate, exported for the negative-control test. A sink combined with a
 * code-bearing value is a leak even when the line never names the `PairingCode` type:
 * `logger.d(tag, state.value.code)`, `"code=${state.value.code}"` and
 * `textContent = `${peer.code}`` must all hit.
 */
export function leakIn(file: string, line: string): boolean {
    const source = expressions(code(line), file.endsWith('.kt'));
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
                    if (leakIn(rel, line)) hits.push(`${root}/${rel}:${index + 1}: ${line.trim()}`);
                });
            }
        }
        expect(hits).toEqual([]);
    });

    it('negative_controls_hit_every_real_leak_shape', () => {
        // Known-leaky lines: these must hit even though none names the PairingCode type.
        const leaks: [string, string][] = [
            [
                'PairingDebugReceiver.kt',
                'logger.d(PAIRING_TAG, PairingDebugHooks.state.value.code.orEmpty())',
            ],
            ['PairingDebugReceiver.kt', 'logger.d(PAIRING_TAG, "code=${state.value.code}")'],
            [
                'PairingDebugReceiver.kt',
                'VelesLog.d(PROBE_TAG, PairingDebugHooks.state.value.codeBytes().toString())',
            ],
            [
                'PairingDebugReceiver.kt',
                'logger.d(PAIRING_TAG, PairingDebugState(open = true, code = text))',
            ],
            [
                'PairingDebugReceiver.kt',
                'result(String(PairingDebugHooks.state.value.codeBytes(), Charsets.US_ASCII))',
            ],
            ['pairing-panel.ts', 'elements.status.textContent = code;'],
            ['pairing-panel.ts', 'elements.status.textContent = `${peer.code}`;'],
            ['pairing-panel.ts', 'console.log("pairing", code);'],
            ['pairing-panel.ts', 'diagnostics.push({ code });'],
        ];
        for (const [file, line] of leaks) expect(leakIn(file, line), `${file}: ${line}`).toBe(true);

        // Safe lines: declarations, machine calls and quoted UI copy are not sinks.
        const safe: [string, string][] = [
            ['PairingDebugReceiver.kt', 'logger.d(PAIRING_TAG, "pairing window open")'],
            ['PairingDebugReceiver.kt', 'dialog(code);'],
            [
                'pairing-panel.ts',
                'elements.status.textContent = "Enter a six-digit code and choose a phone";',
            ],
            ['pairing-panel.ts', 'elements.status.textContent = `State: ${value.phase}`;'],
            ['pairing-panel.ts', 'deps.start(deviceHandle, code);'],
            [
                'PairingDebugReceiver.kt',
                'val code = String(PairingDebugHooks.state.value.codeBytes(), Charsets.US_ASCII)',
            ],
        ];
        for (const [file, line] of safe) expect(leakIn(file, line), `${file}: ${line}`).toBe(false);
    });
});
