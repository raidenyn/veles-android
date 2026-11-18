# Review record: design

- Artifact: design
- Reviewer: custom — GPT-6.2 Sol (`openai/gpt-6.2-sol` via OpenCode), effort medium (session-only custom model)
- Session preset: [custom]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: brainstorm sha256 3d530fff7e7efe707e722eea4a33a11b7cb899b9078a23b2c870d736c2da680d — re-pointed from 625274879084f803c341cd50170bebffa5a6b8a8c184f5027ccac4d3ffb4f118:
  - the brainstorm's pass 2 (scope narrowed) was approved and reviewed in parallel with design rounds 3–5;
  - its one minor fix was then re-pointed with a note;
  - design rounds 3–5 were judged against the narrowed brainstorm.
- Upstream hash added at apply pre-flight, by the owner's explicit decision and without a reviewer round: proposal sha256 af92df8b45b31447acd6adb300a78dc1a6d998d8740a704ff4349fe1d5827237 (re-pointed from 3a7847a5793ff7bd1a0c852607edbe64ab2a2f25667e6c10655b7cf82bfedc9e: owner-directed edits O1-O3, consistency-checked by Sol). The proposal was extracted from the approved design after the design's review, so the design was never judged against it; the audit's complete-upstream-set item required the hash, and the owner chose to record it instead of running a consistency check.

## Pass 1

### Round 1 — changes-required

Reviewed content sha256 b0dc2211945cbfc2a60eee2c45882faeb76aba0527f5b424bef98768c0905e54

The reviewer confirmed that the brainstorm's deferred low finding (prompt matching sits under behaviour 6, outside
the pilot gate) is resolved by §9.

