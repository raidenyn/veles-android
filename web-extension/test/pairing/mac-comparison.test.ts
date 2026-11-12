import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { afterAll, describe, expect, it } from 'vitest';

// OTP-10 task 4.6: a rule, not a convention. The script scans both endpoints'
// production sources and fails on a direct comparison over MAC material (mac,
// proof, confirmation or tag). The same script runs in CI
// (`check-android` and `check-web-extension` lint jobs).
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

function writeRoot(root: string, relativePath: string, source: string): void {
    const path = join(root, relativePath);
    mkdirSync(join(path, '..'), { recursive: true });
    writeFileSync(path, source);
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

    it('fails on a reversed comparison and on receiver-side contentEquals', () => {
        expect(scan('if (expected !== receivedMac) return false;\n', 'Reversed.kt').status).toBe(1);
        expect(
            scan('if (mac.contentEquals(expected)) return false;\n', 'ContentEquals.kt').status,
        ).toBe(1);
    });

    it('fails on expected.contentEquals(mac)', () => {
        expect(
            scan('if (expected.contentEquals(mac)) return true\n', 'ExpectedReceiver.kt').status,
        ).toBe(1);
    });

    it('fails on the infix contentEquals form', () => {
        expect(scan('if (mac contentEquals expected) return true\n', 'Infix.kt').status).toBe(1);
    });

    it('fails on java.util.Arrays.equals over a MAC', () => {
        expect(
            scan('if (java.util.Arrays.equals(mac, expected)) return true\n', 'ArraysEquals.kt')
                .status,
        ).toBe(1);
    });

    it('fails on receiver-side contentEquals with a computed MAC argument', () => {
        expect(
            scan('if (!received.contentEquals(computeMac(key, et))) return true\n', 'Computed.kt')
                .status,
        ).toBe(1);
    });

    it('fails when a constant-time helper shares the line with a direct equality', () => {
        expect(
            scan('if (MessageDigest.isEqual(a, b) && mac == other) return true\n', 'Mixed.kt')
                .status,
        ).toBe(1);
    });

    it('fails on proof, confirmation and tag identifiers', () => {
        expect(scan('if (proof !== expectedProof) return true;\n', 'Proof.ts').status).toBe(1);
        expect(
            scan('if (confirmation.contentEquals(expected)) return true\n', 'Confirmation.kt')
                .status,
        ).toBe(1);
        expect(scan('if (tag == expectedTag) return true;\n', 'Tag.kt').status).toBe(1);
    });

    it('accepts the constant-time helpers, length checks and null checks', () => {
        expect(
            scan(
                [
                    'if (MessageDigest.isEqual(mac, expected)) return true',
                    'if (equalConstantTime(mac, expected)) return true;',
                    'if (mac.length === 32) return true;',
                    'if (proof === null) return true;',
                    'if (mac !== undefined) return true;',
                ].join('\n'),
                'Safe.kt',
            ).status,
        ).toBe(0);
    });

    it('ignores comments', () => {
        expect(scan('// if (mac == expected) return true\n', 'Comment.kt').status).toBe(0);
    });

    it('scans only production roots, never test sources', () => {
        const root = join(scratch, 'root');
        writeRoot(
            root,
            'app/src/main/Clean.kt',
            'if (MessageDigest.isEqual(mac, expected)) true\n',
        );
        writeRoot(root, 'app/src/test/Unsafe.kt', 'if (mac == expected) true\n');
        expect(run(['--root', root]).status).toBe(0);

        writeRoot(root, 'app/src/main/Unsafe.kt', 'if (mac == expected) true\n');
        const result = run(['--root', root]);
        expect(result.status).toBe(1);
        expect(result.output).toContain('app/src/main/Unsafe.kt:1');
        expect(result.output).not.toContain('app/src/test/Unsafe.kt');
    });
});
