# Review record: plan

- Artifact: `plan.md`
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream: `brainstorm.md` sha256 c27ca9d75a2a5b085956825eb2aa827fd3b518d9f65ba6df139b52ff9670a0c7; `proposal.md` sha256 0260f7c0185b8f9957eb6026c228fd827d306227fa373dd5646729d673b4ea65; `design.md` sha256 5b11d7a9ce98550112951d0a763fa17caf5a4711639efa1128f00738a83f3080; `specs/documentation-site/spec.md` sha256 9fc6f000d8307f98a3984320370e5f527ea05cc232bc68314051bcf07ec35357; `specs/component-versioning/spec.md` sha256 714bad1f17bd08338e7a80400b21329ed17b0f97bf51025f3755be0eb8044d0e; `specs/ci-pipeline/spec.md` sha256 a975d5fae45639010597c16b0bfc5d54bac0e28c2631e3b4e3d26066e926b616; `tasks.md` sha256 2835786f20f5943f3cbd7f15c3576d65fcecb496e97b83fdadf3a3468306e534
- Upstream re-pointed after the implementation-time re-review (see the last pass below): the spec, design, tasks and plan were edited to match the built behaviour.

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | Default-ignore re-include by parent folder contradicts the test and tasks t1.1, and would publish `node_modules` under any re-included folder. | Fixed: exact-path re-include only; the dropped-default fixture uses `docs/node_modules/x.md` and `docs/README.md` under `!/docs/`. |
| medium | `<!--` would be reported twice in `.md`. | Fixed: the `<` rule skips `<!--`. |
| medium | The supply-chain remote-code scan rejects `npx` text under `docs/checks/`. | Fixed: Global Constraint added; Task 9 greps for it. |
| low | Expected pre-push node list wrong. | Fixed: `docs`, `tooling`, `installer`, `supply-chain`; unowned-before-Task-8 note. |
| low | `guardArgs` mutation could hit the agent-file line; expected regexes unnamed. | Fixed: full guard command replaced; both expected regexes named. |
| low | No favicon or logo-object tests. | Fixed: three tests added. |
| low | Task 2 commit step contradicted itself. | Fixed: explicit `git add`. |
| low | Task 9 untracked check omitted `docs/checks/`. | Fixed. |
| low | Only the `docs/ci.md` row updated. | Rebutted: the node tree names no commands; the row is the only command listing. |
| guess | Missing `.mintignore` handling, order vs git, nav walker scope, brace counting, wrapper without or with relative import, first-line meaning, subfolder `README.md`. | Fixed: each now stated in the Task 1-3 interfaces with tests where behaviour is observable. |

### Round 2 — changes-required

High finding confirmed resolved; the `docs/ci.md` rebuttal accepted.

| Severity | Finding | Disposition |
|---|---|---|
| medium | With `.mintignore` missing, `check_structure` also reports every page, logo and favicon, so the one-line test cannot pass. | Fixed: `check_repo` returns the missing-file finding alone; test fixture named. |
| low | tasks t1.1's `.github/` fixture replaced without a note. | Fixed: substitution and reason stated. |
| low | `base_site()` `.mintignore` unspecified. | Fixed: spelled out. |

### Round 3 — approve

All three round-2 fixes confirmed; the base fixture publishes everything Tasks 2 and 4 need; the early return does not break Task 1's test.

| Severity | Finding | Disposition |
|---|---|---|
| low | After Task 2, the outside-git test could pass through the missing-`.mintignore` path and stop covering the git error. | Fixed: the test folder holds a `.mintignore` and the output must contain the git failure text. |
| low | `base_site()` navigation and logo paths only implied. | Fixed: named. |

Approved content: plan sha256 37aaa94afdf8d082a9ccc54418abeaa0fa927f2a926ccc3d97e16684404d9a1b (re-pointed after two non-triggering low fixes from round 3: outside-git test fixture, base fixture navigation and logo)

## Pass 2 — implementation-time re-review

Implementation-time re-review (after the code was built): calibration against the Mintlify CLI and the whole-branch review showed approved text stale in places (CONTRIBUTING.md default ignore, privacy wording, braces, pinned RFC links, hand-written descriptions). The controller edited the spec, design, tasks and plan; Opus re-reviewed them.

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| medium | "As built" named Task 9 Step 2; the affected check is Step 4. | Fixed: Step 4, with the new expectation (summary and GitHub link). |
| medium | Plan lines 7 and 162 still listed CONTRIBUTING.md as published. | Fixed: an As built note for Task 5 Step 2 and the Architecture paragraph. |

### Round 2 — approve

Both fixes confirmed; the Task 5 Step 2 note verified by running its own command.

Approved content: plan sha256 8355551c47f25b4b44f442ff27f19da3652c7e428392f1632e0ab79dbf13c641 (re-reviewed after the appended "As built" section and its two fixes)
