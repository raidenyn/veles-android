<!-- PUBLIC REDACTED COPY: another local user's account name and a foreign app name replaced by placeholders; the private original is kept outside the repository (see ../curation-map.json). Otherwise byte-identical. -->
# OTP-23 Mac resume — concrete questions for parent/owner (2026-10-28)

Context: the M-08 resume attempt is fully preserved in
`native-bridge/feasibility/evidence/mac-resume-2026-10-28/` (FINDING.md has the details). The setup blocker
(extension load on Chrome .49) is resolved on Chrome 156.0.8078.74; the run now stops on exactly two scoped
blockers. Per the task's stop rule I am asking instead of spinning. **Nothing has been changed, mounted,
granted, built or relaunched to force either of these.**

## Q1 — M-08 endpoint channel (blocks every Chrome scenario's connected-phase probe)

macOS reports another user's AppTranslocation mount (`/private/var/folders/yc/d7qkpc795mz8nprkhm8xlpr80000gp/
T/AppTranslocation/7BA7CCAC-123C-4593-9838-F34CA30F033C` = `/Users/<another-local-user>/Downloads/<foreign-app>.app`, mounted by
user `<another-local-user>`, stat denied). `lsof` prints a stderr warning on EVERY invocation on this box, and the driver's
honest I-1 rule (any stderr ⇒ census incomplete ⇒ `unavailable`, never zero) then refuses the connected-phase
absence claim and aborts each scenario before teardown. Window half of the probe is already measured (host 0
windows, Chrome control 14 — held at every sample); only the endpoint column + its CDP-listener control are
missing.

Pick one:

- **(a) Preflight-approved one-flag instrumentation change (my recommendation):** add lsof `-w` (suppress
  warning messages) to the pinned lsof argv in `MacAdapter.hygiene_probe`/`MacAdapter.hygiene` so the census
  reflects the target pid's real sockets. Honest semantics unchanged (rc>1, unparsable lines, real stderr on
  failure stay `unavailable`). Needs parent + Opus preflight before I rerun; ~1-line diff + unit test + rerun in
  a NEW unique dir.
- **(b) Environment fix without code change (needs owner at the Mac):** `sudo umount` the foreign AppTranslocation
  mount (or have `<another-local-user>` eject/unmount <foreign-app>). If the owner prefers this, I rerun with zero code changes.
- **(c) Decide the endpoint column may be `unavailable` with the window half + live-pid control carrying M-08**
  — this weakens the W-02-style wording, so it is the parent's call, not mine.

Question for the parent/owner: **(a), (b), or (c)?** If (a): please have Opus preflight the diff before I rely
on new capture.

## Q2 — direct stdin-EOF launch context (the matrix's "stdin EOF" item for M-08; direct launch is NOT
Chrome provenance)

Over ssh (Background context, recorded in evidence) the direct host launch is TCC `permission_pending` — the
Bluetooth duty falls on the launching terminal context, which holds no grant. The historical 6/6 stdin-eof
direct row ran with a Bluetooth-granted launching terminal in the GUI session.

Pick one:

- **(a) Owner runs the single lifecycle command in Terminal.app on the Mac** (`python3
  scripts/scenario_matrix.py lifecycle --scenario stdin-eof --out-dir <new unique dir>` — exact command will be
  in the result file when scheduling happens); if macOS shows "Terminal wants to use Bluetooth", the owner
  allows it — this IS a permission-toggle TCC action and an owner decision (Terminal gains the grant; the
  approved fixture/grant untouched).
- **(b) Skip the direct stdin-eof row for this resume** and adjudicate M-08 on the Chrome-provenance EOF family
  (port.disconnect/tab-close `stdin_closed` exits) + shutdown — matching the matrix sentence but leaving the
  direct-EOF comparison unmeasured this attempt; recorded as BLOCKED, not waived.
- **(c) Defer again** as a fresh owner deferral (a new record, not rewriting history).

Question for the parent/owner: **(a), (b), or (c)?**

## Constraints I will keep under either answer

New unique attempt dirs only (never `--overwrite`); same approved fixture (e7c716c5…, no rebuild/re-sign); no
old Chrome versions, alternate loaders, TCC resets, signing changes, personal-Chrome control; phones/leases and
the homepc end-of-session already handled; no OTP-24 work; no commit/push/matrix promotion; the retained
lifecycle-1 attempt stays immutable evidence.