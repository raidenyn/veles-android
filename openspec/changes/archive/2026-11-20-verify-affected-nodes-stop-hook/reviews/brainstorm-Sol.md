# Review record: brainstorm — Sol

- Artifact: `brainstorm.md`
- Reviewer: Sol (GPT-6.2-Sol, medium)
- Preset: `sol` = [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: none (brainstorm is first in the order)

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | A second `Stop` entry fails `check_agent_files.py` `_hook_findings` (exactly one entry, one command). | fixed: the checker accepts exactly the two `Stop` commands; fixtures and tests updated (Stop hook, Wiring). |
| high | The extension-only acceptance example contradicts the map (`web-extension/src/*` also selects `protocol`, `supply-chain`); exact overrides win before `.github/` globals. | fixed: Selection and Acceptance state the map's real selection and the override precedence. |
| high | The seed table misses Linux-runnable tests (`test:bundle`, `test:browser`, host build-contract suite); connected tests unsettled. | fixed: Inventory requirement covers every documented command; additions named; device tests are a `device` kind outside both gates. |
| high | Adding `tooling` to the `AGENTS.md` basename owner misses exact overrides and node rules; checker owner sets; CI-selection change unacknowledged. | fixed: the drift check moves into the agent-file check (docs node); one override for the table; CI-selection effect stated in Non-goals and Growing the table. |
| medium | Acceptance omits the issue's command list, green CI and the PR note. | fixed: Acceptance section lists them. |
| high | Classification and narrowing unsettled (`scriptsTest`, `bridgeTest`, `rustTest`, protocol). | fixed: `check`/`test`/`device`/`platform` kinds with the rule "executes a test ⇒ test"; split combined Gradle lines; strategy table states each limit. |
| high | No per-command requirements; no contract for environment failures inside a subprocess; NDK and `local.properties`; exit precedence. | fixed: per-command `requires`, `env_exit`, `android_ndk` key, `local.properties` warning, exit precedence 1 > 2 > 0. |
| medium | Git query details, unowned-only and unaccounted paths, module loading under `-I`, argv expansion. | fixed: Selection lists the reads and error policy; module loading; argv lists without a shell. |
| medium | Drift test definition: templates, combined Gradle lines, reverse direction; scoped-rule glob answer. | fixed: two-way drift rules, Gradle task-name matching, templates exempt; the glob limit recorded. |
| high | Remembered-pass fingerprint does not bind plan, shared inputs, environment; no invalidation after failure. | fixed: fingerprint covers argv plan, all node-path blobs, entry, environment; requirements re-checked; failure deletes the record. |
| high | CLI exit codes vs hook protocol; event `cwd` vs `CLAUDE_PROJECT_DIR`; both hooks in one session. | fixed: hook-mode output contract (exit 0, JSON only), event `cwd` resolution, real-session check of both commands. |
| high | The 3-repeat bound does not bound a turn; time; counter scope; child cleanup. | fixed: per-session backstop (5 blocks / 60 min), state file, reset rule, process-group termination. |
| medium | Contended run vs pass; summary size; log retention. | fixed: explicit `NOT RUN` for contention and nothing planned; 300-char and 8 KiB caps; newest 10 runs kept. |

### Round 2 — changes-required

Round-1 findings: 8 resolved, 5 partly (folded into the rows below).

| Severity | Finding | Disposition |
|---|---|---|
| high | Remembered-pass fingerprint narrower than selection (`gradlew`, `gradle/` select Android); generated WASM accepted by existence only. | fixed: fingerprint covers every path that can select the node (paths, ci-paths, globals, overrides) and generated sources; `generated` prerequisites with a producer run when stale. |
| high | The launcher in `CLAUDE_PROJECT_DIR` may be missing or older than the active worktree. | fixed: launcher re-executes the active checkout's verifier; `\|\| true` so a missing script never blocks; real-session test across worktrees at different revisions. |
| high | A 29-minute per-run budget does not enforce the 60-minute session cap; counter reset ambiguous. | fixed: per-run budget = min(29 min, time left); no run with < 1 min left; only a clean executed stop resets. |
| medium | CI-only checks vs docs; Gradle `--tests` example matched as full suite. | fixed: CI-only rule (document then table, or list under "CI only" in `docs/verify.md`); Gradle matching requires a documented line with no other arguments. |
| medium | Probes must see the effective tool; exact supply-chain Node/npm pins; Rust NDK order. | fixed: probes use the command's environment, read pins from the build's own files, follow or defer to the build's resolution (`env_exit`/`env_patterns`). |
| medium | Deleted or moved test paths passed to runners. | fixed: only existing targets are passed; deleted paths inform selection only. |
| medium | The verifier's own invocations in tooling Build & Test. | fixed: listed as `excluded` lines. |

### Round 3 — changes-required

Round-2 findings: 4 resolved, 3 partly (folded into the rows below).

| Severity | Finding | Disposition |
|---|---|---|
| high | The session backstop lives in worktree-local state, so a session that changes worktree gets a fresh allowance. | fixed: hook state moves to the git common dir (`veles-verify/hook-state.json`), locked, keyed by session, newest 50 kept; passes and logs stay worktree-local. |
| medium (round-2 partly) | Fingerprint enumeration omits `retired-paths` and `basename-owners`. | fixed: coverage decided by `components.owner_nodes` itself, not a copy of its rules. |
| medium (round-2 partly) | Launcher compatibility not stated. | fixed: supported case stated; a launching checkout without the script verifies nothing, a stated limit. |
| medium | Producer and `env_patterns` failures contradict the "everything else is FAIL" rule. | fixed: producers follow the command contract; `env_exit`/`env_patterns` are `NOT RUN` with fix text in both places. |

### Round 4 — changes-required

Round-3 findings: all 4 resolved.

| Severity | Finding | Disposition |
|---|---|---|
| high | Selection ownership is not a command's dependency boundary (the Gate B matrix check reads `protocol/conformance/gate-b-journeys.json`, which does not select docs). | fixed: each command declares `inputs` that widen the fingerprint, or `"reuse": false`; a consistency check requires one of them. |
| high | An unchanged source fingerprint does not prove generated output is current (`rustWasm` reads `verify/cargo-tools.toml`). | fixed: verify no longer judges freshness; the producer always runs before its consumer and Gradle's up-to-date checks decide. |
| medium | Backstop admission not atomic across worktrees; pruning can evict a resumable session. | fixed: reservation of budget and a block under the shared lock before a run; the 50-session pruning stated as a limit. |

### Round 5 — changes-required (cap reached)

Round-4 findings: 2 resolved, 1 partly (the row below).

| Severity | Finding | Disposition |
|---|---|---|
| high | A remembered pass can be reused after the git-ignored WASM package is replaced in place; nothing ties the package to the passing run. | fixed per the owner's decision (A): the fingerprint hashes every generated output a node consumes. |
| low | The `rustWasm` example names `verify/cargo-tools.toml`; the task's tool pin is `rust/toolchain-tools.toml`. | fixed: the example names `rust/toolchain-tools.toml`. |

#### Escalation

The pass reached its 5-round cap with one high finding open. Sent to the owner in plain language with the
options: (A) hash the generated output's contents into the consumer's fingerprint — recommended;
(B) never remember a node whose commands consume generated output; (C) accept it as a stated limit.
Owner's decision (2026-11-20): option A, hash the output. Applied; a new pass (Pass 2) confirms the fix.

## Pass 2 — confirmation of the owner's decision (cap 2)

### Round 1 — approve

| Severity | Finding | Disposition |
|---|---|---|
| — | No findings. The generated-output hash, its test and Key Decisions are consistent; the tool-pin fix is correct. Builder detail noted: a deterministic ordering and encoding for the path-and-byte hash. | recorded for design |

Approved content: brainstorm sha256 3391633d78486d00995eff372b327d598d0720ba5d84754bbaa764eb4c66c0f9
