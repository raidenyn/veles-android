# OTP-01 Sub-project 1a — TypeScript/MV3 extension toolchain — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up `web-extension/` (Vite + TypeScript skeleton, vitest, eslint, prettier) and root Gradle tasks that install, format, lint, type-check, test, build, and deterministically zip it — wired into root `check`, reproducible, and gated by a manifest/CSP guard.

**Architecture:** A new top-level `web-extension/` npm project (not a Gradle subproject) holds a minimal loadable MV3 extension. Root `build.gradle.kts` defines an `extension` task group invoking `npm` on PATH with `--prefix web-extension`. Tasks are skipped with descriptive messages when `web-extension/package-lock.json` is absent so Android-only workflows keep working. `extensionPackage` produces a deterministic zip via a Gradle `Zip` task (`reproducibleFileOrder`, `preserveFileTimestamps=false`), then validates the emitted `manifest.json` against a strict baseline.

**Tech Stack:** Gradle 8.11.1 Kotlin DSL, Node 26/npm 11 (minimum floor declared in `engines.node`; Gradle does not pin or download node), Vite 6, TypeScript 5.7, vitest 3, eslint 9 (flat config), prettier 3.

**Spec:** `docs/superpowers/specs/2026-08-26-otp-01-reproducible-toolchains-design.md` (sub-project 1a section + Global decisions). Read both before starting.

## Global Constraints

- node/npm come from PATH; Gradle never downloads them. Minimum floor: `engines.node = ">=20.0.0"`, `engines.npm = ">=10.0.0"` (Node 20 is the oldest still-maintained LTS line).
- Lockfile is the reproducibility contract: `package-lock.json` committed, installs run `npm ci`, drift fails — no fallback to `npm install`.
- All npm deps are **exact pins** (no `^`/`~`). Lockfile is byte-preserving.
- All `extension*` Gradle tasks: if `web-extension/package-lock.json` is absent, print a descriptive skip message and succeed. No exception.
- Root `check` depends on `extensionFormat`, `extensionLint`, `extensionTypecheck`, `extensionTest` (and **not** on `extensionBuild`/`extensionPackage`).
- APK build (`:app:assembleDebug` / `:app:assembleRelease`) must remain unaffected: no new entries in `settings.gradle.kts`, no new dependencies of `:app` tasks.
- Artifact output goes under `build/web-extension/`, **never** inside `web-extension/` source tree (zip path, sha sidecar).
- Manifest policy baseline is fixed by this plan: `manifest_version = 3`, `permissions: []`, no `host_permissions`, CSP `extension_pages = "script-src 'self'; object-src 'self'"` (no `'wasm-unsafe-eval'` in 1a — WASM only arrives in 1b).

## File map

| File | Responsibility |
|---|---|
| `web-extension/package.json` | npm metadata, exact-pinned devDeps, scripts, engines floors |
| `web-extension/package-lock.json` | committed lockfile (reproducibility contract) |
| `web-extension/tsconfig.json` | strict TS config covering `src/`, `vite.config.ts`, `vitest`, `test/` |
| `web-extension/vite.config.ts` | two entry points (`background.ts` → service worker, `content.ts` → content script) |
| `web-extension/eslint.config.js` | ESLint 9 flat config, TS+prettier integration |
| `web-extension/.prettierrc` | prettier settings (no prose wrap, 100-col, single quotes — matches repo Kotlin style) |
| `web-extension/.prettierignore` | exclude `node_modules/`, `dist/`, `package-lock.json` |
| `web-extension/src/manifest.ts` | exports canonical MV3 manifest object (single source of truth) |
| `web-extension/src/background.ts` | service worker entry stub |
| `web-extension/src/content.ts` | content script entry stub |
| `web-extension/test/smoke.test.ts` | vitest: manifest shape, both modules compile |
| `build.gradle.kts` | root Gradle file gaining the `extension` task group and `check` wiring |
| `.gitignore` | add `web-extension/node_modules/`, `web-extension/dist/` |
| `.github/workflows/ci.yml` | **modify** — add `actions/setup-node` + `npm ci` to the two JVM jobs (`lint-check`, `unit-tests`) so the `check`-wired extension tasks actually find node on PATH in CI |

