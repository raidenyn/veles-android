# Review record: design — Opus

- Artifact: design (`design.md`)
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream: brainstorm sha256 e6d95b4ca2c63cf64f1d30f5914a3b8431c02e3d09f5515acaac0b0968c65d20; proposal sha256 cf2885c184c19e6b680e4fa769928b760385965923ecd994803b877064ac5a6d

## Pass 1

### Round 1 — changes-required

Reviewed content: design sha256 4643e5bf82f15a4c6ccb58be73676292682386a2c93fc2452247284b52d20ade

| # | Severity | Finding | Disposition |
|---|---|---|---|
| H1 | high | `check_agent_files.py` `OVERRIDE_TARGETS` and `test_check_agent_files.py` (`EXPECTED_OVERRIDE_TARGETS`, `test_override_target_sets_exact`) pin the four-node `check-docs.yml` override. Changing the map breaks `docs / agent-files`. | fixed: D3 updates all three. The proposal Impact list names the files. |
| M1 | medium | The superset test used a copied term list and could not detect installer drift. | fixed: it extracts the three regex literals from the installer loop and fails closed. The installer file joins `tooling.ci-paths`. |
| M2 | medium | AST bypasses: star import, assignment aliasing, `getattr(subprocess, …)`, `sys.modules`, `vars`/`__dict__`, literal `git -c`/network subcommands. | fixed: rules added and remaining limits stated. |
| M3 | medium | The spec delta needs a carve-out in the "Workflow contract suites move with the workflows" body; "docs-node commands requirement" is not a real name. | fixed: D4 names "Component-tree CI" (Docs node, Site guard step) and the carve-out. |
| L1 | low | The guard's tests must run it in-process. | fixed: `scan(root)` in-process. |
| L2 | low | A call with no positional argument and an unparsable file are unspecified. | fixed: both fail, exit 1. |
| L3 | low | Duplicate `verify/package*.json` entries. | fixed: stated. |
| L4 | low | The `remote-code.test.mjs` rewrite duplicates the existing `scripts/AGENTS.md` test. | fixed: the test is deleted instead. |
| L5 | low | The scope loop at line 1355, the mutation messages and the first-step ordering are unspecified. | fixed: D2 covers all three. |
| L6 | low | The non-`.py` ban rejects a future `docs/checks/AGENTS.md`. | fixed: exempted. |
| L7 | low | Python 3.8 compatibility. | fixed: stated. |
| L8 | low | `docs/agent-guidance.md` also lists the docs commands. | fixed: D4 updates it. |

### Round 2 — changes-required

Reviewed content: design sha256 87bcff948d140b25e7c10aead2b758d7690aaa663ee0bfb6236365a135a0c6f9

| # | Severity | Finding | Disposition |
|---|---|---|---|
| H1 | high | The `-c` ban rejects `_git(root, "ls-files", "-z", "-c", "-i", …)` at `check_site.py:68`, where `-c` means `--cached`. | fixed: only tokens before the subcommand count as global options. Exact-token match; accepted fixture added; `-C` and `--git-dir` added. |
| M1 | medium | The end of the loop body for regex extraction is ambiguous. | fixed: the body ends at the first line indented four spaces or less, with exactly three literals in order. |
| L1 | low | The `os.` process rule is stated twice. | fixed: duplicate removed. |
| L2 | low | `verify/package*.json` wording contradicts the list. | fixed: reworded. |
| L3 | low | `git config` keys could set `core.sshCommand`. | fixed: stated as a limit. |
| — | — | Builder guess: is `subprocess.CalledProcessError(...)` allowed? | fixed: non-process `subprocess` calls on allowed constants pass. |

### Round 3 — approve

Reviewed content: design sha256 f1397f539bd5ca7d3c312edbb49990c1b0f3710339563058935e6710a4f3140f

| # | Severity | Finding | Disposition |
|---|---|---|---|
| L1 | low | `--config-env` escapes the `--config` and `--config=` matches. | fixed: `--config-env` prefix banned. |
| L2 | low | Only helpers named `_git` or `git` are inspected; `git_output` is not. | fixed: stated as a limit. |
| L3 | low | `--upload-pack` and `--receive-pack` are not global options, so banning them as globals does nothing. | rebutted: harmless and kept as defence in depth; their subcommands are banned anyway. |

Approved content: design sha256 645156347d01d1056114c8b3e3c6ced53c509b0bdd42ca95400461899e5e5671 — re-pointed from f1397f53… after 2 non-triggering minor fixes (round 3 L1 and L2).
