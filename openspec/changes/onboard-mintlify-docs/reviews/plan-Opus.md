# Review record: plan

- Artifact: `plan.md`
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream: `brainstorm.md` sha256 c27ca9d75a2a5b085956825eb2aa827fd3b518d9f65ba6df139b52ff9670a0c7; `proposal.md` sha256 0260f7c0185b8f9957eb6026c228fd827d306227fa373dd5646729d673b4ea65; `design.md` sha256 7820ef3cd781f22f5d64fa7df012d95e91ba6d3b0d9cfbde452690398b9db137; `specs/documentation-site/spec.md` sha256 ba76b767564a083feb4f09b8c3d96625ad2e35b4e63cd950bffaf170c447455c; `specs/component-versioning/spec.md` sha256 714bad1f17bd08338e7a80400b21329ed17b0f97bf51025f3755be0eb8044d0e; `specs/ci-pipeline/spec.md` sha256 a975d5fae45639010597c16b0bfc5d54bac0e28c2631e3b4e3d26066e926b616; `tasks.md` sha256 e97cf94b486c2a71fb9f61059edc61059746642619f10f181731315071e69ebc

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