---

### Task 1: Bootstrap `web-extension/` npm project

**Files:**
- Create: `web-extension/package.json`
- Create: `web-extension/.gitignore` (project-local, keeps the root one clean of node entries)
- Create: `web-extension/tsconfig.json`
- Create: `web-extension/vite.config.ts`
- Create: `web-extension/eslint.config.js`
- Create: `web-extension/.prettierrc`
- Create: `web-extension/.prettierignore`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `web-extension/package.json` with `scripts` keys `format`, `format:check`, `lint`, `typecheck`, `test`, `build` — Gradle tasks in Task 3+ invoke these via `npm run <name>`.

- [ ] **Step 1: Create `web-extension/package.json` with exact-pinned devDependencies**

```json
{
  "name": "@veles/web-extension",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "description": "Veles OTP browser extension (MV3). Skeleton toolchain for OTP-01 sub-project 1a.",
  "engines": {
    "node": ">=20.0.0",
    "npm": ">=10.0.0"
  },
  "scripts": {
    "format": "prettier --write .",
    "format:check": "prettier --check .",
    "lint": "eslint .",
    "typecheck": "tsc --noEmit",
    "test": "vitest run",
    "build": "vite build"
  },
  "devDependencies": {
    "@eslint/js": "9.39.2",
    "@types/chrome": "0.1.35",
    "@types/node": "24.10.13",
    "eslint": "9.39.2",
    "eslint-config-prettier": "10.1.8",
    "prettier": "3.8.0",
    "typescript": "5.7.3",
    "typescript-eslint": "8.57.0",
    "vite": "6.4.2",
    "vitest": "3.2.4"
  }
}
```

Exact versions above are placeholders to be resolved in Step 3 — they exist as of writing but verify with `npm view <pkg>@<v> version` and substitute the **latest stable exact pin** if any is unavailable. Every entry must be an exact version (no `^`/`~`).

- [ ] **Step 2: Create config files**

`web-extension/tsconfig.json`:

```json
{
  "compilerOptions": {
    "target": "ES2022",
    "module": "ESNext",
    "moduleResolution": "bundler",
    "strict": true,
    "noEmit": true,
    "esModuleInterop": true,
    "isolatedModules": true,
    "skipLibCheck": true,
    "forceConsistentCasingInFileNames": true,
    "types": ["chrome", "node"],
    "lib": ["ES2022", "DOM", "DOM.Iterable"],
    "resolveJsonModule": true
  },
  "include": ["src", "test", "vite.config.ts", "eslint.config.js"]
}
```

`web-extension/vite.config.ts` (skeleton — two entry points, emitted to `dist/`; manifest copy step added in Task 2):

```ts
import { defineConfig } from 'vite';
import { resolve } from 'node:path';

export default defineConfig({
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    minify: false, // deterministic output; minification is a post-1a concern
    rollupOptions: {
      input: {
        background: resolve(__dirname, 'src/background.ts'),
        content: resolve(__dirname, 'src/content.ts'),
      },
      output: {
        entryFileNames: '[name].js',
        chunkFileNames: 'chunks/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
  },
});
```

`web-extension/eslint.config.js`:

```js
import js from '@eslint/js';
import ts from 'typescript-eslint';
import prettier from 'eslint-config-prettier/flat';

export default ts.config(
  js.configs.recommended,
  ...ts.configs.strict,
  ...ts.configs.stylistic,
  prettier,
  {
    ignores: ['dist/**', 'node_modules/**', 'coverage/**'],
  },
);
```

`web-extension/.prettierrc`:

```json
{
  "printWidth": 100,
  "singleQuote": true,
  "trailingComma": "all",
  "proseWrap": "preserve",
  "endOfLine": "lf"
}
```

