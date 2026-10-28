# SHA256SUMS notes (2026-10-28 B5 finish phase)

- `SHA256SUMS-before-b5-supersede.original` is the byte-exact manifest as captured with the original
  evidence (sha256 `a896446a8aefaa67b7cd936c4569cf7cbcf35364e47b4cc4f0cf517b43e8d51d`); it verified
  181/181 at capture and is retained as history.
- The active `SHA256SUMS` is the SAME list in the SAME order with values recomputed; `diff` against
  the original changes EXACTLY ONE line: `./FINDING.md`
  (`7288c7ec…` → `a06cc6b9…`), because the B5 finish phase appended the SUPERSEDING CORRECTIONS
  section to that derived narrative file (allowed: raw JSONs/traces/telemetry are never rewritten).
  The active manifest verifies 181/181 again.
- No other file in this folder changed between the two manifests; every run directory, dumpsys
  capture, trace, JSON, clock probe, snapshot and retained failure is byte-identical. The v2
  corrections live in `../package1-b5-v2-20261028/` and never touch this folder's raws.
- The `../package1-p2-20261028/SHA256SUMS` (34/34) needed no repair: the P2 folder was not modified
  by this phase.