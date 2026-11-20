## Why

repo-search (Semble) indexed vendored code and evidence dumps, skipped YAML (OpenSpec schema and config, CI workflows) and ranked historical plans above the code they describe. Scenario sets scored against the index showed the gaps; tuning the index rules fixed most of them.

## What Changes

- `.sembleignore`: exclude `native-bridge/host/vendor/` and `native-bridge/feasibility/evidence/`; force-include `*.yaml` and `*.yml`.
- `.semble-weights`: `docs/superpowers/ 0.5`.
- `.github/components.json`: list both files as unowned so `components.py check` passes.

## Capabilities

### New Capabilities
None. Local search tooling only; no requirement changes, so no delta specs (archived with `--skip-specs`).

### Modified Capabilities
None.

## Impact

Local, gitignored `.semble/` index only. No product, build or CI behaviour changes. Retrospective change, written after the work; planning artifacts intentionally skipped.
