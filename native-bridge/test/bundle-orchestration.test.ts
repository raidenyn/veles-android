// OTP-01 sub-project 1c — build-orchestration guard for the Tauri bundle build.
//
// PR #110 review findings 4 & 5: the bundle build orchestration must (a) not
// register `bridgeBuild` and `bridgeBundle` as sibling producers of the same
// `src-tauri/target` tree when `bridgePackage` runs, (b) give the build → bundle
// → package chain a coherent lifecycle (serialize the raw build ahead of the
// bundle build rather than running both in parallel over the shared cargo
// target dir), (c) replace/clean bundle output before publication so stale
// installer artifacts cannot be archived, and (d) strip *all* inherited native
// macOS signing/notarization credentials/identity selection (clearing only
// `TAURI_SIGNING_PRIVATE_KEY*` is insufficient — `APPLE_SIGNING_IDENTITY`,
// `APPLE_CERTIFICATE*`, `APPLE_ID`, `APPLE_PASSWORD`, `APPLE_TEAM_ID` and the
// `APPLE_API_*` notarization credentials can all sign/notarize artifacts).
//
// This test pins the orchestration contract for the *separate* bundle build:
//   - `native-bridge/package.json` declares a `bundle` script that invokes the
//     local pinned `@tauri-apps/cli` (the `tauri` bin from node_modules), never
//     `npx`/`yarn`/`pnpm` (no network fallback), and never `--no-bundle`.
//   - `build.gradle.kts` registers a `bridgeBundle` Exec task that:
//       * runs the local Tauri CLI (via the `bundle` npm script, which uses
//         the node_modules-resolved `tauri` bin),
//       * selects platform-appropriate `--bundles` targets at execution time
//         (Windows: nsis+msi, macOS: app+dmg),
//       * forwards `--locked` + `--features tauri-runtime` to cargo,
//       * hard-codes unsigned output (clears every signing/notarization env
//         var, not just the Tauri updater key),
//       * cleans its own bundle output directory before building so stale
//         installer artifacts cannot survive into the archive, and
//       * fails at execution time on unsupported hosts (Linux) rather than
//         silently skipping.
//   - `bridgeBundle` serializes behind `bridgeBuild` (it dependsOn it), so the
//     raw host build and the bundle build are never sibling producers of the
//     same `src-tauri/target` tree. `bridgePackage` depends on `bridgeBundle`
//     (which transitively runs `bridgeBuild`) plus `bridgeManifests`, and
//     cleans its own archive output before packaging.
//
// These are text-contract assertions over the orchestration files (mirroring
// tauri-config-guard.test.ts) because Gradle Exec task behavior cannot be
// exercised in a plain vitest run without a full Gradle invocation; the
// task-graph / dry-run / controlled-environment behavior is verified
// separately via `./gradlew bridge* --dry-run` and a Linux execution probe.