`web-extension/.prettierignore`:

```
node_modules/
dist/
coverage/
package-lock.json
```

`web-extension/.gitignore`:

```
node_modules/
dist/
coverage/
```

- [ ] **Step 3: Resolve dependency pins**

Run: `cd web-extension && for p in '@eslint/js' '@types/chrome' '@types/node' eslint eslint-config-prettier prettier typescript typescript-eslint vite vitest; do npm view "$p" version; done`

If any version printed differs from `package.json`, update `package.json` to the printed exact version. Then run `npm install` to generate `package-lock.json`.

Expected: `package-lock.json` exists; `git status` shows it untracked.

- [ ] **Step 4: Commit**

```bash
git add web-extension/package.json web-extension/package-lock.json web-extension/.gitignore web-extension/tsconfig.json web-extension/vite.config.ts web-extension/eslint.config.js web-extension/.prettierrc web-extension/.prettierignore
git -c commit.gpgsign=false commit -m "feat(otp-01/1a): bootstrap web-extension npm project (vite+ts+vitest+eslint+prettier)"
```

---

### Task 2: Extension skeleton (manifest + two entry points + smoke tests)

**Files:**
- Create: `web-extension/src/manifest.ts`
- Create: `web-extension/src/background.ts`
- Create: `web-extension/src/content.ts`
- Create: `web-extension/test/smoke.test.ts`
- Modify: `web-extension/vite.config.ts` — emit `dist/manifest.json` from `src/manifest.ts` via a tiny plugin.

**Interfaces:**
- Consumes: `web-extension/` scaffolding from Task 1.
- Produces: `buildExtensionManifest(): chrome.runtime.ManifestV3` (the canonical manifest shape — re-imported by the package-time guard in Task 5), plus `dist/manifest.json` in the build output.

- [ ] **Step 1: Write the failing smoke test**

`web-extension/test/smoke.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { buildExtensionManifest } from '../src/manifest';

describe('extension manifest', () => {
  it('is a valid MV3 manifest with locked-down baseline', () => {
    const m = buildExtensionManifest();
    expect(m.manifest_version).toBe(3);
    expect(m.name).toBe('Veles OTP');
    expect(m.version).toBe('0.1.0');
    expect(m.permissions ?? []).toEqual([]);
    expect(m.host_permissions ?? []).toEqual([]);
    expect(m.background?.service_worker).toBe('background.js');
    expect(m.content_scripts?.[0]?.js).toEqual(['content.js']);
  });

  it('uses a restrictive CSP without wasm-unsafe-eval (WASM arrives in 1b)', () => {
    const csp = buildExtensionManifest().content_security_policy?.extension_pages ?? '';
    expect(csp).toContain("script-src 'self'");
    expect(csp).not.toContain('wasm-unsafe-eval');
    expect(csp).not.toContain('unsafe-eval');
    expect(csp).not.toContain('unsafe-inline');
  });
});

describe('entry-point modules', () => {
  it('background.ts and content.ts compile and export', async () => {
    await expect(import('../src/background')).resolves.toBeDefined();
    await expect(import('../src/content')).resolves.toBeDefined();
  });
});
```

- [ ] **Step 2: Run the test and verify it fails**

Run: `cd web-extension && npm test`
Expected: FAIL — `Cannot find module '../src/manifest'`.

- [ ] **Step 3: Write the manifest generator**

`web-extension/src/manifest.ts`:

```ts
/// <reference types="chrome" />

export function buildExtensionManifest(): chrome.runtime.ManifestV3 {
  return {
    manifest_version: 3,
    name: 'Veles OTP',
    version: '0.1.0',
    description:
      'Delivers one-time passcodes from the Veles Android app to this browser over an authenticated local channel.',
    permissions: [],
    background: { service_worker: 'background.js' },
    content_scripts: [
      {
        matches: ['https://*/*'],
        js: ['content.js'],
        run_at: 'document_idle',
      },
    ],
    content_security_policy: {
      extension_pages: "script-src 'self'; object-src 'self'",
    },
  };
}
```

