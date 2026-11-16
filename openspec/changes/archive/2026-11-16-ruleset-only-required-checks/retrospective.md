# Retrospective — ruleset-only-required-checks

**Wins**

- The root cause was found before any artifact was written: the token is not a repo admin, so the
  classic protection endpoint 404s and the old rule's "stop and ask" fired on every PR. Every later
  artifact argues from that fact instead of from a guess.
- The three places the rule lives (procedure doc, always-on rule, spec requirement) were edited
  together, so the closeout's rule and the procedure it cites cannot disagree.
- The review gate caught real defects cheaply: a misleading "can never report a conclusion" claim, an
  unconditioned "does not stop to ask" scenario, and a plan step whose expected output did not match
  `scripts/components.py`.

**Misses**

- The ff run wrote four artifacts before noticing that the spec delta's MODIFIED block has to keep
  every existing scenario name; `openspec validate --strict` found it, not a reviewer.
- The first brainstorm draft claimed the retired jobs no longer exist, which is wrong — the jobs
  survive in `check-android.yml` and only the bare classic *contexts* are orphaned.

**Plan deviations**

- The plan's three tasks were batched into one implementer dispatch: they are same-shape
  documentation edits, and the batching rule asks for one dispatch with one review.
- No `design.md`: the schema marks it optional and the brainstorm carries the decisions.

**Skill/workflow compliance**

- ff → review gate → apply → verify → finalize, with per-artifact records and a PASS verify.
- Docs node checks and 79 unit tests ran before and after the edit; only the `docs` node was selected.

**Surprises**

- The review-gate hash normalization deliberately ignores checkbox state, so ticking 11 tasks and 12
  plan steps left every approval valid.
- The canonical finalize flow expects a `.worktrees/<change-name>` worktree; this session already ran
  in a harness-owned worktree on the feature branch, so the merge step had nothing to merge.

**Promote candidates**

- Nothing new for the rules; the change *is* the promotion candidate, and it removes the recurring
  "which checks are required?" question from the closeout.
