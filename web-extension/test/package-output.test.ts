import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import { existsSync, readFileSync, readdirSync, rmSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { afterEach, describe, expect, it } from 'vitest';

const WEB_EXTENSION = join(__dirname, '..');
const OUTPUT = join(WEB_EXTENSION, '..', 'build', 'web-extension');
const ZIP = 'veles-extension-0.1.0.zip';
const SIDECAR = `${ZIP}.sha256`;

function packageExtension(): void {
    execFileSync('npm', ['run', 'package'], {
        cwd: WEB_EXTENSION,
        stdio: 'pipe',
    });
}

afterEach(() => rmSync(OUTPUT, { recursive: true, force: true }));

describe('npm run package', () => {
    it('recreates the accepted package output and is byte-identical on repeat', () => {
        rmSync(OUTPUT, { recursive: true, force: true });
        packageExtension();
        writeFileSync(join(OUTPUT, 'stale-sentinel'), 'stale');

        packageExtension();

        expect(readdirSync(OUTPUT).sort()).toEqual([SIDECAR, 'SHA256SUMS', ZIP].sort());
        const zip = readFileSync(join(OUTPUT, ZIP));
        const sidecar = readFileSync(join(OUTPUT, SIDECAR), 'utf8');
        const sums = readFileSync(join(OUTPUT, 'SHA256SUMS'), 'utf8');
        expect(existsSync(join(OUTPUT, 'stale-sentinel'))).toBe(false);
        expect(sidecar).toBe(`${createHash('sha256').update(zip).digest('hex')}  ${ZIP}\n`);
        expect(sums).toBe(
            `${createHash('sha256').update(zip).digest('hex')}  ${ZIP}\n${createHash('sha256').update(sidecar).digest('hex')}  ${SIDECAR}\n`,
        );

        const first = new Map(
            readdirSync(OUTPUT).map((name) => [name, readFileSync(join(OUTPUT, name))]),
        );
        packageExtension();
        for (const [name, bytes] of first) {
            expect(readFileSync(join(OUTPUT, name))).toEqual(bytes);
        }
    });
});