- [ ] **Step 4: Write the entry stubs**

`web-extension/src/background.ts`:

```ts
// MV3 service worker entry — skeleton for OTP-01 1a.
// Real connector/offscreen lifecycle lands in later OTP tasks.
chrome.runtime.onInstalled.addListener(() => {
  // no-op placeholder
});

export {};
```

`web-extension/src/content.ts`:

```ts
// Content script entry — skeleton for OTP-01 1a.
// Kept minimal to keep CSP/permissions surface at the locked-down baseline.
export {};
```

- [ ] **Step 5: Wire `vite.config.ts` to emit `dist/manifest.json`**

Modify `web-extension/vite.config.ts` to add a tiny plugin importing `src/manifest.ts` directly:

```ts
import { defineConfig, type Plugin } from 'vite';
import { resolve } from 'node:path';
import { buildExtensionManifest } from './src/manifest';

function emitManifest(): Plugin {
  return {
    name: 'veles-emit-manifest',
    generateBundle() {
      this.emitFile({
        type: 'asset',
        fileName: 'manifest.json',
        source: JSON.stringify(buildExtensionManifest(), null, 2) + '\n',
      });
    },
  };
}

export default defineConfig({
  plugins: [emitManifest()],
  build: {
    outDir: 'dist',
    emptyOutDir: true,
    sourcemap: false,
    minify: false,
    rollupOptions: {
      input: {
        background: resolve(__dirname, 'src/background.ts'),
        content: resolve(__dirname, 'src/content.ts'),
      },
      output: {
        entryFileNames: '[name].js',
        chunkFileNames: 'chunks/[name]-[hash].js',
        assetFileNames: 'assets/[name]-[hash][extname]',
      },
    },
  },
});
```

(`generateBundle` on a Vite plugin fires during build — no extra dependency.)

- [ ] **Step 6: Run vitest and verify pass**

Run: `cd web-extension && npm test`
Expected: PASS — 3 tests green.

- [ ] **Step 7: Run build and inspect `dist/manifest.json`**

Run: `cd web-extension && npm run build && cat dist/manifest.json`
Expected: valid MV3 JSON matching the smoke tests, `background.js` and `content.js` also present in `dist/`.

- [ ] **Step 8: Run format, lint, typecheck**

Run: `cd web-extension && npm run format:check && npm run lint && npm run typecheck`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add web-extension/src web-extension/test web-extension/vite.config.ts
git -c commit.gpgsign=false commit -m "feat(otp-01/1a): add MV3 skeleton (manifest generator, background/content stubs, smoke tests)"
```

---

### Task 3: Gradle task group `extension` (install/format/lint/typecheck/test)

**Files:**
- Modify: `build.gradle.kts` (root)

**Interfaces:**
- Consumes: `web-extension/package-lock.json` from Task 1, scripts from Task 2.
- Produces: Gradle tasks `extensionInstall`, `extensionFormat`, `extensionLint`, `extensionTypecheck`, `extensionTest`, all wired into root `check`. Helper function `execNpm(script: String)` — reused by Task 4 for `extensionBuild` and Task 5 does not invoke npm.

- [ ] **Step 1: Write the failing smoke check**

Run: `./gradlew tasks --all | grep extension`
Expected: no tasks found (this validates the "before" state).

- [ ] **Step 2: Add the task group to `build.gradle.kts`**

Append to `build.gradle.kts`:

```kotlin
// OTP-01 sub-project 1a — web-extension toolchain task group.
// All tasks skip with a descriptive message when web-extension/package-lock.json is absent,
// so Android-only and verify/ workflows keep working without node on PATH.
val webExtDir = rootDir.resolve("web-extension")
val webExtLockfile = webExtDir.resolve("package-lock.json")
val webExtAvailable = providers.gradleProperty("veles.skipWebExt").map { false }.orElse(true).get() &&
    webExtLockfile.isFile

