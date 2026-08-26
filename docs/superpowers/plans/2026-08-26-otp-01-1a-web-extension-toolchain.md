# OTP-01 Sub-project 1a — TypeScript/MV3 extension toolchain — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up `web-extension/` (Vite + TypeScript skeleton, vitest, eslint, prettier) and root Gradle tasks that install, format, lint, type-check, test, build, validate, and deterministically zip it — wired into root `check`, reproducible, and gated by a strict manifest/CSP exact-match.

**Architecture:** A new top-level `web-extension/` npm project (not a Gradle subproject) holds a minimal loadable MV3 extension. Root `build.gradle.kts` defines an `extension` task group invoking `npm` via `workingDir = webExtDir`. Every task skips with a descriptive message when `web-extension/package-lock.json` is absent OR when `-Pveles.skipWebExt=true` is passed (value-based, `toBooleanStrict`). `extensionBuild` always re-runs (no `outputs.dir` declared) to eliminate the stale-artifact class of bugs; `extensionInstall` uses fine-grained inputs for correct up-to-date behavior. `extensionPackage` produces a deterministic zip plus a `.zip.sha256` sidecar.

**Tech Stack:** Gradle 8.11.1 Kotlin DSL, Node 22 (LTS, satisfies Vite 6 / Vitest 3 / typescript-eslint 8 engine requirements), Vite 6, TypeScript 5.7, vitest 3, eslint 9 (flat config), prettier 3.

**Spec:** `docs/superpowers/specs/2026-08-26-otp-01-reproducible-toolchains-design.md` (sub-project 1a section + Global decisions). Read both before starting.

## Global Constraints

