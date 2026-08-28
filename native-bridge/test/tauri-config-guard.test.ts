import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = fileURLToPath(new URL('.', import.meta.url));
const CONF_PATH = join(__dirname, '..', 'src-tauri', 'tauri.conf.json');

function readConfig(): Record<string, unknown> {
    return JSON.parse(readFileSync(CONF_PATH, 'utf8'));
}

// OTP-01 sub-project 1c — Tauri headless posture guard.
// The native-bridge is a headless Native Messaging host: no windows, no tray,
// no menu, no autostart, no login item. The process exits on stdin EOF.
// Exact-match assertions so weakening posture (adding a window, enabling the
// updater, wiring tray/menu) is caught at `npm test` time.
describe('tauri.conf.json headless posture (exact match)', () => {
    it('productName is Veles Native Bridge', () => {
        expect(readConfig().productName).toBe('Veles Native Bridge');
    });

    it('version is present and non-empty', () => {
        const v = readConfig().version;
        expect(typeof v).toBe('string');
        expect((v as string).length).toBeGreaterThan(0);
    });

    it('app.windows is exactly []', () => {
        expect(readConfig().app).toHaveProperty('windows');
        expect((readConfig().app as { windows: unknown }).windows).toEqual([]);
    });

    it('app.trayIcon is absent', () => {
        expect(readConfig().app).not.toHaveProperty('trayIcon');
    });

    it('app.macOSPrivateApi is absent or false', () => {
        const macos = (readConfig().app as { macOSPrivateApi?: boolean }).macOSPrivateApi;
        expect(macos ?? false).toBe(false);
    });

    it('build.beforeDevCommand and beforeBuildCommand are absent', () => {
        const build = readConfig().build as Record<string, unknown>;
        expect(build).not.toHaveProperty('beforeDevCommand');
        expect(build).not.toHaveProperty('beforeBuildCommand');
    });

    it('app.withGlobalTauri is false', () => {
        expect((readConfig().app as { withGlobalTauri?: boolean }).withGlobalTauri).toBe(false);
    });

    it('bundle.active is false (no signed bundling)', () => {
        expect((readConfig().bundle as { active: boolean }).active).toBe(false);
    });

    it('bundle.targets is exactly []', () => {
        expect((readConfig().bundle as { targets: unknown }).targets).toEqual([]);
    });

    it('plugins is absent or empty', () => {
        const cfg = readConfig();
        if ('plugins' in cfg) {
            expect(Object.keys(cfg.plugins as object).length).toBe(0);
        }
    });

    it('no top-level tauri field (Tauri 2 schema)', () => {
        expect(readConfig()).not.toHaveProperty('tauri');
    });
});
