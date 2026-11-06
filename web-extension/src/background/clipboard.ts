import type { LifecycleSender } from './lifecycle';

export interface ClipboardApi {
    authorize(token: unknown, sender: LifecycleSender): Promise<boolean>;
    create(): Promise<void>;
    write(code: string, admit: () => Promise<boolean>): Promise<boolean>;
    close(): Promise<void>;
}

export class ClipboardCoordinator {
    private queue: Promise<unknown> = Promise.resolve();
    private pending = 0;
    constructor(private readonly api: ClipboardApi) {}

    copy(token: unknown, code: unknown, sender: LifecycleSender): Promise<{ ok: boolean }> {
        if (typeof code !== 'string' || !/^[0-9]{6}$/.test(code) || this.pending >= 16) {
            return Promise.resolve({ ok: false });
        }
        this.pending++;
        const operation = this.queue
            .then(async () => {
                let ok = false;
                let attempted = false;
                try {
                    if (!(await this.api.authorize(token, sender))) return { ok: false };
                    attempted = true;
                    await this.api.create();
                    if (await this.api.authorize(token, sender))
                        ok = await this.api.write(code, () => this.api.authorize(token, sender));
                    // A revoked result cannot update current UI/status.
                    ok = ok && (await this.api.authorize(token, sender));
                } catch {
                    ok = false;
                } finally {
                    if (attempted) {
                        try {
                            await this.api.close();
                        } catch {
                            ok = false;
                        }
                    }
                }
                return { ok };
            })
            .finally(() => {
                this.pending--;
            });
        this.queue = operation.catch(() => undefined);
        return operation;
    }
}

interface ClipboardDocument {
    createElement(tag: 'textarea'): { value: string; select(): void; remove(): void };
    body: { appendChild(element: unknown): unknown };
    execCommand(command: string): boolean;
}

// Deliberately no navigator.clipboard fallback: offscreen clipboard support
// uses DOM execCommand; clipboard denial is a reported result, never a retry.
export function copySyntheticCode(code: unknown, doc: ClipboardDocument): boolean {
    if (typeof code !== 'string' || !/^[0-9]{6}$/.test(code)) return false;
    const input = doc.createElement('textarea');
    try {
        input.value = code;
        doc.body.appendChild(input);
        input.select();
        return doc.execCommand('copy');
    } catch {
        return false;
    } finally {
        input.value = '';
        input.remove();
    }
}
