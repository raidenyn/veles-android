# Review record: specs

- Artifact: `specs` (three spec files)
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream: `brainstorm.md` sha256 c27ca9d75a2a5b085956825eb2aa827fd3b518d9f65ba6df139b52ff9670a0c7; `proposal.md` sha256 0260f7c0185b8f9957eb6026c228fd827d306227fa373dd5646729d673b4ea65; `design.md` sha256 1ca82091c710a184fee6d1462dc4bceab1f29a47b0e5c5fedc4f3ddb30cafb78
- Upstream re-pointed after the implementation-time re-review (see the last pass below): the spec, design, tasks and plan were edited to match the built behaviour.

## Pass 1

### Round 1 — approve

#### specs/documentation-site/spec.md

| Severity | Finding | Disposition |
|---|---|---|
| medium | "Capability wrapper pages" requires a wrapper for every capability with no exception, in tension with *Wrapper lifecycle* and the Developer scenario. | Fixed: exception added; scenario scoped to wrapped capabilities. |
| medium | The `CONTRIBUTING.md` fallback leaves the allowlist and published set undefined. | Fixed: the fallback drops the file from the allowlist and published set. |
| low | Default ignores not listed. | Fixed: listed. |
| low | User page file names missing. | Fixed: all five named. |
| low | Description source undefined for the six specs whose Purpose is `TBD`. | Fixed: written by hand there. |
| low | Specification sub-groups, Start-here file names and home page not in the spec. | Deferred: navigation layout, not observable behaviour; fixed in design D2. |
| low | Calibration rule dropped; unit-test duty has no scenario of its own. | Deferred: calibration is design D6 and a task; the clean-tree scenario plus per-rule tests are the observable contract. |
| low | Hosting SHALL is not testable from the repository. | Deferred: the owner note is the testable part, as the scenario states. |
| low | CODEOWNERS scenario only observable on GitHub. | Deferred: the requirement text is checkable in the file; kept as written. |

Approved content: specs/documentation-site/spec.md sha256 ba76b767564a083feb4f09b8c3d96625ad2e35b4e63cd950bffaf170c447455c (re-pointed after five non-triggering minor fixes from round 1: wrapper exception, contributing fallback, default ignore list, user page file names, TBD descriptions; plus one cosmetic rewording of *Capability wrapper pages* to stay under the 500-character strict limit, same meaning)

#### specs/component-versioning/spec.md

| Severity | Finding | Disposition |
|---|---|---|
| low | "only `docs.json` or `.mintignore`" could say "either or both". | Deferred: cosmetic; meaning is clear. |

Approved content: specs/component-versioning/spec.md sha256 714bad1f17bd08338e7a80400b21329ed17b0f97bf51025f3755be0eb8044d0e

#### specs/ci-pipeline/spec.md

| Severity | Finding | Disposition |
|---|---|---|
| low | Step order in `agent-files` implied, not enforced by the contract. | Deferred: the contract asserts the exact command in that job; order is not behaviour that matters. |
| low | Whether running the guard in a different job fails the contract. | Deferred: the scenario names the job, which suffices. |

Approved content: specs/ci-pipeline/spec.md sha256 a975d5fae45639010597c16b0bfc5d54bac0e28c2631e3b4e3d26066e926b616

## Pass 2 — implementation-time re-review

Implementation-time re-review (after the code was built): calibration against the Mintlify CLI and the whole-branch review showed approved text stale in places (CONTRIBUTING.md default ignore, privacy wording, braces, pinned RFC links, hand-written descriptions). The controller edited the spec, design, tasks and plan; Opus re-reviewed them.

### Round 1 — changes-required (specs approved; design, plan changes-required)

#### specs/documentation-site/spec.md

| Severity | Finding | Disposition |
|---|---|---|
| low | "Published set" lacks the default-ignore caveat. | Fixed: "apart from Mintlify's default ignores". |
| low | Requirement title "Single-source build and contributing pages" no longer fits. | Fixed: renamed "Build page import and Contributing summary". |
| low | One source line much longer than the rest. | Deferred: cosmetic. |

### Round 2 — approve

Both fixes confirmed; `openspec validate --strict` passes; the spec is MDX-safe.

Approved content: specs/documentation-site/spec.md sha256 9fc6f000d8307f98a3984320370e5f527ea05cc232bc68314051bcf07ec35357 (triggering normative edits re-reviewed: Published set, new Default ignores requirement, Build page import and Contributing summary with a new scenario, Absolute links to unpublished files, Capability wrapper pages description wording, Published-set computation, Structural errors)

Approved content: specs/component-versioning/spec.md sha256 714bad1f17bd08338e7a80400b21329ed17b0f97bf51025f3755be0eb8044d0e (unchanged; upstream design re-pointed)

Approved content: specs/ci-pipeline/spec.md sha256 a975d5fae45639010597c16b0bfc5d54bac0e28c2631e3b4e3d26066e926b616 (unchanged; upstream design re-pointed)

## Pass 3 — AGENTS.md line cap

Master capped every AGENTS.md at 40 lines while the change was in review, so the site guidance moved from docs/AGENTS.md into docs/documentation-site.md (docs/AGENTS.md keeps a short pointer). The controller edited the spec, design, tasks and plan; Opus re-reviewed them.

### Round 1 — approve

No critical or high finding. The old "Site guidance" requirement became "Site guidance in AGENTS.md" and "Site guidance document" (with a new scenario that the document stays unpublished); `openspec validate --strict` passes and the spec stays MDX-safe.

Approved content: specs/documentation-site/spec.md sha256 2fb46d9f585f826c87d5fc7f2268dc7000bd0092095c46c22caeb3ddc8c1f9bc (triggering normative edit re-reviewed)

Approved content: specs/component-versioning/spec.md sha256 714bad1f17bd08338e7a80400b21329ed17b0f97bf51025f3755be0eb8044d0e (unchanged; upstream design re-pointed)

Approved content: specs/ci-pipeline/spec.md sha256 a975d5fae45639010597c16b0bfc5d54bac0e28c2631e3b4e3d26066e926b616 (unchanged; upstream design re-pointed)
