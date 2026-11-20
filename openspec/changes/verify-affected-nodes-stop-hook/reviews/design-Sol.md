# Review record: design — Sol

- Artifact: `design.md`
- Reviewer: Sol (GPT-6.2-Sol, medium)
- Preset: `sol` = [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes:
  - `brainstorm.md` sha256 3391633d78486d00995eff372b327d598d0720ba5d84754bbaa764eb4c66c0f9
  - `proposal.md` sha256 826874093f598bd0fa9c43928783802a74ca309c704bd48babc8854123cb3b0c

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | Fingerprint omits the effective environment (inherited variables, resolved SDK/NDK, `local.properties`) and ignored declared inputs. | fixed: D6 `env.vars` allowlist, resolved paths, `local.properties` hash; `inputs` walked on the filesystem with summary files for large trees. |
| high | Vitest `related` cannot be intersected by appending the allowlist; empty match succeeds. | fixed: D4 separates list-only target resolution from the run; empty is `none related`; fallback to `name-map`+`self` if no list-only resolver. |
| high | Admission ignores reserved block slots; reservation fields missing; block charged before repeat suppression. | fixed: D12 slots count live reservations; reservation fields; block charged only when emitted; clean settle leaves other reservations. |
| medium | `--list` behaviour, stop-on-first-failure per node, CLI outcome → hook mapping undefined. | fixed: D1 modes, D4 stop rule, D3/D12 selection errors block with fix; internal errors fail open. |
| medium | SDK warning only in `config show`; migration lacks the real acceptance run. | fixed: D8 warning on every Android run; Migration step 4. |
| high | No dependency plan for the extension's bundle and browser tests (build, `dist`, Chromium). | fixed: D2 producers `rust.wasm`, `web-extension.build`, Chromium `machine-path` requirement. |
| high | Drift check incompatible with the offline guard; no union rule for split Gradle lines; Android lint command inline. | fixed: D13 sibling module, `shlex` added to the allowlist; union rule; Android rule fenced. |
| medium | Producer schema lacks `env_exit`/`env_patterns`; no fix per match; no machine path kind. | fixed: D2 schema with fix per entry; `machine-path` kind. |
| medium | `name-map` self-run; Android test discovery; crate breadth. | fixed: D4 rules for each. |
| medium | Pins and resolvers per command undefined. | fixed: D7 resolver table; supply-chain pins deferred to its diagnostics. |
| high | Time cap covers only the command wait; expired reservations not charged. | fixed: D9/D12 one deadline over the whole hook with cleanup margin; expired reservations charged in full. |
| high | Fingerprint misses mode, symlinked generated content, and edits during the run. | fixed: D6 modes, symlinks make a node non-reusable, before/after stability check. |
| high | Common-dir comparison not normalised. | fixed: D11 uses `agent_rules.discover_checkout` semantics and `realpath`. |
| medium | Limit notice reads worktree-local `last-run.json`; pruning can drop live sessions. | fixed: D12 session `synopsis`; pruning skips live reservations. |
| medium | Override presence not enforced; workflow-contract mutation fixtures. | fixed: D13 required override entry; D14 mutation cases. |

### Round 2 — changes-required

Round-1 findings: 7 resolved, 6 partly, 1 not (folded into the rows below).

| Severity | Finding | Disposition |
|---|---|---|
| high | F1: reuse key misses installed-dependency edits and tool changes at the same path when the build checks the pin. | fixed: D6 `stat` input mode for `node_modules/`; every tool of a reusable command declared and recorded (path and version output); D2 validation. |
| high | F2: `--list` vs resolvers; related schema; allowlist only on one strategy. | fixed: D1 lets list-only resolvers run in `--list`, with `targets unresolved`; D2 full `related` schema; `within` applies to every strategy (D4). |
| high | F3: the bundle suite also reads `dist/`; the unit suite rebuilds it. | fixed: D2 `web-extension.build` required by bundle and browser tests; D5 producer chain immediately before each consumer. |
| high | F4: deadline not enforced end to end; dispatch carries no deadline; orphaned groups. | fixed: D9 every external call and lock under the deadline, `running.json` with reaping; D11 gate owner, `--deadline`, launcher kill and reap; D12 budget bounded by the passed deadline. |
| high | F5: drift matching ignores `cwd`. | fixed: D13 working directory from `cd` lines or "Run from" sentences; forward and reverse matching compare it. |
| high | F6: producers before all checks; producer failure stopping independent checks. | fixed: D4 order and failure isolation. |
| medium | F7: zero-work outcomes; selection-error repeat identity. | fixed: D10 messages; D12 outcome table and identity. |
| medium | F8: argument-bearing Gradle examples, multiline lines, `cd`, non-shell blocks. | fixed: D13 reading rules. |
| medium | F9: Chromium glob vs the browser script's resolution. | fixed: D7 `playwright-chromium` resolver honouring `VELES_TEST_CHROMIUM`; CI caveat in the fix. |
| medium | F10: crate breadth vs whole node suite. | fixed: D4 crate commands are the Gradle tasks, narrower than the node's tests; open question removed. |
| medium | F11: stability check vs producer-refreshed outputs. | fixed: D6 source-side stability; generated part captured before the consumer. |
| low (round-1 partly) | `discover_checkout` semantics; `config show` warning. | fixed: D11 states the helper's semantics; D8 keeps the warning in both places. |
| low (round-1 partly) | NDK resolver order. | fixed: D7 first existing candidate, mismatch is `NOT RUN`. |

### Round 3 — changes-required

Round-2 findings: 7 resolved, 5 partly (folded into the rows below).

| Severity | Finding | Disposition |
|---|---|---|
| high | Dependency edits preserving mode, size and mtime keep the key; `JAVA_OPTS` missing. | fixed: D6 `stat` mode hashes device, inode, mode, size and `st_ctime_ns` (any write updates ctime, which cannot be set back); `JAVA_OPTS`, `_JAVA_OPTIONS`, `JDK_JAVA_OPTIONS`, `HOME` added; Gradle machine inputs (`~/.gradle/gradle.properties`, `~/.gradle/init.d/`). |
| high | Dead-run cleanup skipped while its reservation is live; no watchdog on the same-checkout path. | fixed: D9 orphans decided by the run lock (`flock` released on death), never by reservations; D11 the launcher is always the watchdog and the gate always runs in a child. |
| high | Counters reset on "nothing changed", contrary to the brainstorm. | fixed: D12 nothing changed and unowned-only leave counters unchanged; clean needs at least one node selected. |
| medium | Run lock retried until the deadline; CLI unbounded. | fixed: D9 run lock tried once (immediate contention); state locks retried at most 10 s. |
| medium | Disagreeing generated snapshots of one path. | fixed: D6 makes the node non-reusable. |
| medium | Argument-bearing Gradle forward match; exclusions without cwd. | fixed: D13 exact argv-and-cwd forward match; `excluded` keyed by doc, argv and `cwd` (D2 schema). |

### Round 4 — changes-required

Round-3 findings: 4 resolved, 2 partly (folded into the rows below).

| Severity | Finding | Disposition |
|---|---|---|
| high | Two cleanup owners; group exit cannot be confirmed by `waitpid`. | fixed: D9 the next run-lock holder is the only cleanup owner, confirms each group gone with `killpg(pgid, 0)`, else contention; D11 the launcher kills only its direct child and never reads `running.json`. |
| high | The watchdog enforces the outer deadline, not the admitted one. | fixed: D11 the child reports its admitted deadline on a dedicated pipe; kill at that deadline + 30 s; D12 expiry charges `budget_s + 120 s`. |
| high | Gradle machine inputs assume the default user home. | fixed: D6 `{gradle_user_home}` resolved as Gradle does; non-reusable when `gradle.user.home` is set through options. |
| medium | Exact exclusions not checked before the Gradle union rule. | fixed: D13 exclusions first. |

### Round 5 — changes-required (cap reached)

Round-4 findings: 3 resolved, 1 partly (row 1 below).

| Severity | Finding | Disposition |
|---|---|---|
| high | Gradle home can be redirected through `JAVA_TOOL_OPTIONS` (`gradle.user.home`, `user.home`). | accepted limit (owner): documented in design.md Known limits. |
| high | Admission reserves only `budget_s`; the watchdog grace and expiry charge can exceed the 60-minute cap. | accepted limit (owner): documented in design.md Known limits. |
| high | Deadline expiry outside a running command yields `no hit`/`not reached`, not a blocking timeout. | accepted limit (owner): documented in design.md Known limits. |
| high | Re-invoking `rustWasm` does not refresh the package for Cargo settings Gradle does not track. | accepted limit (owner): documented in design.md Known limits. |
| medium | Which node owns a shared producer's `doc`. | fixed: D13 a producer's `doc` may name any node's doc and counts for that node. |

#### Escalation

The pass reached its 5-round cap with four high findings open. The agent agrees with all of them and has a
concrete fix for each; sent to the owner with the options: (A) apply the fixes and run a confirmation pass
capped at 2 rounds — recommended; (B) apply the fixes without further review; (C) drop remembered passes
from the hook, which removes most of the remaining risk but reverses an owner decision.

Owner's decision (2026-11-20): skip all four as too rare; document them and continue. The four are listed
under Known limits in design.md; the medium finding is fixed. No further pass was requested.

Approved content: design sha256 2cb7cd4b1c30634eab4340716439ee439fbe12c7a6ee2b3c8bde343d8276afdb — approved by the owner's decision at the escalation; Sol's open findings are accepted limits.
