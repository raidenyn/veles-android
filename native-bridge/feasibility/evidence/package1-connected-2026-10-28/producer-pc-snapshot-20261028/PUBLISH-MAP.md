# Producer snapshot publish map (2026-10-28 B5 finish/integration curation)

The capture manifest `MANIFEST.json` (236 entries, original, PRESERVED UNCHANGED; verified 236/236
against the on-disk bytes this curation) is the historical record of the READ-ONLY producer
snapshot taken from the run PC (`C:\Users\yuriy\otp23-probe\package1-p1` full tree with
`__pycache__` excluded, plus the pinned host binary and the as-run `.bat`/`.ps1` launchers).

Active checksums: `SHA256SUMS` in this folder is the ACTIVE publish manifest and covers EXACTLY the
published tracked candidate bytes (all 236 captured files plus `MANIFEST.json` and this map; the
manifest itself is not self-listed). Honesty checks performed this curation:

- Git-ignored content: NONE of the 236 captured paths matches a `.gitignore` pattern of this
  repository (each path probed with `git check-ignore`); no `__pycache__`, `target`,
  `node_modules`, `dist` or other cache/output directory exists in the snapshot — the capture
  already excluded caches and the publisher map re-verified it.
- Private/caches: nothing removed. No raw measurement, listing, launcher, binary or evidence file
  was deleted, replaced or rewritten. The only file NOT in the original capture list is this map
  and the active `SHA256SUMS`.
- Machine context, honestly: the launcher scripts (`otp23-probe-root-launchers/`) and the tree
  listings embed the run PC's local paths and account name (`C:\Users\yuriy\…`) — they are the
  actual as-run instrumentation and are published as measurement records, not as reusable
  infrastructure. No credential, secret or key material was found or captured.
- The host binary `host-binary/otp23-host.exe` (sha256 `8521de4d58bdba0858e717e3dca3bcc0850b272ba3c1544bda158afb9e60ae33`,
  726,528 bytes) is the INTENTIONALLY frozen measured instance referenced across the package-1
  evidence; it is tracked with exact size+hash here, not force-added from an ignored location
  (it matches no ignore rule).
- Any file that were later found to be a cache or private artifact would be EXCLUDED from the
  active `SHA256SUMS` and documented here by path + sha256 (never deleted from the PC capture, and
  the original `MANIFEST.json` never rewritten).
- Git export: the parent evidence folder's `.gitattributes` (`* -text -whitespace`) makes every
  byte export-identical, so the active checksums verify from a clean checkout.