- node/npm come from PATH; Gradle never downloads them.
- `web-extension/package.json` `engines.node = ">=22.0.0"` — matches CI (Node 22 LTS) and the strictest devDep (typescript-eslint@8.57 requires ≥20.9; Vite 6 / Vitest 3 require `^18 || ^20 || >=22`).
- No `engines.npm` entry — npm version is implied by node major.
- Lockfile is the reproducibility contract: `package-lock.json` committed, installs run `npm ci`, drift fails — no fallback to `npm install`.
- All npm deps are **exact pins** (no `^`/`~`). The pins listed in Task 1 are the reviewed set — verify availability with `npm view <pkg>@<version> version` and substitute only if a pin is unavailable; do not float to latest.
- All `extension*` tasks skip (with a descriptive log) when `web-extension/package-lock.json` is absent or `-Pveles.skipWebExt=true` is passed.
- Root `check` depends on `extensionFormat`, `extensionLint`, `extensionTypecheck`, `extensionTest` only — never on `extensionBuild`, `validateExtensionManifest`, `extensionArtifactTest`, or `extensionPackage`. `extensionTest` must be **source-only** (never reads `dist/`); bundle assertions live in a separate task.
- APK build (`:app:assembleDebug` / `:app:assembleRelease`) must remain unaffected: no new entries in `settings.gradle.kts`, no new dependencies of `:app` tasks.
- Artifact zips and sha sidecars land under `build/web-extension/`. `web-extension/dist/` is a deliberate exception (Vite's conventional output); root `clean` is extended to delete it.
- Manifest policy baseline is fixed by this plan and enforced exactly:
  - `manifest_version == 3`
  - `permissions` is exactly `[]`
  - `host_permissions` absent
  - `content_security_policy` exactly `{ extension_pages: "script-src 'self'; object-src 'self'" }`
  - No `'wasm-unsafe-eval'` in 1a (allowed starting in 1b).

## File map

| File | Responsibility |
|---|---|
| `web-extension/package.json` | npm metadata, exact-pinned devDeps, scripts, `engines.node` |
| `web-extension/package-lock.json` | committed lockfile (reproducibility contract) |
| `web-extension/tsconfig.json` | strict TS config, `resolveJsonModule: true` for version import |
| `web-extension/vite.config.ts` | two entry points + plugin that emits `manifest.json` into `dist/` |
| `web-extension/eslint.config.js` | ESLint 9 flat config, TS + prettier integration |
| `web-extension/.prettierrc` | prettier settings (100-col, single quotes, LF) |
| `web-extension/.prettierignore` | exclude `node_modules/`, `dist/`, `package-lock.json` |
| `web-extension/.gitignore` | exclude `node_modules/`, `dist/`, `coverage/` |
| `web-extension/src/manifest.ts` | exports canonical MV3 manifest; reads version from `package.json` |
| `web-extension/src/background.ts` | service worker entry stub (browser-global access behind a guard) |
| `web-extension/src/content.ts` | content script entry stub |
| `web-extension/test/setup.ts` | vitest global setup; stubs `chrome` API surface |
| `web-extension/test/smoke.test.ts` | vitest: manifest shape, both modules compile (chrome stubbed) |
| `web-extension/test/bundle.test.ts` | vitest: `dist/` file-set and manifest against canonical shape |
| `build.gradle.kts` | root Gradle file gaining the `extension` task group, `clean` extension, `check` wiring |
| `.github/workflows/ci.yml` | new `web-extension` job running the extension tasks under `setup-node@v6` |
| `CLAUDE.md`, `docs/reproducible-builds.md` | documentation updates in Task 7 |

---

### Task 1: Bootstrap `web-extension/` npm project

**Files:**
- Create: `web-extension/package.json`
- Create: `web-extension/.gitignore`
- Create: `web-extension/tsconfig.json`
- Create: `web-extension/vite.config.ts` (skeleton — manifest-emission wired in Task 2)
- Create: `web-extension/eslint.config.js`
- Create: `web-extension/.prettierrc`
- Create: `web-extension/.prettierignore`

**Interfaces:**
- Consumes: nothing (first task).
- Produces: `web-extension/package.json` with scripts `format:check`, `lint`, `typecheck`, `test`, `test:bundle`, `build` — Gradle tasks in Task 3+ invoke these via `npm run <name>`. `vitest` is configured to load `test/setup.ts` globally.

- [ ] **Step 1: Create `web-extension/package.json` with the reviewed exact pins**

```json
{
  "name": "@veles/web-extension",
  "version": "0.1.0",
  "private": true,
  "type": "module",
  "description": "Veles OTP browser extension (MV3). Skeleton toolchain for OTP-01 sub-project 1a.",
  "engines": {
    "node": ">=22.0.0"
  },
  "scripts": {
    "format": "prettier --write .",
    "format:check": "prettier --check .",
    "lint": "eslint .",
    "typecheck": "tsc --noEmit",
    "test": "vitest run test/smoke.test.ts",
    "test:bundle": "vitest run test/bundle.test.ts",
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

Version-pinning rule: this set is reviewed as a unit. **Do not float to latest** — the plan was reviewed against these versions.

- [ ] **Step 2: Verify each pin is actually available**

```bash
cd web-extension
for pin in '@eslint/js@9.39.2' '@types/chrome@0.1.35' '@types/node@24.10.13' \
           'eslint@9.39.2' 'eslint-config-prettier@10.1.8' 'prettier@3.8.0' \
           'typescript@5.7.3' 'typescript-eslint@8.57.0' 'vite@6.4.2' 'vitest@3.2.4'; do
  echo -n "$pin -> "; npm view "$pin" version 2>&1 | tail -1
done
```

Expected: each line prints the same version string (not a 404 or registry error). If a pin is unavailable, replace **only that entry** with the closest stable earlier release in the same major line and note the substitution in the commit message.

- [ ] **Step 3: Create config files**

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
    "resolveJsonModule": true,
    "types": ["chrome", "node"],
    "lib": ["ES2022", "DOM", "DOM.Iterable"]
  },
  "include": ["src", "test", "vite.config.ts", "eslint.config.js"]
}
```

`web-extension/vite.config.ts` (skeleton — manifest emit lands in Task 2; vitest configured to load the test setup):

```ts
import { defineConfig } from 'vitest/config';
import { resolve } from 'node:path';

export default defineConfig({
  test: {
    setupFiles: ['./test/setup.ts'],
    environment: 'node',
  },
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

(Using `vitest/config` instead of `vite` so the `test` block type-checks; Vite still uses the same export.)

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

- [ ] **Step 4: Generate `package-lock.json`**

Run: `cd web-extension && npm install`
Expected: `package-lock.json` produced; `git status` shows it untracked.

Sanity-check the lockfile didn't widen any pin:
Run: `grep -E '"(vite|vitest|typescript|eslint)": "' web-extension/package.json`
Expected: every value still matches the exact pin in Step 1.

- [ ] **Step 5: Commit**

```bash
git add web-extension/package.json web-extension/package-lock.json web-extension/.gitignore \
        web-extension/tsconfig.json web-extension/vite.config.ts web-extension/eslint.config.js \
        web-extension/.prettierrc web-extension/.prettierignore
git -c commit.gpgsign=false commit -m "feat(otp-01/1a): bootstrap web-extension npm project (vite+ts+vitest+eslint+prettier)"
```

---

### Task 2: Extension skeleton (manifest + two entry points + stubbed-chrome smoke tests)

**Files:**
- Create: `web-extension/src/manifest.ts` — reads version from `package.json` so zip name and manifest version stay in sync
- Create: `web-extension/src/background.ts` — guards against missing `chrome` global so it can be imported under vitest/Node
- Create: `web-extension/src/content.ts`
- Create: `web-extension/test/setup.ts` — vitest global setup, stubs minimum `chrome` API surface
- Create: `web-extension/test/smoke.test.ts`
- Modify: `web-extension/vite.config.ts` — add `generateBundle` plugin emitting `dist/manifest.json`

**Interfaces:**
- Consumes: scaffolding from Task 1.
- Produces:
  - `buildExtensionManifest(): chrome.runtime.ManifestV3` — used by Task 5's Vite plugin to emit `dist/manifest.json` and by vitest bundle assertions as the canonical shape (single source of truth on the TypeScript side).
  - Task 5's Gradle `validateExtensionManifest` parses the emitted `dist/manifest.json` JSON and validates it independently — the two systems share the baseline values via the JSON output, not via cross-language function reuse.

- [ ] **Step 1: Write the failing smoke test**

`web-extension/test/setup.ts`:

```ts
import { vi } from 'vitest';

// MV3 APIs are browser-globals. Stub the minimum surface background.ts uses
// so the module can be imported under vitest's Node environment.
vi.stubGlobal('chrome', {
  runtime: {
    onInstalled: { addListener: vi.fn() },
    getManifest: vi.fn(),
  },
});
```

`web-extension/test/smoke.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { buildExtensionManifest } from '../src/manifest';
import pkg from '../package.json';

describe('extension manifest', () => {
  it('is a valid MV3 manifest at the locked-down baseline', () => {
    const m = buildExtensionManifest();
    expect(m.manifest_version).toBe(3);
    expect(m.name).toBe('Veles OTP');
    expect(m.version).toBe(pkg.version);
    expect(m.permissions).toEqual([]);
    expect(m).not.toHaveProperty('host_permissions');
    expect(m.background?.service_worker).toBe('background.js');
    expect(m.content_scripts?.[0]?.js).toEqual(['content.js']);
  });

  it('uses a restrictive CSP without wasm-unsafe-eval (allowed in 1b)', () => {
    const csp = buildExtensionManifest().content_security_policy;
    expect(csp).toEqual({ extension_pages: "script-src 'self'; object-src 'self'" });
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
import pkg from '../package.json';

export function buildExtensionManifest(): chrome.runtime.ManifestV3 {
  return {
    manifest_version: 3,
    name: 'Veles OTP',
    version: pkg.version,
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
//
// The chrome-global guard exists so vitest (Node environment) can import this
// module without a browser runtime; the test setup stubs chrome.runtime anyway,
// but defensive coding here prevents a hard ReferenceError if the stub is
// missed in a future test file.
if (typeof chrome !== 'undefined' && chrome.runtime?.onInstalled) {
  chrome.runtime.onInstalled.addListener(() => {
    // no-op placeholder
  });
}

export {};
```

`web-extension/src/content.ts`:

```ts
// Content script entry — skeleton for OTP-01 1a.
// Kept minimal to keep CSP/permissions surface at the locked-down baseline.
export {};
```

- [ ] **Step 5: Add the manifest-emission plugin to `vite.config.ts`**

Replace `web-extension/vite.config.ts` with:

```ts
import { defineConfig } from 'vitest/config';
import type { Plugin } from 'vite';
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
  test: {
    setupFiles: ['./test/setup.ts'],
    environment: 'node',
  },
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

- [ ] **Step 6: Run vitest and verify pass**

Run: `cd web-extension && npm test`
Expected: PASS — 3 tests green.

- [ ] **Step 7: Build and inspect the artifact**

Run: `cd web-extension && npm run build && cat dist/manifest.json`
Expected: valid MV3 JSON; `background.js` and `content.js` also present.

- [ ] **Step 8: Run format, lint, typecheck**

Run: `cd web-extension && npm run format:check && npm run lint && npm run typecheck`
Expected: all pass.

- [ ] **Step 9: Commit**

```bash
git add web-extension/src web-extension/test web-extension/vite.config.ts
git -c commit.gpgsign=false commit -m "feat(otp-01/1a): MV3 skeleton (manifest generator, stubbed-chrome smoke tests)"
```

---

### Task 3: Gradle task group `extension` — toolchain check, install, format/lint/typecheck/test

**Files:**
- Modify: `build.gradle.kts` (root)

**Interfaces:**
- Consumes: `web-extension/package-lock.json` from Task 1, npm scripts from Task 2.
- Produces:
  - `extensionToolchainCheck` — fails fast with the spec's exact message if `node`/`npm` missing or below floor.
  - `extensionInstall`, `extensionFormat`, `extensionLint`, `extensionTypecheck`, `extensionTest` — wired into root `check`.
  - Helper `registerWebExtExecTask(...)` reused by Task 4 (`extensionBuild`) and Task 5 (`extensionArtifactTest`).
  - Root `clean` extended to delete `web-extension/dist/`.

- [ ] **Step 1: Verify the before-state**

Run: `./gradlew tasks --group=extension 2>&1 | head -20`
Expected: `Could not determine the tasks to execute` or no tasks in group `extension`.

- [ ] **Step 2: Add the task group to `build.gradle.kts`**

Append to root `build.gradle.kts`:

```kotlin
// OTP-01 sub-project 1a — web-extension toolchain task group.
// Tasks skip with a descriptive message when web-extension/package-lock.json is absent
// or -Pveles.skipWebExt=true is passed — Android-only and verify/ workflows keep
// working without node on PATH.
val webExtDir = rootDir.resolve("web-extension")
val webExtLockfile = webExtDir.resolve("package-lock.json")
val webExtPkg = webExtDir.resolve("package.json")
val webExtDist = webExtDir.resolve("dist")

// Presence skip is value-based: -Pveles.skipWebExt=false leaves tasks enabled.
val skipWebExt = providers.gradleProperty("veles.skipWebExt")
    .map { it.toBooleanStrict() }
    .orElse(false)

// Toolchain check — fails fast on missing or under-floored node/npm.
val extensionToolchainCheck = tasks.register("extensionToolchainCheck") {
    group = "extension"
    description = "Verify node/npm exist on PATH and satisfy the declared floors."
    onlyIf {
        when {
            skipWebExt.get() -> {
                logger.lifecycle("Skipping extensionToolchainCheck: -Pveles.skipWebExt=true.")
                false
            }
            !webExtLockfile.isFile -> {
                logger.lifecycle("Skipping extensionToolchainCheck: web-extension/package-lock.json absent.")
                false
            }
            else -> true
        }
    }
    doLast {
        fun probe(tool: String, vararg args: String): String {
            return try {
                val proc = ProcessBuilder(tool, *args)
                    .redirectErrorStream(true)
                    .start()
                val out = proc.inputStream.bufferedReader().readText().trim()
                check(proc.waitFor() == 0) { "$tool ${args.joinToString(" ")} exited non-zero" }
                out
            } catch (e: java.io.IOException) {
                throw GradleException(
                    "Could not find '$tool' on PATH; " +
                        "install Node.js >= 22.0.0 (declared in web-extension/package.json engines.node)",
                    e,
                )
            }
        }

        val nodeVersion = probe("node", "--version").removePrefix("v")
        val npmVersion = probe("npm", "--version")

        // Floor check: node major >= 22. npm is bundled with node, so no separate floor.
        val major = nodeVersion.substringBefore('.').toIntOrNull()
            ?: throw GradleException("Unparseable `node --version` output: '$nodeVersion'")
        if (major < 22) {
            throw GradleException(
                "Node.js $nodeVersion is below the declared floor >= 22.0.0 " +
                    "(see web-extension/package.json engines.node).",
            )
        }
        logger.lifecycle("web-extension toolchain: node $nodeVersion, npm $npmVersion — OK.")
    }
}

// npm exec helper — every task that runs npm goes through this.
fun registerWebExtExecTask(taskName: String, taskDescription: String, script: String): TaskProvider<Exec> =
    tasks.register<Exec>(taskName) {
        group = "extension"
        description = taskDescription
        onlyIf {
            when {
                skipWebExt.get() -> {
                    logger.lifecycle("Skipping $taskName: -Pveles.skipWebExt=true.")
                    false
                }
                !webExtLockfile.isFile -> {
                    logger.lifecycle("Skipping $taskName: web-extension/package-lock.json absent.")
                    false
                }
                else -> true
            }
        }
        workingDir = webExtDir
        commandLine = listOf("npm", "run", script)
    }

val extensionInstall = tasks.register<Exec>("extensionInstall") {
    group = "extension"
    description = "Run `npm ci` in web-extension/ to hydrate node_modules per the committed lockfile."
    onlyIf {
        when {
            skipWebExt.get() -> {
                logger.lifecycle("Skipping extensionInstall: -Pveles.skipWebExt=true.")
                false
            }
            !webExtLockfile.isFile -> {
                logger.lifecycle("Skipping extensionInstall: web-extension/package-lock.json absent.")
                false
            }
            else -> true
        }
    }
    dependsOn(extensionToolchainCheck)
    workingDir = webExtDir
    commandLine = listOf("npm", "ci")
    // npm ci is driven by both package.json (dep spec) and lockfile (exact resolution).
    // Also track .npmrc (registry/proxy overrides) — it's optional.
    inputs.file(webExtPkg)
    inputs.file(webExtLockfile)
    inputs.file(webExtDir.resolve(".npmrc")).optional(true)
    outputs.dir(webExtDir.resolve("node_modules"))
}

val extensionFormat = registerWebExtExecTask(
    taskName = "extensionFormat",
    taskDescription = "Run prettier --check over web-extension/.",
    script = "format:check",
)
extensionFormat.configure { dependsOn(extensionInstall) }

val extensionLint = registerWebExtExecTask(
    taskName = "extensionLint",
    taskDescription = "Run eslint over web-extension/.",
    script = "lint",
)
extensionLint.configure { dependsOn(extensionInstall) }

val extensionTypecheck = registerWebExtExecTask(
    taskName = "extensionTypecheck",
    taskDescription = "Run tsc --noEmit over web-extension/.",
    script = "typecheck",
)
extensionTypecheck.configure { dependsOn(extensionInstall) }

val extensionTest = registerWebExtExecTask(
    taskName = "extensionTest",
    taskDescription = "Run vitest run in web-extension/ (source-only; bundle assertions live in extensionArtifactTest).",
    script = "test",
)
extensionTest.configure { dependsOn(extensionInstall) }

// Wire source-level checks into root `check` so CI quality gates ride along.
tasks.named("check") {
    dependsOn(extensionFormat, extensionLint, extensionTypecheck, extensionTest)
}

// Root clean also removes web-extension/dist/ (the one generated-artifact exception
// allowed inside a source tree — see spec Global decision 3).
tasks.named("clean") {
    doLast {
        delete(webExtDist)
    }
}
```

- [ ] **Step 3: Verify tasks register and run**

Run: `./gradlew tasks --group=extension`
Expected: `extensionToolchainCheck`, `extensionInstall`, `extensionFormat`, `extensionLint`, `extensionTypecheck`, `extensionTest` listed.

Run: `./gradlew extensionToolchainCheck extensionInstall extensionFormat extensionLint extensionTypecheck extensionTest`
Expected: all pass (Node 22 present per Global Constraints; `node --version` printed in log).

- [ ] **Step 4: Verify the skip paths (lockfile restored before flag tests)**

```bash
set -e
BACKUP="/tmp/veles-lock-backup-$$"
trap 'mv "$BACKUP" web-extension/package-lock.json 2>/dev/null || true' EXIT

# Phase 1: missing-lockfile skip
mv web-extension/package-lock.json "$BACKUP"
./gradlew extensionInstall extensionTest 2>&1 | grep -E "Skipping extensionInstall|Skipping extensionTest" \
  || { echo "FAIL: missing-lock skip not observed"; exit 1; }

# Phase 2: restore lockfile so the flag paths exercise real runs
mv "$BACKUP" web-extension/package-lock.json

# Phase 3: value-based skip flag
./gradlew -Pveles.skipWebExt=true extensionInstall 2>&1 | grep "Skipping extensionInstall" \
  || { echo "FAIL: skip=true did not skip"; exit 1; }
./gradlew -Pveles.skipWebExt=false extensionInstall \
  || { echo "FAIL: skip=false did not run"; exit 1; }
```

Expected: phase 1 logs skip messages; phase 3 first invocation skips, second runs `npm ci`. The trap is only a safety net for failures mid-script.

- [ ] **Step 5: Verify `clean` removes `web-extension/dist/`**

```bash
cd web-extension && npm run build && cd ..
test -d web-extension/dist || { echo "FAIL: dist/ missing"; exit 1; }
./gradlew clean
test ! -d web-extension/dist || { echo "FAIL: dist/ still present"; exit 1; }
echo OK
```

Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add build.gradle.kts
git -c commit.gpgsign=false commit -m "feat(otp-01/1a): extension task group (toolchain/install/format/lint/typecheck/test) + clean wiring"
```

---

### Task 4: `extensionBuild` and `extensionPackage` (deterministic zip + sha256 sidecar)

**Files:**
- Modify: `build.gradle.kts` — append to the `extension` block from Task 3

**Interfaces:**
- Consumes: `extensionInstall`, `extensionTypecheck`, helper `registerWebExtExecTask`, `webExtDir`, `webExtLockfile`, `skipWebExt`.
- Produces: `extensionBuild` (always re-runs — no `outputs.dir` declared, eliminates stale-artifact class of bugs); `extensionPackage` emitting `build/web-extension/veles-extension-<version>.zip` and `build/web-extension/veles-extension-<version>.zip.sha256`.

- [ ] **Step 1: Add `extensionBuild`**

Append:

```kotlin
val extensionBuild = registerWebExtExecTask(
    taskName = "extensionBuild",
    taskDescription = "Run vite build in web-extension/ to produce dist/. Always re-runs (no outputs.dir declared).",
    script = "build",
)
extensionBuild.configure {
    dependsOn(extensionInstall, extensionTypecheck)
    // Deliberately no outputs.dir: declaring only webExtDir/"dist" without modeling
    // all src inputs (src/**, public/**, vite.config.ts, tsconfig.json, package.json)
    // risks stale-artifact re-use. The build is fast (<10s); always-run is the safer
    // contract for a deterministic-artifact pipeline.
}
```

- [ ] **Step 2: Add `extensionPackage` with deterministic zip + sha256 sidecar**

Append:

```kotlin
val extensionPackage = tasks.register<Zip>("extensionPackage") {
    group = "extension"
    description = "Package web-extension/dist/ into a deterministic zip under build/web-extension/ plus a .sha256 sidecar."
    onlyIf {
        when {
            skipWebExt.get() -> {
                logger.lifecycle("Skipping extensionPackage: -Pveles.skipWebExt=true.")
                false
            }
            !webExtLockfile.isFile -> {
                logger.lifecycle("Skipping extensionPackage: web-extension/package-lock.json absent.")
                false
            }
            else -> true
        }
    }
    dependsOn(extensionBuild)

    // Extension version comes from package.json — same source manifest.ts reads,
    // keeping zip name and manifest content in sync.
    @Suppress("UNCHECKED_CAST")
    val pkg = groovy.json.JsonSlurper().parse(webExtPkg) as Map<String, Any?>
    val extVersion = pkg["version"]?.toString()
        ?: throw GradleException("web-extension/package.json missing 'version'.")

    archiveFileName.set("veles-extension-$extVersion.zip")
    destinationDirectory.set(layout.buildDirectory.dir("web-extension"))

    from(webExtDist)

    // Deterministic-zip recipe — see spec "Deterministic packaging recipe".
    isReproducibleFileOrder = true
    isPreserveFileTimestamps = false
    dirPermissions { unix("0755") }
    filePermissions { unix("0644") }
    entryCompression = ZipEntryCompression.DEFLATED
    metadataCharset = "UTF-8"
    includeEmptyDirs = false

    // sha256 sidecar is a declared output (not just a side-effect file).
    val sidecar = layout.buildDirectory.file("web-extension/veles-extension-$extVersion.zip.sha256")
    outputs.file(sidecar)

    doLast {
        val zip = archiveFile.get().asFile
        val digest = java.security.MessageDigest.getInstance("SHA-256")
            .digest(zip.readBytes())
            .joinToString("") { "%02x".format(it) }
        val side = sidecar.get().asFile
        side.parentFile.mkdirs()
        side.writeText("$digest  ${zip.name}\n")
        logger.lifecycle("Wrote ${side.name}: $digest")
    }
}
```

- [ ] **Step 3: Verify output and byte-determinism**

```bash
./gradlew clean extensionPackage
sha256sum build/web-extension/veles-extension-0.1.0.zip
FIRST=$(cat build/web-extension/veles-extension-0.1.0.zip.sha256 | cut -d' ' -f1)

./gradlew clean extensionPackage
sha256sum build/web-extension/veles-extension-0.1.0.zip
SECOND=$(cat build/web-extension/veles-extension-0.1.0.zip.sha256 | cut -d' ' -f1)

test "$FIRST" = "$SECOND" || { echo "FAIL: non-deterministic zip"; exit 1; }
echo "OK: $FIRST"
```

Expected: same sha256 both runs; `OK` line prints.

- [ ] **Step 4: Commit**

```bash
git add build.gradle.kts
git -c commit.gpgsign=false commit -m "feat(otp-01/1a): extensionBuild + deterministic extensionPackage with sha256 sidecar"
```

---

### Task 5: Strict manifest/CSP guard + dist artifact test

**Files:**
- Modify: `build.gradle.kts` — add `validateExtensionManifest` and `extensionArtifactTest`; wire `extensionPackage.dependsOn` on both.
- Create: `web-extension/test/bundle.test.ts` — vitest against built `dist/` content.
- Modify: `web-extension/package.json` — already has `test:bundle` script from Task 1 (used by `extensionArtifactTest`).

**Interfaces:**
- Consumes: `extensionBuild`, `extensionPackage`, `webExtDist`.
- Produces: `validateExtensionManifest` (Gradle-side, **exact match** against the canonical baseline) and `extensionArtifactTest` (npm-side, asserts `dist/` file set and that `dist/manifest.json` round-trips through `buildExtensionManifest()`).

- [ ] **Step 1: Write the bundle test**

`web-extension/test/bundle.test.ts`:

```ts
import { describe, it, expect } from 'vitest';
import { readFileSync, readdirSync, statSync } from 'node:fs';
import { join } from 'node:path';
import { buildExtensionManifest } from '../src/manifest';

function walk(dir: string, base = ''): string[] {
  return readdirSync(dir).flatMap((entry) => {
    const path = join(dir, entry);
    const rel = base ? `${base}/${entry}` : entry;
    return statSync(path).isDirectory() ? walk(path, rel) : [rel];
  });
}

const DIST = join(__dirname, '..', 'dist');

describe('dist/ contents (post-build artifact)', () => {
  it('contains exactly the expected file set', () => {
    // Run after `npm run build`. The exact list is the contract:
    // any change (new chunk, new asset, new manifest field) breaks this test
    // and forces a conscious review. Hash-named files (chunks/-[hash].js,
    // assets/[name]-[hash][extname]) are part of the asserted list because
    // under the committed lockfile they are deterministic.
    const files = walk(DIST).sort();
    expect(files).toEqual(['background.js', 'content.js', 'manifest.json']);
  });

  it('dist/manifest.json matches the canonical generator exactly', () => {
    const emitted = JSON.parse(readFileSync(join(DIST, 'manifest.json'), 'utf8'));
    expect(emitted).toEqual(JSON.parse(JSON.stringify(buildExtensionManifest())));
  });
});
```

- [ ] **Step 2: Run the bundle test against a fresh build; expect pass**

Run: `cd web-extension && npm run build && npm run test:bundle`
Expected: PASS — 2 tests green. (If the file-set test fails because a chunk file is emitted, that's a real signal — investigate why before editing the expected list.)

Then verify the test would fail without a build:
Run: `cd web-extension && rm -rf dist && npm run test:bundle 2>&1 | grep -E "ENOENT|no such file|fails" | head -5`
Expected: non-zero exit, error mentioning missing dist/.
Restore: `cd web-extension && npm run build`.

- [ ] **Step 3: Add `validateExtensionManifest` to `build.gradle.kts`**

Append:

```kotlin
val validateExtensionManifest = tasks.register("validateExtensionManifest") {
    group = "extension"
    description = "Validate web-extension/dist/manifest.json against the locked-down MV3 baseline (exact match)."
    onlyIf {
        when {
            skipWebExt.get() -> {
                logger.lifecycle("Skipping validateExtensionManifest: -Pveles.skipWebExt=true.")
                false
            }
            !webExtLockfile.isFile -> {
                logger.lifecycle("Skipping validateExtensionManifest: web-extension/package-lock.json absent.")
                false
            }
            else -> true
        }
    }
    dependsOn(extensionBuild)
    inputs.file(webExtDist.resolve("manifest.json"))
    doLast {
        val manifestFile = webExtDist.resolve("manifest.json")
        check(manifestFile.isFile) {
            "Expected $manifestFile after extensionBuild; not found. Run `./gradlew extensionBuild`."
        }
        @Suppress("UNCHECKED_CAST")
        val m = groovy.json.JsonSlurper().parse(manifestFile) as Map<String, Any?>

        fun fail(reason: String): Nothing =
            throw GradleException("web-extension manifest guard failed: $reason")

        if (m["manifest_version"] != 3) {
            fail("manifest_version must be 3, got ${m["manifest_version"]}")
        }

        // `permissions` must be exactly the empty list — not absent, not a non-list.
        if (!m.containsKey("permissions")) {
            fail("permissions key must be present and equal to []")
        }
        if (m["permissions"] != emptyList<Any>()) {
            fail("permissions must be exactly [], got ${m["permissions"]}")
        }

        // `host_permissions` must be absent entirely.
        if (m.containsKey("host_permissions")) {
            fail("host_permissions must be absent in 1a, got ${m["host_permissions"]}")
        }

        // CSP must be exactly the locked-down map.
        val expectedCsp = mapOf(
            "extension_pages" to "script-src 'self'; object-src 'self'",
        )
        val actualCsp = m["content_security_policy"]
        if (actualCsp != expectedCsp) {
            fail(
                "content_security_policy must be exactly $expectedCsp, got $actualCsp",
            )
        }

        logger.lifecycle("web-extension manifest guard: MV3 baseline (exact match) OK.")
    }
}

extensionPackage.configure {
    dependsOn(validateExtensionManifest)
}
```

- [ ] **Step 4: Add `extensionArtifactTest`**

Append:

```kotlin
val extensionArtifactTest = registerWebExtExecTask(
    taskName = "extensionArtifactTest",
    taskDescription = "Run vitest bundle tests against web-extension/dist/ (asserts file set and manifest round-trip).",
    script = "test:bundle",
)
extensionArtifactTest.configure {
    dependsOn(extensionBuild)
}

extensionPackage.configure {
    dependsOn(extensionArtifactTest)
}
```

- [ ] **Step 5: Verify the guard bites**

Run: `./gradlew extensionPackage`
Expected: SUCCESS, guard log line visible.

Negative tests — each edit should produce the named failure, then revert:

```bash
# 1) permissions not empty
sed -i 's/permissions: \[\]/permissions: ["tabs"]/' web-extension/src/manifest.ts
./gradlew extensionPackage 2>&1 | grep 'permissions must be exactly \[\]' || { echo "FAIL: guard missed"; exit 1; }
git checkout web-extension/src/manifest.ts

# 2) host_permissions present
sed -i 's|permissions: \[\]|permissions: [],\n    host_permissions: ["*://*/*"]|' web-extension/src/manifest.ts
./gradlew extensionPackage 2>&1 | grep 'host_permissions must be absent' || { echo "FAIL: guard missed"; exit 1; }
git checkout web-extension/src/manifest.ts

# 3) CSP weakened
sed -i "s|script-src 'self'|script-src 'self' 'unsafe-eval'|" web-extension/src/manifest.ts
./gradlew extensionPackage 2>&1 | grep 'content_security_policy must be exactly' || { echo "FAIL: guard missed"; exit 1; }
git checkout web-extension/src/manifest.ts

# 4) manifest_version wrong — typecast bypass needed so TypeScript compile succeeds and the Gradle guard is what fires
sed -i 's/manifest_version: 3/manifest_version: 2 as unknown as 3/' web-extension/src/manifest.ts
./gradlew extensionPackage 2>&1 | grep 'manifest_version must be 3' || { echo "FAIL: guard missed"; exit 1; }
git checkout web-extension/src/manifest.ts
```

All four must produce the expected failure text.

- [ ] **Step 6: Commit**

```bash
git add build.gradle.kts web-extension/test/bundle.test.ts
git -c commit.gpgsign=false commit -m "feat(otp-01/1a): strict manifest/CSP guard + bundle artifact test"
```

---

### Task 6: CI wiring — dedicated `web-extension` job

**Files:**
- Modify: `.github/workflows/ci.yml`

**Interfaces:**
- Consumes: tasks from Tasks 3–5.
- Produces: a new job that runs `./gradlew extensionInstall extensionFormat extensionLint extensionTypecheck extensionTest extensionBuild validateExtensionManifest extensionArtifactTest extensionPackage` under `setup-node@v6` with Node 22. The existing `lint-check`, `unit-tests`, and `instrumented-tests` jobs are **not modified**.

- [ ] **Step 1: Add the new job**

Append to `.github/workflows/ci.yml` (same indentation as the existing `lint-check` / `unit-tests` jobs):

```yaml
  web-extension:
    name: web-extension
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v7
      - uses: actions/setup-java@v5
        with:
          distribution: temurin
          java-version-file: .java-version
      - uses: gradle/actions/setup-gradle@v6
      - uses: actions/setup-node@v6
        with:
          node-version: '22'
          cache: 'npm'
          cache-dependency-path: web-extension/package-lock.json
      - run: ./gradlew extensionInstall extensionFormat extensionLint extensionTypecheck extensionTest
      - run: ./gradlew extensionBuild validateExtensionManifest extensionArtifactTest extensionPackage
      - uses: actions/upload-artifact@v7
        with:
          name: veles-extension-zip
          path: build/web-extension/
          if-no-files-found: error
```

Notes:
- The two `./gradlew` invocations stay separate so the source-level checks surface failures before the slower build/validate/package sequence runs.
- The job does not run `:app:*` tasks — APK build jobs unchanged.
- `setup-java`/`setup-gradle` are present because Gradle needs a JDK itself; node setup comes after so a Gradle-level failure doesn't waste node cache setup.

- [ ] **Step 2: Verify YAML parses**

Run: `python3 -c "import yaml; yaml.safe_load(open('.github/workflows/ci.yml'))"`
Expected: no output (parse succeeded).

If `actionlint` is installed, also: `actionlint .github/workflows/ci.yml` and expect no findings.

- [ ] **Step 3: Commit**

```bash
git add .github/workflows/ci.yml
git -c commit.gpgsign=false commit -m "ci(otp-01/1a): dedicated web-extension job running the full extension task pipeline"
```

---

### Task 7: Documentation updates + final end-to-end verification

**Files:**
- Modify: `CLAUDE.md` — new command rows in "Build & Test Commands".
- Modify: `docs/reproducible-builds.md` — sentence noting npm deps are exact-pinned (not node/npm themselves; reference-env pins land in 1d).

**Interfaces:**
- Consumes: everything.
- Produces: user-visible docs matching reality; final `./gradlew clean check extensionPackage` green on a clean tree.

- [ ] **Step 1: Update `CLAUDE.md`**

Insert into "Build & Test Commands" after `connectedDebugAndroidTest`:

```bash
# Web extension (OTP-01 sub-project 1a) — requires node/npm on PATH (node >= 22, npm bundled)
./gradlew extensionToolchainCheck extensionInstall                      # verify tools + hydrate node_modules
./gradlew extensionFormat extensionLint extensionTypecheck extensionTest  # source-level quality gates
./gradlew extensionBuild validateExtensionManifest extensionArtifactTest extensionPackage  # -> build/web-extension/veles-extension-<version>.zip + .sha256
```

- [ ] **Step 2: Update `docs/reproducible-builds.md`**

Append after the "Pinned toolchain" table:

```markdown
The web-extension's npm dependencies are exact-pinned in `web-extension/package.json`
(no `^`/`~`) and resolved via the committed `package-lock.json`. Developer Node/npm are
not pinned — `engines.node >= 22.0.0` is a floor, and CI runs Node 22 LTS. The
zero-trust reference environment with an exact Node runtime pin lands in OTP-01
sub-project 1d (`verify/Dockerfile.web`).
```

- [ ] **Step 3: Commit docs**

```bash
git add CLAUDE.md docs/reproducible-builds.md
git -c commit.gpgsign=false commit -m "docs(otp-01/1a): web-extension toolchain commands + reproducibility note"
```

- [ ] **Step 4: Final end-to-end verification on a clean tree**

```bash
git status --short                  # expect clean
./gradlew clean check               # expect SUCCESS — no extensionBuild side-effects
./gradlew extensionPackage          # expect SUCCESS — zip + sha256 sidecar produced
ls -la build/web-extension/
./gradlew :app:assembleDebug        # expect SUCCESS — APK build unaffected
```

Expected:
- `git status` shows nothing to commit.
- First `./gradlew clean check` does not run `extensionBuild`/`extensionPackage`/artifact test.
- `extensionPackage` produces `build/web-extension/veles-extension-0.1.0.zip` and `.zip.sha256`.
- APK builds normally.

- [ ] **Step 5: Final commit (if any straggler files)**

```bash
git status --short   # expect clean; if not:
git add -A && git -c commit.gpgsign=false commit -m "chore(otp-01/1a): final cleanup"
```

---

## Self-review status (post-review revision)

- **Spec coverage:** every spec requirement under "Sub-project 1a" maps to a task: layout → Tasks 1–2; Gradle task table (now including toolchain check + artifact test) → Tasks 3–5; deterministic zip recipe (with sha256 sidecar) → Task 4; skip path (value-based) → Task 3 Step 4; manifest/CSP guard exact-match → Task 5 Step 3; versioning single-source → Task 2 Step 3 + Task 4 Step 2; CI wiring → Task 6 (dedicated job — fixes the gap reviewer found where JVM jobs never invoke extension tasks); documentation → Task 7.
- **Reviewer-driven changes applied:**
  1. (Critical) chrome-global stub in `test/setup.ts` + defensive guard in `background.ts`.
  2. (Critical) artifact assertions split into `extensionArtifactTest`, wired to `extensionPackage`, not `check`.
  3. (Critical) CI is a new dedicated job, not a modification of JVM jobs.
  4. (Critical) `extensionInstall` inputs now include `package.json`, `package-lock.json`, optional `.npmrc`. `extensionBuild` declares no outputs — always re-runs.
  5. (Critical) manifest guard uses exact equality on `permissions`, `host_permissions` absence, and the full CSP map.
  6. (Critical) Task 1 Step 2 verifies the listed pins rather than floats to latest.
  7. (Critical) Spec acceptance criteria restructured to enumerate all six RFC clauses verbatim with owning sub-projects; three decomposition-specific criteria appended.
  8. (Important) `web-extension/dist/` is an explicit spec exception; root `clean` extended to delete it.
  9. (Important) `engines.node = ">=22.0.0"`; `engines.npm` dropped.
  10. (Important) `.zip.sha256` sidecar added.
  11. (Important) zip recipe fixes: `metadataCharset = "UTF-8"`, `includeEmptyDirs = false`, `dirPermissions`/`filePermissions` (non-deprecated), deflate-level claim dropped from spec.
  12. (Important) `veles.skipWebExt` is value-based (`toBooleanStrict`).
  13. (Important) `extensionToolchainCheck` implements missing-tool and below-floor failures with the spec's exact message text.
  14. (Important) `manifest.ts` reads `version` from `package.json` — single source of truth.
  15. (Important) bundle test no longer filters by `-`; asserts the exact file set (deterministic under the lockfile).
  16. (Important) spec/plan wasm-unsafe-eval disagreement resolved — forbidden in 1a, permitted starting in 1b.
  17. (Important) spec now notes 1b's `jniLibs` path is an open question to be resolved in 1b's own spec.
  18. (Important) license policy language rewritten to SPDX allow/deny with explicit categories.
  19. (Important) docs wording corrected — exact pins refer to npm deps; node itself has a floor; reference-env pins land in 1d.
  20. (Minor) spec layout now lists `eslint.config.js` (not `.eslintrc.cjs`).
  21. (Minor) Task 3 verification expects six tasks (`extensionToolchainCheck`, `extensionInstall`, `extensionFormat`, `extensionLint`, `extensionTypecheck`, `extensionTest`); Build/Package arrive in Task 4.
  22. (Minor) lockfile-skip smoke uses `trap` so it restores on failure.
  23. (Minor) `.apply { configure }` replaced with direct `configure`.
- **Placeholder scan:** none remaining — every code block has concrete content, every command is runnable as written.
- **Type consistency:** helper `registerWebExtExecTask` is defined in Task 3 with parameters `(taskName, taskDescription, script)` and consumed with named arguments in Tasks 4 and 5. `webExtDir`/`webExtLockfile`/`webExtPkg`/`webExtDist`/`skipWebExt` are defined Task 3 and consumed Tasks 4–5 with unchanged names. npm scripts referenced by Gradle tasks (`format:check`, `lint`, `typecheck`, `test`, `test:bundle`, `build`) all exist in `web-extension/package.json` per Task 1 Step 1.
