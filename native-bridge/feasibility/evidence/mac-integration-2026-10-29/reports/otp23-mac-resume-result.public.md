<!-- PUBLIC REDACTED COPY: another local user's account name and a foreign app name replaced by placeholders; the private original is kept outside the repository (see ../curation-map.json). Otherwise byte-identical. -->
# OTP-23 Mac M-08 resume — execution result (2026-10-28, operator: ollama-cloud/glm-5.3-flash, no subagents)

Task: /tmp/opencode/otp23-mac-resume-task.md · Owner record: /tmp/opencode/otp23-mac-resume-owner-record.json
("Updated and relaunched", Chrome read back 156.0.8078.74 on the awake Mac — re-verified live from
Info.plist before runs). Repo: branch contract/otp23-desktop-alignment, clean HEAD 9c3ddc54a3721eced84e49f02ed76ef156f0baed,
unchanged by this session (only a new untracked evidence dir). Matrix unchanged: **23 PASS / 6 BLOCKED /
3 waived, NO-GO; M-08 stays BLOCKED — not adjudicated**; no commit/push/matrix promotion; OTP-24 untouched.

## Observed (actual measurements, all paths/hashes below)

1. **Setup re-measured working (task step 2).** `evidence/mac-resume-2026-10-28/ext-load-attempt1/ext-load.json`:
   `Extensions.loadUnpacked` 0.006 s → id `gmekhgkipjkikmaadaajljinkgfielei` exactly; connector `otp23` ready
   0.297 s; host launched on demand; `otp23.hello()` = `ready, platform=macos, bridge_version
   otp23-feasibility/0.1.0 plain, api_version 1`. Chrome **156.0.8078.74**, driver argv only (no extra flags),
   disposable profile, `VELES_OTP23_DETACHED_CLOSE` unset, context recorded (`launchctl_managername: Background`,
   uid 501, ssh). The earlier .49-era `ConnectionError: websocket closed` did NOT reproduce. No causality claim;
   the old invalid-manifest repro remains invalid input.
2. **M-08 lifecycle-1 partial run** (`evidence/mac-resume-2026-10-28/lifecycle-1/`, unique dir, never
   `--overwrite`): all four Chrome scenarios reached ready → discovered BOTH phones → two independent byte-proven
   links (16-byte exact echoes, distinct per-handle proofs) → fresh scan → 6 connected-phase hygiene samples per
   scenario. Every sample: **host owns 0 windows at ANY CGWindowList layer** (all_layers=0), window positive
   control HELD (owned Chrome pid = 14 layer-0 visible windows in the same sample), context audit usable,
   both host+Chrome `pid_alive=true`. Then every Chrome scenario FAILED at the driver's evidence-integrity
   requirement before any teardown step: the lsof endpoint column and the CDP-listener control recorded
   `unavailable`, and the driver refused unverifiable absence claims (by design). `LC-stdin-eof-truncated` PASS
   (exit 2, framing rejected, stdout drained). `LC-stdin-eof` (DIRECT) BLOCKED `permission_denied`
   (`scan_failed/permission_pending`: in the ssh/Background context the Bluetooth TCC duty falls on the launching
   process — the driver's own tcc_note — which holds no grant). Retained verbatim; nothing retried into a pass.
3. **Phone telemetry, same-run, clean:** run-id `mac-m08-resume-20261028T1546Z`; both phones final
   `transport-*.json`: `final=true, final_after_service_stopped=true, clean=true, truncated=false`; one peer each
   (`576d2120c83dc839` = the Mac central); 4 connects/4 disconnects/4 subscribes; 40 notify API vs 40 callback
   successes, 0 non-zero, 0 unbound/dropped. Fixtures stopped only after last host exit + >30 s + uncertainty;
   stop markers bracketed with the MAC clock read back over ssh (not the PC), pre-declared 30 s release bound is
   NOT claimed for this partial attempt. MAC clock bound explicitly: 6T **−735 ms ±16.8 s**, 10Pro **−1209 ms
   ±15.8 s** (phone-minus-Mac; conservative sum; no stale Windows offsets reused; no fabricated Mac anchors).
4. **Staging + identity (all pinned):** new work dir `/Users/raiden/homepc/work/otp23-mac-resume` (not otp23-p2);
   13 driver/extension/identity files byte-verify identical to HEAD (re-read on the Mac); approved app via
   read-only symlink, resolved to `/Users/raiden/homepc/work/otp23-cd971f0/.../VelesOtp23PoC.app`, binary sha256
   `e7c716c5d8c9c05ac8aaa838d5bdbdcc0918f352c270cb330d8edb5befeeb91c`, `codesign --verify --strict` OK before AND
   after, id `me.nagaev.veles.bridge`, team `7T39M26Z99`. Android fixtures re-read: app `063ac7ea…677a5e`, test
   `8ca89381…ede1c` on both phones (matches approved; no install/wipe). Scoped user-level registration created for
   this run only and removed after (dir byte-identical before/after; no TCC change; manifest sha
   `17a196cd…4fca` recorded); no owned test Chrome/processes left anywhere; personal Chrome never touched.
5. **New helpers (separate artifacts, no frozen B5/v4 source touched):** `helpers/mac_phone_clock_probe.py`
   (binds the MAC clock; sha256 of every published file in `SHA256SUMS`, self-pinned `.gitattributes -text
   -whitespace`) and `helpers/mac_ext_load_probe.py` (diagnostics only).

## Inference vs observation boundary (explicit)

- OBSERVED: everything in §1–§4 verbatim from run outputs; window-absence-while-linked with held positive
  controls; clean EOF-truncated exit; phone-side notify/callback parity on both links.
- INFERRED (labelled, low confidence): the phone DIS events are Chrome-session-close cleanup releases
  (teardown never ran), consistent with the ~1 s post-sample cleanup window — NOT provable at ±16 s uncertainty
  (`phones/dis-correlation.json`, `within_bound_claimed:false`).
- NOT claimed: any per-scenario teardown bound, any M-08 PASS/BLOCKED promotion, any Chrome-defect or
  update-causality statement, any prompt-owner observation.

## Residuals and exact blockers (stop reasons — per the task's stop rule)

1. **lsof endpoint channel (M-08 connected-phase requirement):** this macOS box reports another user's
   AppTranslocation mount (`/private/var/folders/yc/…/T/AppTranslocation/7BA7CCAC…` = <foreign-app>.app, mounted by user
   `<another-local-user>`, stat denied); `lsof` therefore prints a stderr warning on every invocation and the honest I-1 rule
   (any stderr ⇒ `unavailable`, never zero) makes every endpoint sample unusable, which is exactly what the
   driver asserted. Resolving it needs EITHER a minimal instrumentation change (e.g. lsof `-w`; does not weaken
   the no-stderr semantics for real failures) → requires parent + Opus preflight, OR `sudo umount` of that
   foreign mount (environment fix) → owner. I did not make the change or touch the mount.
2. **Direct stdin-EOF launch context (M-08 matrix item "stdin EOF"):** needs a Bluetooth-granted launch context
   (historically the Aqua Terminal run); over ssh it is honestly `permission_denied`. Options and the concrete
   question are in `/tmp/opencode/otp23-mac-resume-human-action.md`. Any TCC prompt decision belongs to the owner.

## M-08 adjudicability

**Not independently adjudicable from this attempt.** The teardown rows (shutdown / literal
`port.disconnect()` / tab-close / direct stdin-EOF) never executed because the connected-phase hygiene
endpoint half could not be verified — so the M-08 criterion "port closure, stdin EOF and shutdown each leave
no process" remains unmeasured this attempt. Everything measured here is reusable (working setup on .74,
window-absence with controls, byte-proven links, telemetry pipeline, Mac clock binding). With answers to the
two questions above, a single rerun in a new unique dir completes the full set with no other changes.

