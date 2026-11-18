# Apply Receipt

**Change**: `onboard-mintlify-docs`
**Iteration**: `1`
**Applied at**: `2026-11-18`
**Executor**: `subagent-driven-development` (implementers: Sonnet 5.5; task, fix-round and whole-branch reviewers: Opus 5.5, medium effort)

---

## Workspace

- **Worktree**: `/home/dev/.t3/worktrees/veles-android/t3-1d43cb9b-d6172cd9b0adb106` (the session's own isolated worktree, no second worktree)
- **Branch**: `docs/onboard-mintlify-docs-json`

---

## Commits

- **Range**: `4c2a8f7a..HEAD` (base of the branch: `41241962`; the first commit scaffolds the change directory)
- **Count**: `21`

---

## Tasks

- **Completed**: `35 of 35` checkboxes in tasks.md flipped to `- [x]`
- **Remaining**: none

---

## Deviations from the approved text

Recorded in the "As built" sections of `tasks.md` and `plan.md` and in the spec and design (all re-reviewed by Opus, `reviews/*` Pass 2): Mintlify's default ignore list always wins over a `.mintignore` re-include (so `CONTRIBUTING.md` is not published and the Contributing page is a summary linking to GitHub); imports are resolved against the published set; the privacy page says Veles sends codes nowhere in this version because the app does not send OTPs yet; braces stay a guard warning although expression-like braces fail the Mintlify build (follow-up change); two RFC links are pinned to a commit because the files are not on `master`; all 28 wrapper descriptions are hand-written.

## Tests run

Docs checks (163+ unit tests on Python 3.14 and 3.8), the site guard and agent-file check on the repository, the workflow contract suite (59), the components check and tests, `scriptsTest`, and the pinned Mintlify CLI (`mint@4.2.1199`) `validate`, `broken-links` and a local `mint dev` check of titles, imports, the product switcher route and Not Found for unpublished paths on a scratch copy of the published set. Docs, comments and tooling only: no Android, web-extension, Rust, protocol or native-bridge logic changed, so the real-device rule is exempt. Not run locally: the supply-chain scan (needs `npm ci`), installer builds and the Windows/macOS jobs; hosted CI runs every selected node.

---

## Next step

Run `openspec-verify-change` for `onboard-mintlify-docs`.
