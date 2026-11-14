# Verification notes (`consolidate-agent-files`)

Inputs for `verify.md` (tasks 9.1–9.7). Everything below was produced on 2026-11-14 from a
disposable fixture project **outside** this worktree (design D11), except where a command says
otherwise. Nothing in this file is inferred from documentation unless the row says
**documentation-only**.

## Tool versions

| Tool | Version | How read |
|---|---|---|
| Claude Code | `2.1.325` | `claude --version` |
| OpenCode | `2.3.0` | `opencode --version` (`opencode v2.3.0`) |
| `opencode-rules` plugin | `2.0.0` | `opencode plugin list` (two entries; see caveat) and `~/.cache/opencode/npm/opencode-rules@2.0.0` |
| OpenSpec CLI | `2.0.0` | `openspec --version` |
| Python (agent-file check) | `3.14.4` | `python3 --version` |
| Antigravity | — | **not run**, see blocker |

## Fixture

`/tmp/opencode/verify-matrix`, deleted and recreated for this run:

- `AGENTS.md` → `Root guidance token: ROOTGUIDE-ZZ`
- `android/AGENTS.md` → `Android node guidance token: ANDROIDGUIDE-ZZ`
- `android/x.txt`, `rust/x.txt`, `native-bridge/host/x.txt`, `native-bridge/installer/x.txt`
- `.agents/rules/repo-token.md` — `trigger: always_on`, body `MARKER-REPO-ZZ`
- `.agents/rules/rust-token.md` — `trigger: glob`, YAML-list `globs:` and `paths:` both `- "rust/**"`, body `MARKER-RUST-ZZ`
- `.agents/rules/bridge-token.md` — `trigger: glob`, YAML-list `globs:` and `paths:` both `- "native-bridge/**"`, extra key `description: bridge parent scope`, body `MARKER-BRIDGE-ZZ`
- `.claude/rules` → `../.agents/rules`
- `opencode.json` copied verbatim from the repository root

The matrix rows below were produced from that state. **Afterwards**, for the manual-discovery
exercise only, a `## Rules` section was added to the fixture's `AGENTS.md` and `android/AGENTS.md`
with the line "If your tool does not load scoped rules, read `.agents/rules/rust-token.md` by hand.";
that edit is described in its own section and does not belong to the matrix runs.

Probe prompt template, with `<file>` substituted per scenario:

```text
First read the file <file>. Then reply with exactly the marker tokens of every workspace rule
active in this conversation, one per line, or NONE. Do not read any other file.
```

Command lines used (from `/tmp/opencode/verify-matrix`):

```bash
# Claude Code
claude -p '<prompt>' --allowedTools Read --output-format text
# OpenCode — isolated config dir so only the project plugin loads
OPENCODE_CONFIG_DIR=/tmp/opencode/verify-matrix-cfg opencode --standalone run -m openai/gpt-6.1-sol '<prompt>'
```

## Scoping matrix (task 9.5)

Every row is a fresh session; the tokens shown are the ones the model actually printed, except the
italic token in row 4, which was not printed. The extra `description` key in `bridge-token.md`
produced no parser error or warning in either tool.

