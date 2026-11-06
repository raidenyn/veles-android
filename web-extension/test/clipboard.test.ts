import { describe, expect, it } from 'vitest';
import {
    ClipboardCoordinator,
    copySyntheticCode,
    type ClipboardApi,
} from '../src/background/clipboard';

function rig() {
    let valid = true;
    const operations: string[] = [];
    const api: ClipboardApi = {
        authorize: async () => valid,
        create: async () => {
            operations.push('create');
        },
        write: async (code) => {
            operations.push(`write:${code}`);
            return true;
        },
        close: async () => {
            operations.push('close');
        },
    };
    return {
        api,
        operations,
        revoke: () => {
            valid = false;
        },
        clipboard: new ClipboardCoordinator(api),
    };
}

describe('synthetic clipboard coordinator', () => {
    it('refuses helper execution admission after dispatch when the generation is revoked', async () => {
        const r = rig();
        let execute!: () => Promise<boolean>;
        let copied = false;
        r.api.write = (_code, authorize) =>
            new Promise((resolve) => {
                execute = async () => {
                    copied = await authorize();
                    resolve(copied);
                    return copied;
                };
            });
        const pending = r.clipboard.copy('token', '012345', {});
        for (let i = 0; i < 10; i++) await Promise.resolve();
        r.revoke();
        expect(await execute()).toBe(false);
        expect(await pending).toEqual({ ok: false });
        expect(copied).toBe(false);
    });
    it('rejects non-six-digit input and unauthorized senders without creating a helper', async () => {
        const r = rig();
        for (const code of ['', '12345', '1234567', '１２３４５６', 'secret']) {
            expect(await r.clipboard.copy('token', code, {})).toEqual({ ok: false });
        }
        r.revoke();
        expect(await r.clipboard.copy('token', '012345', {})).toEqual({ ok: false });
        expect(r.operations).toEqual([]);
    });

    it('serializes helpers and closes each after its immediate request', async () => {
        const r = rig();
        expect(
            await Promise.all([
                r.clipboard.copy('token', '012345', {}),
                r.clipboard.copy('token', '654321', {}),
            ]),
        ).toEqual([{ ok: true }, { ok: true }]);
        expect(r.operations).toEqual([
            'create',
            'write:012345',
            'close',
            'create',
            'write:654321',
            'close',
        ]);
    });

    it('does not copy a generation revoked during helper creation', async () => {
        const r = rig();
        r.api.create = async () => {
            r.revoke();
        };
        expect(await r.clipboard.copy('token', '012345', {})).toEqual({ ok: false });
        expect(r.operations).toEqual(['close']);
    });

    it.each(['create', 'write', 'close'] as const)(
        'reports %s failure and still attempts cleanup',
        async (stage) => {
            const r = rig();
            r.api[stage] = async () => {
                r.operations.push(stage);
                throw new Error('denied');
            };
            expect(await r.clipboard.copy('token', '012345', {})).toEqual({ ok: false });
            expect(r.operations.at(-1)).toBe('close');
        },
    );

    it('caps pending work at 16, then rejects queued stale work', async () => {
        const r = rig();
        let release!: () => void;
        r.api.create = () =>
            new Promise((resolve) => {
                release = resolve;
            });
        const requests = Array.from({ length: 16 }, () => r.clipboard.copy('token', '012345', {}));
        expect(await r.clipboard.copy('token', '012345', {})).toEqual({ ok: false });
        await Promise.resolve();
        await Promise.resolve();
        r.revoke();
        release();
        expect((await Promise.all(requests)).every((result) => !result.ok)).toBe(true);
        expect(r.operations).toEqual(['close']);
    });
});

describe('clipboard-only DOM operation', () => {
    it.each([true, false])('erases and removes temporary text after result %s', (success) => {
        const input = {
            value: '',
            select() {
                return;
            },
            remove() {
                removed = true;
            },
        };
        let removed = false;
        const doc = {
            createElement: () => input,
            body: {
                appendChild() {
                    return;
                },
            },
            execCommand: () => success,
        };
        expect(copySyntheticCode('012345', doc)).toBe(success);
        expect(input.value).toBe('');
        expect(removed).toBe(true);
    });
    it('erases temporary text even when the browser throws', () => {
        let removed = false;
        const input = {
            value: '',
            select() {
                return;
            },
            remove() {
                removed = true;
            },
        };
        expect(
            copySyntheticCode('012345', {
                createElement: () => input,
                body: {
                    appendChild() {
                        return;
                    },
                },
                execCommand() {
                    throw new Error('denied');
                },
            }),
        ).toBe(false);
        expect(input.value).toBe('');
        expect(removed).toBe(true);
    });
});
