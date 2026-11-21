# Spike: vitest resolver and command inventory

Tasks 1.1 and 1.2 of `verify-affected-nodes-stop-hook`. Environment: Linux dev container, Node
`v26.13.1`, npm `11.20.0`, `web-extension/node_modules` installed with `npm ci`; the repository at
`c254ce83` (`docs(openspec): scaffold verify-affected-nodes-stop-hook change`) on
`chore/verify-affected-nodes-stop-hook`; `git merge-base origin/master HEAD` =
`597ee2c691f2d4c2dec1c4450072eca86a76054e`. The throwaway files used below were removed and the tree is
clean (`git status --porcelain` prints nothing; `web-extension/node_modules` is git-ignored).

## Vitest (task 1.1)

**Question.** Does vitest 4.1.12 offer a list-only related resolver: given explicit changed paths, print
one existing test path per line (or JSON from which that list is read) **without executing any test**?

**Probe.** A throwaway source file and a test that fails whenever it is executed:

```ts
// web-extension/src/__spike_probe__.ts
export function spikeProbe(): string {
    return 'probe';
}
```

```ts
// web-extension/test/__spike_probe__.test.ts
import { describe, expect, it } from 'vitest';
import { spikeProbe } from '../src/__spike_probe__';

describe('spike probe', () => {
    it('fails if executed', () => {
        expect(spikeProbe()).toBe('this-test-must-be-executed-to-fail');
    });
});
```

### Candidates

All commands ran in `web-extension/` (`cd web-extension`), after `npm ci`
(`added 158 packages, and audited 159 packages in 7s`).

| # | Command | Output | Exit | Ran the test? | Qualifies |
|---|---|---|---|---|---|
| A | `npx vitest list --filesOnly src/__spike_probe__.ts` | none | 0 | no | no: a positional operand is a test-file filter, not a changed source; a source path matches no test file |
| A2 | `npx vitest list --filesOnly test/__spike_probe__.test.ts` | `test/__spike_probe__.test.ts` | 0 | no | list-only, but needs the test path already known; no related resolution |
| B | `npx vitest list --filesOnly --changed` | `test/__spike_probe__.test.ts` | 0 | no | resolves related tests list-only, but derives the changed set from git, not from explicit paths |
| C | `npx vitest related --run --reporter=json --passWithNoTests src/__spike_probe__.ts` | JSON: `numTotalTests: 1`, `numFailedTests: 1`, `success: false`; `failureMessages`: `AssertionError: expected 'probe' to be 'this-test-must-be-executed-to-fail'` | 1 | **yes** | no: it executes the tests |
| D | `npx vitest list --filesOnly --changed=597ee2c691f2d4c2dec1c4450072eca86a76054e` | `test/__spike_probe__.test.ts` | 0 | no | list-only, but takes only a revision (`changedSince`), never `{changed}` paths |
| E | `npx vitest related --run --filesOnly --passWithNoTests src/__spike_probe__.ts` | `CACError: Unknown option --filesOnly` | 1 | no | `related` has no list mode |
| F | `npx vitest list --filesOnly --changed src/__spike_probe__.ts` | `test/__spike_probe__.test.ts` | 0 | no | the path is consumed by `--changed`'s optional `[since]` value, so it is not an explicit-path input |

Why the candidates behave this way (installed source, `node_modules/vitest/dist/`):

- the `list` command runs `collect()`, which calls `ctx.getRelevantTestSpecifications(cliFilters)` and,
  with `--filesOnly`, prints those specifications without collecting or running tests
  (`chunks/cli-api.B0Pz1_R7.js`, `filterTestsBySource`): positional filters are **test-file name
  filters** (`globTestSpecifications`), and related-file filtering happens only when
  `config.changed` (git VCS) or `config.related` is set;
- `config.related` is set by the `related` subcommand (`runRelated` sets `argv.related = relatedFiles`
  and calls `start("test", ...)`), which has no list branch and rejects `--filesOnly`; in `list` mode it
  is set only by the `--changed` branch from the git VCS;
- `--changed` with a string resolves through git:
  `git diff --name-only <since>...HEAD` plus `git diff --cached --name-only` plus
  `git ls-files --other --modified --exclude-standard`. That is not D3's union either (it has no
  `--no-renames` and no explicit working-tree diff), and it cannot receive the selection's path list.

The failing probe test printing as a path with exit 0 (A2, B, D) while `related --run` failed it with exit
1 (C) is the proof that the `list --filesOnly` paths execute no test.

### Decision

**No qualifying `files-arg` resolver: `resolve` is none.** `vitest related` accepts explicit changed
paths but always runs tests; `vitest list --filesOnly` is list-only but takes its changed set from
`--changed[=<since>]` (git), not from explicit paths. The extension therefore uses **`name-map` plus
`self`** (design D4):

- strategy `name-map`: `sources: web-extension/src/**/*.ts`, `tests: web-extension/test/**/<stem>.test.ts`,
  a changed file matching the `tests` glob is its own target;
- strategy `self` for the changed test files;
- `within: package.json:scripts.test` for both, so a resolved file that `npm test` does not name (a
  bundle, browser or connector-browser suite) is not a task-gate target;
- `run: ["npx", "vitest", "run", "{targets}"]` in `web-extension/`.

`docs/verify.md` must state that the extension has no `files-arg` resolver and why.

The observed `list --filesOnly --changed=<base>` behaviour is recorded above as a rejected alternative,
not as a strategy: it has no `{changed}` input and does not use the selection's own git reads. If a later
task wants it, it needs a new `{base}` placeholder and a design change; the brief and D4 specify the
`name-map` fallback.

### Evidence

```text
$ cd web-extension && npm ci
added 158 packages, and audited 159 packages in 7s

$ npx vitest --version
vitest/4.1.12 linux-x64 node-v26.13.1

$ npx vitest list --filesOnly src/__spike_probe__.ts
exit=0                      # no output

$ npx vitest list --filesOnly test/__spike_probe__.test.ts
test/__spike_probe__.test.ts
exit=0

$ npx vitest list --filesOnly --changed
test/__spike_probe__.test.ts
exit=0

$ npx vitest related --run --reporter=json --passWithNoTests src/__spike_probe__.ts
exit=1
{"numTotalTestSuites":2,"numPassedTestSuites":0,"numFailedTestSuites":2,...,"numTotalTests":1,
 "numPassedTests":0,"numFailedTests":1,...,"success":false,
 "testResults":[{"assertionResults":[{"fullName":"spike probe fails if executed","status":"failed",
 "failureMessages":["AssertionError: expected 'probe' to be 'this-test-must-be-executed-to-fail'"...

$ npx vitest related --run --filesOnly --passWithNoTests src/__spike_probe__.ts
CACError: Unknown option `--filesOnly`
exit=1

$ npx vitest list --filesOnly --changed=597ee2c691f2d4c2dec1c4450072eca86a76054e
test/__spike_probe__.test.ts
exit=0

$ node -e "import('./node_modules/vitest/dist/chunks/cac.DzDtg8hz.js').then(m=>{const r=m.p(['vitest','list','--filesOnly','--changed','src/__spike_probe__.ts']);console.log(JSON.stringify({filter:r.filter,changed:r.options.changed}))})"
{"filter":[],"changed":"src/__spike_probe__.ts"}
# the operand becomes the [since] value, not a filter or a changed-path list
```

Cleanup: `rm -f web-extension/src/__spike_probe__.ts web-extension/test/__spike_probe__.test.ts`;
`git status --porcelain` empty.

## Inventory (task 1.2)

### Legend

Classification rules, quoted from `tasks.md` task 1.2:

> Walk every node's `AGENTS.md`, its listed rules and its CI workflow steps; record per node which
> commands become `check`, `test`, `device`, `platform` or `excluded`, and which Android guards beyond
> raw-`Log` join the table. Every CI test or static check that runs on Linux MUST map to a table entry or
> to an aggregate entry that runs it (for example the tooling workflow's
> `node --test verify/test/*.test.mjs`); "CI only" is reserved for device, macOS/Windows, signing and
> release steps, and for destructive sequences that clean the repository's `build/` directory (such as
> the Rust build–`./gradlew clean`–`rust/scripts/assert-clean.sh` contract in `check-rust.yml`), which
> would delete the verifier's own state and lock under `build/verify/`; each is recorded with its reason.
> CI provisioning and setup steps the verifier represents as requirements or fix text rather than
> commands are listed under "CI only" too, with the requirement that stands for them.

**Kinds.** `check`, `test`, `device`, `platform` and `excluded` are the table's vocabulary. Two more
values appear for clarity:

- `producer` — outside the command kinds: the argv is not a gate command; it becomes a `producers[]`
  entry referenced by a `generated` requirement (design D2, D5). Calling it `excluded` would be wrong.
- `—` — outside the command kinds: a non-command row (a `cd` line, or a CI-only step with nothing
  local to run). In `Local argv` it means there is no local command (a CI-only step); in `Doc` or
  `CI job` it means no table entry or no CI step.
- `excluded` — in the vocabulary: a documented line or CI step with no table entry; the Reason column
  says why.

**Columns.** `Source` is the doc line (`path:line`) and/or the workflow step (`file:line (job)`).
`Local argv (cwd)` is what the verifier would run without a shell; a `${…}`/`<…>` marks an argument the
strategy supplies. `Doc` is the doc the table entry points at; `(add; 2.1)` means the line is missing
today and task 2.1 must add it. `CI job` uses the check name `ci.yml` reports (`<node> / <job>`);
`—` means no CI step runs it. Rows are keyed by unique argv and cwd: a row whose Source lists several
workflow steps is the same command executed by several CI steps (for example on another runner).
CI-only steps have no local entry and always get their own row.

**Prose mentions.** A prose line that names a command already listed in a fence gets no separate row:
`android/AGENTS.md:31` (`./gradlew protocolCheck`), `protocol/AGENTS.md:28` (`./gradlew rustHostJni`),
`web-extension/AGENTS.md:5` (`./gradlew rustWasm`, `npm run build`, `npm test`) and
`verify/AGENTS.md:4` (`verify/verify-supply-chain.sh`). The one prose command not listed elsewhere
(`docs/AGENTS.md:21`, `agent_rules.py pointers --write`) has its own row below.

