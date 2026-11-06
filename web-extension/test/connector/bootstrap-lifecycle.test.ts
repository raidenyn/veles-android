import { describe, expect, it } from 'vitest';
import { BootstrapLifecycle, activateGeneration } from '../../src/connector/bootstrap-lifecycle';

describe('bootstrap lifecycle guard', () => {
    it('releases ownership when worker activation rejects instead of stranding a lock', async () => {
        let released = false;
        const result = await activateGeneration(
            async () => {
                throw new Error('worker stopped');
            },
            async () => {
                released = true;
            },
        );
        expect(result).toBe(null);
        expect(released).toBe(true);
    });
    it('remembers freeze during pending ownership and reloads on resume', () => {
        const events = new Map<string, () => void>();
        let reloads = 0;
        const guard = new BootstrapLifecycle(
            (name, listener) => {
                events.set(name, listener);
            },
            () => {
                reloads++;
            },
        );
        events.get('freeze')?.();
        let releases = 0;
        guard.cleanup(() => {
            releases++;
        });
        expect(guard.ended).toBe(true);
        expect(releases).toBe(1);
        events.get('resume')?.();
        expect(reloads).toBe(1);
    });
    it('runs cleanup once for overlapping freeze/pagehide/beforeunload', () => {
        const events = new Map<string, () => void>();
        const guard = new BootstrapLifecycle(
            (name, listener) => {
                events.set(name, listener);
            },
            () => undefined,
        );
        let cleanup = 0;
        guard.cleanup(() => {
            cleanup++;
        });
        events.get('freeze')?.();
        events.get('pagehide')?.();
        events.get('beforeunload')?.();
        expect(cleanup).toBe(1);
    });
});