fun registerWebExtExecTask(name: String, description: String, script: String): TaskProvider<Exec> =
    tasks.register<Exec>(name) {
        group = "extension"
        this.description = description
        onlyIf {
            if (!webExtAvailable) {
                logger.lifecycle(
                    "Skipping $name: web-extension/package-lock.json absent " +
                        "(or -Pveles.skipWebExt=true set); web-extension toolchain not required for APK workflows.",
                )
                false
            } else {
                true
            }
        }
        workingDir = webExtDir
        commandLine = listOf("npm", "run", script)
    }

val extensionInstall = tasks.register<Exec>("extensionInstall") {
    group = "extension"
    description = "Run `npm ci` in web-extension/ to hydrate node_modules per the committed lockfile."
    onlyIf {
        if (!webExtAvailable) {
            logger.lifecycle("Skipping extensionInstall: web-extension/package-lock.json absent.")
            false
        } else {
            true
        }
    }
    workingDir = webExtDir
    commandLine = listOf("npm", "ci")
    inputs.file(webExtLockfile)
    outputs.dir(webExtDir.resolve("node_modules"))
}

val extensionFormat = registerWebExtExecTask(
    name = "extensionFormat",
    description = "Run prettier --check over web-extension/.",
    script = "format:check",
).apply { configure { dependsOn(extensionInstall) } }

val extensionLint = registerWebExtExecTask(
    name = "extensionLint",
    description = "Run eslint over web-extension/.",
    script = "lint",
).apply { configure { dependsOn(extensionInstall) } }

val extensionTypecheck = registerWebExtExecTask(
    name = "extensionTypecheck",
    description = "Run tsc --noEmit over web-extension/.",
    script = "typecheck",
).apply { configure { dependsOn(extensionInstall) } }

val extensionTest = registerWebExtExecTask(
    name = "extensionTest",
    description = "Run vitest run in web-extension/.",
    script = "test",
).apply { configure { dependsOn(extensionInstall) } }

