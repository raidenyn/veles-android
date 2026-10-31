# OTP-23 Mac M-08 resume attempt (2026-10-28) — FINDING (public redacted copy; see curation/)

Scope: owner resumed Mac tests after confirming "Updated and relaunched" (Chrome 156.0.8078.74 on the
Mac, read back live). Approved fixture UNCHANGED (`e7c716c5…eb91c`, `me.nagaev.veles.bridge`,
team `7T39M26Z99`, codesign --verify --strict OK before AND after; Android fixtures re-read, hashes
match: app `063ac7ea…`, test `8ca89381…` on both phones; no install/wipe/signing/TCC/Chrome-control
actions). Staged a NEW Mac work dir `/Users/raiden/homepc/work/otp23-mac-resume` (13 files byte-
pinned to HEAD `9c3ddc5`; read-only symlink to the approved app; old `otp23-p2` untouched). No
`--overwrite` anywhere; no commit/push/matrix change; OTP-24 not touched.

## 1. The prior step-2 blocker did NOT reproduce (setup now measured)

`ext-load-attempt1/ext-load.json` (diagnostics, unchanged driver code path): disposable profile,
registered manifest copied byte-for-byte (validated), `Extensions.loadUnpacked` took **0.006 s** and
returned exactly `gmekhgkipjkikmaadaajljinkgfielei`; connector page defined `otp23` in 0.297 s; the
host launched on demand and `otp23.hello()` returned `type=ready, platform=macos, bridge_version=
otp23-feasibility/0.1.0 plain, api_version 1`. Chrome **156.0.8078.74** exactly, no extra flags,
`VELES_OTP23_DETACHED_CLOSE` unset, context recorded (launchctl managername `Background`, uid 501,
ssh). Clean Chrome close. The 2026-10-28-earlier `ConnectionError: websocket closed` at
`Extensions.loadUnpacked` did NOT recur on .74; the invalid-manifest repro stays invalid input; no
causality claim is made beyond this setup being measured working now.

## 2. M-08 lifecycle-1 (partial): what actually happened (observed)

`lifecycle --scenario all` (unique dir, no overwrite; run provenance recorded). 4 of 4 Chrome
scenarios got ready, discovered BOTH phones, connected both, byte-proved both links (16-byte
exact echoes, distinct per-handle proofs), restarted the scan, and sampled the connected-phase
hygiene probe 6× each. Observed at every sample: **host owns 0 windows at ANY CGWindowList layer**
(all_layers 0) while both links + scan are active, with the positive window control held (owned
Chrome pid: 14 layer-0 visible windows seen in the same sample) — the window side of M-08 is real
measured data this time (the historical runs only had `top_level_windows: 'unavailable'`).

Then every Chrome scenario FAILED at the driver's own evidence-integrity requirement BEFORE any
teardown step ran: the endpoint half of the same probe was `unavailable`, because **`lsof` writes a
stderr warning on this system** — macOS reports another local user's AppTranslocation mount
(`…/T/AppTranslocation/7BA7CCAC… = <foreign-source-path>, mounted by uid≠self`, `stat` denied) and
lsof prints `can't stat() nullfs file system…` on EVERY invocation. The honest I-1 rule
(any stderr ⇒ census incomplete ⇒ `unavailable`, never zero) marks the sample unusable, and the
driver correctly refuses to claim unverifiable absence and aborts. `LC-stdin-eof-truncated` PASSED
(exit 2, framing rejection, stdout drained — unchanged from history). `LC-stdin-eof` (DIRECT
launch) was BLOCKED `permission_denied` before any BLE: in this ssh/Background launch context the
Bluetooth TCC duty falls on the launching process (recorded via the driver's own tcc_note), which
has no grant — `startScan` → `permission_pending`. No grant exists for this context; the driver
never bypasses or resets TCC. Every failure is retained verbatim (retained failures are evidence);
nothing was retried into a pass.

## 3. The two scoped blockers (each needs a concrete decision — see human-action file)

1. **Endpoint channel**: the ONLY missing probe column on macOS is the lsof endpoint census + its
   mandatory CDP-listener control. Resolved in the P0-A code phase (2026-10-28, parent + Opus
   preflight): a precise classifier for exactly this foreign-nullfs mount-stat warning with
   mandatory recorded diagnostics (see the code-phase record); no `-w`, no foreign mount/app
   control.
2. **Direct stdin-EOF context**: needs a Bluetooth-granted launch context (GUI Terminal Aqua run by
   the owner with, if prompted, an explicit Terminal-Bluetooth allow — a TCC action) — historical
   6/6 was run with the launching terminal holding the grant; over ssh it is honestly BLOCKED.

NOT attempted, NOT generalized: no alternate loaders, no browser switches, no repeated arbitrary
repros, no TCC reset, no signing change, no new instances, no causality claims about Chrome.

## 4. Phone telemetry (captured during the same run; clean)

Both phones instrumented run-id `mac-m08-resume-20261028T1546Z` with
`otp23_transport_evidence=true`; each saw exactly ONE peer (`576d2120c83dc839` = the Mac central;
4 connects / 4 disconnects / 4 subscribes), 40 notify API calls vs 40 callback successes, all
code 0, nothing unbound/dropped: `final=true, final_after_service_stopped=true, clean=true,
truncated=false` for both. The 4 link proofs + 4 4096-byte probe records really moved bytes on
both links during the samples. Stop markers: fixtures stopped only after the last host exit + >30 s
+ uncertainty; markers bracketed with phone dates and the MAC clock read back by ssh
(`stop-markers.csv`; phone-local stop epochs preserved in the transport files:
6T 1793202424991, 10Pro 1793202430481). MAC clock binding measured explicitly
(`clock-offset-mac.json`): 6T −735 ms ±16.8 s, 10Pro −1209 ms ±15.8 s (conservative sum; no stale
Windows offsets reused; no fabricated Mac anchors). That raw clock probe is now understood to carry
a whole-second truncation bias and whole-second-latency uncertainty — it stays a CORROBORATING RAW
DIAGNOSTIC, never acceptance proof; the acceptance-grade binding is the reviewed in-band causal
anchor (`scripts/mac_release_bound.py`). `phones/dis-correlation.json` labels: the phone
DIS events are Chrome-close cleanup releases (not teardown evidence), consistent with (not provable
at that uncertainty) the ~1 s post-sample cleanup window. `within_bound_claimed: false` for this
partial attempt.

## 5. State left behind

Owned registration created then removed (`registration/record.json`; dir identical to before);
fixture identity re-verified after (sha + strict codesign); no otp23 processes left; owned test
Chrome closed; phones stopped and final telemetry pulled; **matrix unchanged — still 23 PASS /
6 BLOCKED / 3 waived, NO-GO; M-08 stays BLOCKED (rerun attempt measured, not adjudicated; no
canonical PASS claimed pending independent review)**.

## 6. Adjudicability statement for the parent

M-08 **cannot** be independently adjudicated from this attempt alone: the teardown rows
(shutdown / literal port.disconnect / tab-close / direct stdin-EOF) never executed because the
driver correctly refuses to pass connected-phase hygiene whose endpoint half is `unavailable`, and
the direct case is TCC-blocked in this launch context. What IS measured and reusable regardless:
setup works on Chrome 156.0.8078.74 (load+host+ready), connected-phase window-absence with held
positive controls, byte-proven two-links+scan, phone telemetry pipeline with clean finals and Mac
clock binding. The P0-A code phase (parent decision + Opus preflight) implements the endpoint
classifier and the Windows-only release gate; a single rerun per scenario in NEW unique dirs
(three separate fixture run ids) is planned after independent code verification, with no other
changes.