| Severity | Finding | Disposition |
|---|---|---|
| high | Delivery in a parallel tool batch: the first denial marks the rule delivered, so a sibling gated call can run before the model has seen it. | Fixed (§4). A denial is *pending*; only a same-gate-pattern re-run passes and confirms; a different-pattern sibling is denied again. The same-kind sibling residual is documented (Risks) and exercised in the spike (behaviour 2). |
| high | No atomic read–check–update transaction, no rule for when the generation is captured, and pruning could revive an old generation. | Fixed (§4). A per-session lock (flock or msvcrt), the decision and the generation read inside the lock, an ordering assumption checked by the spike, and pruning that never removes the current session and restarts a pruned one at generation 0, the safe direction. |
| high | The compaction state machine is undefined: one `compacted` event against two-phase adapters; pending sticky across a cancel or repeat; which context gets credit. | Fixed (§4, §5). `compact-start` and `compact-end` with a `pending_sticky` union that survives a cancel; compaction applies per context; children are never credited with the parent's deliveries. |
| high | Workflow migration criteria test primitives, not equal coverage (resuming monitoring from a PR URL, local merge, issue-only prompts, background references). | Fixed (§8, §9). Closeout gains PR-view command moments, PR-URL and keyword prompt moments and a `git push` gate; per-rule workflow scenarios (four for closeout, three for tracker intake, including a negative case) are now part of each move's ship condition. |
| high | The pointer is not unconditionally loaded (a parent `CLAUDE.md`, subagents). | Fixed (§9). The supported configuration is defined; behaviour 7 tests pointer visibility with hooks disabled; the absent-pointer exposure is stated and documented. |
| high | "Allow" could map to Claude's `permissionDecision: "allow"` and bypass permission prompts. | Fixed (§3, §10). Pass is no output; the engine never emits allow; the check enforces it; a test and behaviour 1 confirm that a command which normally asks still asks. |
| medium | The frontmatter grammar is unspecified and differs from the checker's restricted grammar; there is a risk of the checker and the runtime diverging. | Fixed (§1). Flat string lists in the existing grammar, an example, error cases, and one shared parser imported by the check. |
| medium | Supported command and path forms, and effective-checkout resolution, are undefined. | Fixed (§3, §6). In-scope and out-of-scope forms (git global options, env prefixes; variables and aliases excluded), real-path `SKILL.md` matching, and rules taken from the event's `cwd`. |
| medium | Failure reporting is overpromised (no notice is possible without Python) and has no timeout. | Fixed (§3). The notice goes where possible (the engine, or the JS plugin when Python is missing); Claude Code without Python is pointer-only and documented; 5-second hook timeouts; the notice wording is generalized. |
| medium | Command-moment success and failure semantics differ between the adapters. | Fixed (§3). Moments fire only after success (`PostToolUse`, a non-error `execute.after`); background launches and chains are specified. |
| medium | Per-rule byte limits do not bound combined delivery (Claude's 10,000-character cap); OpenCode `context` injection is per call. | Fixed (§3, §5). A 9,000-byte joined cap that marks only the rules included, and persisted delivery paths (output append, `session.synthetic`); per-call context is not counted. |
| medium | The migration needs normative spec changes (layout spec, nine rules, one plugin) and misses the `openspec/config.yaml` reference. | Fixed (§8). The spec deltas are named and tied to each rule's move decision; `openspec/config.yaml` and the other live references are listed. |
| medium | The new executable entry points sit outside CODEOWNERS coverage. | Fixed (§10). CODEOWNERS gains `/scripts/agent_rules.py` and `/scripts/opencode-hooked-rules/`. |
| low | The bump statements are unqualified, and the cited file pins the binary. | Fixed (§7, Context). Bumps are scoped to touched components, the breaking-change override is noted, and `cliff.toml`, `scripts/AGENTS.md` and `docs/releasing.md` are cited. |

### Round 2 — changes-required

Reviewed content sha256 7d0e12ca57a24d1ef4545df2115b566c2831f3d867d6242e67b5e80763431236

Round-1 status per the reviewer: pointer visibility, permission bypass, frontmatter, failure reporting, normative
migration, CODEOWNERS and release guidance resolved. Parallel delivery, atomicity and pruning, compaction
lifecycle, workflow coverage, matching and checkout, command success, and size and persistence were not resolved
and are carried below.

After this round the owner narrowed the scope to the mechanism plus the commit-type pilot (the brainstorm's
pass 2), and design.md was rewritten for that scope.

| Severity | Finding | Disposition |
|---|---|---|
| high | Parallel delivery: a moment's confirmation can race a gated sibling in the same batch; overlapping gate patterns are unspecified. | Fixed in part, and the rest rebutted as a documented residual. A call records every gate pattern it matches, and a re-run passes on any of them (§4, §6). Reason for the residual: a moment and a gated sibling in one batch, or two same-kind siblings, cannot be told apart from a real re-run with the information hooks receive. The case is unusual, its failure is one unguided call with the format still enforced by commitlint, and it is exercised in the spike (behaviour 2) and documented (Risks). |
| high | Pruning is not serialized with a victim's transaction; the non-interleaving assumption is untested. | Fixed (§4). A victim is pruned only under its own lock, tried without waiting and skipped when busy. The ordering assumption is stated as a host property, with its failure direction (one missed re-arm, never a block) and the re-arm check in behaviour 4. |
| high | Compaction lifecycle: child start/end routing and child re-arm are unspecified. | Fixed by narrowing. Sticky re-delivery is out of scope, so compaction is a single re-arm event. A compacting subagent re-arms its own key when identified, otherwise the whole session re-arms (the extra-delivery direction). Behaviour 4 tests isolation. |
| high | Equal workflow coverage (bare issue numbers, isolated closeout prompt triggers). | Resolved by narrowing (owner's call). The workflow rules stay always-on in this change. |
| high | New: delivery can be credited before the host accepts the text. | Fixed (§3, Acknowledgement). On OpenCode, `hook` decides without writing and the plugin calls `confirm` only after a successful injection. On Claude Code the state is written just before the print, and the residual over-credit after a host failure is documented. |
| medium | `git -C <dir>` or an inline `cd` target another checkout while rules come from `cwd`. | Fixed by narrowing the scope (§3). The `cwd` checkout's rules are used, which is accepted because every worktree of this repository carries the same rule; cross-checkout resolution is out of scope. |
| medium | Command success on OpenCode is undefined; there is no negative test. | Fixed (§3, §8). Success is completion plus a zero exit on both hosts, the OpenCode field is checked in the spike, and behaviour 3 requires a failing matching command not to count. |
| medium | Size and persistence: the sticky overflow. | Resolved by narrowing. Sticky is out of scope; the 9,000-byte cap with partial crediting remains. |
| medium | New: shared state across worktrees is not bound to the rule content. | Fixed (§1, §4). Delivery is recorded against the rule identity (file name plus sha256). |
| medium | New: CI routing does not run docs for an engine-only change. | Fixed (§9). `global-overrides` map the engine and plugin files to `["tooling", "docs"]`, with a `components.py changed` test. |

### Round 3 — changes-required

Reviewed content sha256 3dff5c6efe0679790c7f36b84728d07043619a407b84e03f2460ca04bb66e229

Round-2 status per the reviewer:
- Resolved: OpenCode command success, worktree rule identity, CI routing.
- Resolved by narrowing: workflow coverage, cross-checkout commands (as a stated boundary), sticky overflow.
- Accepted as a recorded trade-off: the two same-batch residuals (the reviewer notes `PostToolBatch`, now recorded
  as a follow-up).
- Carried below: overlapping gates and the pending set, the hook/confirm gap, child-key reset, and the definition
  of host acceptance.

| Severity | Finding | Disposition |
|---|---|---|
| high | OpenCode's read-only `hook` followed by a separate `confirm` is not the single locked transaction §4 promises; a late confirmation can credit a compacted or pruned context, and the denial's acceptance point is undefined. | Fixed (§3, §5, §10). `confirm` is removed. Both hosts record the decision (delivery or pending denial) in one locked transaction per event. The write-then-host-acceptance residual, including cancellation and timeout, is documented for both hosts. The OpenCode plugin voids the credit with a context-scoped `compacted` re-run when its own injection throws. |
| medium | How a second denial updates the pending pattern set is unspecified; `deny A → deny B → retry A` is untested. | Fixed (§4, §10). Denials add to the set and never replace it; the sequence has a test. |
| medium | A session-wide generation conflicts with resetting only an identified child's key. | Fixed (§4, §8, §10). Generations are per context key. A child compaction increments only its key, with the whole-session fallback when the child is unidentified. The spike and the tests check that the parent survives a child reset. |
| medium | The example gate misses in-scope forms such as `git -C "work tree" commit` and `git --no-pager commit`. | Fixed (§1, §6). The example regex now handles quoted `-C` and long options, checked against the listed forms; each in-scope form gets a matcher test. |

### Round 4 — changes-required

Reviewed content sha256 2de1fd95495021b07141e8ee839a9538f9323e3ef8094e0cfcf25b4efa8efa21

Round-3 status per the reviewer:
- Resolved: pending accumulation, per-context generations, and the split-transaction race.
- Carried below: the host-failure contradiction, and the matcher's remaining forms.

| Severity | Finding | Disposition |
|---|---|---|
| high | The residual says a host timeout stops the command, while §3 says timeouts fail open; the spike tests neither failure path. | Fixed (§3, §8). Failure after the write fails open on both hosts and never blocks. The stale-credit residual and its bound (until the context's next compaction) are stated. New behaviour 6 forces a post-write failure through a test-only switch, on both hosts, under the ship condition. |
| medium | The failed-injection rollback reuses `compacted`, which is too broad and not bound to the failed generation. | Fixed (§3). A dedicated `void` event carries the exact key, rule identity and generation returned by `hook`. The engine removes only that entry, and only if the generation still matches. Unit tests cover the no-op cases. |
| medium | The example regex misses `git --git-dir="work tree/.git" commit` and `git --work-tree . commit`. | Fixed (§1, §6). The gate is `git`, then anything up to a shell operator, then `commit`, checked against all listed forms. The over-match on `git log --grep commit` is accepted as one round trip under the over-match policy. |

### Round 5 — approve

Reviewed content sha256 ca4b6acabdbe1ce8070f2a68a154945c7895fae599f3141ce5afbd7e127c8fae

Round-4 status per the reviewer: the timeout contradiction is resolved, the regex matches the listed forms, and
`void` replaces the broad rollback. No critical or high finding remains.

| Severity | Finding | Disposition |
|---|---|---|
| medium | §5 still says the plugin sends `compacted` after a failed injection, contradicting §3. | Fixed: §5 now sends `void`. |
| medium | Key, rule and generation do not identify a unique write; a sibling rewrite or pruning can make a late `void` erase newer credit. | Fixed: a unique, never-reused write id from the store index; `void` removes the entry only if it still carries that id. |
| medium | The stale-credit residual is understated: it covers every later gate until compaction, not one call. | Fixed: the residual is restated, and behaviour 6 records the first and a second later call. |
| medium | The matcher misses a quoted argument that contains an operator (`git -C "work;tree" commit`). | Fixed by narrowing: such arguments are out of scope, with a boundary test. |
| low | The record header points at the earlier brainstorm approval hash. | Fixed: the upstream hash is re-pointed with a note. |

Approved content: design sha256 28dc53c714da46769e60d65d7c65a98d93a74947fafac241b5a5812921052682 — re-pointed from ca4b6acabdbe1ce8070f2a68a154945c7895fae599f3141ce5afbd7e127c8fae after five non-triggering minor fixes (the round-5 findings: §5 void wording, unique write id for void, stale-credit residual restated, operator-in-quote matcher boundary, upstream hash re-point)

### Owner-directed edit round (apply) — consistency check, approve

During apply the owner reviewed the implementation diff and directed three changes: O1 a shorter root AGENTS.md
pointer block (no fixed sentence), O2 a short docs/AGENTS.md section with the feature documentation moved to
docs/hook-delivered-rules.md, O3 `!` and `BREAKING CHANGE:` used only with `feat`. The controller applied them to the
planning artifacts (design §2/§7/§9, specs hooked-rule-delivery and commit-conventions, tasks 5.4, plan fixed
texts/test row/Task 8 docs bullet, proposal Impact). Sol ran a consistency check of the edit diff: verdict approve,
no critical or high finding. Medium and low findings (all fixed as non-triggering wording edits, four in total:
design §2 line-budget wording, plan Task 5 test row and Task 8 steps, proposal Impact, plus the O3 acceptance check
in plan Task 8): design.md, plan.md, proposal.md. The upstream hashes in the header above are re-pointed for these
edits.

Approved content: design sha256 734486f5fcce862b667b326ab363a067a6b571b4cb10d87d3ae2d7774d046748 — re-pointed from 28dc53c714da46769e60d65d7c65a98d93a74947fafac241b5a5812921052682 after the owner-directed edits O1-O3 (consistency check approved) and four non-triggering wording fixes