tasks.named("check") {
    dependsOn(extensionFormat, extensionLint, extensionTypecheck, extensionTest)
}
```

- [ ] **Step 3: Verify tasks register and skip when appropriate**

Run: `./gradlew tasks --all | grep -E 'extension(Install|Format|Lint|Typecheck|Test|Build|Package)'`
Expected: all seven tasks listed under "extension" group (Build and Package added in Task 4).

Since `web-extension/package-lock.json` exists from Task 1, also run:

Run: `./gradlew :extensionInstall :extensionFormat :extensionLint :extensionTypecheck :extensionTest`
Expected: all pass (node is on PATH per Global Constraints).

Verify skip path:

Run: `mv web-extension/package-lock.json /tmp/ && ./gradlew extensionInstall extensionTest && mv /tmp/package-lock.json web-extension/`
Expected: tasks SKIPPED with the descriptive message; build still succeeds.

- [ ] **Step 4: Commit**

```bash
git add build.gradle.kts
git -c commit.gpgsign=false commit -m "feat(otp-01/1a): add Gradle extension task group (install/format/lint/typecheck/test) wired into check"
```

---

### Task 4: `extensionBuild` and `extensionPackage` (deterministic zip)

**Files:**
- Modify: `build.gradle.kts` (append to the `extension` block added in Task 3)

**Interfaces:**
- Consumes: `extensionInstall`, helper `registerWebExtExecTask`, `webExtAvailable` flag, `webExtDir`.
- Produces: tasks `extensionBuild` (invokes `vite build`) and `extensionPackage` (Gradle `Zip` of `dist/`). Output goes to `build/web-extension/veles-extension-<version>.zip`. Version read from `web-extension/package.json` `version` field.

- [ ] **Step 1: Add `extensionBuild`**

Append inside the `extension` task-block region of `build.gradle.kts`:

```kotlin
val extensionBuild = registerWebExtExecTask(
    name = "extensionBuild",
    description = "Run vite build in web-extension/ to produce dist/.",
    script = "build",
).apply {
    configure {
        dependsOn(extensionInstall, extensionTypecheck)
        outputs.dir(webExtDir.resolve("dist"))
    }
}
```

- [ ] **Step 2: Add `extensionPackage` with deterministic zip settings**

Also append:

```kotlin
val extensionPackage = tasks.register<Zip>("extensionPackage") {
    group = "extension"
    description = "Package web-extension/dist into a deterministic, reproducible zip under build/web-extension/."
    onlyIf {
        if (!webExtAvailable) {
            logger.lifecycle("Skipping extensionPackage: web-extension/package-lock.json absent.")
            false
        } else {
            true
        }
    }
    dependsOn(extensionBuild)

    // Read version from web-extension/package.json (single source of truth for the extension artifact).
    val pkg = groovy.json.JsonSlurper().parse(webExtDir.resolve("package.json")) as Map<*, *>
    val extVersion = pkg["version"].toString()

    archiveFileName.set("veles-extension-$extVersion.zip")
    destinationDirectory.set(layout.buildDirectory.dir("web-extension"))

    from(webExtDir.resolve("dist"))

    // Deterministic-zip recipe (see spec "Deterministic packaging recipe"):
    isReproducibleFileOrder = true
    isPreserveFileTimestamps = false
    dirMode = "0755".toInt(8)
    fileMode = "0644".toInt(8)
    entryCompression = ZipEntryCompression.DEFLATED
    // Gradle Zip tasks already set constant DOS-time when preserveFileTimestamps=false.
}
```

- [ ] **Step 3: Verify output and determinism**

Run: `./gradlew clean extensionPackage && sha256sum build/web-extension/*.zip && ./gradlew clean extensionPackage && sha256sum build/web-extension/*.zip`
Expected: the two sha256 values are identical.

- [ ] **Step 4: Commit**

```bash
git add build.gradle.kts
git -c commit.gpgsign=false commit -m "feat(otp-01/1a): add extensionBuild + deterministic extensionPackage zip"
```

---

### Task 5: Manifest/CSP guard at package time

**Files:**
- Modify: `build.gradle.kts` — add a `validateExtensionManifest` task and make `extensionPackage` depend on it; the guard re-imports the manifest from the built `dist/manifest.json` (not from source) so it inspects the actual artifact.
- Modify: `web-extension/test/smoke.test.ts` — extend to assert the zipped file list matches an expected snapshot.

**Interfaces:**
- Consumes: `extensionBuild` output at `web-extension/dist/manifest.json`.
- Produces: `validateExtensionManifest` task in group `extension`, wired so `extensionPackage.dependsOn(validateExtensionManifest)`. The zip-snapshot assertion in vitest guards against silent file additions by future Vite/plugin bumps.

- [ ] **Step 1: Extend smoke test with zip entry snapshot**

Append to `web-extension/test/smoke.test.ts`:

```ts
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';

describe('dist/ contents (post-build snapshot)', () => {
  function walk(dir: string, base = ''): string[] {
    return readdirSync(dir).flatMap((entry) => {
      const path = join(dir, entry);
      const rel = join(base, entry);
      return statSync(path).isDirectory() ? walk(path, rel) : [rel.replace(/\\/g, '/')];
    });
  }

  it('emits exactly the expected file set', () => {
    // Run after `npm run build`; if dist/ is missing this fails loudly, which is what we want.
    const files = walk(join(__dirname, '..', 'dist')).sort();
    // Hash-named chunks/assets must be excluded — their names vary by build. We assert
    // only the stable top-level entries.
    const stable = files.filter((f) => !f.includes('-'));
    expect(stable).toEqual(['background.js', 'content.js', 'manifest.json']);
  });

  it('emitted manifest matches the canonical generator', () => {
    const emitted = JSON.parse(
      readFileSync(join(__dirname, '..', 'dist', 'manifest.json'), 'utf8'),
    );
    expect(emitted).toEqual(buildExtensionManifest());
  });
});
```

- [ ] **Step 2: Run vitest, expect it fails (dist/ missing or stale after Task 4's cleans)**

Run: `cd web-extension && npm test`
Expected: FAIL — no `dist/`. Then `npm run build && npm test` → PASS. This proves the snapshot test reacts to build output.

- [ ] **Step 3: Add the Gradle guard**

Append to the `extension` block in `build.gradle.kts`:

```kotlin
val validateExtensionManifest = tasks.register("validateExtensionManifest") {
    group = "extension"
    description = "Validate web-extension/dist/manifest.json against the locked-down MV3 baseline."
    onlyIf {
        if (!webExtAvailable) {
            logger.lifecycle("Skipping validateExtensionManifest: web-extension/package-lock.json absent.")
            false
        } else {
            true
        }
    }
    dependsOn(extensionBuild)
    inputs.file(webExtDir.resolve("dist/manifest.json"))
    doLast {
        val manifestFile = webExtDir.resolve("dist/manifest.json")
        check(manifestFile.isFile) { "Expected $manifestFile after extensionBuild; not found." }
        @Suppress("UNCHECKED_CAST")
        val m = groovy.json.JsonSlurper().parse(manifestFile) as Map<String, Any?>
        fun fail(reason: String): Nothing =
            throw GradleException("web-extension manifest guard failed: $reason")
        if (m["manifest_version"] != 3) fail("manifest_version must be 3, got ${m["manifest_version"]}")
        val perms = (m["permissions"] as? List<*>) ?: emptyList<Any>()
        if (perms.isNotEmpty()) fail("permissions must be empty in 1a, got $perms")
        val hostPerms = (m["host_permissions"] as? List<*>) ?: emptyList<Any>()
        if (hostPerms.isNotEmpty()) fail("host_permissions must be empty in 1a, got $hostPerms")
        val csp = ((m["content_security_policy"] as? Map<*, *>)?.get("extension_pages") as? String).orEmpty()
        if ("wasm-unsafe-eval" in csp) fail("wasm-unsafe-eval not permitted in 1a")
        if ("unsafe-eval" in csp) fail("unsafe-eval not permitted")
        if ("unsafe-inline" in csp) fail("unsafe-inline not permitted")
        logger.lifecycle("web-extension manifest guard: MV3 baseline OK.")
    }
}

extensionPackage.configure { dependsOn(validateExtensionManifest) }
```

- [ ] **Step 4: Verify the guard bites**

Run: `./gradlew extensionPackage`
Expected: BUILD SUCCESSFUL, guard log line visible.

Smoke-violate the guard temporarily: edit `web-extension/src/manifest.ts` to add `"wasm-unsafe-eval"` to the CSP, run `./gradlew clean extensionPackage`, expect FAILURE at `validateExtensionManifest` naming wasm-unsafe-eval. Revert.

- [ ] **Step 5: Commit**

```bash
git add build.gradle.kts web-extension/test/smoke.test.ts
git -c commit.gpgsign=false commit -m "feat(otp-01/1a): add manifest/CSP guard + vitest dist snapshot"
```

---

### Task 6: CI wiring (setup Node in the two JVM jobs)

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: tasks from Tasks 3–5 (which run under `./gradlew spotlessCheck`, `./gradlew detekt`, `./gradlew testDebugUnitTest`, `./gradlew check`-adjacent entry points).
- Produces: Node 22 (LTS) + cached `~/.npm` available to `lint-check` and `unit-tests` jobs. `instrumented-tests` job is **not** modified — it doesn't run extension tasks (and running them there would needlessly extend emulator time).

- [ ] **Step 1: Add `setup-node` to both jobs**

Insert into `.github/workflows/ci.yml` after each `actions/setup-java` step in the `lint-check` and `unit-tests` jobs (not in `instrumented-tests`):

```yaml
      - uses: actions/setup-node@v6
        with:
          node-version: '22'
          cache: 'npm'
          cache-dependency-path: web-extension/package-lock.json
      - name: Install web-extension dependencies
        working-directory: web-extension
        run: npm ci
```

Node 22 chosen because it is the LTS line currently shipping npm 11, satisfying the `engines.npm >= 10` floor without overshooting into a just-released major.

- [ ] **Step 2: Verify workflow YAML parses**

Run: `python3 -c "import yaml,sys; yaml.safe_load(open('.github/workflows/ci.yml'))"` (or use `actionlint` if installed: `actionlint .github/workflows/ci.yml`)
Expected: no parse errors.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git -c commit.gpgsign=false commit -m "ci(otp-01/1a): set up node + npm ci in lint-check and unit-tests jobs"
```

---

### Task 7: Documentation updates + final `check` wiring validation

**Files:**
- Modify: `CLAUDE.md` — add new commands to "Build & Test Commands".
- Modify: `docs/reproducible-builds.md` — add a sentence noting the web-extension toolchain joins the pinned-toolchain family (full wiring lands in 1d).
- Modify: `README.md` — one-line "Sub-projects" pointer if a logical spot exists; otherwise skip (don't fake a section).

**Interfaces:**
- Consumes: every task in group `extension` from Tasks 3–5.
- Produces: user-visible documentation matching reality; final `./gradlew clean check` green on a clean working tree.

- [ ] **Step 1: Update `CLAUDE.md`**

Insert into the "Build & Test Commands" block after `connectedDebugAndroidTest`:

```bash
# Web extension (OTP-01 sub-project 1a) — requires node/npm on PATH
./gradlew extensionInstall extensionFormat extensionLint extensionTypecheck extensionTest
./gradlew extensionBuild extensionPackage  # produces build/web-extension/veles-extension-<version>.zip
```

- [ ] **Step 2: Update `docs/reproducible-builds.md`**

Append after the "Pinned toolchain" table:

```markdown
The web-extension toolchain (node, npm, vite, vitest, eslint, prettier) is pinned via
`web-extension/package.json` (exact versions, no carets) and `web-extension/package-lock.json`.
Its Docker-based reference environment and byte-compare harness land in OTP-01 sub-project 1d.
```

- [ ] **Step 3: Commit docs**

```bash
git add CLAUDE.md docs/reproducible-builds.md
git -c commit.gpgsign=false commit -m "docs(otp-01/1a): document web-extension toolchain + reproducibility note"
```

- [ ] **Step 4: Final end-to-end verification on a clean tree**

Run: `git status` (expect clean), then `./gradlew clean check extensionPackage`
Expected: SUCCESS — all unit tests pass, all lint/format/typecheck pass, zip produced at `build/web-extension/veles-extension-0.1.0.zip`.

Run: `./gradlew :app:assembleDebug`
Expected: SUCCESS — APK build unaffected by the new task group.

- [ ] **Step 5: Final commit (if any straggler files)**

```bash
git status — should be clean already. If not, `git add -A && git -c commit.gpgsign=false commit -m "chore(otp-01/1a): final cleanup"`
```

---

## Self-review status

- **Spec coverage:** every spec requirement under "Sub-project 1a" maps to a task: layout → Tasks 1–2; Gradle task table → Tasks 3–5; deterministic zip recipe → Task 4; error-handling skip path → Task 3 helper; manifest/CSP guard → Task 5; wiring into `check` → Task 3; versioning → Task 4 (reads `package.json`); CI wiring → Task 6 (caught in review — `ci.yml` had no `setup-node`); documentation → Task 7. Global Constraints section re-states spec Global decisions 1–5.
- **Placeholder scan:** none — every code block has concrete content, every command is runnable as written (subject to exact-pin substitution check in Task 1 Step 3).
- **Type consistency:** helper `registerWebExtExecTask` defined Task 3, consumed Task 4 (`extensionBuild`). Flags `webExtAvailable`/`webExtDir`/`webExtLockfile` defined Task 3, consumed Tasks 4–5. Smoke test edited in Task 2 Step 1 and extended in Task 5 Step 1 — additive, new imports, no name collision.