## Next-test plan after M-08 (other open Mac rows; nothing launched)

- **M-03** (row BLOCKED only on the prompt-owner observation; signing clause satisfied by the 2026-10-27
  amendment): the fixture's original prompt is past and must NOT be inferred from Settings. Minimum remaining:
  a new owner decision on how to observe the prompt owner on the existing account (e.g. a deliberately
  TCC-removed observation via a NEW instance is a new-instance + permission-toggle decision), or an owner
  decision to leave it human-gated/deferred. No test I can run without that decision.
- **M-04** (access_denied/retryable already observed, recovery2 PASS retained): remaining = the EXACT
  System-Settings retry guidance text in recorded host/connector output + prompt owner observed. Investigation
  recorded that no such guidance text exists in the harness (production UX = OTP-07 / #88). Minimum remaining:
  owner decision accepting the scope-move amendment (row wording), else no runnable path on this fixture.
- **M-11** (unsigned dev-registered host on the EXISTING account): minimum concrete plan: (1) owner approves a
  plain unsigned build from the same source tree; (2) disposable-profile-scoped registration; (3) Chrome launch +
  TCC authorization result + scan recorded and compared with M-03..M-06; account class recorded truthfully. The
  TCC prompt for the unsigned binary is expected — that is a permission-toggle human action requiring the owner's
  specific new decision; nothing was launched here.

## Files (all sha256-listed in evidence/mac-resume-2026-10-28/SHA256SUMS)

`FINDING.md` · `stage/manifest.json` · `stage/stage.tgz` · `registration/record.json` ·
`ext-load-attempt1/ext-load.json` · `lifecycle-1/{lifecycle.json,6 scenario jsons,5 host-trace logs,run-provenance.json}` ·
`phones/{fixture-identity.json,clock-offset-mac.json,stop-markers.csv,dis-correlation.json,transport-2be02e41.json,transport-864a3db0.json,2 instrument logs,stop-bracket}`
· `helpers/{mac_phone_clock_probe.py,mac_ext_load_probe.py}` · `.gitattributes` — 32 files, self-verified.

Local gates deliberately NOT run now (no commit yet): `spotlessCheck detekt lintDebug` etc. per the task's
"publication gates later once changes stabilize".