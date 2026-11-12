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

/** A sink that could carry the six-digit code outside the on-screen readout. */
const KOTLIN_SINK =
    /\b(?:VelesLog|logger?|Log\.[dweiv]|println|print|setResultData|result\s*\(|Notification(?:Compat|Manager)?|putExtra)\b/;
const TYPESCRIPT_SINK =
    /\b(?:console\.[a-z]+|diagnostics?\.push|DiagnosticRing|textContent\s*=|setResultData|\bresult\s*\(|\blog\s*\()/i;

/** Strip line comments so a sentence about "never logged" is not a false positive. */
function code(line: string): string {
    return line.replace(/\/\/.*$/, '').replace(/^\s*\*.*$/, '');
}

function leakIn(file: string, line: string): boolean {
    const source = code(line);
    if (source.trim().length === 0) return false;
    if (/\bPairingCode\b/.test(source) && KOTLIN_SINK.test(source)) return true;
    if (/\bPairingCode\b/.test(source) && /\bcode\b/.test(source)) {
        // Declaration and redacted-toString lines are not sinks; anything else that
        // combines the type with a code value is suspicious in a debug surface.
        if (!/^\s*(?:import|class|override fun toString|private val digits|\*)/.test(source)) {
            return true;
        }
    }
    // Quoted UI copy ("Enter a six-digit code") is not a code value; strip literals
    // before looking for a `code` expression under a sink.
    const expressions = source.replace(/'[^']*'|"[^"]*"|`[^`]*`/g, "''");
    if (
        TYPESCRIPT_SINK.test(expressions) &&
        /\b(?:pairingCode|codeInput\.value|code)\b/.test(expressions)
    ) {
        // Calls that only validate or pass the code to the machine are not sinks.
        if (
            /\b(?:deps\.start|startPairing|codeInput\.value\s*=|const code\s*=|code\.length)\b/.test(
                expressions,
            )
        ) {
            return false;
        }
        return true;
    }
    return false;
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
});