**Sources read.** Root and node `AGENTS.md` (root, `android`, `web-extension`, `rust`, `protocol`,
`native-bridge`, `native-bridge/host`, `native-bridge/installer`,
`native-bridge/installer/{macos,windows}`, `verify`, `scripts`, `docs`); every rule listed in their
`## Rules` (`.agents/rules/android-lint-before-push.md`, `.agents/rules/native-bridge-test-app-tcc.md`,
plus the always-on rules, which contain no commands); `.github/workflows/check-*.yml` and
`build-supply-chain.yml` (the `supply-chain` node's CI workflow), plus the release workflows named where
a node doc points at them (`release-build.yml`, `publish-release.yml`, `build-native-{windows,macos}.yml`).
`ci.yml`'s `changes` job and `commit-guard`, and `pr-title.yml`, validate selection and commit/PR
messages rather than the tree; their documented counterparts are recorded under `scripts` (tooling)
below.

**Requirements named below.** R1 `java-home` (JDK 21, `.java-version`); R2 `android-sdk`; R3
`android-ndk` (the `rust/ndk-version` pin); R4 `rustup-toolchain` (the channel in each
`rust-toolchain.toml`); R5 `path-tool` — `node` (`version_cmd: node --version`, `min: 22`, `exact: 26.8.1`
where CI pins it); R6 `path-tool` — `npm` record-only (the supply-chain script's `env_exit` carries the
11.19.0 pin); R7 `path-tool` — `python` (`min: 3.8`); R8 `playwright-chromium`; R9 `path` requirement —
`web-extension/node_modules`, plus a declared `inputs` entry of `mode: stat` (D6); R10 `path` —
`native-bridge/node_modules`, `inputs` `mode: stat`; R11 `path` — `verify/node_modules`; R12
`path-tool` — `cargo-deny` and `cargo-cyclonedx`, resolved from `build/verify-tools/*/bin` through the
step's environment/`path_prepend`. The canonical fix text for each is in "Setup requirements" below; a
setup row names the requirement and its short fix.

### Node: root

No commands. Root `AGENTS.md` holds the node map, `## Rules` pointers and the hook-delivered rules; all
of its rule files contain no runnable commands (the pre-push rule's prose becomes
`python3 -I scripts/verify.py --branch` in task 14.2, not a table entry).

### Node: android

Fences: `android/AGENTS.md` `## Build & Test` runs from `android/`; rule
`.agents/rules/android-lint-before-push.md` (also listed by `rust` and `protocol`).

| Source | Kind | Local argv (cwd) | Doc | CI job | Reason |
|---|---|---|---|---|---|
| `android/AGENTS.md:22` | excluded | `./gradlew assembleDebug` (`android/`) | `android/AGENTS.md` | `rust / jni-integration` | build artifact, not a gate; its only CI steps are in the jni-integration sequence that runs `./gradlew clean` (CI only — build/-cleaning sequence) |
| `android/AGENTS.md:23` | excluded | `./gradlew assembleRelease` (`android/`) | `android/AGENTS.md` | `release-build / android`; `publish-release / build-android` | release build (R8-minified, signed only with `VELES_KEYSTORE_*`): release/signing, not a gate |
| `android/AGENTS.md:24`; `check-android.yml:88 (unit-tests)` | test | `./gradlew testDebugUnitTest` (`android/`) | `android/AGENTS.md` | `android / unit-tests` | unit tests; `related` is `name-map` with FQN `--tests` read from existing test files |
| `android/AGENTS.md:25` | excluded | `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.handlers.RegexMessageHandlerTest"` (`android/`) | `android/AGENTS.md` | — | example of a focused run; the `related` strategy generates this argv from changed sources |
| `android/AGENTS.md:26`; `check-android.yml:166 (instrumented-tests)` | device | `./gradlew connectedDebugAndroidTest` (`android/`) | `android/AGENTS.md` | `android / instrumented-tests` | device/emulator test; both gates report `NOT RUN (device test; real-device rule)` |
| `android/AGENTS.md:27` | excluded | `./gradlew connectedDebugAndroidTest -Pandroid.testInstrumentationRunnerArguments.class=me.nagaev.permissions.ui.VelesPermissionsAppTests` (`android/`) | `android/AGENTS.md` | — | example of a focused device run; the `device` entry covers the suite |
| `.agents/rules/android-lint-before-push.md:9` (the inline lint command; fence it in 2.3); `check-android.yml:32 (lint)` | check | `./gradlew spotlessCheck` (`android/`) | `.agents/rules/android-lint-before-push.md` | `android / lint` | format check; 2.3 moves this inline line into a fenced block under "Run from `android/`" — the rule's existing fence is the raw-`Log` guard, not this line |
| `.agents/rules/android-lint-before-push.md:9`; `check-android.yml:34 (lint)` | check | `./gradlew detekt` (`android/`) | `.agents/rules/android-lint-before-push.md` | `android / lint` | static analysis |
| `.agents/rules/android-lint-before-push.md:9`; `check-android.yml:64 (lint)` | check | `./gradlew lintDebug` (`android/`) | `.agents/rules/android-lint-before-push.md` | `android / lint` | Android lint; needs R1, R2 (and R3 only if the task graph reaches the NDK) |
| `.agents/rules/android-lint-before-push.md:14-27` (existing fence); `check-android.yml:39 (lint)` | check | `android/scripts/check-raw-log.sh` (`.`) | `.agents/rules/android-lint-before-push.md` | `android / lint` | raw `android.util.Log` guard: Linux static check; the script holds the fence's greps, `::error::` lines and exit status verbatim (2.2); 2.3 replaces that inline block with a call to the script; **joins the table** |
| `check-android.yml:37 (lint)` | check | `node scripts/check-mac-comparisons.mjs --scan android/app/src/main` (`.`) | `.agents/rules/android-lint-before-push.md` (add; 2.1) | `android / lint` | constant-time MAC comparison guard: Linux static check, missing from the node docs; **joins the table**; needs R5 |
| `check-android.yml:53 (lint)` | check | `android/scripts/check-pairing-code.sh` (`.`) | `.agents/rules/android-lint-before-push.md` (add; 2.1/2.3) | `android / lint` | pairing-code logging guard: Linux static check; the script holds today's inline grep (2.1 documents it); **joins the table** |
| `check-android.yml:122 (release-hook-guard)` | check | `./gradlew :app:releaseHookGuard` (`android/`) | `android/AGENTS.md` (add; 2.1) | `android / release-hook-guard` | scans the assembled unsigned release APK's DEX, manifest and resources for debug hooks: Linux static check, must map; missing from the docs |
| `check-android.yml:81,111,139` | excluded | `rustup show active-toolchain` (`rust/`) | — | `android / unit-tests`, `android / release-hook-guard`, `android / instrumented-tests` | CI only — setup: R4; fix: run `rustup show active-toolchain` in `rust/` so the pinned channel is installed |
| `check-android.yml:114,142` | excluded | `${ANDROID_SDK_ROOT}/cmdline-tools/latest/bin/sdkmanager "ndk;<version>"` (`android/`) | — | `android / release-hook-guard`, `android / instrumented-tests` | CI only — setup: R3 (`./gradlew -q rustNdkVersion` gives the pin); fix: install that NDK and set `ANDROID_NDK_HOME`, or `config set android_ndk <dir>` |
| all `check-android.yml` jobs | excluded | — | — | `android / lint`, `android / unit-tests`, `android / release-hook-guard`, `android / instrumented-tests` | CI only — setup: R1; fix: install JDK 21 and `config set java_home <jdk>` |
| `check-android.yml:151 (instrumented-tests)` | excluded | — | — | `android / instrumented-tests` | CI only — device: KVM/emulator provisioning for the `device` entry; no local entry |

The four `uses: actions/upload-artifact@…` steps in this workflow are not commands.

### Node: web-extension

Fence: `web-extension/AGENTS.md` `## Build & Test` starts with `cd web-extension`; `## Rules`: none.

| Source | Kind | Local argv (cwd) | Doc | CI job | Reason |
|---|---|---|---|---|---|
| `web-extension/AGENTS.md:11` | — | `cd web-extension` | `web-extension/AGENTS.md` | — | not a command: the doc parser (D13) reads it as the block's working directory |
| `web-extension/AGENTS.md:12`; `check-web-extension.yml:47,90,124` | excluded | `npm ci` (`web-extension/`) | `web-extension/AGENTS.md` | `web-extension / lint`, `web-extension / unit-tests`, `web-extension / browser-tests` | setup, not a gate: R9 (`stat`); fix: `cd web-extension && npm ci` — CI uses `npm ci --ignore-scripts` (record the CI form in the requirement) |
| `web-extension/AGENTS.md:13`; `check-web-extension.yml:49 (lint)` | check | `npm run format:check` (`web-extension/`) | `web-extension/AGENTS.md` | `web-extension / lint` | Prettier check |
| `web-extension/AGENTS.md:14`; `check-web-extension.yml:51 (lint)` | check | `npm run lint` (`web-extension/`) | `web-extension/AGENTS.md` | `web-extension / lint` | ESLint |
| `web-extension/AGENTS.md:15`; `check-web-extension.yml:61 (lint)` | check | `npm run typecheck` (`web-extension/`) | `web-extension/AGENTS.md` | `web-extension / lint` | `tsc --noEmit`; requires the `rust.wasm` producer |
| `web-extension/AGENTS.md:16`; `check-web-extension.yml:92 (unit-tests)` | test | `npm test` (`web-extension/`) | `web-extension/AGENTS.md` | `web-extension / unit-tests` | source-level vitest suite (the script names every file); `related` is `name-map` + `self` with `within: package.json:scripts.test`; requires the `rust.wasm` producer |
| `web-extension/AGENTS.md:17`; `check-web-extension.yml:94 (unit-tests)` | test | `npm run test:bundle` (`web-extension/`) | `web-extension/AGENTS.md` | `web-extension / unit-tests` | bundle suite; CI runs `npm run build` immediately before; requires `rust.wasm` then `web-extension.build` (the suite reads `dist/`; its own guard build writes `../build/web-extension-guard`, not `dist/`) |
| `web-extension/AGENTS.md:18` | excluded | `npm run package` (`web-extension/`) | `web-extension/AGENTS.md` | `release-build / web-extension`; `publish-release / build-web-extension` | release packaging (`build/web-extension/*.zip` + SHA256SUMS): release, not a gate |
| `rust/AGENTS.md:17`; `check-web-extension.yml:46,89,123`; `check-rust.yml:116` | producer | `./gradlew rustWasm` (`.`) | `rust/AGENTS.md` | `web-extension / lint`, `web-extension / unit-tests`, `web-extension / browser-tests`; `rust / jni-integration` | producer `rust.wasm`, output `web-extension/rust-wasm/pkg`; required by typecheck and every extension test; also run inside the jni-integration build/-cleaning sequence |
| `check-web-extension.yml:126,94`; `check-rust.yml:117` | producer | `npm run build` (`web-extension/`) | `web-extension/AGENTS.md` (add; 2.1) | `web-extension / unit-tests`, `web-extension / browser-tests`; `rust / jni-integration` | producer `web-extension.build`, output `web-extension/dist`; requires `rust.wasm`; the current doc only names it in prose (`web-extension/AGENTS.md:4`), so 2.1 adds the fence line |
| `check-web-extension.yml:130 (browser-tests)` | test | `npm run test:browser` (`web-extension/`) | `web-extension/AGENTS.md` (add; 2.1) | `web-extension / browser-tests` | Playwright browser suite reading `dist/`; missing from the docs; requires `rust.wasm`, `web-extension.build` and R8 |
| `check-web-extension.yml:128 (browser-tests)` | excluded | `npx playwright-core install --with-deps chromium` (`web-extension/`) | — | `web-extension / browser-tests` | CI only — setup: R8; fix: run that install command, or set `VELES_TEST_CHROMIUM` to an existing Chromium; CI's browser build is tied to the pinned `ubuntu-24.04` image |
| `check-web-extension.yml:54 (lint)` | check | `node scripts/check-mac-comparisons.mjs --scan web-extension/src` (`.`) | `web-extension/AGENTS.md` (add a repository-root block; 2.1) | `web-extension / lint` | constant-time MAC guard: Linux static check, missing from the docs; **joins the table**; needs R5 |
| `check-web-extension.yml:56 (lint)` | check | `web-extension/scripts/check-pairing-code.sh` (`.`) | `web-extension/AGENTS.md` (add a repository-root block; 2.1) | `web-extension / lint` | pairing-code debug-surface guard: Linux static check; the script holds today's inline grep; **joins the table** |
| `check-web-extension.yml` (all jobs) | excluded | — | — | `web-extension / lint`, `web-extension / unit-tests`, `web-extension / browser-tests` | CI only — setup: R5 (`node-version: '22'`); fix: install Node >= 22 and put it on `PATH`, or `config set path_prepend <node bin>` |
| `check-web-extension.yml:40,83,116` | excluded | `rustup show active-toolchain` (`rust/`) | — | `web-extension / lint`, `web-extension / unit-tests`, `web-extension / browser-tests` | CI only — setup: R4; fix: run `rustup show active-toolchain` in `rust/` |
| `check-web-extension.yml` lint/unit/browser jobs | excluded | — | — | `web-extension / lint`, `web-extension / unit-tests`, `web-extension / browser-tests` | CI only — setup: R1; fix: install JDK 21 and `config set java_home <jdk>` |

`npm run test:connector-browser` (`web-extension/package.json`) is in no doc and no CI step; it is outside
this inventory (noted for `docs/verify.md`, not a table entry).

### Node: rust

Fence: `rust/AGENTS.md` `## Build & Test` runs from the repository root.

| Source | Kind | Local argv (cwd) | Doc | CI job | Reason |
|---|---|---|---|---|---|
| `rust/AGENTS.md:14`; `check-rust.yml:46,78,124` | excluded | `./gradlew rustInstall` (`.`) | `rust/AGENTS.md` | `rust / lint`, `rust / unit-tests`, `rust / jni-integration` | setup, not a gate: installs the exact-pinned CLIs into `build/rust-tools/`; CI refills that cache after the checks |
| `rust/AGENTS.md:15`; `check-rust.yml:43 (lint)` | check | `./gradlew rustFormat` (`.`) | `rust/AGENTS.md` | `rust / lint` | `cargo fmt --check` on the host, WASM and Android targets; needs R4 |
| `rust/AGENTS.md:15`; `check-rust.yml:43 (lint)` | check | `./gradlew rustLint` (`.`) | `rust/AGENTS.md` | `rust / lint` | clippy with `-D warnings` for host, host-JNI, WASM and Android JNI; needs R4 and R3 |
| `rust/AGENTS.md:15`; `check-rust.yml:76 (unit-tests)` | test | `./gradlew rustTest` (`.`) | `rust/AGENTS.md` | `rust / unit-tests` | native + WASM tests; stated limit: this one crate's command is all of its tests (D4); needs R4, R5 |
| `rust/AGENTS.md:16` | excluded | `./gradlew rustJni` (`.`) | `rust/AGENTS.md` | — | product input build (three Android JNI ABIs), not a gate; the release pipeline builds it |
| `rust/AGENTS.md:17`; `check-rust.yml:116` | producer | `./gradlew rustWasm` (`.`) | `rust/AGENTS.md` | `rust / jni-integration` | producer `rust.wasm` (shared with `web-extension`) |
| `rust/AGENTS.md:18`; `check-rust.yml:114`; `check-protocol.yml:46` | producer | `./gradlew rustHostJni` (`.`) | `rust/AGENTS.md` | `rust / jni-integration`; `protocol / contract-tests` | producer `rust.hostJni`, output `build/rust/host-jni`; test-only cdylib loaded by `testDebugUnitTest`, `:app:protocolJvmTest` and the session loopback |
| `rust/AGENTS.md:19`; `check-rust.yml:120` | excluded | `rust/scripts/assert-clean.sh` (`.`) | `rust/AGENTS.md` | `rust / jni-integration` | CI only — build/-cleaning sequence: it only makes sense after `./gradlew clean`, which deletes `build/verify/` |
| `check-rust.yml:109,121` | excluded | `./gradlew assembleDebug` (`.`) | — | `rust / jni-integration` | CI only — build/-cleaning sequence (the same command as `android/AGENTS.md:22`, from the root alias) |
| `check-rust.yml:110,122` | excluded | `rust/scripts/verify-apk-jni.sh <apk>` (`.`) | — | `rust / jni-integration` | CI only — build/-cleaning sequence: needs the APK the preceding step assembled; not documented |
| `check-rust.yml:119` | excluded | `./gradlew clean` (`.`) | — | `rust / jni-integration` | CI only — build/-cleaning sequence: deletes `build/` including `build/verify/` (verifier state and lock) |
| `check-rust.yml:117` | excluded | `npm ci` (`web-extension/`) | — | `rust / jni-integration` | CI only — build/-cleaning sequence: provisions R9 inside the sequence (the same command as `web-extension/AGENTS.md:12`) |
| `check-rust.yml:33,66,99` | excluded | `rustup show active-toolchain` (`rust/`) | — | `rust / lint`, `rust / unit-tests`, `rust / jni-integration` | CI only — setup: R4; fix as above |
| `check-rust.yml:35-39,68-72,101-105` | excluded | `sdkmanager "ndk;<version>"` (`.`) | — | `rust / lint`, `rust / unit-tests`, `rust / jni-integration` | CI only — setup: R3 (`./gradlew -q rustNdkVersion`); fix: install that NDK, set `ANDROID_NDK_HOME`, or `config set android_ndk <dir>` |
| `check-rust.yml` lint/unit/jni jobs | excluded | — | — | `rust / lint`, `rust / unit-tests`, `rust / jni-integration` | CI only — setup: R1 (`setup-java`), R5 (`setup-node`); fixes as above |

The remaining steps of `check-rust.yml` are artifact uploads. `build-rust.yml` is release-only and adds
`rustPackage`/`rustJni` inputs, already covered as excluded product builds.

### Node: protocol

Fence: `protocol/AGENTS.md` `## Build & Test` runs from the repository root.

| Source | Kind | Local argv (cwd) | Doc | CI job | Reason |
|---|---|---|---|---|---|
| `protocol/AGENTS.md:22`; `check-protocol.yml:49 (contract-tests)` | test | `./gradlew protocolCheck` (`.`) | `protocol/AGENTS.md` | `protocol / contract-tests` | the OTP-02 contract aggregate (Node checks, Rust consumer, both TypeScript consumers, `:app:protocolJvmTest`, session loopback); branch-only (no `related`); Gradle itself runs the producers, which Task 3 must reconcile with D5's explicit chain |
| `check-protocol.yml:46 (contract-tests)` | producer | `./gradlew rustHostJni` (`.`) | `rust/AGENTS.md` | `protocol / contract-tests` | producer `rust.hostJni`; CI builds it before `protocolCheck` because the loopback child JVM loads it |
| `check-protocol.yml:48 (contract-tests)` | excluded | `python3 --version && python3 -c 'import sys; assert sys.version_info >= (3, 8)'` (`.`) | — | `protocol / contract-tests` | CI only — setup: R7; fix: install Python 3.8 or newer and put it on `PATH`, or `config set path_prepend <dir>` |
| `check-protocol.yml:39 (contract-tests)` | excluded | `rustup show active-toolchain` (`rust/`) | — | `protocol / contract-tests` | CI only — setup: R4; fix as above |
| `check-protocol.yml` contract-tests | excluded | — | — | `protocol / contract-tests` | CI only — setup: R1, R5 (`node-version: '22'`); fixes as above |

### Node: bridge-scaffold (`native-bridge/`)

Fence: `native-bridge/AGENTS.md` `## Build & Test` runs from the repository root.

| Source | Kind | Local argv (cwd) | Doc | CI job | Reason |
|---|---|---|---|---|---|
| `native-bridge/AGENTS.md:15` | excluded | `./gradlew bridgeInstall` (`.`) | `native-bridge/AGENTS.md` | `bridge-scaffold / lint`, `bridge-scaffold / unit-tests` (via the tasks that depend on it) | setup, not a gate: `npm ci --ignore-scripts` into `native-bridge/node_modules`; R10 (`stat`); fix: `cd native-bridge && npm ci --ignore-scripts` (or `./gradlew bridgeInstall`) |
| `native-bridge/AGENTS.md:15`; `check-bridge-scaffold.yml:38 (lint)` | check | `./gradlew bridgeFormat` (`.`) | `native-bridge/AGENTS.md` | `bridge-scaffold / lint` | Prettier check |
| `native-bridge/AGENTS.md:15`; `check-bridge-scaffold.yml:38 (lint)` | check | `./gradlew bridgeLint` (`.`) | `native-bridge/AGENTS.md` | `bridge-scaffold / lint` | ESLint |
| `native-bridge/AGENTS.md:15`; `check-bridge-scaffold.yml:38 (lint)` | check | `./gradlew bridgeTypecheck` (`.`) | `native-bridge/AGENTS.md` | `bridge-scaffold / lint` | `tsc --noEmit` |
| `check-bridge-scaffold.yml:38 (lint)` | check | `./gradlew bridgeRustFormat` (`.`) | `native-bridge/AGENTS.md` (add; 2.1) | `bridge-scaffold / lint` | `cargo fmt --check` of `src-tauri`; missing from the docs; **joins the table** (a Linux static check) |
| `check-bridge-scaffold.yml:38 (lint)` | check | `./gradlew bridgeRustLint` (`.`) | `native-bridge/AGENTS.md` (add; 2.1) | `bridge-scaffold / lint` | `cargo clippy -D warnings` of `src-tauri`; missing from the docs; **joins the table** |
| `native-bridge/AGENTS.md:15`; `check-bridge-scaffold.yml:60 (unit-tests)` | test | `./gradlew bridgeTest` (`.`) | `native-bridge/AGENTS.md` | `bridge-scaffold / unit-tests` | test aggregate (`bridgeNpmTest` + `bridgeRustFormat` + `bridgeRustLint` + `bridgeRustTest`); the spec's "mixed Gradle task is a test" scenario |
| `check-bridge-scaffold.yml:60 (unit-tests)` | excluded | `./gradlew bridgeNpmTest` (`.`) | `native-bridge/AGENTS.md` | `bridge-scaffold / unit-tests` | covered by the `bridgeTest` aggregate (it depends on this task); no separate entry |
| `check-bridge-scaffold.yml:60 (unit-tests)` | excluded | `./gradlew bridgeRustTest` (`.`) | `native-bridge/AGENTS.md` | `bridge-scaffold / unit-tests` | covered by the `bridgeTest` aggregate (it depends on this task); no separate entry |
| `native-bridge/AGENTS.md:16` | excluded | `./gradlew bridgeBuild` (`.`) | `native-bridge/AGENTS.md` | — | unsigned product build (`cargo tauri build --no-bundle`): build artifact, not a gate; release pipeline runs it |
| `native-bridge/AGENTS.md:17` | excluded | `./gradlew bridgeManifests` (`.`) | `native-bridge/AGENTS.md` | — | generates Chrome native-messaging manifests: packaging artifact, not a gate |
| `native-bridge/AGENTS.md:18` | excluded | `./gradlew bridgePackage` (`.`) | `native-bridge/AGENTS.md` | — | deterministic packaging into `build/native-bridge/`: release artifact, not a gate |
| `check-bridge-scaffold.yml:37,59` | excluded | `rustup show active-toolchain` (`native-bridge/src-tauri/`) | — | `bridge-scaffold / lint`, `bridge-scaffold / unit-tests` | CI only — setup: R4 (pin 1.98.0 in `native-bridge/src-tauri/rust-toolchain.toml`); fix: run `rustup show active-toolchain` there |
| `check-bridge-scaffold.yml` all jobs | excluded | — | — | `bridge-scaffold / lint`, `bridge-scaffold / unit-tests` | CI only — setup: R5 (`node-version: '22'`); fix as above; `bridgeInstall` provisions R10 |
| `check-bridge-scaffold.yml` all jobs | excluded | — | — | `bridge-scaffold / lint`, `bridge-scaffold / unit-tests` | CI only — setup: R1; fix: install JDK 21 and `config set java_home <jdk>` |
| `.agents/rules/native-bridge-test-app-tcc.md:64-66` (listed by `native-bridge/AGENTS.md:27`) | excluded | `codesign --verify --strict path/to/App.app`; `codesign -dvvv path/to/App.app`; `codesign -d -r- path/to/App.app` | `.agents/rules/native-bridge-test-app-tcc.md` | — | macOS-only manual signing-identity verification with a placeholder path: not a gate; excluded so the rule's fence is covered |
| `.agents/rules/native-bridge-test-app-tcc.md:72` | excluded | `log show --start "<before>" --end "<after>" … --predicate 'process == "tccd" …'` | `.agents/rules/native-bridge-test-app-tcc.md` | — | macOS-only TCC log capture: not a gate |
| `.agents/rules/native-bridge-test-app-tcc.md` (build text) | excluded | `native-bridge/host/scripts/macos-test-bundle.sh` | `.agents/rules/native-bridge-test-app-tcc.md` | — | macOS-only test-bundle build named in prose; not a fenced command, so not a drift line |

### Node: native-host (`native-bridge/host/`)

Fence: `native-bridge/host/AGENTS.md` `## Build & Test` runs from the repository root; `## Rules`: none.

| Source | Kind | Local argv (cwd) | Doc | CI job | Reason |
|---|---|---|---|---|---|
| `native-bridge/host/AGENTS.md:12`; `check-native-host.yml:42,75,98` | check | `./gradlew bridgeHostToolchainCheck` (`.`) | `native-bridge/host/AGENTS.md` | `native-host / lint`, `native-host / unit-tests (windows)`, `native-host / unit-tests (macos)` | asserts the pinned 1.98.0 toolchain and `rustup`/`cargo` on `PATH` |
| `native-bridge/host/AGENTS.md:12`; same jobs | check | `./gradlew bridgeHostFormat` (`.`) | `native-bridge/host/AGENTS.md` | as above | `cargo fmt --check` |
| `native-bridge/host/AGENTS.md:12`; same jobs | check | `./gradlew bridgeHostLint` (`.`) | `native-bridge/host/AGENTS.md` | as above | `cargo clippy -D warnings` |
| `native-bridge/host/AGENTS.md:12`; `check-native-host.yml:59,75,98` | test | `./gradlew bridgeHostTest` (`.`) | `native-bridge/host/AGENTS.md` | `native-host / unit-tests (linux)`, `native-host / unit-tests (windows)`, `native-host / unit-tests (macos)` | portable control-plane tests on Linux; Windows/macOS compile the real adapters |
| `native-bridge/host/AGENTS.md:13`; `check-native-host.yml:133,164` | excluded | `./gradlew bridgeHostBuild` (`.`) | `native-bridge/host/AGENTS.md` | `native-host / compile (windows)`, `compile (macos)` | product build (locked release executable + SHA256SUMS): build artifact, not a gate; CI compiles it only on Windows/macOS |
| `native-bridge/host/AGENTS.md:14`; `check-native-host.yml:117 (build-contract)` | test | `python3 -m unittest discover -s native-bridge/host/scripts -p 'test_build_contract.py' -v` (`.`) | `native-bridge/host/AGENTS.md` | `native-host / build-contract` | the host's Python build-contract suite; branch-only (no `related`), per D4's stated limit. The entry uses the documented argv **with** `-v`; the CI step at `check-native-host.yml:117` runs the same discovery without `-v` and maps to the same entry |
| `check-native-host.yml:75,98,133` | excluded | — | — | `native-host / unit-tests (windows)`, `native-host / unit-tests (macos)`, `native-host / compile (windows)` | the Windows/macOS executions map to the same check/test entries (the argv differs only by the wrapper); no separate entry — recorded here so every CI step is accounted for |
| `check-native-host.yml:92-93 (macos)`, `check-native-host.yml:158-159 (macos)` | excluded | — | — | `native-host / unit-tests (macos)`, `compile (macos)` | CI only — macOS: Xcode/`DEVELOPER_DIR` provisioning; no D7 resolver exists, so `docs/verify.md` lists it with the pinned image |
| `check-native-host.yml:168 (macos)` | excluded | `bash native-bridge/host/scripts/package-macos-artifact.sh build/native-bridge/host build/native-bridge/host-archive` (`.`) | — | `native-host / compile (macos)` | CI only — macOS: mode-preserving artifact packaging for the check artifact |
| `check-native-host.yml:41,58,74,97,116,132,163` | excluded | `rustup show active-toolchain` (`native-bridge/host/`) | — | all `native-host` jobs | CI only — setup: R4 (host's 1.98.0 pin); fix as above |
| `check-native-host.yml` all jobs | excluded | — | — | all `native-host` jobs | CI only — setup: R1; fix as above |

### Node: installer (`native-bridge/installer/`)

Fence: `native-bridge/installer/AGENTS.md` `## Build & Test` runs from the repository root; `## Rules`:
none. Sub-node docs: `native-bridge/installer/macos/AGENTS.md` and
`native-bridge/installer/windows/AGENTS.md` (their rows follow this table).

| Source | Kind | Local argv (cwd) | Doc | CI job | Reason |
|---|---|---|---|---|---|
| `native-bridge/installer/AGENTS.md:14`; `check-installer.yml:40,79,105` | check | `./gradlew bridgeInstallerFormat` (`.`) | `native-bridge/installer/AGENTS.md` | `installer / lint`, `installer / unit-tests (windows)`, `installer / unit-tests (macos)` | `cargo fmt --check` (locked) |
| `native-bridge/installer/AGENTS.md:14`; same jobs | check | `./gradlew bridgeInstallerLint` (`.`) | `native-bridge/installer/AGENTS.md` | as above | `cargo clippy -D warnings` (locked) |
| `native-bridge/installer/AGENTS.md:14`; `check-installer.yml:60,79,105`; `macos/AGENTS.md:27`; `windows/AGENTS.md:14` | test | `./gradlew bridgeInstallerTest` (`.`) | `native-bridge/installer/AGENTS.md` | `installer / unit-tests (linux)`, `installer / unit-tests (windows)`, `installer / unit-tests (macos)` | the crate's tests; the Windows/macOS lines in the sub-node docs map to this entry |
| `native-bridge/installer/AGENTS.md:15`; `check-installer.yml:125 (scripts-tests)`; `macos/AGENTS.md:27` | test | `./gradlew bridgeInstallerScriptsTest` (`.`) | `native-bridge/installer/AGENTS.md` | `installer / scripts-tests` | Python script suites incl. `test_build_contract.py`; disabled on Windows by `build.gradle.kts`; branch-only (no `related`) |
| `native-bridge/installer/AGENTS.md:18`; `macos/AGENTS.md:28`; `windows/AGENTS.md:16`; `build-native-windows.yml:116`, `build-native-macos.yml:91` | platform | `./gradlew bridgeInstallerUnsigned` (`.`) | `native-bridge/installer/AGENTS.md` | `publish-release / build-windows`, `publish-release / build-macos` | CI only — macOS/Windows + release: the production-channel unsigned input tree, needs an approved tracked `release-<platform>.json`; `platform` entry (expected skip) |
| `native-bridge/installer/AGENTS.md:19`; `macos/AGENTS.md:29`; `windows/AGENTS.md:17` | platform | `./gradlew bridgeInstallerPackageFixture` (`.`) | `native-bridge/installer/AGENTS.md` | — | CI only — macOS/Windows: the FIXTURE-channel input tree from the tracked `fixture-<platform>.json`; `platform` entry |
| `native-bridge/installer/AGENTS.md:20` | check | `./gradlew bridgeInstallerSupplyChain` (`.`) | `native-bridge/installer/AGENTS.md` | — | runs `verify/verify-supply-chain.sh` incl. `native-installer.cdx.json`: a Linux check; the same script is the `supply-chain` node's check, so Task 3 must share one entry/id rather than run it twice |
| `build-native-windows.yml:124`, `build-native-macos.yml:99` | platform | `./gradlew bridgeInstallerDebug` (`.`) | `native-bridge/installer/AGENTS.md` (add; 2.1) | `publish-release / build-windows`, `publish-release / build-macos` | CI only — macOS/Windows + release: the debug fixture-channel installer; not documented in any AGENTS.md |
| `check-installer.yml:79 (windows)`, `check-installer.yml:105 (macos)` | excluded | — | — | `installer / unit-tests (windows)`, `installer / unit-tests (macos)` | the platform executions map to the same check/test entries; no separate entry |
| `build-native-windows.yml:66-70 (windows)`, `build-native-macos.yml:70-74 (macos)` | excluded | — | — | `publish-release / build-windows`, `publish-release / build-macos` | CI only — macOS/Windows + release: workflow input validation, not a repository check |
| `build-native-windows.yml:56-57 (windows)` | excluded | `pwsh native-bridge/installer/scripts/provision-nsis.ps1 …` (`.`) | — | `publish-release / build-windows` | CI only — Windows + release: provisions the pinned NSIS into `build/native-bridge/installer/tools/nsis`; no D7 resolver, documented in `docs/verify.md` |
| `build-native-windows.yml:83-85 (windows)` | excluded | — | — | `publish-release / build-windows` | CI only — Windows: MSVC toolchain provisioning |
| `check-installer.yml:95-96 (macos)`, `build-native-macos.yml:60-61 (macos)` | excluded | — | — | `installer / unit-tests (macos)`; `publish-release / build-macos` | CI only — macOS: Xcode/`DEVELOPER_DIR` provisioning; no resolver |
| `check-installer.yml:36,39,56,59,75,78,101,104,121,124`; `build-native-windows.yml:62,65`; `build-native-macos.yml:66,69` | excluded | `rustup show active-toolchain` (`native-bridge/host/`, `native-bridge/installer/`) | — | all `installer` jobs; `publish-release / build-windows`, `publish-release / build-macos` | CI only — setup: R4 (two pins); fix as above |
| all `check-installer.yml` jobs | excluded | — | — | `installer / lint`, `installer / unit-tests (linux)`, `installer / unit-tests (windows)`, `installer / unit-tests (macos)`, `installer / scripts-tests` | CI only — setup: R1; fix: install JDK 21 and `config set java_home <jdk>` |
| `build-native-windows.yml:53`, `build-native-macos.yml:57` | excluded | — | — | `publish-release / build-windows`, `publish-release / build-macos` | CI only — macOS/Windows + release: the release runners pin Python 3.12; fix: install Python 3.12 and `config set path_prepend <dir>` (R7's `>= 3.8` does not cover the release pin) |
| `publish-release.yml` `sign-windows` / `sign-macos` | excluded | — | `native-bridge/installer/windows/AGENTS.md`; `native-bridge/installer/macos/AGENTS.md` | `publish-release / sign-windows`, `publish-release / sign-macos` | CI only — signing: the owner-only release signing/notarization; never a local gate |

#### Sub-doc: `native-bridge/installer/macos/AGENTS.md`

| Source | Kind | Local argv (cwd) | Doc | CI job | Reason |
|---|---|---|---|---|---|
| `macos/AGENTS.md:27` | test | `./gradlew bridgeInstallerTest bridgeInstallerScriptsTest` (`.`) | `native-bridge/installer/AGENTS.md` | `installer / unit-tests (macos)`, `installer / scripts-tests` | same two entries as the installer rows; the sub-doc line is their source, no duplicate entry |
| `macos/AGENTS.md:28` | platform | `./gradlew bridgeInstallerUnsigned` (`.`) | `native-bridge/installer/AGENTS.md` | `publish-release / build-macos` | same entry as the installer row (macOS/Windows + release) |
| `macos/AGENTS.md:29` | platform | `./gradlew bridgeInstallerPackageFixture` (`.`) | `native-bridge/installer/AGENTS.md` | — | same entry as the installer row |

#### Sub-doc: `native-bridge/installer/windows/AGENTS.md`

| Source | Kind | Local argv (cwd) | Doc | CI job | Reason |
|---|---|---|---|---|---|
| `windows/AGENTS.md:14` | test | `./gradlew bridgeInstallerTest` (`.`) | `native-bridge/installer/AGENTS.md` | `installer / unit-tests (windows)` | same entry as the installer row |
| `windows/AGENTS.md:16` | platform | `./gradlew bridgeInstallerUnsigned` (`.`) | `native-bridge/installer/AGENTS.md` | `publish-release / build-windows` | same entry |
| `windows/AGENTS.md:17` | platform | `./gradlew bridgeInstallerPackageFixture` (`.`) | `native-bridge/installer/AGENTS.md` | — | same entry |
| `publish-release.yml` `sign-windows` | excluded | — | `native-bridge/installer/windows/AGENTS.md` | `publish-release / sign-windows` | CI only — signing |

### Node: supply-chain (`verify/`)

Fence: `verify/AGENTS.md` `## Build & Test` runs from the repository root; `## Rules`: none.

| Source | Kind | Local argv (cwd) | Doc | CI job | Reason |
|---|---|---|---|---|---|
| `verify/AGENTS.md:14`; `build-supply-chain.yml:32` | excluded | `npm ci --ignore-scripts --prefix verify` (`.`) | `verify/AGENTS.md` | `supply-chain / build` | setup, not a gate: R11; fix: `npm ci --ignore-scripts --prefix verify` |
| `verify/AGENTS.md:15`; `build-supply-chain.yml:33` | excluded | `./gradlew installCargoDeny` (`.`) | `verify/AGENTS.md` | `supply-chain / build` | setup, not a gate: R12; fix: `./gradlew installCargoDeny` (and `verifyCargoCyclonedx` for the SBOM tool) |
| `verify/AGENTS.md:16`; `build-supply-chain.yml:34` | check | `verify/verify-supply-chain.sh` (`.`) | `verify/AGENTS.md` | `supply-chain / build` | the supply-chain verifier; `env_exit` 2 (wrong Node/npm, missing tool/input) with the script's fix text; needs R5, R6, R11, R12 |
| `verify/AGENTS.md:17` | excluded | `verify/aggregate-checksums.sh <commit>` (`.`) | `verify/AGENTS.md` | — | the script no longer exists (removed in #133, `5e5ab09e`): a stale doc line; 2.1 removes it (it cannot be an entry) |
| `verify/AGENTS.md:18` | excluded | `node --test verify/test/*.test.mjs` (`.`) | `verify/AGENTS.md` | `tooling / unit-tests` | the aggregate is run by the tooling node (`verify/test/` is a tooling path, see §Findings): the supply-chain doc line is `excluded` with why "runs as the tooling node's aggregate"; the entry itself is the tooling node's aggregate row |
| `build-supply-chain.yml:24,28` | excluded | — | — | `supply-chain / build` | CI only — setup: R5/R6 exact pins (26.8.1 / 11.19.0); fix: install those versions and put them first on `PATH` (`config set path_prepend <dir>`); the script re-checks the pins with `env_exit` 2 |
| `build-supply-chain.yml` | excluded | — | — | `supply-chain / build` | CI only — setup: R1; fix as above |

### Node: tooling (`scripts/`)

Fence: `scripts/AGENTS.md` `## Build & Test` runs from the repository root; `## Rules`: none. CI:
`check-tooling.yml`; `ci.yml`'s `changes` job also runs `components.py`/`last_green.py`.

| Source | Kind | Local argv (cwd) | Doc | CI job | Reason |
|---|---|---|---|---|---|
| `scripts/AGENTS.md:12` | excluded | `./gradlew installGitCliff` (`.`) | `scripts/AGENTS.md` | — | setup, not a gate: pinned SHA-256-verified git-cliff into `build/release-tools/`; `scriptsTest` depends on it |
| `scripts/AGENTS.md:13`; `check-tooling.yml:45` | test | `./gradlew scriptsTest` (`.`) | `scripts/AGENTS.md` | `tooling / unit-tests` | the stdlib-Python suites in `scripts/tests` (runs `python3 -I -m unittest discover -s scripts/tests -v` against the pinned git-cliff) |
| `scripts/AGENTS.md:14`; `check-tooling.yml:40` | test | `node --test scripts/check-mac-comparisons.test.mjs` (`.`) | `scripts/AGENTS.md` | `tooling / unit-tests` | unit tests of the MAC guard; needs R5 |
| `scripts/AGENTS.md:15`; `check-tooling.yml:42` | test | `node --test scripts/opencode-hooked-rules/index.test.mjs` (`.`) | `scripts/AGENTS.md` | `tooling / unit-tests` | tests of the OpenCode hooked-rules plugin; needs R5 |
| `scripts/AGENTS.md:16` | excluded | `python3 -I scripts/commitlint.py --message "feat(app): add thing"` (`.`) | `scripts/AGENTS.md` | — | example invocation of the commit validator (args are supplied by the caller); `pr-title.yml` and `ci.yml`'s `commit-guard` own it — a message check, not a tree check |
| `scripts/AGENTS.md:17`; `ci.yml:89 (changes)` | check | `python3 -I scripts/components.py check` (`.`) | `scripts/AGENTS.md` | `changes` | validates the component map; Linux static check |
| `scripts/AGENTS.md:18`; `ci.yml:80 (changes)` | excluded | `python3 -I scripts/components.py changed --base <sha>` (`.`) | `scripts/AGENTS.md` | `changes` | the CI selector: placeholders supplied by the workflow; `verify.py` implements its own selection (D3) and must not use this as a gate |
| `scripts/AGENTS.md:19`; `ci.yml:74 (changes)` | excluded | `python3 -I scripts/last_green.py --pr <n> --head <sha> --head-repo <owner/name> --head-ref <ref>` (`.`) | `scripts/AGENTS.md` | `changes` | GitHub-API last-green evidence with placeholders: selection support (network), not a tree check |
| `scripts/AGENTS.md:20` | excluded | `python3 -I scripts/project_flow.py --help` (`.`) | `scripts/AGENTS.md` | — | usage/help demo; the real commands (`check`, `sync`, `next`, `start`, `followup`) need GitHub and are event-driven, not a gate |
| `check-tooling.yml:29` | test | `node --test verify/test/*.test.mjs` (`.`) | `scripts/AGENTS.md` (add; 2.1) | `tooling / unit-tests` | the aggregate entry (task 1.2's example) covers the CI's single `node --test verify/test/workflow-contracts.test.mjs`; `verify/test/` is a tooling path, but the line is documented in `verify/AGENTS.md` today, so 2.1 adds it to `scripts/AGENTS.md`; Task 3 MUST expand this glob to explicit file arguments, because the table's `run` argv is executed without a shell (D2) |
| `check-tooling.yml:36` | check | `sh verify/check-root-aliases.sh` (`.`) | `scripts/AGENTS.md` (add; 2.1) | `tooling / unit-tests` | resolves every documented root Gradle alias with a dry run: Linux static check; `verify/check-root-aliases.sh` is in tooling's `ci-paths`; missing from the docs |
| `check-tooling.yml:38` | check | `sh verify/check-root-aliases.sh --self-test` (`.`) | `scripts/AGENTS.md` (add; 2.1) | `tooling / unit-tests` | proves the alias check fails on an unresolved alias; a separate entry so the branch gate runs it; missing from the docs |
| `check-tooling.yml:44` | excluded | `python3 --version && python3 -c 'import sys; assert sys.version_info >= (3, 8)'` (`.`) | — | `tooling / unit-tests` | CI only — setup: R7; fix: install Python 3.8+ and put it on `PATH` |
| `check-tooling.yml:26` | excluded | — | — | `tooling / unit-tests` | CI only — setup: R5 (`node-version: '26.8.1'`); fix: install Node 26.8.1 (or >= 22 for the JS tests) and `config set path_prepend <node bin>` |
| `check-tooling.yml` | excluded | — | — | `tooling / unit-tests` | CI only — setup: R1; fix as above |

`ci.yml`'s `ci-ok` job only aggregates job results; `commit-guard` is the `commitlint.py` message check
recorded in the `scripts/AGENTS.md:16` row.

### Node: docs (`docs/`)

Fence: `docs/AGENTS.md` `## Commands` runs from the repository root; `## Rules`: none. The documentation
site paragraph (`docs/AGENTS.md:29`) adds `check_site.py` in prose.

| Source | Kind | Local argv (cwd) | Doc | CI job | Reason |
|---|---|---|---|---|---|
| `docs/AGENTS.md:9`; `check-docs.yml:29 (agent-files)` | check | `python3 docs/checks/check_offline.py --root .` (`.`) | `docs/AGENTS.md` | `docs / agent-files` | offline guard over `docs/checks/` |
| `docs/AGENTS.md:10`; `check-docs.yml:30 (agent-files)` | check | `python3 docs/checks/check_agent_files.py --root .` (`.`) | `docs/AGENTS.md` | `docs / agent-files` | structural agent-file check; this change makes it run the table drift check too |
| `docs/AGENTS.md:11` | check | `python3 docs/checks/check_gate_b_matrix.py` (`.`) | `docs/AGENTS.md` | — | Gate B evidence-matrix validator: documented local check, no CI step |
| `docs/AGENTS.md:12`; `check-docs.yml:40 (unit-tests)` | test | `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v` (`.`) | `docs/AGENTS.md` | `docs / unit-tests` | stdlib suites of the docs checks; needs R7 |
| `docs/AGENTS.md:21` | excluded | `python3 -I scripts/agent_rules.py pointers --write` (`.`) | `docs/AGENTS.md` | — | hook-delivered-rule maintenance command under "Hook-delivered rules": regenerates the pointer block, not a gate; outside the drift check's `## Commands` section |
| `docs/AGENTS.md:29`; `check-docs.yml:31 (agent-files)` | check | `python3 docs/checks/check_site.py --root .` (`.`) | `docs/AGENTS.md` (move into `## Commands`; 2.1) | `docs / agent-files` | documentation-site guard; Linux static check, currently only in prose |
| `check-docs.yml` | excluded | — | — | `docs / agent-files`, `docs / unit-tests` | CI only — setup: R7 (the workflow installs nothing); fix: install Python 3.8+ and put it on `PATH` |

### Node: summary of rows per node

| Node | check | test | device | platform | producer | excluded | Total |
|---|---|---|---|---|---|---|---|
| root | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| android | 7 | 1 | 1 | 0 | 0 | 8 | 17 |
| web-extension | 5 | 3 | 0 | 0 | 2 | 6 | 16 |
| rust | 2 | 1 | 0 | 0 | 2 | 10 | 15 |
| protocol | 0 | 1 | 0 | 0 | 1 | 3 | 5 |
| bridge-scaffold | 5 | 1 | 0 | 0 | 0 | 12 | 18 |
| native-host | 3 | 2 | 0 | 0 | 0 | 6 | 11 |
| installer | 3 | 2 | 0 | 3 | 0 | 9 | 17 |
| installer/macos (sub-doc) | 0 | 1 | 0 | 2 | 0 | 0 | 3 |
| installer/windows (sub-doc) | 0 | 1 | 0 | 2 | 0 | 1 | 4 |
| supply-chain | 1 | 0 | 0 | 0 | 0 | 6 | 7 |
| tooling | 3 | 4 | 0 | 0 | 0 | 8 | 15 |
| docs | 4 | 1 | 0 | 0 | 0 | 2 | 7 |
| **Total** | **33** | **18** | **1** | **7** | **5** | **71** | **135** |

Every CI `run:` step in `check-*.yml` plus `build-supply-chain.yml` is either a row in its node table
(mapped to an entry) or a CI-only row above; `uses:` steps and artifact uploads are not commands and are
represented by the setup rows.

### Setup requirements (canonical fix text)

| Id | Requirement | Canonical fix |
|---|---|---|
| R1 | `java-home` (JDK 21, `.java-version`) | Install a JDK 21, then `python3 -I scripts/verify.py config set java_home <jdk>` (or set `JAVA_HOME`) |
| R2 | `android-sdk` | Set `sdk.dir` in `android/local.properties` or `ANDROID_HOME`/`ANDROID_SDK_ROOT` to the SDK, else `config set android_sdk <dir>` |
| R3 | `android-ndk` (pin `rust/ndk-version`) | `"$ANDROID_SDK_ROOT/cmdline-tools/latest/bin/sdkmanager" "ndk;<version>"` with the version from `./gradlew -q rustNdkVersion`; set `ANDROID_NDK_HOME`, or `config set android_ndk <dir>` |
| R4 | `rustup-toolchain` | Run `rustup show active-toolchain` in the directory of the node's `rust-toolchain.toml` (`rust/`, `native-bridge/host`, `native-bridge/installer`, `native-bridge/src-tauri`) so the pinned channel is installed |
| R5 | `path-tool` — `node` (`min` 22; `exact` 26.8.1 for tooling/supply-chain) | Install Node 26.8.1 (or >= 22 for the extension) and put it on `PATH`, or `config set path_prepend <node bin>` |
| R6 | `path-tool` — `npm` (record-only; the supply-chain script's `env_exit` carries the exact 11.19.0 pin) | Install npm 11.19.0 alongside Node 26.8.1 and put it on `PATH` |
| R7 | `path-tool` — `python` (`min` 3.8) | Install Python 3.8 or newer and put it on `PATH`, or `config set path_prepend <dir>` |
| R8 | `playwright-chromium` | `cd web-extension && npx playwright-core install --with-deps chromium` (CI ties the browser to its pinned `ubuntu-24.04` image), or set `VELES_TEST_CHROMIUM=<chromium>` |
| R9 | `path` — `web-extension/node_modules` (plus a declared `inputs` entry `{"path": "web-extension/node_modules", "mode": "stat"}`) | `cd web-extension && npm ci` (CI: `npm ci --ignore-scripts`) |
| R10 | `path` — `native-bridge/node_modules` (plus a declared `inputs` entry `{"path": "native-bridge/node_modules", "mode": "stat"}`) | `cd native-bridge && npm ci --ignore-scripts` (or `./gradlew bridgeInstall`) |
| R11 | `path` — `verify/node_modules` | `npm ci --ignore-scripts --prefix verify` |
| R12 | `path-tool` — `cargo-deny`, `cargo-cyclonedx` (resolved from `build/verify-tools/*/bin`) | `./gradlew installCargoDeny` and `./gradlew verifyCargoCyclonedx` |

macOS-only provisioning with no D7 resolver (Xcode 26.6 / Build `17F113`, its SDK, `DEVELOPER_DIR`,
NSIS, Visual Studio, Python 3.12 on the release runners) is `platform`/`excluded` and belongs in
`docs/verify.md`'s "CI only" list; it is not represented as a requirement.

### Findings for later tasks

1. **Extension related tests.** No `files-arg` resolver (see §Vitest); use `name-map` + `self` with
   `within: package.json:scripts.test`.
2. **Android guards.** Beyond raw-`Log`, the constant-time MAC comparison guard and the pairing-code guard
   join the table; `android/scripts/check-raw-log.sh`, `android/scripts/check-pairing-code.sh` and
   `web-extension/scripts/check-pairing-code.sh` hold the inline CI blocks verbatim, called from the
   repository root. `:app:releaseHookGuard` also joins as a check.
3. **Docs to extend (2.1).** `npm run build`, `npm run test:browser` (the web extension's existing
   `cd web-extension` block); `node scripts/check-mac-comparisons.mjs --scan android/app/src/main` and
   `android/scripts/check-pairing-code.sh` (the Android rule, whose block runs from the repository
   root; 2.3 replaces the rule's existing guard fence with the `android/scripts/check-raw-log.sh` call);
   `./gradlew :app:releaseHookGuard` (`android/AGENTS.md`, `android/`); `bridgeRustFormat`,
   `bridgeRustLint` (bridge scaffold); `node --test verify/test/*.test.mjs`,
   `sh verify/check-root-aliases.sh`, `sh verify/check-root-aliases.sh --self-test` (tooling);
   `node scripts/check-mac-comparisons.mjs --scan web-extension/src` and
   `web-extension/scripts/check-pairing-code.sh` (a new repository-root block in
   `web-extension/AGENTS.md`, because its only block starts with `cd web-extension` and both commands
   run from the root); move `check_site.py` into the docs `## Commands` fence; delete the stale
   `verify/aggregate-checksums.sh` line.
4. **`verify/test/*.test.mjs` ownership.** The command is documented in `verify/AGENTS.md` (supply-chain
   node) but `verify/test/` is a tooling path, and CI runs it in `tooling / unit-tests`. The entry must
   live in `tooling` (doc line added to `scripts/AGENTS.md`); the supply-chain doc line becomes an
   `excluded` entry with that reason.
5. **Producer rows.** `rustWasm` (`rust.wasm`), `rustHostJni` (`rust.hostJni`) and `npm run build`
   (`web-extension.build`) are `producers[]` entries, not command kinds. `bridgeInstallerUnsigned`,
   `bridgeInstallerPackageFixture` and `bridgeInstallerDebug` are `platform` entries (macOS/Windows).
6. **`./gradlew bridgeTest`** stays a `test`; `bridgeNpmTest` and `bridgeRustTest` are recorded
   `excluded` because only this aggregate runs them (no separate entries). Its other dependencies,
   `bridgeRustFormat`/`bridgeRustLint`, are also `check` entries for the task gate, so a branch-gate plan
   that runs those checks and then `bridgeTest` would run them twice — Task 3 MUST either drop the two
   check entries when `bridgeTest` runs, or keep only the checks and accept no branch-gate Rust test
   (dropping them from the plan while `bridgeTest` runs is recommended).
7. **`./gradlew protocolCheck`** is one aggregate `test`; Gradle runs its producers itself. Task 3 must
   decide whether to keep it as one entry (accepting that Gradle, not the verifier, orders
   `rustHostJni`/`rustWasm`) or split it into the consumer commands with explicit producer steps.
8. **Shared rules in reverse coverage.** `.agents/rules/android-lint-before-push.md` is listed by
   `android`, `rust` and `protocol`; `.agents/rules/native-bridge-test-app-tcc.md` is listed by
   `native-bridge`. D13 says reverse coverage checks "the node rules its `## Rules` lists", so the guard
   lines would need coverage in every listing node unless `verify_table.py` treats a rule line once when
   any node covers it. Recommend the latter (one entry, doc = the rule), recorded here for Task 9.
9. **Sub-node docs.** `native-bridge/installer/{macos,windows}/AGENTS.md` are the only docs of their
   directories but are not the `installer` node's `AGENTS.md`; D13's "the node's `AGENTS.md`" must accept
   them (or the table needs duplicate entries with those docs). Their commands map to the installer
   entries; the sub-doc rows above are deduped.
10. **Stale doc line.** `verify/aggregate-checksums.sh` does not exist (removed in #133); 2.1 removes the
    line rather than excluding it forever.

## Phase A environment (task 10.2)

The Phase A gates ran on the Linux dev container at `57f01790` plus the Phase A landing changes: Node
`v26.13.1`, npm `11.20.0`, Python `3.14.4`, and a `/usr/lib/jvm` holding only a JRE
(`openjdk-21-jre-headless`, no `javac`). Four environment gaps were fixed. The machine config
(`<git-common-dir>/veles-verify/tools.json`, `git-common-dir` = `/home/dev/repos/veles-android/.git`,
shared by every worktree) now holds:

| key | value | why |
| --- | --- | --- |
| `java_home` | `/home/dev/.jdk/zulu21` (Zulu 21.0.12.1) | `java` on PATH is the JRE: `:app:compileDebugJavaWithJavac` fails with "Toolchain installation '/usr/lib/jvm/java-21-openjdk-amd64' does not provide the required capabilities: [JAVA_COMPILER]". Zulu 21.0.12.1 matches `.java-version` (21.0.12). |
| `android_sdk` | `/home/dev/.android-sdk` | `ANDROID_HOME` was unset and there was no `android/local.properties`; `:app:lintReportDebug` fails with "SDK location not found". The SDK carries `ndk/29.0.14206865`, so `android-ndk` needs no separate key. |
| `path_prepend` | `/home/dev/.local/veles-verify/bin` | Node 26.8.1 with npm 11.19.0, plus the pinned `cargo-deny` 0.20.2 and `cargo-cyclonedx` 0.5.9. |

Provisioning behind those values:

- `npm ci --ignore-scripts --prefix verify`. Without it `node --test verify/test/*.test.mjs` failed the
  license-checker case: `spawnSync` on the missing `node_modules/.bin/license-checker-rseidelsohn`
  returns status `null`, and `assert.equal(null, 0, undefined)` throws `ERR_INVALID_ARG_TYPE`.
- `./gradlew verifyCargoDeny verifyCargoCyclonedx` installed the pinned tools under
  `build/verify-tools/{cargo-deny,cargo-cyclonedx}/bin/`. The two binaries were copied into the
  `path_prepend` directory because `path_prepend` carries one directory and `build/` is per-worktree
  and disposable; re-run the Gradle tasks and re-copy after a pin bump.
- Node **26.8.1** with npm 11.19.0, from the nodejs.org tarball into `~/.local/node-26.8.1`, with
  `bin/node`, `bin/npm` and `bin/npx` symlinked into the `path_prepend` directory.
  `verify/verify-supply-chain.sh` asserts those exact versions and the installed `v26.13.1` made the
  step exit 2 (`docs/reproducible-builds.md`, "floor versus exact reference").
- Playwright's Chromium: `playwright-core install chromium` rejects this host ("Playwright does not
  support chromium on ubuntu26.04-x64"). The pinned `chromium-1208` ubuntu24.04 archive was fetched
  with `PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64` and unpacked into
  `~/.cache/ms-playwright/chromium-1208/` with `INSTALLATION_COMPLETE` and `DEPENDENCIES_VALIDATED`;
  `apt-get install` added the shared libraries `ldd` reported missing (`libatk-1.0`,
  `libatk-bridge`, `libatspi`, `libcairo`, `libcups`, `libgbm`, `libpango`, the `libx*` set,
  `libasound`, `libxkbcommon`). `web-extension`'s `npm run test:browser` then passed. An earlier
  install attempt stalled in `oopDownloadBrowserMain.js` after the archive download, so the cached
  archive was unpacked by hand.
- Everything else probed clean without a key: all four pinned Rust 1.98.0 toolchains, `python3`,
  `playwright-chromium` (once unpacked) and the generated `rust.wasm` / `web-extension.build`
  outputs. `android/local.properties` was deliberately not created: the verifier passes the SDK
  through the step environment.

## Phase A findings (task 10.2)

The gates surfaced four defects that the earlier task-level checks could not see; all are fixed in
the Phase A landing.

1. **The 8 KiB summary dropped status lines and the missing-tool fix.** On a branch that changes ~60
   paths, the `tests: none related (<paths>)` notes alone exceed 8 KiB, and `render()` truncated the
   head mid-note: `web-extension`'s status line, the supply-chain fix and the `last-run.json` pointer
   all disappeared, against D10 and the "Summary overflow" scenario. `render()` now keeps every
   status line and the pointer, and drops notes and failure details that do not fit
   (`scripts/tests/test_verify_run.py`, `test_summary_overflow_from_notes_keeps_status_lines_and_pointer`).
2. **`npx` in the table is forbidden by the supply-chain policy.** `verify/check-remote-code.mjs`
   scans `scripts/` and rejects any `npx` in `scripts/verify-commands.json`, so
   `supply-chain.supply-chain` failed with `remote-code policy rejected scripts/verify-commands.json:
   npx is forbidden`. The design's `npx vitest run {targets}` became
   `./node_modules/.bin/vitest run {targets}` (the local binary npx would resolve; the same document
   says `verify/node_modules/.bin/` — "no `npx` or global fallback"), the now-unused `npx`
   requirement was removed, and the playwright fix text names `node_modules/.bin/playwright-core`.
3. **`cargo-cyclonedx --version` is not a version command.** The probe ran it and the tool exits 2
   (`error: unexpected argument '--version' found`); the Gradle `verifyCargoCyclonedx` task uses
   `cargo-cyclonedx cyclonedx --version`, and the table now does too.
4. **The installer's cargo output broke `bridgeFormat`.** `verify/generate-sboms.sh` runs
   cargo-cyclonedx in `native-bridge/installer/`, which creates `installer/target/`; Prettier then
   checked that directory's JSON and `bridgeFormat` failed on 75 files. `native-bridge/.prettierignore`
   now ignores `installer/target/`, as it already ignored `src-tauri/target/` and `host/`.

Phase A result: the task gate (`python3 -I scripts/verify.py`) exits 0 in 4 m 39 s and the branch gate
(`--branch`) exits 0 in 10 m 3 s, with every node `PASS` and only the expected `NOT RUN`s (the device
test, the macOS/Windows producers, and `protocol: NOT RUN (nothing planned: …)` in the task gate).

## Task-gate timings (task 11.2)

Phase B measured the task gate on four scratch commits on `V` = `ab479c90` (`chore(scripts): keep
failure details before summary notes`). Each scenario created a fresh worktree at `V`, provisioned it
as the node `AGENTS.md` files say (`npm ci` in `web-extension/`, `npm ci --ignore-scripts --prefix
verify`, plus `./gradlew installCargoDeny` when the `supply-chain` node was selected), and committed
exactly one changed path. The timed command was:

```bash
python3 -I scripts/verify.py --base ab479c900e3c7ce6fdf22baf4b43f0a2c2623146
```

- **Cold** — the first run in that worktree after `./gradlew --stop` and `(cd android && ./gradlew --stop)`,
  with no verifier state, no Gradle project caches (`build/`, `.gradle`) and no gate output beyond the
  provisioned `node_modules` and `build/verify-tools/cargo-deny` setup tool.
- **Warm** — the same command again in the same worktree, without stopping daemons.
- Every run exited 0; the totals and step times are `elapsed_s` from `build/verify/last-run.json`.
  Each worktree was removed afterwards (`git worktree remove --force`), so no scratch commit remains.

Machine: Linux dev container; Zulu JDK 21.0.12.1 (`java_home`), Android SDK with NDK 29.0.14206865,
Rust 1.98.0, Node 26.8.1 / npm 11.19.0 (the supply-chain pin), Python 3.14.4, and pinned
`cargo-deny` 0.20.2 / `cargo-cyclonedx` 0.5.9 in `path_prepend`.

| Scenario | Changed path | Nodes (task gate) | Cold total s | Warm total s | Slowest step (cold / warm) |
| --- | --- | --- | --- | --- | --- |
| extension-only | `web-extension/src/background.ts` | `web-extension`, `supply-chain` (`protocol` NOT RUN) | 560.24 | 87.67 | `rust.wasm` 285.28 / `supply-chain.supply-chain` 52.30 |
| rust | `rust/veles-crypto/src/lib.rs` | `android`, `rust`, `web-extension` (`protocol` NOT RUN) | 1193.37 | 91.64 | `android.release-hook-guard` 393.34 / `rust.test` 12.27 |
| Android | `android/app/src/main/java/me/nagaev/veles/otp/handlers/RegexMessageHandler.kt` | `android` (incl. its related `RegexMessageHandlerTest`; `protocol` NOT RUN) | 935.59 | 66.03 | `android.release-hook-guard` 510.41 / `android.release-hook-guard` 18.31 |
| docs | `docs/ci.md` | `docs` | 1.41 | 1.40 | `docs.offline-check` 0.51 / 0.41 |

The largest cold total is 1,193.37 s (rust), below 1,695 s (the 1,740-s budget less the 45-s reserve):
**budget stands**.

Two environment facts belong with these numbers. The gate's `supply-chain` step reads
`build/verify-tools/cargo-deny/bin/cargo-deny` directly, so `./gradlew installCargoDeny` is
provisioning in every worktree that selects it (CI and `verify/AGENTS.md` treat it as setup); the
`rustWasm` and `verifyCargoCyclonedx` tool installs are *not* provisioned — Gradle runs them on demand,
which is why the cold totals carry their build cost and the warm ones do not. And a detached-HEAD
linked worktree cannot configure Gradle at all: `AndroidGitVersionExtension.scan` throws
`org.eclipse.jgit.errors.NoWorkTreeException: Bare Repository has neither a working tree, nor an
index`, so each worktree was created detached as specified and then put on a throwaway local branch at
`V` before the timed run (the branch was deleted with the worktree).

## Phase C acceptance (task 15)

`V` = `1bf36ac4ddb2349105386d4323173d686a1252ee` (`docs(openspec): tick the Phase C wiring tasks`) on
`chore/verify-affected-nodes-stop-hook`; `git merge-base origin/master HEAD` =
`597ee2c691f2d4c2dec1c4450072eca86a76054e`. Machine: Linux dev container, Node `v26.13.1` on the system
`PATH` and Node `26.8.1` with npm `11.19.0` supplied by `path_prepend`
(`/home/dev/.local/veles-verify/bin`, which also carries the pinned `cargo-deny` 0.20.2 and
`cargo-cyclonedx` 0.5.9), Zulu JDK 21.0.12.1 (`java_home`), Android SDK with NDK 29.0.14206865
(`android_sdk`), Python 3.14.4. Every scratch worktree and branch used below was removed afterwards
(`git worktree remove --force`, `git branch -D`); the checkout used for the CLI runs is clean
(`git status --porcelain` prints nothing) and the machine config is back to the Phase A values
(`config show` below).

### Environment debt: Playwright Chromium (restored before the branch gate)

The browser cache had not been restored after Phase B (`~/.cache/ms-playwright` was absent), so the
table's documented fix was tried first:

```text
$ cd web-extension && ./node_modules/.bin/playwright-core install --with-deps chromium
BEWARE: your OS is not officially supported by Playwright; installing dependencies for ubuntu26.04-x64 as a fallback.
Cannot install dependencies for ubuntu26.04-x64 with Playwright 1.58.2!
Error: ERROR: Playwright does not support chromium on ubuntu26.04-x64
exit=1
```

This is the host limitation Phase A recorded. The pinned browser (playwright-core 1.58.2 pins
Chromium revision 1208, `145.0.7632.6`, `channel: 'chromium'` in `web-extension/scripts/test-browser.mjs`)
was installed the way Phase A did it: the download was fetched with a platform override, and — the
`oopDownloadBrowserMain.js` child stalled again after the archive reached 100% (16 m 28 s, killed) —
the archive was unpacked by hand:

```text
$ PLAYWRIGHT_HOST_PLATFORM_OVERRIDE=ubuntu24.04-x64 ./node_modules/.bin/playwright-core install chromium
Downloading Chrome for Testing 145.0.7632.6 (playwright chromium v1208) ... 100% of 167.3 MiB
# oopDownloadBrowserMain.js then stalled; killed after 16 m 28 s (exit=143)
$ unzip -q /tmp/playwright-download-zvUJUw/playwright-download-chromium-ubuntu24.04-x64-1208.zip -d ~/.cache/ms-playwright/chromium-1208
$ touch ~/.cache/ms-playwright/chromium-1208/{INSTALLATION_COMPLETE,DEPENDENCIES_VALIDATED}
$ ldd ~/.cache/ms-playwright/chromium-1208/chrome-linux64/chrome | grep -c "not found"   # 16
$ sudo apt-get update && sudo apt-get install -y --no-install-recommends libatk1.0-0t64 libatk-bridge2.0-0t64 \
    libcups2t64 libxcb1 libxkbcommon0 libatspi2.0-0t64 libx11-6 libxcomposite1 libxdamage1 libxext6 \
    libxfixes3 libxrandr2 libgbm1 libcairo2 libpango-1.0-0 libasound2t64
$ node -e "require('playwright-core').chromium.launch({channel:'chromium',headless:true}).then(async b=>console.log(await b.version()))"
145.0.7632.6
```

The requirement probe then resolved the executable and the branch gate ran `web-extension.browser`
(`ok`, 5.9 s).

### Step 1 — all gates (task 15.1)

Run in the checkout at `V`, in this order:

| # | Command | Exit | Result |
|---|---|---|---|
| 1 | `./gradlew scriptsTest` | 0 | `Ran 1130 tests in 35.079s` / `OK`; `BUILD SUCCESSFUL in 46s` (46.35 s wall) |
| 2 | `python3 -I scripts/components.py check` | 0 | no output |
| 3 | `python3 docs/checks/check_agent_files.py --root .` | 0 | no output |
| 4 | `python3 -m unittest discover -s docs/checks -p 'test_*.py'` | 0 | `Ran 329 tests in 55.514s` / `OK` |
| 5 | `node --test verify/test/*.test.mjs` | 0 | `tests 116`, `pass 116`, `fail 0` |
| 6 | `python3 -I scripts/verify.py --branch` | 0 | 10 nodes `PASS` in 14 m 17.4 s (`elapsed_s` 856.49); only the expected `NOT RUN`s (the component map has ten nodes; there is no `root` node) |

Gate 6 output (every node line; the wrapped `NOT RUN` notes are the expected skips):

```text
android: PASS
  NOT RUN (device test; real-device rule)
bridge-scaffold: PASS
docs: PASS
installer: PASS
  NOT RUN (macOS and Windows only; CI job publish-release / build-windows, build-macos)
  NOT RUN (macOS and Windows only; CI job none (no CI job; a local fixture producer))
  NOT RUN (macOS and Windows only; CI job publish-release / build-windows, build-macos)
native-host: PASS
protocol: PASS
rust: PASS
supply-chain: PASS
tooling: PASS
web-extension: PASS
real  14m17.355s
exit=0
```

`web-extension.browser ok 5.9` in `last-run.json` is the Playwright run; `android.release-hook-guard`,
`rust.wasm`, `web-extension.bundle` and the other steps are `ok` too. No gate was weakened or skipped.

### Step 2 — CLI acceptance in a disposable worktree (task 15.2)

Scratch worktree: `git worktree add --detach /tmp/opencode/verify-scratch 1bf36ac4…`; each case reset
with `git reset --hard 1bf36ac4…` and the worktree was removed at the end. `config show` (run in the
scratch worktree first) was:

```text
$ python3 -I scripts/verify.py config show
config: /home/dev/repos/veles-android/.git/veles-verify/tools.json (version 1)
java_home: /home/dev/.jdk/zulu21 (version 21.0.12.1)
android_sdk: /home/dev/.android-sdk (valid SDK)
android_ndk: (not set)
path_prepend: /home/dev/.local/veles-verify/bin (directory)
```

**2a — only `web-extension/src/` changed.** One committed line appended to
`web-extension/src/background.ts`, then:

```text
$ python3 -I scripts/verify.py --list --base 1bf36ac4ddb2349105386d4323173d686a1252ee
protocol: selected by web-extension/src/background.ts
  NOT RUN (branch gate only: protocol.contract)
supply-chain: selected by web-extension/src/background.ts
  supply-chain.supply-chain: cd . && verify/verify-supply-chain.sh
web-extension: selected by web-extension/src/background.ts
  web-extension.format: cd web-extension && npm run format:check
  web-extension.lint: cd web-extension && npm run lint
  web-extension.mac-comparisons: cd . && node scripts/check-mac-comparisons.mjs --scan web-extension/src
  web-extension.pairing-code: cd . && web-extension/scripts/check-pairing-code.sh
  rust.wasm: cd . && ./gradlew rustWasm
  web-extension.typecheck: cd web-extension && npm run typecheck
  tests: none related (web-extension/src/background.ts)
  NOT RUN (branch gate only: web-extension.bundle)
  NOT RUN (branch gate only: web-extension.browser)
exit=0
```

Exactly three nodes: `web-extension`, `protocol`, `supply-chain`.

**2b — a `rust/` change also selects `android`, with the reason.** One committed line appended to
`rust/veles-crypto/src/lib.rs`:

```text
$ python3 -I scripts/verify.py --list --base 1bf36ac4ddb2349105386d4323173d686a1252ee
android: selected by rust/veles-crypto/src/lib.rs
  android.spotless: cd android && ./gradlew spotlessCheck
  android.detekt: cd android && ./gradlew detekt
  android.lint: cd android && ./gradlew lintDebug
  android.raw-log: cd . && android/scripts/check-raw-log.sh
  android.pairing-code: cd . && android/scripts/check-pairing-code.sh
  android.mac-comparisons: cd . && node scripts/check-mac-comparisons.mjs --scan android/app/src/main
  android.release-hook-guard: cd android && ./gradlew :app:releaseHookGuard
  tests: none related (rust/veles-crypto/src/lib.rs)
  NOT RUN (device test; real-device rule)
protocol: selected by rust/veles-crypto/src/lib.rs
  NOT RUN (branch gate only: protocol.contract)
rust: selected by rust/veles-crypto/src/lib.rs
  rust.format: cd . && ./gradlew rustFormat
  rust.lint: cd . && ./gradlew rustLint
  rust.test: cd . && ./gradlew rustTest
web-extension: selected by rust/veles-crypto/src/lib.rs
  … (the same extension steps as 2a)
exit=0
```

**2c — an absent tool gives `NOT RUN` with its fix and exit 2.** A docs-only scratch commit
(`docs/ci.md`) selects only `docs`, whose five steps all require `python`; `path_prepend` supplies
Node and the cargo tools but **not** Python, so no `config set` was needed for this case and only
`PATH` was narrowed (a directory of symlinks to `/usr/bin` etc. without `python*`):

```text
$ env PATH=/tmp/opencode/no-python-bin bash -c 'command -v python3 || echo "python3 absent from PATH"'
python3 absent from PATH
$ env PATH=/tmp/opencode/no-python-bin /usr/bin/python3 -I scripts/verify.py --base 1bf36ac4ddb2349105386d4323173d686a1252ee
docs: NOT RUN (docs.offline-check: path-tool: python3 is required (3.8 or newer))
  tests: none related (docs/ci.md)
  docs.offline-check: NOT RUN
    path-tool: python3 is required (3.8 or newer)
    python3 is not on the effective PATH
    candidates: none found under $HOME/.cargo/bin, $HOME/.nvm/versions/node/*/bin
    fix: Install Python 3.8 or newer and put it on PATH, or run `python3 -I scripts/verify.py config set path_prepend <dir>`.
    rerun: cd . && python3 docs/checks/check_offline.py --root .
  … (the other four docs steps report the same NOT RUN and fix)
exit=2
```

**2d — the Node variant: Node 22 absent gives `NOT RUN` with the Node fix and exit 2.** A branch
worktree at `V` (a detached worktree cannot configure Gradle) with one committed line in
`web-extension/src/background.ts` selects `web-extension`, `protocol` and `supply-chain`. `config show`
first showed `path_prepend: /home/dev/.local/veles-verify/bin` — it supplies Node 26.8.1 — so for this
run the prepend was replaced by a directory holding only `cargo-deny` and `cargo-cyclonedx`, and `PATH`
was narrowed to a symlink farm without `node`/`npm`/`npx`:

```text
$ python3 -I scripts/verify.py config show
path_prepend: /home/dev/.local/veles-verify/bin (directory)
$ python3 -I scripts/verify.py config set path_prepend /tmp/opencode/veles-verify-no-node
path_prepend: /tmp/opencode/veles-verify-no-node (directory)
$ env PATH=/tmp/opencode/no-node-bin python3 -I scripts/verify.py config show
path_prepend: /tmp/opencode/veles-verify-no-node (directory)
$ env PATH=/tmp/opencode/no-node-bin bash -c 'command -v node || echo "node absent from PATH"'
node absent from PATH
$ env PATH=/tmp/opencode/no-node-bin python3 -I scripts/verify.py --base 1bf36ac4ddb2349105386d4323173d686a1252ee
protocol: NOT RUN (nothing planned: no check and no related test for the changed files)
  NOT RUN (branch gate only: protocol.contract)
supply-chain: NOT RUN (supply-chain.supply-chain: path-tool: node is required (22 or newer))
web-extension: NOT RUN (web-extension.format: path-tool: node is required (22 or newer))
  tests: none related (web-extension/src/background.ts)
  NOT RUN (branch gate only: web-extension.bundle)
  NOT RUN (branch gate only: web-extension.browser)
  supply-chain.supply-chain: NOT RUN
    path-tool: node is required (22 or newer)
    node is not on the effective PATH
    candidates: none found under $HOME/.cargo/bin, $HOME/.nvm/versions/node/*/bin
    fix: Install Node 22 or newer and put it on PATH, or run `python3 -I scripts/verify.py config set path_prepend <node bin>`.
    rerun: cd . && verify/verify-supply-chain.sh
  web-extension.format: NOT RUN
    … (the same NOT RUN and fix; rerun: cd web-extension && npm run format:check)
  web-extension.lint: NOT RUN
    … (the same NOT RUN and fix; rerun: cd web-extension && npm run lint)
  web-extension.mac-comparisons: NOT RUN
    … (the same NOT RUN and fix)
  web-extension.typecheck: NOT RUN
    … (the same NOT RUN and fix; rerun: cd web-extension && npm run typecheck)
real  3m35.780s
exit=2
```

`last-run.json` shows what the time was: `elapsed_s` 215.53, the extension's `rust.wasm` producer ran
(`ok`, 215.0 s) and the three selected commands were `not_run` (0.0 s each) with the Node fix above;
the node statuses are `supply-chain: NOT RUN`, `web-extension: NOT RUN`, `protocol: NOT RUN (nothing
planned)`. The config was restored and confirmed:

```text
$ python3 -I scripts/verify.py config set path_prepend /home/dev/.local/veles-verify/bin
$ python3 -I scripts/verify.py config show
path_prepend: /home/dev/.local/veles-verify/bin (directory)
$ bash -c 'command -v node; node --version'
/usr/bin/node
v26.13.1
```

The worktree and its branch were removed afterwards. (Step 3c repeats the same environment with the
hook. The Python case above stays because it is the cheapest node: `docs` has no producer, so that
case isolates the probe and the exit code without a Gradle build.)

### Step 3 — the real Stop hook (task 15.3)

Disposable worktree on a branch at `V` (a detached worktree cannot configure Gradle, see Phase B):
`git worktree add -b verify-hook-acceptance /tmp/opencode/verify-hook-acc 1bf36ac4…`, provisioned with
`npm ci --ignore-scripts` in `web-extension/`, `npm ci --ignore-scripts` in `native-bridge/`,
`npm ci --ignore-scripts --prefix verify`, `./gradlew installCargoDeny verifyCargoCyclonedx`, then a
manual task gate (`python3 -I scripts/verify.py`) to warm the Gradle/dist/SBOM outputs: exit 0,
`elapsed_s` 1614.41 (26 m 55 s cold; every node `PASS`, `protocol: NOT RUN (nothing planned …)`).
`build/verify/passes.json` was deleted before the baseline stop so that the first hook run really ran
the tasks instead of reading remembered passes. Claude Code turns are real `claude -p` turns (native
`2.1.329`); the worktree carries `.claude/settings.json` with both `Stop` commands
(`agent_rules.py hook` and `verify.py --hook claude`, timeout 1800).

**3a — baseline: a stop with no further edit is a clean, silent pass.**

```text
$ (time timeout 1700 claude -p 'Reply with exactly: DONE and do nothing else.') > hook-run1.log 2>&1
exit=0
Plugin "kotlin-lsp" is enabled in project settings but isn't installed — …
DONE
real  9m17.513s
```

No `Stop hook feedback` line, no `systemMessage`: the hook wrote nothing to stdout, as a pass must.
`build/verify/last-run.json`: `elapsed_s` 547.19, every node `PASS`/`NOT RUN` as expected; the session
settled `blocks 0`, `repeat {id: null, n: 0}` and `spent_s 0.0` (a clean settlement resets the
session), so the run time is taken from `last-run.json`.

**3b — a lint error blocks, naming the extension lint step with its tail; fixing it makes the next
stop silent.** A real ESLint error was introduced in the worktree
(`const unusedScratchValue = 1;` before `export {}` in `web-extension/src/background.ts`;
`npm run lint` → `389:7 error 'unusedScratchValue' is assigned a value but never used
@typescript-eslint/no-unused-vars`, exit 1). The configured command was then invoked directly with a
synthetic Stop event (`cwd` = the worktree) to capture its stdout exactly:

```text
$ CLAUDE_PROJECT_DIR=/tmp/opencode/verify-hook-acc bash -c 'python3 -I "$CLAUDE_PROJECT_DIR/scripts/verify.py" --hook claude || true' < event-stop.json
real  2m26.355s
exit=0
{"decision": "block", "reason": "verify: the task gate found problems; fix them and stop again (attempt 1 of 3)\n\nandroid: PASS (remembered)\n  tests: none related (…)\n  NOT RUN (device test; real-device rule)\nbridge-scaffold: PASS\n  NOT RUN (branch gate only: bridge.test)\ndocs: PASS (remembered)\ninstaller: PASS (remembered)\n  …\nnative-host: PASS (remembered)\n  …\nprotocol: NOT RUN (nothing planned: no check and no related test for the changed files)\n  NOT RUN (branch gate only: protocol.contract)\nrust: PASS (remembered)\nsupply-chain: PASS\ntooling: PASS (remembered)\n  …\nweb-extension: FAIL\n  NOT RUN (branch gate only: web-extension.bundle)\n  NOT RUN (branch gate only: web-extension.browser)\n  web-extension.lint: FAIL (exit 1)\n    rerun: cd web-extension && npm run lint\n    log: …/web-extension.lint.1.log\n    tail:\n      > @veles/web-extension@0.1.0 lint\n      > eslint .\n      /tmp/opencode/verify-hook-acc/web-extension/src/background.ts\n        389:7  error  'unusedScratchValue' is assigned a value but never used  @typescript-eslint/no-unused-vars\n      ✖ 1 problem (1 error, 0 warnings)\nsteps: bridge.format OK; …; web-extension.lint FAIL; … skipped"}
```

stderr was empty. The synthetic session settled `spent_s 145.5`, `blocks 1`,
`repeat {id: [web-extension, web-extension.lint, fail], n: 1}`.

The same error was then left in place for a real Claude Code turn (the prompt forbade edits so the
turn could not repair it):

```text
$ (time env … claude -p 'Do not modify any files, do not run any commands, and do not use any tools. Reply with exactly: DONE' --output-format stream-json --verbose) > hook-run2-lint.log
exit=0
real  3m23.381s
# the stream carries four Stop invocations: three "Stop hook feedback:" user messages
#   "verify: the task gate found problems; fix them and stop again (attempt 1|2|3 of 3)" each naming
#   "web-extension.lint: FAIL (exit 1)" with the eslint tail, then
#   "Stop says: verify: the task gate still has problems; not blocking again (attempt 4 of 3)"
#   as a systemMessage pass; result: num_turns 4, is_error false
```

Session settlement: `blocks 3`, `repeat {id: [web-extension, web-extension.lint, fail], n: 4}`,
`spent_s` 181.9 s for the four hook runs **in total**; the first run is ≥95.6 s (it also re-ran
`supply-chain` at 95.6 s), which leaves ≈28.8 s per run for the other three — every other node was
remembered.

The error was removed, and the next stop:

```text
$ (time env … claude -p 'Do not modify any files, … Reply with exactly: DONE' --output-format stream-json --verbose) > hook-run3-fixed.log
exit=0
real  2m36.0s
grep -c "Stop hook feedback" hook-run3-fixed.log   # 0
```

`last-run.json`: `elapsed_s` 146.03, `web-extension: PASS` (format 9.9 s, lint 11.3 s, typecheck
11.8 s, `rust.wasm` 4.4 s), every other node `PASS (remembered)`; the new session settled clean
(`spent_s 0.0`, `blocks 0`). Silent pass.

**3c — Node absent from the verifier's effective environment blocks with the Node fix.** `config show`
first (the same output as Step 2; `path_prepend` = `/home/dev/.local/veles-verify/bin`, which *does*
supply Node 26.8.1), then the prepend directory was replaced for the run by one holding only the two
cargo tools, and `PATH` was narrowed to a symlink farm without `node`/`npm`/`npx`:

```text
$ python3 -I scripts/verify.py config set path_prepend /tmp/opencode/veles-verify-no-node
path_prepend: /tmp/opencode/veles-verify-no-node (directory)
$ env PATH=/tmp/opencode/no-node-bin bash -c 'command -v node || echo "node absent"; command -v python3'
node absent
/tmp/opencode/no-node-bin/python3
$ env PATH=/tmp/opencode/no-node-bin python3 -I scripts/verify.py config show
path_prepend: /tmp/opencode/veles-verify-no-node (directory)
$ env PATH=/tmp/opencode/no-node-bin bash -c 'python3 -I "$CLAUDE_PROJECT_DIR/scripts/verify.py" --hook claude || true' < event-stop-nonode.json
real  4m08.894s
exit=0
{"decision": "block", "reason": "verify: the task gate found problems; fix them and stop again (attempt 1 of 3)\n\nandroid: NOT RUN (android.mac-comparisons: path-tool: node is required (22 or newer))\n  NOT RUN (device test; real-device rule)\nbridge-scaffold: NOT RUN (bridge.format: path-tool: node is required (22 or newer))\n  NOT RUN (branch gate only: bridge.test)\ndocs: PASS\ninstaller: FAIL\n  …\nnative-host: PASS\nprotocol: NOT RUN (nothing planned: no check and no related test for the changed files)\n  NOT RUN (branch gate only: protocol.contract)\nrust: PASS\nsupply-chain: NOT RUN (supply-chain.supply-chain: path-tool: node is required (22 or newer))\ntooling: PASS\n  …\nweb-extension: NOT RUN (web-extension.format: path-tool: node is required (22 or newer))\n  …\n  web-extension.format: NOT RUN\n    path-tool: node is required (22 or newer)\n    node is not on the effective PATH\n    candidates: none found under $HOME/.cargo/bin, $HOME/.nvm/versions/node/*/bin\n    fix: Install Node 22 or newer and put it on PATH, or run `python3 -I scripts/verify.py config set path_prepend <node bin>`.\n    rerun: cd web-extension && npm run format:check\n  …"}
```

Every Node-dependent step reports the same `NOT RUN` and the Node fix. One unrelated failure also
appeared in this run: `installer.test: FAIL (exit 1)`, the pre-existing
`native-bridge/installer/tests/cli.rs:565` `the_launcher_mode_refuses_a_missing_or_malformed_frame`
panicking on `write: … BrokenPipe` — a one-off pipe race (the child exits before the parent writes its
frame). The test passed five times immediately afterwards when run directly, and
`./gradlew bridgeInstallerTest --rerun-tasks` passed three times with the same node-less `PATH`; it did
not recur in the later runs (the next run recorded `installer: PASS (remembered)`). It is master code,
not part of this change.

A real Claude Code turn with the same node-less `PATH` and config:

```text
$ (time env PATH=/tmp/opencode/no-node-bin claude -p 'Do not modify any files, … Reply with exactly: DONE' --output-format stream-json --verbose) > hook-run4-nonode.log
exit=0
real  4m05.0s
# three "Stop hook feedback:" messages, attempt 1..3 of 3, each starting
#   "verify: the task gate found problems; …\n\nandroid: NOT RUN (… node is required (22 or newer))"
# and showing `fix: Install Node 22 or newer and put it on PATH, … config set path_prepend <node bin>`;
# then "Stop says: verify: the task gate still has problems; not blocking again (attempt 4 of 3)"
# systemMessage pass; result: num_turns 4, is_error false
```

Session settlement: `blocks 3`, `spent_s 224.9`, `repeat {id: [android, android.mac-comparisons,
not_run], n: 4}`. The config was then restored and confirmed:

```text
$ python3 -I scripts/verify.py config set path_prepend /home/dev/.local/veles-verify/bin
$ python3 -I scripts/verify.py config show
path_prepend: /home/dev/.local/veles-verify/bin (directory)
$ bash -c 'command -v node; node --version'
/usr/bin/node
v26.13.1
```

**3d — a stop moved into a linked worktree runs that worktree's own verifier.** A `claude -p` turn cannot
express the move: the Stop event's `cwd` is the session's project directory (a Bash `cd` does not move
it), and in this machine the launcher's checkout must be the session checkout at `V` because the
primary checkout is on `master` (`165b2e0f`) at a revision that carries neither `scripts/verify.py` nor
the hook. The turn was tried from the primary checkout anyway and Claude Code's permission check
refused the `cd`:

```text
$ cd /home/dev/repos/veles-android && (time claude -p 'Run exactly this Bash command: cd /tmp/opencode/verify-hook-acc && pwd && git rev-parse --short HEAD. Then reply with exactly: DONE' …)
"The command did not run: the permission check blocked it because it changes into
 `/tmp/opencode/verify-hook-acc` before running git, which could pick up hooks or config from that
 directory." — result, exit 0, 11 s
```

The move was then exercised the way the brief allows, by invoking the configured command directly with
a synthetic event whose `cwd` is a linked worktree. Two runs together prove the case, because in the
full run both sides were at `V`, and that alone cannot tell the two copies of `scripts/verify.py`
apart.

*Which script ran.* One scratch commit in the target worktree adds a marker that only that copy can
print, and records it inside the target's own tree:

```text
# target worktree /tmp/opencode/verify-marker (branch verify-marker-target, one commit on V)
$ git log --oneline -1
acbb2df7 chore(scratch): mark this checkout's verifier
$ grep -n "linked-worktree marker" scripts/verify.py
4586:        marker = "verify: linked-worktree marker 7c9e1f2a5b3d4c60 (this checkout's verifier ran)"
# the launcher checkout at V cannot produce it
$ grep -c "linked-worktree marker" scripts/verify.py
0
$ cat event-marker.json
{"session_id":"…","cwd":"/tmp/opencode/verify-marker","hook_event_name":"Stop","stop_hook_active":false}
$ CLAUDE_PROJECT_DIR=/home/dev/.t3/…/t3-a6c1e51b-… bash -c 'python3 -I "$CLAUDE_PROJECT_DIR/scripts/verify.py" --hook claude || true' < event-marker.json
real  0m0.592s
exit=0
# stdout: 0 bytes
# stderr:
verify: linked-worktree marker 7c9e1f2a5b3d4c60 (this checkout's verifier ran)
$ cat /tmp/opencode/verify-marker/build/verify/acceptance-marker.txt
verify: linked-worktree marker 7c9e1f2a5b3d4c60 (this checkout's verifier ran)
/tmp/opencode/verify-marker
# the launcher checkout was not touched: its last-run.json kept this mtime and md5 across the call
$ ls -l --time-style=full-iso build/verify/last-run.json; md5sum build/verify/last-run.json
-rw------- 1 dev dev 18464 2026-11-20 21:47:25.119552938 +0000 build/verify/last-run.json
955372635b3cbe1aa837fb1282026291  build/verify/last-run.json
```

The marker string exists only in the target's `scripts/verify.py`, so the launcher's relay of it on
stderr proves that the dispatched child is that worktree's own script; the marker file records the
target as the child's `cwd`. The scratch commit makes that verifier exit before the gate, so this run
proves dispatch and only dispatch. The worktree was removed afterwards.

*Where the state goes.* The full task-gate run below (target and launcher both at `V`, so this half
alone does not identify the script) shows the child's state landing in the target's tree and not the
launcher's:

```text
$ cd /home/dev/.t3/worktrees/veles-android/t3-… && cat event-stop-moved.json
{"session_id":"398d4757-…","transcript_path":"/tmp/opencode/transcript.jsonl","cwd":"/tmp/opencode/verify-hook-acc","hook_event_name":"Stop","stop_hook_active":false}
$ CLAUDE_PROJECT_DIR=/home/dev/.t3/…/t3-a6c1e51b-… bash -c 'python3 -I "$CLAUDE_PROJECT_DIR/scripts/verify.py" --hook claude || true' < event-stop-moved.json
real  4m41.270s
exit=0
```

That first attempt blocked on `bridge-scaffold: FAIL`, because Gradle reused the daemon that the
node-less run had started (`A problem occurred starting process 'command 'node''` in
`:bridgeNodeCheck`); `./gradlew --stop` in both roots fixed the daemon environment, and the same
invocation then gave the clean pass the case needs:

```text
$ ./gradlew --stop; (cd android && ./gradlew --stop)
$ CLAUDE_PROJECT_DIR=… bash -c 'python3 -I "$CLAUDE_PROJECT_DIR/scripts/verify.py" --hook claude || true' < event-stop-moved2.json
real  0m53.657s
exit=0
# stdout: empty (silent pass); stderr: empty
```

File evidence around the clean run:

```text
# the launcher checkout (must not change)
$ ls -l --time-style=full-iso build/verify/last-run.json
-rw------- 1 dev dev 18925 2026-11-20 20:29:44.558126249 +0000 build/verify/last-run.json
$ md5sum build/verify/last-run.json
733c5762a70306f019a9d1dd2cb1eca1  build/verify/last-run.json
# the linked worktree (must change)
$ ls -l --time-style=full-iso /tmp/opencode/verify-hook-acc/build/verify/last-run.json
-rw------- 1 dev dev 14663 2026-11-20 21:41:25.035325195 +0000 /tmp/opencode/verify-hook-acc/build/verify/last-run.json
{"elapsed_s": 53.09, "nodes": {"android": "PASS (remembered)", "bridge-scaffold": "PASS",
 "docs": "PASS (remembered)", "installer": "PASS (remembered)", "native-host": "PASS (remembered)",
 "protocol": "NOT RUN", "rust": "PASS (remembered)", "supply-chain": "PASS (remembered)",
 "tooling": "PASS (remembered)", "web-extension": "PASS (remembered)"}}
```

Within that call the launcher's `last-run.json` kept its 20:29:44 mtime and md5 (read immediately
before and after it), while the target's was rewritten in the target's tree. (That launcher file was
rewritten later by unrelated work in this checkout, so it is not compared again here.)

### Measured hook times

Wall clock per hook invocation (`real` of the invocation), and the gate's own `elapsed_s`:

| Case | Method | Hook wall clock | Gate `elapsed_s` |
|---|---|---|---|
| 3a baseline, no remembered passes | `claude -p` turn (one Stop) | 557.5 s (turn 9 m 17.5 s) | 547.19 |
| 3b lint error | configured command + synthetic event | 146.4 s (2 m 26.4 s) | 145.5 (`spent_s`) |
| 3b lint error | `claude -p`, 4 Stops (3 blocks + notice) | 203.4 s (turn 3 m 23.4 s) | 181.9 total (`spent_s`; first ≥95.6 s) |
| 3b fixed | `claude -p` turn (one Stop) | 156.0 s (turn 2 m 36.0 s) | 146.03 |
| 3c Node absent | configured command + synthetic event | 248.9 s (4 m 08.9 s) | 248.1 (`spent_s`) |
| 3c Node absent | `claude -p`, 4 Stops (3 blocks + notice) | 245.0 s (turn 4 m 05.0 s) | 224.9 total (`spent_s`) |
| 3d dispatch marker (target's own script) | configured command + synthetic event | 0.6 s | — (the target exits before the gate) |
| 3d worktree move (stale daemon) | configured command + synthetic event | 281.3 s (4 m 41.3 s) | 280.55 (`bridge.format` FAIL) |
| 3d worktree move (clean) | configured command + synthetic event | 53.7 s | 53.09 |

A clean settlement resets the session counters (`spent_s 0`), so clean runs are read from
`build/verify/last-run.json`; blocked sessions keep their `spent_s` in
`<git-common-dir>/veles-verify/hook-state.json`. The slowest single hook run measured here is the
cold first-run baseline at 547 s, well inside the 1740-s budget; a warm stop that only re-runs one
node is ~45–150 s. The budget has little room for a cold first stop in a fresh worktree, though: the
cold full-selection task gate took 1614.41 s, about 80 s under the 1695 s cap (1740 s less the 45 s
reserve), so a cold first stop in a fresh worktree is near the budget.

### Limits recorded by these runs

- Playwright Chromium cannot be installed with the documented command on this Ubuntu 26.04 host;
  the cached ubuntu-24.04 build (the CI image's platform) is used, as in Phase A.
- The Stop event's `cwd` cannot be moved by a `claude -p` turn (and the permission check refuses a
  `cd` out of the project), so the "moved into a linked worktree" case is exercised through the
  configured hook command with a synthetic event — the real launcher path.
- A Gradle daemon started under a node-less `PATH` keeps that environment; `./gradlew --stop` after
  any environment change, or the next `bridge.format` fails with `starting process 'command 'node''`.
- `native-bridge/installer/tests/cli.rs:565` has a one-off pipe-race flake (not reproduced; master
  code, not this change).

## Post-verify gate rerun: a second master-code flake

The first `python3 -I scripts/verify.py --branch` run on the final tree (after apply.md, verify.md and
retrospective.md) exited 1 on `bridgeHostTest`: `native-bridge/host/tests/runtime.rs:629`,
`staged_stale_terminal_confirmed_by_the_synchronous_release`, assertion `left: 1, right: 0` on the
cleanup count. This branch changes no file under `native-bridge/host` (`git diff --stat 597ee2c6..HEAD
-- native-bridge/host` is empty) and the file's last commits are `f57200ef` and `2c7f0ce1` on master.

Investigation: the test passed 3/3 in isolation (`cargo test --test runtime
staged_stale_terminal_confirmed_by_the_synchronous_release`) and the whole `./gradlew bridgeHostTest`
suite passed on one clean rerun (2 m 46 s). Recorded as a flaky, timing-sensitive master-code test
under full-suite load — the second such flake seen (the first was
`native-bridge/installer/tests/cli.rs:565`'s BrokenPipe). It is reported, not masked: no retry loop,
no empty commit and no check was weakened. The final pre-push gate was re-run on the committed tree.
