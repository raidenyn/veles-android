# Verification Report

**Change**: `onboard-mintlify-docs`
**Verified at**: `2026-11-18`
**Iteration**: `1`
**Verifier**: `Claude (openspec-verify-change), with the controller's own command output`

---

## 1. Structural Validation (`openspec validate --all --json`)

- [x] All items `"valid": true`

**Result**:

```text
totals: items 29, passed 29, failed 0 (1 change, 28 specs)
openspec validate onboard-mintlify-docs --strict: Change 'onboard-mintlify-docs' is valid
```

| Item | Type | Issues |
|---|---|---|
| — | — | — |

---

## 2. Task Completion (`tasks.md`)

- [x] All `- [ ]` have been changed to `- [x]`

35 of 35 checkboxes are `- [x]`; none remain.

**Incomplete tasks** (if any):

| Task | Reason incomplete | Blocks archive? |
|---|---|---|
| — | — | — |

---

## 3. Delta Spec Sync State

| Capability | Sync status | Notes |
|---|---|---|
| `documentation-site` | ✗ pending sync | New capability; `openspec/specs/documentation-site/` does not exist yet. The closeout syncs and archives once, when checks are green. |
| `component-versioning` | ✗ pending sync | MODIFIED "Documentation node" (adds `docs.json` and `.mintignore`, one scenario). |
| `ci-pipeline` | ✗ pending sync | MODIFIED "Component-tree CI" (Docs node scenario, one scenario). |

Pending sync is expected here; archive runs once in the closeout, never from verify.

---

## 4. Design / Specs Coherence Spot Check

| Sample item | design description | specs counterpart | Gap |
|---|---|---|---|
| Allowlist | D1: `/*` plus `!/` re-includes, the real `.mintignore` list | "Allowlist format", "Published set" | none; `.mintignore` equals D1 (no `!/CONTRIBUTING.md`) |
| Default ignores | D4 and D6: Mintlify's default ignore list always wins (verified with `mint@4.2.1199`) | "Default ignores", "Published-set computation" | none |
| Imports | D6: imports resolve against the published set | "Structural errors" (missing or unpublished) | none |
| Contributing page | D4: summary page linking to GitHub | "Build page import and Contributing summary" | none |
| Wrappers | D3: one `.mdx` per capability, hand-written description | "Capability wrapper pages", "Wrapper lifecycle" | none |
| Braces | D6: warning, calibration found expression-like braces fail the build | "Guard warnings" (warning) | known, follow-up change |
| Ownership and guidance | D7 | "Allowlist ownership", "Site guidance" | none |

**Drift warnings** (non-blocking):

- Braces: the guard only warns for a brace outside code, but the Mintlify CLI rejects expression-like braces (`{a: 1}`, `{a b}`, `{{x}}`). Today's published set has none. Promoting those to an error needs a spec change (follow-up change).
- The Contributing page is a summary because Mintlify's default ignore list beats a `.mintignore` re-include; a future Mintlify behaviour change would allow a real import.
- Brainstorm and proposal (unedited, historical) still describe the Contributing import as the primary path with the fallback; they are consistent with what was built.

---

## 5. Implementation Signal

- [x] No unstaged files in the worktree (apply.md and verify.md are committed with this report)
- [ ] All related commits have been pushed

Nothing is pushed yet; finalize pushes the branch and creates the pull request, which needs the user's go-ahead.

**Commit range** (if known): `41241962..HEAD` (branch `docs/onboard-mintlify-docs-json`)

Evidence run in this session: docs checks 167 tests on Python 3.14 and 3.8; the site guard and agent-file check on the repository (no findings); workflow contract suite 59/59; components check; `scriptsTest`; pinned Mintlify CLI `validate` and `broken-links` on the published set; `mint dev` spot check of the product route, spec wrapper titles, Build import, Contributing summary and Not Found for unpublished paths. Not run locally: supply-chain scan (needs `npm ci`), installer builds, Windows and macOS jobs, and the hosted Mintlify deployment (reported after the pull request exists). Docs and tooling only, so the real-device rule is exempt.

---

## 6. Review Records (review gate)

Preset `opus` = [Opus]; hashes recomputed with the documented command.

| Artifact | Reviewer | Hash state | Open critical or high findings |
|---|---|---|---|
| brainstorm | Opus | current | none |
| proposal | Opus | current | none |
| design | Opus | current (re-reviewed, Pass 2) | none |
| specs/documentation-site | Opus | current (re-reviewed, Pass 2) | none |
| specs/component-versioning | Opus | current (upstream design re-pointed with note) | none |
| specs/ci-pipeline | Opus | current (upstream design re-pointed with note) | none |
| tasks | Opus | current (re-pointed after two minor fixes, with note) | none |
| plan | Opus | current (re-reviewed, Pass 2) | none |

- [x] Every planning file has an approved record from every reviewer the record header lists
- [x] Every recorded hash — the file's own and each upstream file's — matches the file on disk
- [x] Every changed hash carries the note for its non-triggering edit
- [x] The recorded upstream set is the full upstream set
- [x] No critical or high finding is open

**Failed items** (FAIL if any):

| Artifact | Failure |
|---|---|
| — | — |

---

## Overall Decision

- [ ] ✅ PASS
- [x] ⚠️ PASS WITH WARNINGS — can proceed to finalize. Warnings: the brace follow-up above; commits not yet pushed (finalize does that); the hosted Mintlify check and the deployed-site spot check can only be done once the pull request and deployment exist, and will be reported, not assumed.
- [ ] ❌ FAIL

**Next step**:

Proceed to the finalize artifact (push the branch, create the pull request, hand off to the closeout). This pushes to the shared remote, so it waits for the user's go-ahead.
