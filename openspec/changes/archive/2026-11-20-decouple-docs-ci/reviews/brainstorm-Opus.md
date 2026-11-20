# Review record: brainstorm — Opus

- Artifact: brainstorm (`brainstorm.md`)
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream: none (brainstorm is the first planning file)

## Pass 1

### Round 1 — approve

Reviewed content: the first draft of `brainstorm.md`. Its hash was not captured before the fixes below.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| M1 | medium | The guard sits in the directory it guards and runs inside `unittest discover`, so a test module could monkeypatch it. | fixed: the guard is a separate first step, `python3 docs/checks/check_offline.py --root .`, pinned by `assertDocsCheckWorkflow` (four commands). |
| M2 | medium | The guard rules would reject today's files: sibling imports, two `importlib` uses with non-literal paths, and `["git"] + …` BinOp argument vectors. | fixed: the allow-list includes the sibling modules, the BinOp form is accepted, and the `importlib` uses are an explicit (file, function) exemption list. |
| M3 | medium | The call checks miss aliased imports, `getattr`, `compile`, `os.posix_spawn`/`os.fork`, and `git` argument vectors that fetch code. | fixed: aliases and from-imports of process functions are rejected and the extra calls are banned. The dynamic `git` tail is named as a limit covered by review, and the "stricter" claim is narrowed to the listed forms. |
| M4 | medium | The tooling suite already enforces a superset of the installer boundary on every workflow (`workflow-contracts.test.mjs` around lines 2098–2120). | fixed: §2 now rests the exemption on that all-workflow boundary, adds only the stricter `environment:` and `git push` rules, adds a superset-pinning test, and corrects the cons. |
| M5 | medium | The list of tests to change is incomplete: the remote-code scope test, `remote-code.test.mjs`, the override test, and a `git ls-files verify` ownership test. | fixed: listed under Key Decisions. |
| L1 | low | `selection_spec` includes `global-overrides`, so last-green evidence resets once for every node. | deferred: stated in `design.md` under risks. |
| L2 | low | "Every workflow change must update the contract test" overstates the spec, which covers add, move, rename and delete. | deferred: the brainstorm's wording is background only; `design.md` and the spec delta use the precise wording. |
| L3 | low | `verify/test/*.test.mjs` other than workflow-contracts runs in no CI job (pre-existing). | deferred: out of scope; noted in `design.md`. |
| L4 | low | `scripts/agent_rules.py`, which the docs check executes, never selects supply-chain (pre-existing). | deferred: out of scope; noted in `design.md`. |
| L5 | low | The #174 and #175 examples were not checked against CI history. | rebutted: both were reproduced by running `scripts/components.py changed` on those commits during exploration. |

Approved content: brainstorm sha256 e6d95b4ca2c63cf64f1d30f5914a3b8431c02e3d09f5515acaac0b0968c65d20 — re-pointed after 5 non-triggering minor fixes (M1–M5 above); L1–L5 recorded without an edit.
