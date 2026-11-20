# OpenSpec: version, update procedure and the superspec fork

The CLI range is stated in [`CONTRIBUTING.md`](../CONTRIBUTING.md): `>= 2.0.0, < 3` (2.0.0 last verified;
1.x is unsupported). Do not repeat the range elsewhere. The generated skill list is in
[`docs/openspec-skills.md`](openspec-skills.md).

## Update procedure

Run updates on Linux or macOS only — `openspec update` under Windows has not been shown to work on 2.x.
The procedure is:

1. Assert `openspec --version` is in the supported range.
2. Run `openspec update --dry-run`.
3. Run `openspec update` and commit the regenerated skills.
4. Run `openspec update` again to prove idempotence (an empty diff).

A newer 2.x that rewrites skill text is a normal "regenerate skills" diff, not a failure.

Generated skills live in `.agents/skills/openspec-*`, with one `.claude/skills/<name>` symlink per skill.
Never hand-edit them: `openspec update` overwrites them, and lasting changes belong in a non-`openspec-`
skill or in `openspec/config.yaml`. `.agents/skills/.openspec-target` is a tool-ID list, not version
metadata, and is excluded when the two skill directories are compared.

CI does not install the OpenSpec CLI; the structural agent-file check catches a 1.x regression through the
forbidden legacy directories.

## The `superspec` schema fork

`openspec/schemas/superspec/` (schema version v5) is a **third-party** schema installed by the
`superspec-setup` integration, not by OpenSpec. This repository forks it: its `/opsx:<name>` command
references were rewritten to the OpenSpec 2 skill names (`/openspec-propose`, `/openspec-apply-change`, …)
because the `/opsx:` commands no longer exist after 2.0. `openspec` does not manage or overwrite
`openspec/schemas/`, but reinstalling the schema restores the upstream `/opsx:` references, so re-apply the
skill-name rewrite after any reinstall and verify with `grep -rn '/opsx:' openspec/schemas` (expected: no
hits).

The `(schema version v5)` text lives in [`docs/AGENTS.md`](AGENTS.md), not in this file; the version is also
in `schema.yaml`, its description history and the README/INTEGRATION mentions.

A reinstall also drops the fork's own edits, so re-apply each divergence as well:

1. **Apply pre-flight**: the `apply:` instruction's step 0a review pre-flight — refuse to start unless the
   plan has an approved record from every reviewer its preset lists, every recorded hash is current (note
   condition: a hash re-pointed for a non-triggering edit must carry its note; a triggering edit that was
   not re-reviewed refuses the start), the upstream set is complete and a coding model is confirmed.
2. **Sixth verify check**: the review-records audit in the `verify` instruction, its "6. Review records"
   section in `templates/verify.md`, and the six-check counts in `README.md` and `INTEGRATION.md`. The same
   note condition: a hash re-pointed after a non-triggering edit must carry its note, and a re-point
   without it or a triggering edit that was not re-reviewed is FAIL.
3. **Finalize hand-off**: the `finalize` instruction's always-create PR step (no prompt, no skip), the open-PR
   confirmation and the fail-closed stop, the receipt written only after confirmation, the `tracker.md`
   record (committed and pushed as part of the PR) and the In Review confirmation, the per-outcome hand-off
   to `.agents/hooked-rules/repo-superspec-closeout.md`, and the matching "Next step" bullets in
   `templates/finalize.md`.
4. **Archive once, in the closeout**: the replaced direct-archive directions in the `verify` and `finalize`
   instructions, `apply:` step 4, `templates/verify.md`'s PASS option and next-step note, the `README.md`
   golden path and `INTEGRATION.md`'s golden path and command table. Archive runs once, inside the closeout,
   when the required checks are green.
5. **Version bump**: `version: 5` in `schema.yaml` with its "v5 (current)" description entry, and the
   current-version mentions in `README.md`, `INTEGRATION.md` and `docs/AGENTS.md`.
6. **Derived project statuses**: the draft spec pre-review PR (`gh pr create --draft`), finalize's
   `gh pr ready` step and its `state,isDraft` confirmation, the closing keyword and follow-up list in the PR
   body, the In Review confirmation (only after a `project_flow.py sync` that exits 0 and prints
   `sync: complete`, re-run once for the closing-keyword lag before reporting another Status; the Status is
   never written), the post-merge Done check that needs the same complete `sync`, the `merge-locally`
   close-the-issue-then-`sync` step and the claim-based `discarded` and `kept-as-is` bullets, in `schema.yaml`,
   `INTEGRATION.md` and `templates/finalize.md`.