import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { join, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = fileURLToPath(new URL('.', import.meta.url));
const BRIDGE_DIR = resolve(__dirname, '..');
const REPO_DIR = resolve(BRIDGE_DIR, '..');

function readPackageJson(): Record<string, unknown> {
    return JSON.parse(readFileSync(join(BRIDGE_DIR, 'package.json'), 'utf8'));
}

function readBuildGradle(): string {
    return readFileSync(join(REPO_DIR, 'build.gradle.kts'), 'utf8');
}

// Extract a single `tasks.register<Exec>("name") { ... }` block (balanced
// braces) so assertions are scoped to that task, not the whole build file.
function extractExecTask(gradle: string, name: string): string {
    const startIdx = gradle.indexOf(`tasks.register<Exec>("${name}")`);
    expect(startIdx, `task ${name} not found`).toBeGreaterThan(-1);
    let depth = 0;
    let i = startIdx;
    // Walk to the opening brace of the configuration block.
    while (i < gradle.length && gradle[i] !== '{') i++;
    for (; i < gradle.length; i++) {
        if (gradle[i] === '{') depth++;
        else if (gradle[i] === '}') {
            depth--;
            if (depth === 0) return gradle.slice(startIdx, i + 1);
        }
    }
    throw new Error(`unbalanced braces for task ${name}`);
}

describe('native-bridge bundle build orchestration', () => {
    it('package.json declares a `bundle` script', () => {
        const scripts = readPackageJson().scripts as Record<string, string>;
        expect(typeof scripts.bundle).toBe('string');
        expect(scripts.bundle.length).toBeGreaterThan(0);
    });

    it('the `bundle` script uses the local pinned Tauri CLI, not npx', () => {
        const bundle = (readPackageJson().scripts as Record<string, string>).bundle;
        // The local `tauri` bin resolves from node_modules/.bin; npx/yarn/pnpm
        // would fetch from the network on a missing package.
        expect(bundle).toMatch(/\btauri\b/);
        expect(bundle).not.toMatch(/\bnpx\b/);
        expect(bundle).not.toMatch(/\byarn\b/);
        expect(bundle).not.toMatch(/\bpnpm\b/);
    });

    it('the `bundle` script never passes --no-bundle', () => {
        const bundle = (readPackageJson().scripts as Record<string, string>).bundle;
        expect(bundle).not.toMatch(/--no-bundle/);
    });

    it('build.gradle.kts registers a bridgeBundle Exec task', () => {
        const gradle = readBuildGradle();
        expect(gradle).toMatch(/tasks\.register<Exec>\("bridgeBundle"\)/);
    });

    it('bridgeBundle runs the local Tauri bundle script (npm run bundle)', () => {
        const bundle = extractExecTask(readBuildGradle(), 'bridgeBundle');
        // The task must invoke the local CLI through the pinned npm script,
        // not a system `tauri` binary and not npx.
        expect(bundle).toMatch(/["']npm["'],\s*["']run["'],\s*["']bundle["']/);
        expect(bundle).not.toMatch(/npx/);
    });

    it('bridgeBundle selects Windows installer targets (nsis + msi)', () => {
        const gradle = readBuildGradle();
        // WiX = msi, NSIS = nsis. Both must be requested so packaging gets
        // real unsigned installer inputs. (Asserted against the
        // platform-selection expression at task-config scope.)
        expect(gradle).toMatch(/"nsis,msi"/);
    });

    it('bridgeBundle selects macOS targets (app + dmg)', () => {
        const gradle = readBuildGradle();
        // .app bundle plus the .dmg installer output.
        expect(gradle).toMatch(/"app,dmg"/);
    });

    it('bridgeBundle forwards --locked and the tauri-runtime feature to cargo', () => {
        const bundle = extractExecTask(readBuildGradle(), 'bridgeBundle');
        expect(bundle).toMatch(/--locked/);
        expect(bundle).toMatch(/--features/);
        expect(bundle).toMatch(/tauri-runtime/);
    });

    it('bridgeBundle hard-codes unsigned output (clears signing env vars)', () => {
        const bundle = extractExecTask(readBuildGradle(), 'bridgeBundle');
        // The task delegates to the shared stripSigningEnv helper (asserted
        // separately to clear every signing/notarization env var).
        expect(bundle).toMatch(/stripSigningEnv\(this as Exec\)/);
    });

    it('bridgeBundle fails on unsupported platforms (Linux)', () => {
        const gradle = readBuildGradle();
        // Execution-time failure (in doFirst), not a silent skip and not a
        // configuration-time failure — the bridgeBundle task must remain
        // configurable for unrelated Gradle invocations (`./gradlew tasks`,
        // `bridgeFormat`, `check`) on Linux, then fail fast only when the
        // bundle build is actually executed on a non-Windows/non-Mac host.
        expect(gradle).toMatch(/bridgeBundle: unsupported host platform/);
        expect(gradle).toMatch(/Linux is not a target for native-bridge packaging/);
    });

    it('bridgeBundle serializes behind bridgeBuild (no sibling producers of src-tauri/target)', () => {
        const bundle = extractExecTask(readBuildGradle(), 'bridgeBundle');
        // Finding 4: bridgeBuild and bridgeBundle were both registered with
        // `outputs.dir(src-tauri/target)` — the same cargo target tree — so
        // when bridgePackage depended on both they ran as sibling producers of
        // one shared output dir, racing over `target/`. bridgeBundle must
        // instead dependOn(bridgeBuild) so the raw host build runs first and
        // the bundle build runs after, never in parallel over `target/`.
        expect(bundle).toMatch(/dependsOn\([^)]*\bbridgeBuild\b/);
    });

    it('neither bridgeBuild nor bridgeBundle declares the whole src-tauri/target tree as its Gradle output', () => {
        // Finding 4: cargo owns `src-tauri/target/` as a shared incremental
        // cache. Declaring it as a Gradle task output makes two tasks fight
        // over the same declared output and breaks up-to-date checks. Each
        // task must declare only the narrow artifacts it actually produces:
        //   bridgeBuild  → the host binary
        //   bridgeBundle → the installer bundle output dir only
        // The literal `dir("target")` / `dir("src-tauri/target")` output
        // declaration must be gone from both tasks.
        const build = extractExecTask(readBuildGradle(), 'bridgeBuild');
        const bundle = extractExecTask(readBuildGradle(), 'bridgeBundle');
        // `outputs.dir(...)` whose argument ends in `target"` (the bare cargo
        // target tree) is the sibling-producer smell. Narrow outputs (the
        // binary, `target/release/bundle`, `target/release`) are allowed.
        expect(build).not.toMatch(/outputs\.dir\([^)]*\btarget"\s*\)/);
        expect(bundle).not.toMatch(/outputs\.dir\([^)]*\btarget"\s*\)/);
    });

    it('bridgeBundle cleans the bundle output directory before building (no stale installer artifacts)', () => {
        const bundle = extractExecTask(readBuildGradle(), 'bridgeBundle');
        // Finding 5: a previous bundle build leaves installer artifacts under
        // `target/release/bundle/`. Without a clean-before-build, a later
        // build that emits fewer/different artifacts archives the stale ones.
        // bridgeBundle must delete its bundle output dir in doFirst before the
        // Tauri CLI runs.
        expect(bundle).toMatch(/delete\(/);
        expect(bundle).toMatch(/release\/bundle/);
    });

    it('bridgeBundle removes every macOS signing/notarization credential instead of exporting empty values', () => {
        const gradle = readBuildGradle();
        const bundle = extractExecTask(readBuildGradle(), 'bridgeBundle');
        // Finding 5: clearing only TAURI_SIGNING_PRIVATE_KEY* leaves inherited
        // macOS code-signing identity selection and notarization credentials
        // active, which can sign/notarize the artifacts. bridgeBundle must
        // delegate to stripSigningEnv, which clears all of these.
        expect(bundle).toMatch(/stripSigningEnv\(this as Exec\)/);
        // The shared helper must clear every signing/notarization env var the
        // Tauri/macOS toolchain respects (asserted against the whole file so
        // the helper's complete env-var set is pinned regardless of which task
        // block it lives in).
        const expectedSigningKeys = [
            'TAURI_SIGNING_PRIVATE_KEY',
            'TAURI_SIGNING_PRIVATE_KEY_PASSWORD',
            'APPLE_SIGNING_IDENTITY',
            'APPLE_CERTIFICATE',
            'APPLE_CERTIFICATE_PASSWORD',
            'APPLE_ID',
            'APPLE_PASSWORD',
            'APPLE_TEAM_ID',
            'APPLE_PROVIDER_SHORT_NAME',
            'APPLE_API_KEY',
            'APPLE_API_ISSUER',
            'APPLE_API_KEY_PATH',
        ];
        for (const key of expectedSigningKeys) {
            expect(gradle).toContain(`"${key}"`);
        }
        expect(gradle).toMatch(/exec\.environment\.remove\(key\)/);
    });

    it('bridgeBuild also strips macOS signing/notarization env vars (raw build must stay unsigned)', () => {
        const build = extractExecTask(readBuildGradle(), 'bridgeBuild');
        // The raw host build must likewise be unable to sign/notarize even
        // though it produces no installer; an inherited signing identity
        // could codesign the host binary. It delegates to the same helper.
        expect(build).toMatch(/stripSigningEnv\(this as Exec\)/);
    });

    it('bridgePackage depends only on bridgeBundle (bridgeBuild is transitive and manifests are independent)', () => {
        const pkg = extractExecTask(readBuildGradle(), 'bridgePackage');
        // Finding 4: bridgePackage must not depend on bridgeBuild directly —
        // bridgeBundle dependsOn(bridgeBuild), so the build → bundle →
        // package chain is a single serialized line, not a diamond with two
        // producers of `src-tauri/target`.
        expect(pkg).toMatch(/dependsOn\([^)]*\bbridgeBundle\b/);
        expect(pkg).not.toMatch(/dependsOn\([^)]*\bbridgeManifests\b/);
        // bridgeBuild must NOT appear in bridgePackage's own dependsOn — it
        // is reached transitively through bridgeBundle.
        expect(pkg).not.toMatch(/dependsOn\([^)]*\bbridgeBuild\b/);
    });

    it('bridgePackage declares the raw host binary as an input and owns only platform archive directories', () => {
        const pkg = extractExecTask(readBuildGradle(), 'bridgePackage');
        expect(pkg).toMatch(/inputs\.file\(bridgeHostBinary\)/);
        expect(pkg).toMatch(/outputs\.dir\(bridgeWindowsPackageOutput\)/);
        expect(pkg).toMatch(/outputs\.dir\(bridgeMacosPackageOutput\)/);
        expect(pkg).not.toMatch(/outputs\.dir\(bridgeBuildOutput\)/);
    });

    it('bridgeBundle preserves Tauri headless-DMG CI behavior', () => {
        const bundle = extractExecTask(readBuildGradle(), 'bridgeBundle');
        expect(bundle).toMatch(/environment\("CI", "true"\)/);
        expect(bundle).not.toMatch(/environment\("CI", "1"\)/);
    });

    it('bridgePackage cleans its archive output before packaging (no stale archives)', () => {
        const pkg = extractExecTask(readBuildGradle(), 'bridgePackage');
        // Finding 5: a previous package run leaves archives under
        // `build/native-bridge/`. Without a clean-before-package, a later run
        // with fewer/different artifacts leaves stale archives behind.
        // bridgePackage must delete its output dir in doFirst before the
        // packaging script runs.
        expect(pkg).toMatch(/delete\(/);
        // The output root is build/native-bridge (bridgeBuildOutput).
        expect(pkg).toMatch(/native-bridge/);
    });
});