| # | Scenario (fixture) | Claude Code 2.1.325 | OpenCode 2.3.0 + `opencode-rules` 2.0.0 | Expected |
|---|---|---|---|---|
| 1 | Root session, read `android/x.txt` | `ROOTGUIDE-ZZ`, `MARKER-REPO-ZZ`, `ANDROIDGUIDE-ZZ` | `ROOTGUIDE-ZZ`, `MARKER-REPO-ZZ`, `ANDROIDGUIDE-ZZ` | REPO only, no RUST/BRIDGE — **met by both** |
| 2 | Root session, read `rust/x.txt` | `MARKER-REPO-ZZ`, `MARKER-RUST-ZZ` | `ROOTGUIDE-ZZ`, `MARKER-REPO-ZZ`, `MARKER-RUST-ZZ` | REPO + RUST, no BRIDGE — **met by both** |
| 3 | Root session, read `native-bridge/host/x.txt` then `native-bridge/installer/x.txt` | `ROOTGUIDE-ZZ`, `MARKER-REPO-ZZ`, `MARKER-BRIDGE-ZZ` | `ROOTGUIDE-ZZ`, `MARKER-REPO-ZZ`, `MARKER-BRIDGE-ZZ` | REPO + BRIDGE, parent scope reaches both children — **met by both**, extra key tolerated |
| 4 | Root session, read `rust/x.txt` twice | `MARKER-REPO-ZZ`, `MARKER-RUST-ZZ` (once each) | `ROOTGUIDE-ZZ`, `MARKER-REPO-ZZ` — *`MARKER-RUST-ZZ` missing* | REPO + RUST each exactly once — **Claude Code met, OpenCode deviates** (see below) |
| 5 | Session started inside `android/` | guidance lines `Root guidance token: ROOTGUIDE-ZZ` and `Android node guidance token: ANDROIDGUIDE-ZZ`; rule markers `NONE` | guidance line `Android node guidance token: ANDROIDGUIDE-ZZ` only; rule markers `NONE` | no rule marker; `ANDROIDGUIDE-ZZ` present; the ancestor chain is required (D5c and the spec) — Claude Code loads both files (2/2 runs), **OpenCode deviates** by loading the node file only (3/3 runs) |
| 6 | Root session reading `rust/x.txt` with `opencode.json` moved aside (plugin-unavailable proxy) | not run (no plugin layer) | rule markers `NONE`; model answered `Root guidance token: ROOTGUIDE-ZZ` from `AGENTS.md` | no rule markers — **met**; the discovery fallback is exercised separately below |

Scenario 1 also shows that both tools load a nested `AGENTS.md` when a file beside it is read
(`ANDROIDGUIDE-ZZ` appears without any instruction to open `android/AGENTS.md`).

### No-read baseline (activation timing)

The post-read rows above cannot by themselves show *when* a rule appears, so a root session that
reads no file was run first. Both tools delivered the always-on rule only:

```bash
# Claude Code — from /tmp/opencode/verify-matrix
claude -p 'Reply with exactly the marker tokens of every workspace rule active in this conversation,
  one per line, or NONE. Do not read any file and do not use any tool.' --output-format text
```

```text
ROOTGUIDE-ZZ
MARKER-REPO-ZZ
```

```bash
# OpenCode — from /tmp/opencode/verify-matrix
OPENCODE_CONFIG_DIR=/tmp/opencode/verify-matrix-cfg opencode --standalone run -m openai/gpt-6.1-sol \
  'Reply with exactly the marker tokens of every workspace rule active in this conversation,
   one per line, or NONE. Do not read any file and do not use any tool.'
```

```text
ROOTGUIDE-ZZ
MARKER-REPO-ZZ
```

No `MARKER-RUST-ZZ` and no `MARKER-BRIDGE-ZZ` before any read in either tool. Together with the
post-read rows (RUST after a `rust/` read, BRIDGE after `native-bridge/` reads, neither after an
`android/` read) this establishes the activation order for these three fixture rules. It does **not**
establish anything about the real rules beyond the repository probes below, which have their own
no-read baseline.

### Scenario 4 in OpenCode — observed defect (reported, not fixed)

Reproduced **4/4** runs. Asking the model to quote every chunk of context containing `MARKER-`
shows the repo rule block only; the `rust-token` block is genuinely absent, not merely omitted from
the summary. The session store confirms it: a single-read session contains a persisted
`msg_rule_admission_…` message holding `MARKER-RUST-ZZ`, while all four double-read sessions contain
no such message, and a later `session.context` pass does not re-add it. The plugin derives the
admission message id only from the rule keys (`msg_rule_admission_<identity>`, see
`buildRuleAdmissionPart` in `opencode-rules@2.0.0`), so re-admission of the same rule in the same
session collides with the persisted message; the outcome observed here is that the rule is lost for
the rest of the exchange. Two controls keep this from being a probe artefact:

| Control | Result |
|---|---|
| Read `rust/x.txt`, then `android/x.txt`, then report | `ROOTGUIDE-ZZ`, `MARKER-REPO-ZZ`, `ANDROIDGUIDE-ZZ`, `MARKER-RUST-ZZ` — RUST survives a *different* second read |
| `opencode run` turn 1 reads `rust/x.txt`, `--continue` turn 2 asks for rule markers | `ROOTGUIDE-ZZ`, `MARKER-REPO-ZZ` — RUST already gone across turns |

Single-read runs (scenario 2) reproduced 3/3 with `MARKER-RUST-ZZ` present, so this is specific to
reading the same path again, not general flakiness. Impact for this repository: an agent that reads
the same file twice in one OpenCode session can silently lose the scoped rule; the node
`AGENTS.md` rule list is the mitigation already documented. No file was changed for this.

### Scenario 5 — node-folder start, per tool

| Tool | `AGENTS.md` files loaded with cwd `android/` | Rule files delivered | Runs |
|---|---|---|---|
| Claude Code 2.1.325 | root `AGENTS.md` **and** `android/AGENTS.md` | none | 2/2 |
| OpenCode 2.3.0 | `android/AGENTS.md` **only** — the root `AGENTS.md` (node map, plugin pointer) is not loaded | none | 3/3 |

The expectation is unchanged: design D5c and the spec require the **ancestor chain** to load when the
agent starts inside a node folder, and neither tool delivers a rule file. Claude Code meets it.
**OpenCode deviates from D5c and from the spec's "`AGENTS.md` chain still loads" requirement**: three
consecutive fresh sessions started in `android/` saw the node guidance only, never the root file, so
the node map and the root's plugin/discovery pointers are absent in exactly the degraded state the
requirement is about. The node file's own start-at-root notice and rule list are present, which is
the documented mitigation, but that does not satisfy the chain requirement. Reproduced 3/3; reported
as an observed deviation, no guidance file changed.

## OpenCode: plugin configuration caveats (tasks 9.2)

Both were observed on the fixture, not read from documentation:

| Invocation | Rule markers received |
|---|---|
| `OPENCODE_CONFIG_DIR=<scratch> opencode run …` (no `--standalone`) | none — the managed background service ignores the isolated config directory, so not even the always-on repo rule arrives |
| `opencode --standalone run …` with the default config (user config **and** project `opencode.json`) | none — two `opencode-rules` instances load and project rules are not delivered |
| `OPENCODE_CONFIG_DIR=<scratch> opencode --standalone run …` | all expected markers (matrix above) |

Supporting output: `opencode debug config` merges `/home/dev/.config/opencode/opencode.jsonc`
(`opencode-rules@latest`) with the project `opencode.json` (`opencode-rules@>=2.0.0 <3`), and
`opencode plugin list` lists `opencode-rules 2.0.0` twice, once per source. This matches the
"Observed caveat" already written in `docs/AGENTS.md`: configure the plugin once, at project level.
All OpenCode matrix runs above therefore used the isolated config directory **plus** `--standalone`.

## Plugin-unavailable manual discovery (the scenario 6 fallback)

Scenario 6 only proves that no rule is delivered. The spec's "Plugin unavailable" scenario also
requires that the agent can **find** the rule through guidance, so the fallback was exercised. The
fixture's guidance was given the pointer line an agent would follow:

```text
# AGENTS.md (fixture root)

Root guidance token: ROOTGUIDE-ZZ

## Rules

If your tool does not load scoped rules, read `.agents/rules/rust-token.md` by hand.
```

`android/AGENTS.md` received the same `## Rules` line, for the node-folder variant. `opencode.json`
was moved aside again (`mv opencode.json opencode.json.off`) for the duration and restored afterwards.

