## Design Summary

**Intent.** A pull request that only changes the docs procedures (the docs check scripts under
`docs/checks/`, the docs workflow `.github/workflows/check-docs.yml`, or the tooling contract test that
pins that workflow) currently starts the slow `installer` and `supply-chain` CI nodes. The owner wants
those PRs to run only the `docs` node (plus `tooling` when the docs workflow or its contract changes),
without weakening any guarantee those slow nodes gave.

**Why the couplings exist today.** The component map (`.github/components.json`, read by
`scripts/components.py`) follows one rule from `docs/ci.md`: a check that reads a file must run when
that file changes. Three couplings follow from that rule:

1. **`docs/checks/` selects `supply-chain`.** `verify/check-remote-code.mjs` (run by
   `verify/verify-supply-chain.sh`, the `supply-chain / build` job) lists `docs/checks/` in
   `REMOTE_CODE_SCOPES`, so `docs/checks/` is in supply-chain's `ci-paths`. Example: #174 (AGENTS.md
   line cap) ran supply-chain.
2. **`.github/workflows/check-docs.yml` selects `installer` and `supply-chain`** (plus `docs` and
   `tooling`) through its `global-overrides` entry. The installer suite
   (`native-bridge/installer/scripts/test_build_contract.py`, `check_gate_boundary`) reads every
   workflow and rejects any workflow other than `publish-release.yml` that references signing
   credentials, enters a release environment, or creates releases or pushes tags. The remote-code scan
   also lists the file by name. Example: #175 (Mintlify site) ran installer and supply-chain.
3. **`verify/test/` selects `supply-chain`.** The supply-chain node owns the whole `verify/` prefix,
   but neither `verify/verify-supply-chain.sh` nor the remote-code scan reads `verify/test/` (the
   scanner filters it out). Every workflow change must update `verify/test/workflow-contracts.test.mjs`
   (ci-pipeline spec), so docs-workflow PRs pick up supply-chain this way as well.

**Design (scope A, chosen by the owner).**

1. **Docs checks guard themselves.** Remove `docs/checks/` from `REMOTE_CODE_SCOPES` and from
   supply-chain's `ci-paths`. Add a stdlib-only Python guard, `docs/checks/check_offline.py`, that
   runs as its own process: a new first step `python3 docs/checks/check_offline.py --root .` in the
   `agent-files` job, which `assertDocsCheckWorkflow` then pins (four commands instead of three). A
   separate process means no test module can monkeypatch it. Its tests in `test_check_offline.py` run
   under the existing `unittest discover` step. The guard parses every `*.py` file under
   `docs/checks/` with `ast` and fails when a file:
   - imports a module that is neither on an explicit stdlib allow-list nor a sibling module in
     `docs/checks/` (today: `check_agent_files`, `check_site`, `check_gate_b_matrix`,
     `gate_b_timing`). The stdlib allow-list starts from today's imports: `__future__`, `argparse`,
     `ast`, `collections`, `contextlib`, `copy`, `importlib.util`, `io`, `json`, `math`, `os`, `re`,
     `shutil`, `subprocess`, `sys`, `tempfile` and `unittest`. Network and code-download modules
     (`urllib`, `http`, `socket`, `ssl`, `ftplib`, `smtplib`, `xmlrpc`, `ensurepip`, `venv`, `pip` and
     the like) are never added;
   - imports `subprocess` or `os` under an alias, or uses `from os import …` or
     `from subprocess import …` for a process, exec or spawn function;
   - calls `subprocess.*` with a first argument that is neither a list literal whose first element
     is the constant `"git"` nor a `+` expression whose left operand is such a list. Today's forms
     `["git"] + list(args)` and `["git"] + args` both qualify. It also fails on any `shell=True`;
   - calls `os.system`, `os.popen`, `os.exec*`, `os.spawn*`, `os.posix_spawn*`, `os.fork*`, `eval`,
     `exec`, `compile`, `getattr` or `__import__`;
   - uses `importlib` outside an explicit exemption list in the guard. The list holds
     (file, function) pairs, each of which loads the agent-rules engine `scripts/agent_rules.py`:
     today `check_agent_files.py` (line 736) and `test_check_agent_files.py` (line 49). A new
     `importlib` use fails until the guard is edited.
   The guard does not inspect the dynamic tail of a `git` argument vector, which callers build at
   run time. That tail is covered by review, as it was under the regex scan. The guard is stricter
   than the scan only for the forms listed above. Its negative tests feed each forbidden form in as a
   fixture string and expect it to fail.
2. **The tooling contract owns `check-docs.yml`.** The tooling suite
   `verify/test/workflow-contracts.test.mjs` already covers this workflow in two ways:
   - It enforces the release boundary on **every** workflow except `publish-release.yml`, with the same
     comment stripping as the installer suite. Its signing-name list is a superset of the installer's
     (it adds `signtool`, `notarytool`, `codesign` and `VELES_ANDROID_CERT`), and it rejects the same
     release environments and the same publishing commands.
   - `assertDocsCheckWorkflow` pins `check-docs.yml` to exact `python3` commands, read-only
     permissions, no secrets and no package installs.

   Add to `assertDocsCheckWorkflow` the two stricter rules it lacks, each with a negative mutation:
   no `environment:` key at all, and no `git push` of any kind. Add one tooling test that pins the
   superset: each of the installer boundary's signing names, environments and publishing samples is
   rejected by the tooling all-workflow checks. Then:
   - `check_gate_boundary` skips `check-docs.yml` by name in its all-workflows loop, with a comment that
     names the tooling all-workflow boundary as the owner. A test asserts that only that one file is
     skipped;
   - `REMOTE_CODE_SCOPES` drops `.github/workflows/check-docs.yml`;
   - the `global-overrides` entry for `check-docs.yml` becomes `["docs", "tooling"]`.
