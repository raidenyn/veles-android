# Review record: specs

- Artifact: `specs` (three spec files)
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream: `brainstorm.md` sha256 c27ca9d75a2a5b085956825eb2aa827fd3b518d9f65ba6df139b52ff9670a0c7; `proposal.md` sha256 0260f7c0185b8f9957eb6026c228fd827d306227fa373dd5646729d673b4ea65; `design.md` sha256 7820ef3cd781f22f5d64fa7df012d95e91ba6d3b0d9cfbde452690398b9db137

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
