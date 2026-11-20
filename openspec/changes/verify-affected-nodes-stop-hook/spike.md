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
