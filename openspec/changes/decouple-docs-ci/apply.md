# Apply Receipt

> Generated at the end of the apply phase to mark code-implementation
> complete and provide verify with the state it needs.
> Overwritten on each apply iteration; iteration counter grows.

**Change**: `decouple-docs-ci`
**Iteration**: `1`
**Applied at**: `2026-11-20 02:21`
**Executor**: `subagent-driven-development`

---

## Workspace

- **Worktree**: `/home/dev/.t3/worktrees/veles-android/t3-f402d01c-996b02dde3595f53`
- **Branch**: `chore/decouple-supply-installer-ci`

---

## Commits

- **Range**: `165b2e0f..HEAD`
- **Count**: 12 (this receipt's own commit follows)

---

## Tasks

- **Completed**: `17 of 18` checkboxes in tasks.md flipped to `- [x]`
- **Remaining**: `6.2` (the post-push CI confirmation, which runs in finalize/closeout)

---

## Selection evidence

Three one-line commits on a scratch branch (deleted after the run), each on top of the last,
run with `python3 -I scripts/components.py changed --base C^ --head C`:

| Scratch commit | File touched | Nodes selected |
|---|---|---|
| `fcf5369b` | `docs/checks/check_site.py` | `docs` |
| `cf2a6c33` | `.github/workflows/check-docs.yml` | `docs`, `tooling` |
| `ed89b415` | `verify/test/workflow-contracts.test.mjs` | `tooling` |

All other nodes were `false` in every run.

Historical commits re-run against the new map:

| Commit | Change | Nodes selected |
|---|---|---|
| `f37cc4bc` | #174 | `docs`, `native-host` (the host `AGENTS.md` coupling is out of scope) |
| `165b2e0f` | #175 | `docs`, `tooling` |

---

## Pre-push checks (Task 6.1)

`python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)` selects
`docs`, `tooling`, `supply-chain`, `installer`, `protocol`, `native-host`. Results:

### docs
- `python3 docs/checks/check_offline.py --root .` — PASS (exit 0, no output).
- `python3 docs/checks/check_agent_files.py --root .` — PASS (exit 0).
- `python3 docs/checks/check_gate_b_matrix.py` — PASS (exit 0, prints its usual "not Gate B
  acceptance" note).
- `python3 docs/checks/check_site.py --root .` — PASS (exit 0).
- `python3 -m unittest discover -s docs/checks -p 'test_*.py' -v` — PASS (284 tests, OK).
- Same suite under Python 3.8 (`uv run --python 3.8 …`) — PASS (284 tests, OK).

### tooling
- `./gradlew installGitCliff installCargoDeny` — PASS.
- `./gradlew scriptsTest` — PASS.
- `node --test scripts/check-mac-comparisons.test.mjs` — PASS.
- `node --test scripts/opencode-hooked-rules/index.test.mjs` — PASS.
- `python3 -I scripts/commitlint.py --message "feat(app): add thing"` — PASS (exit 0).
- `python3 -I scripts/components.py check` — PASS (exit 0).
- `python3 -I scripts/project_flow.py --help` — PASS (usage printed).
- `python3 -I scripts/last_green.py --pr <n> --head <sha> --head-repo <owner/name> --head-ref <ref>`
  — NOT RUN: needs an open PR, its head repository/ref and GitHub API access.

### supply-chain
- `npm ci --ignore-scripts --prefix verify` — PASS.
- `./gradlew installCargoDeny` — PASS.
- `verify/verify-supply-chain.sh` — PASS (exit 0) with the pinned Node 26.8.1 on `PATH`; the host
  default Node 26.13.1 fails the script's exact-version guard, so the pinned interpreter was
  downloaded to `/tmp/opencode/node-v26.8.1-linux-x64` for the run.
- `node --test verify/test/*.test.mjs` — PASS (116 tests, 115 pass, 1 skipped, 0 fail).
- `verify/aggregate-checksums.sh <commit>` — NOT RUN: the script listed in `verify/AGENTS.md` is
  not present in the tree and is marked optional there.

### installer
- `./gradlew bridgeInstallerFormat bridgeInstallerLint bridgeInstallerTest` — PASS.
- `./gradlew bridgeInstallerScriptsTest` — PASS.
- `./gradlew bridgeInstallerSupplyChain` — PASS.
- `./gradlew bridgeInstallerUnsigned` and `bridgeInstallerPackageFixture` — NOT RUN: Windows and
  macOS input producers, not runnable on Linux.

### protocol
- `./gradlew protocolCheck` — PASS with `JAVA_HOME=/home/dev/.jdk/zulu21`,
  `ANDROID_HOME=/home/dev/.android-sdk` and `ANDROID_SDK_ROOT=/home/dev/.android-sdk`.
- `./gradlew rustHostJni` was not run separately; `protocolCheck` built and ran the consumers it
  covers on this host.

### native-host
- `./gradlew bridgeHostToolchainCheck bridgeHostFormat bridgeHostLint bridgeHostTest` — PASS.
- `./gradlew bridgeHostBuild` — PASS.
- `python3 -m unittest discover -s native-bridge/host/scripts -p 'test_build_contract.py' -v` —
  PASS (25 tests, OK).
- Windows and macOS adapters — NOT RUN on Linux, as the node document states.

---

## Device testing

No Android or Chrome behaviour changes: the change is CI selection, CI guards and documentation.
No device or browser run is applicable.

---

## Next step

Run `openspec-verify-change`; then finalize creates the PR, where Task 6.2 confirms the `changes`
summary and that `docs / agent-files` ran the offline guard first.