3. **Supply-chain owns only what it reads under `verify/`.** Replace supply-chain's `verify/` prefix
   with `verify/lib/` plus the exact top-level `verify/` files. `verify/test/` stays owned by `tooling`
   alone. A new top-level file under `verify/` is then unaccounted, which fails `changes` and selects
   every node: the map's existing fail-closed behaviour.

**Outcome.** A PR touching only `docs/checks/`, `check-docs.yml` or
`verify/test/workflow-contracts.test.mjs` selects `docs` and/or `tooling` and never `installer` or
`supply-chain`.

## Alternatives Considered

### Alternative A: Move the guarantees to the cheap nodes (chosen)
- **Approach**: As in the design above. Each guarantee moves to a check the docs or tooling node
  already runs, and the slow nodes stop reading the docs files.
- **Pros**: The guarantees stay. Docs-procedure PRs drop from minutes of multi-OS jobs to seconds. The
  docs guard is stricter than the regex scan for Python. No change to `scripts/components.py`.
- **Cons**: The release boundary stays duplicated between the installer suite (now with one named
  exemption) and the tooling all-workflow checks. A pinning test keeps the tooling side a superset.
  The docs guard can be loosened by a docs-only change; review catches that, as it did for edits to
  the scanner. Listing `verify/` files explicitly costs a map
  edit whenever a top-level verifier file is added.
- **Why chosen**: It removes all three couplings without lowering any bar, and it uses only mechanisms
  the map already has.

### Alternative B: Weaken the selection rule (accept the risk)
- **Approach**: Drop the docs paths from installer and supply-chain selection and keep the scans as
  they are, so a broken docs file is caught on `master` or the next release.
- **Pros**: Smallest diff.
- **Cons**: It breaks the documented rule that a PR cannot pass and then fail on `master`. A remote-code
  regression in `docs/checks/` would surface only after merge.
- **Why not chosen**: It lowers the guarantee the owner explicitly wants kept ("there is a good
  reason for that right now").

### Alternative C: A cheap scoped supply-chain job
- **Approach**: Split `supply-chain` into a fast `remote-code` job and the slow `build` job, and add
  path selection inside a node so that docs paths start only the fast job.
- **Pros**: The existing scanner keeps covering `docs/checks/`.
- **Cons**: The component map selects whole nodes, not jobs, so this needs a new map concept, a new
  evidence shape for last-green selection, and changes to `ci.yml`. It also does nothing for the
  installer coupling.
- **Why not chosen**: Much larger blast radius for a subset of the benefit.

## Agreed Approach

Alternative A, scope "core", chosen by the owner after an explanation of each coupling. The
gate-read documents stay coupled to their readers on purpose (see Key Decisions).

## Key Decisions

- **In scope:** couplings 1–3 above.
- **Out of scope (owner's choice):** `docs/otp-25-installers.md` → `installer`,
  `docs/reproducible-builds.md` → `native-host` and `native-bridge/host/AGENTS.md` → `native-host`. The
  installer and host gates assert those documents' text, so an edit can break them, and they are
  installer and host documents, not docs procedures.
- **Agent call:** the docs guard is a unittest module discovered by the existing
  `python3 -m unittest discover -s docs/checks -p 'test_*.py'` step. `check-docs.yml` and its contract
  then need no new command.
- **Agent call:** the installer boundary exempts `check-docs.yml` by exact name only. Any other
  workflow, including a new one, stays under the generic boundary.
- **Agent call:** `verify/check-android-layout.sh`, which no workflow runs today, stays owned by
  supply-chain like every other non-test `verify/` file, because the remote-code scan's `verify` scope
  reads it.
- The ci-pipeline spec, `docs/ci.md` and `scripts/tests/test_components.py` change with the map,
  including the "Docs workflow edit" scenario and the `check-docs.yml` scanner-scope scenario.
- Tests that change with the map:
  - In `workflow-contracts.test.mjs`, the remote-code scope test (around line 1355) is inverted:
    `docs/checks/` and `check-docs.yml` are no longer scopes.
  - `verify/test/remote-code.test.mjs` ("flags a docs/checks script") is retargeted to a scope that
    is still scanned.
  - In `test_components.py`, the `check-docs.yml` override test pins `["docs", "tooling"]`.
  - A new `test_components.py` test lists `git ls-files verify` (without `verify/test/` and
    `verify/AGENTS.md`) and asserts that every file selects `supply-chain`. A verifier file added
    only to another node's paths therefore still starts the node whose scan reads it.

## Open Questions

None blocking. The builder confirms the exact top-level `verify/` file list at implementation time
with `git ls-files verify`.
