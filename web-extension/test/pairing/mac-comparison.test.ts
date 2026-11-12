import { spawnSync } from 'node:child_process';
import { mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, describe, expect, it } from 'vitest';

// OTP-10 task 4.6: a rule, not a convention. The script scans both endpoints'
// production sources and fails on a direct comparison over MAC or fingerprint
// material; the same script runs in CI (`check-android` and
// `check-web-extension` lint jobs).
const SCRIPT = new URL('../../../scripts/check-mac-comparisons.mjs', import.meta.url).pathname;
const REPOSITORY = new URL('../../../', import.meta.url).pathname;
const scratch = mkdtempSync(join(tmpdir(), 'veles-mac-comparisons-'));

afterAll(() => {
    rmSync(scratch, { recursive: true, force: true });
});

interface Run {
    status: number | null;
    output: string;
}

function run(args: string[]): Run {
    const result = spawnSync(process.execPath, [SCRIPT, ...args], {
        cwd: REPOSITORY,
        encoding: 'utf8',
    });
    return { status: result.status, output: `${result.stdout}${result.stderr}` };
}

function scan(source: string, name: string): Run {
    const path = join(scratch, name);
    writeFileSync(path, source);
    return run(['--file', path]);
}

describe('MAC comparison rule', () => {
    it('passes on the repository sources', () => {
        const result = run(['--root', REPOSITORY]);
        expect(result.output).toBe('');
        expect(result.status).toBe(0);
    });

    it('fails on a direct Kotlin MAC equality', () => {
        const result = scan('if (mac == expected) return true\n', 'Direct.kt');
        expect(result.status).toBe(1);
        expect(result.output).toContain('Direct.kt:1');
    });

    it('fails on a direct TypeScript MAC equality', () => {
        const result = scan('if (receivedMac === expectedMac) return true;\n', 'Direct.ts');
        expect(result.status).toBe(1);
        expect(result.output).toContain('Direct.ts:1');
    });

    it('fails on a reversed comparison and on contentEquals', () => {
        expect(scan('if (expected !== receivedMac) return false;\n', 'Reversed.kt').status).toBe(1);
        expect(
            scan('if (mac.contentEquals(expected)) return false;\n', 'ContentEquals.kt').status,
        ).toBe(1);
    });

    it('accepts the constant-time helpers', () => {
        expect(
            scan(
                [
                    'if (MessageDigest.isEqual(mac, expected)) return true',
                    'if (equalConstantTime(mac, expected)) return true;',
                    'if (mac.length === 32) return true;',
                ].join('\n'),
                'Safe.kt',
            ).status,
        ).toBe(0);
    });

    it('ignores comments and test-only sources outside the production roots', () => {
        expect(scan('// if (mac == expected) return true\n', 'Comment.kt').status).toBe(0);
    });
});