**Root start** — command and observed trace, from `/tmp/opencode/verify-matrix`:

```bash
OPENCODE_CONFIG_DIR=/tmp/opencode/verify-matrix-cfg opencode --standalone run -m openai/gpt-6.1-sol \
  'What marker token does the rust workspace rule carry? Reply with the exact token and nothing else.'
```

```text
> build · gpt-6.1-sol
→ Read .agents/rules/rust-token.md
MARKER-RUST-ZZ
```

2/2 fresh sessions: the agent followed the `AGENTS.md` pointer, read the rule file and reported
`MARKER-RUST-ZZ`. The prompt names neither the rule file nor its path.

**Node-folder start** — same prompt, from `/tmp/opencode/verify-matrix/android`:

```text
> build · gpt-6.1-sol
✗ Read .agents/rules/rust-token.md failed
  Error: File not found: .agents/rules/rust-token.md — the folder /tmp/opencode/verify-matrix/android/.agents does not exist.
→ Read /tmp/opencode/verify-matrix/.agents/rules/rust-token.md
MARKER-RUST-ZZ
```

2/2 fresh sessions recovered and reported `MARKER-RUST-ZZ`, but both first tried the listed path
verbatim and failed, because `.agents/rules/...` is written relative to the repository root while the
working directory was `android/`; one run found it with a `**/rust-token.md` glob, the other with the
resolved absolute path. The fallback therefore **works with one extra step**; the listed path is not
directly readable from a node folder. This is an observed detail of the existing guidance wording,
reported here for the owner; no guidance file was changed.

Verdict for the fallback: **observed to work** at the root (2/2) and at a node start (2/2, after a
failed first path attempt). No failure of the fallback had to be recorded.

## Real-repository probes (tasks 9.1, 9.2)

Run from the repository root, one tool call each. The first probe reads no file, so it measures the
start-of-session state; the second reads `android/AGENTS.md`.

| Tool | Probe | Observed |
|---|---|---|
| Claude Code 2.1.325 | **no file read**, list delivered rule files | `repo-conventional-commits.md`, `repo-no-time-based-tests.md`, `repo-pre-push-checks.md`, `repo-signing-keys.md` — no `android-lint-before-push.md` |
| OpenCode 2.3.0 (isolated config + `--standalone`) | **no file read**, same question | the same four files, no `android-lint-before-push.md` |
| Claude Code 2.1.325 | read `android/AGENTS.md`, list delivered rule files | the same four **plus** `android-lint-before-push.md` |
| OpenCode 2.3.0 (isolated config + `--standalone`) | same | the same four **plus** `android-lint-before-push.md` |

The no-read probe establishes that all four `repo-*` rules are present before any read. The post-read
probe is the only one of each pair that contains `android-lint-before-push.md`, which establishes that
it is delivered after an `android/` read in both tools. Neither probe alone would show the ordering;
together they do, for these five files. Not established by these probes: the exact moment within a
turn at which the Android rule is injected, and delivery of any other node rule (no other node rule
exists in this repository). No `.claude/CLAUDE.md` and no `CLAUDE.md` exists anywhere in the chain.

Claude Code's no-read answer first printed a garbled list, then said "Correction: the second file name
has a typo" and printed the four correct names once each — a model correction, not a second delivery.

## Skills

### Structural evidence (observed)

```text
git ls-files -s .claude/skills .claude/rules   → 12 entries, every one mode 120000
git ls-files .claude/skills                   → 11 paths
find .claude/skills -maxdepth 1 -type l        → 11
git ls-files .agents/skills                   → 12 paths: 11 skill directories + .openspec-target
```

Targets, read with `readlink`, all resolve (`test -e` passes for all 11):

