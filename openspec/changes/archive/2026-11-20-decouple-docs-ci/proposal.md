## Why

A pull request that changes only the docs procedures also starts the slow `installer` and
`supply-chain` CI nodes: the docs check scripts, the docs workflow, or the tooling contract that pins
that workflow. Those nodes run multi-OS installer suites and the full SBOM and licence build. They
start because they read docs-owned files, not because a docs edit can change what they protect.
Moving each guarantee to a check that the cheap `docs` and `tooling` nodes already run keeps every
guarantee and makes docs-procedure PRs fast.

## What Changes

**Docs check scripts and the remote-code scan**
- From: `docs/checks/` is a `REMOTE_CODE_SCOPES` entry and a supply-chain `ci-path`, so editing a docs check runs `supply-chain / build`.
- To: a stdlib-only guard, `docs/checks/check_offline.py`, runs as its own step in `docs / agent-files`. It allow-lists the imports the docs checks may use, rejects network modules, and limits subprocess calls to `git`. It also rejects any file under `docs/checks/` other than a `*.py` file (today there are none), so no shell or JS file can escape it. `docs/checks/` leaves the scan and supply-chain selection.
- Reason: the docs node owns the guarantee and checks it in seconds; for the forms it checks, the guard is stricter than the regex scan. Trade-off: a docs-only PR can loosen the guard and use the loosened form in the same PR with only `docs` running. That is caught by review, as an edit to the scanner is today, since neither path has a CODEOWNERS entry.
- Impact: non-breaking; `docs / agent-files` gains one command.

**The docs workflow and the release boundary**
- From: `.github/workflows/check-docs.yml` selects `docs`, `tooling`, `installer` and `supply-chain`, because the installer boundary suite reads every workflow and the remote-code scan lists this one.
- To: it selects `docs` and `tooling`. The tooling contract suite already enforces a superset of the installer boundary on every workflow. It also gains two stricter rules for this workflow (no `environment:`, no `git push`) and a test that pins the superset. The installer boundary skips this one file by name, and the remote-code scan drops it.
- Reason: one owner for the workflow's safety, in a node that runs anyway.
- Impact: non-breaking; every other workflow keeps its current selection.

**Supply-chain ownership of `verify/`**
- From: supply-chain owns the whole `verify/` prefix, so editing `verify/test/workflow-contracts.test.mjs` starts it, although its build never reads `verify/test/`.
- To: supply-chain owns `verify/lib/` and each top-level `verify/` file the scan or build reads. `verify/test/` belongs to `tooling` alone. A new test asserts that every tracked `verify/` file outside `verify/test/`, except `verify/AGENTS.md` (which a `global-overrides` entry maps to `docs`), still selects supply-chain.
- Reason: selection should follow what the node reads.
- Impact: non-breaking; a new top-level `verify/` file is unaccounted until it is mapped, which fails closed.

Out of scope: the gate-read documents `docs/otp-25-installers.md`, `docs/reproducible-builds.md` and
`native-bridge/host/AGENTS.md`. The installer and host gates assert their text, so they keep selecting
those gates.

## Capabilities

### New Capabilities

None.

### Modified Capabilities

- `ci-pipeline`: node selection for `check-docs.yml`, `docs/checks/` and `verify/test/`; the remote-code
  scan scopes; the docs workflow's commands and boundary contract; and the new docs offline guard.

## Impact

- `.github/components.json`, `.github/workflows/check-docs.yml`
- `docs/checks/check_offline.py` and `docs/checks/test_check_offline.py` (new)
- `docs/checks/check_agent_files.py` and `docs/checks/test_check_agent_files.py` (they pin the `check-docs.yml` override)
- `docs/agent-guidance.md` (the docs command list)
- `verify/check-remote-code.mjs`, `verify/test/workflow-contracts.test.mjs`, `verify/test/remote-code.test.mjs`
- `native-bridge/installer/scripts/test_build_contract.py`
- `scripts/tests/test_components.py`
- `docs/ci.md`, `docs/AGENTS.md`, `openspec/specs/ci-pipeline/spec.md` (through the delta)
- Last-green evidence: the map change resets the evidence for every node once.