```text
.claude/rules -> ../.agents/rules
.claude/skills/openspec-apply-change        -> ../../.agents/skills/openspec-apply-change
.claude/skills/openspec-archive-change      -> ../../.agents/skills/openspec-archive-change
.claude/skills/openspec-bulk-archive-change -> ../../.agents/skills/openspec-bulk-archive-change
.claude/skills/openspec-continue-change     -> ../../.agents/skills/openspec-continue-change
.claude/skills/openspec-explore             -> ../../.agents/skills/openspec-explore
.claude/skills/openspec-ff-change           -> ../../.agents/skills/openspec-ff-change
.claude/skills/openspec-new-change          -> ../../.agents/skills/openspec-new-change
.claude/skills/openspec-propose             -> ../../.agents/skills/openspec-propose
.claude/skills/openspec-sync-specs          -> ../../.agents/skills/openspec-sync-specs
.claude/skills/openspec-update-change       -> ../../.agents/skills/openspec-update-change
.claude/skills/openspec-verify-change       -> ../../.agents/skills/openspec-verify-change
```

`.agents/skills/.openspec-target` is a regular file (index mode `100644`) containing
`claude own-files` / `opencode`. `.claude/settings.json` is a regular file.

### Are skills listed twice?

**Documentation-only / inconclusive.** Neither tool has a command that prints its resolved skill
list, and a model self-report is not evidence: asked to list skill names, Claude Code 2.1.325
printed a list that included the 11 `openspec-*` skills, then said "I need to correct that list" and
printed the whole list again — the duplication came from the model, not from the tool. Recorded as
unverified rather than guessed.

## Antigravity — blocker (documentation-only, task 9.3)

**Not observed.** The T3 Antigravity driver (`gemini-3.8-flash`) loaded neither a `trigger: glob`
rule nor an `always_on` control rule from `.agents/rules`, so it cannot validate the IDE behaviour,
and `homepc agy` fails in this environment with `ANTIGRAVITY_LS_ADDRESS is not set`. The same
result is already recorded as the D5a spike outcome in `traceability.md`. Antigravity therefore
remains **documentation-only** for `AGENTS.md`, `.agents/rules`, `.agents/skills` and the node map;
it is a blocker, not a silent downgrade. The node `## Rules` lists and the root node map are the
mitigation.

## Windows — blocker (task 9.4)

**Not run.** No accessible Windows checkout in this environment, so the following are unverified and
must not be claimed: committed symlinks with `core.symlinks` true and false, the check's Windows
warning, `openspec update` on 2.x under Windows, and native rule scoping in the tools that run
there. The check's Windows branch is covered by its own injected-platform tests (task 6.5); that is
code coverage, not a Windows observation.

## Idempotence (task 2.4)

Two separate observations, taken at different points in the change, and they are not the same
evidence:

**(a) The actual second `openspec update`** (task 2, Step 4 — the real command, not a dry run), run
after the schema edits:

```text
$ openspec update
✓ All 2 tool(s) up to date (v2.0.0)
  Tools: claude, opencode

Use --force to refresh files anyway.
Note: 1 more workflow is available (onboard).
exit=0
```

`git status --short` was captured before and after that run and was byte-identical (`diff` clean), so
the command reported the tree up to date and left `git status --short` unchanged. The tree was already
dirty, so this does not by itself show that no tracked file changed; the up-to-date report plus the
unchanged status snapshot is the evidence. Source: `.superpowers/sdd/plan/task-2-report.md`.

**(b) Dry-run confirmation** (this task), on the committed tree:

```text
$ openspec --version
2.0.0
$ openspec update --dry-run
No changes: skills are up to date.        # exit 0
```

`git status --short` was empty before and after. This reconfirms (a) without writing; it does not add
independent evidence that the real command rewrites nothing, because (a) ran against the already-dirty
tree and relies on the unchanged status snapshot.

## Traceability table (task 9.6)

`openspec/changes/consolidate-agent-files/traceability.md` is complete: 19 rows (13 `CLAUDE.md`
sections plus E1–E6), **all `written`**, none `planned`, none dropped. Every destination named by a
row exists, and the facts that had no other documentation home were found in their new home:

| Evidence | Command / result |
|---|---|
| 13 guidance files exist | `find . -name AGENTS.md` → root + `android/`, `web-extension/`, `rust/`, `protocol/`, `native-bridge/`, `native-bridge/host/`, `native-bridge/installer/`, `.../macos/`, `.../windows/`, `verify/`, `scripts/`, `docs/` |
| `codeFormat`, `SeedCallback`, `TestResultFlow`, `otp23-fixture.keystore` | present in `android/AGENTS.md` |
| `core.hooksPath`, `native-host.cdx.json`, retired-workflow list | present in `scripts/AGENTS.md`; `native-host.cdx.json` also in `native-bridge/host/AGENTS.md` |
| `bridgeInstallerSupplyChain` | present in `native-bridge/installer/AGENTS.md` |
| macOS signing identity / TCC | present only in `native-bridge/installer/macos/AGENTS.md` (declares itself canonical) |
| `android-lint-before-push.md` listed by its scope | `android/AGENTS.md:135`; also named by `rust/AGENTS.md:42` and `protocol/AGENTS.md:34` |
| `repo-*` rules listed by the root file | `AGENTS.md:31–32` |
| Node/npm pins | `verify/AGENTS.md` (Node 26.8.1, npm 11.19.0) |
| Release runbook pointer | `docs/releasing.md` exists |

No destination was missing, so no row was left unverified.

## Agent-file check

```text
$ python3 docs/checks/check_agent_files.py --root .
(no output)
exit 0
```

## Documentation-only vs observed

| Claim | Status |
|---|---|
| Claude Code scoping matrix (scenarios 1–5) | **observed**, 2.1.325 |
| OpenCode scoping matrix (scenarios 1–3, 6) | **observed**, 2.3.0 + plugin 2.0.0 |
| No-read baseline (fixture and repository) | **observed** in both tools — always-on rule only, no scoped rule before a read |
| OpenCode scenario 4 | **observed deviation** — scoped rule lost when the same file is read twice |
| Nested `AGENTS.md` loads on read | **observed** in both tools |
| Node-folder start behaviour | **observed**: no rule file in either tool; Claude Code loads the ancestor chain, **OpenCode deviates** by loading the node file only (3/3 runs) |
| OpenCode duplicate-global-plugin and `--standalone` caveats | **observed** |
| Plugin unavailable: scoped rules stop being delivered | **observed** as a proxy by removing the project `opencode.json` (no rule markers) |
| Plugin unavailable: manual discovery through guidance | **observed** — root start 2/2 and node start 2/2 read `.agents/rules/rust-token.md` and reported `MARKER-RUST-ZZ`; the node start needed a retry because the listed path is root-relative |
| Skill symlink layout (11 links, targets, modes, skill parity) | **observed** structurally in the repository |
| Skills listed twice | **not determinable**; recorded as unverified |
| Antigravity `AGENTS.md`, rules, skills, node map | **documentation-only / blocker** |
| Windows symlinks, check warning, `openspec update`, native scoping | **not run / blocker** |
| `openspec update` idempotence | **observed** — real second run exited 0 with a byte-identical before/after status (task 2 report); dry run on the committed tree reports no changes |
| Traceability completeness | **observed** (19/19 `written`, destinations exist) |

## Node-folder limitation, per tool

| Tool | Started in `android/` |
|---|---|
| Claude Code 2.1.325 | loads root and node `AGENTS.md` (meets D5c); delivers no rule file; the notice and the node rule list tell the agent to read `android-lint-before-push.md` by hand |
| OpenCode 2.3.0 | loads only `android/AGENTS.md` — **deviates from D5c (3/3 runs)**; delivers no rule file; the node file's notice and list mitigate, but the root node map and plugin pointer are absent, and a hand-read of a listed `.agents/rules/…` path fails once from this folder before it resolves |
| Antigravity | unverified (blocker) |
