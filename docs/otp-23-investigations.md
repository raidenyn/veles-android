# OTP-23 investigations (2026-10-27, with follow-up)

Baseline `cab0438` (PR #123, prerequisite PR #122, both open). **The decision stays NO-GO.** No matrix row changed status,
no waiver was added, OTP-24 is not implemented, nothing was merged, reset or force-pushed. Only W-01, M-01 and M-02 remain
waived. Plain Rust stays the selected runtime; retaining Tauri for packaging is still not approved (matrix D-01). Raw evidence:
`native-bridge/feasibility/evidence/investigations-2026-10-27/` (bytes preserved; its own `.gitattributes` sets
`-text -whitespace`; `SHA256SUMS` lists every file). Full, untruncated issue bodies and comments for #86-#90 as read on
2026-10-27 are in its `issues/` folder.

Issue state: #86 (OTP-23) is open and has **no assignee**; #87 (OTP-24), #88 (OTP-07), #89 (OTP-25) and #90 (OTP-08) are open
with the carried-forward obligations. #86 assigns this pass's three early questions to itself: the background interpretation,
the Windows `access_denied` root cause and the macOS distribution/signing/permission model. Nothing was edited on GitHub.

## 1. Chrome Native Messaging: connector tab, MV3 worker, timers, freeze and discard

### 1.1 Source evidence, pinned to both Chrome versions
Files were fetched from Chromium tags **155.0.8059.85** and **156.0.8078.48** (`chromium-source/`). For
`frame_scheduler_impl.cc`, `native_message_port.cc` and `discard_eligibility_policy.cc` the two tags are byte-identical. The
installed Chrome on the Mac is 156.0.8078.48; another macOS account's already-running browser was 155.0.8059.85. The Chrome
version of each retained evidence run is whatever that run recorded; this document makes no claim about it.

| Question | Evidence | Reading |
|---|---|---|
| Does the Native Messaging port touch the renderer scheduler? | `native_message_port.cc` (both tags) has no scheduler, throttling or freeze reference; the port sits in the browser process and forwards host output with `weak_channel_delegate_->PostMessage(...)`. | The port itself does not register anything with the page scheduler. |
| Which pages escape intensive timer throttling? | `SchedulingPolicy::DisableAggressiveThrottling` is a trait that can only be attached when a feature registers with the frame scheduler (`OnStartedUsingNonStickyFeature`/`OnStartedUsingStickyFeature` in `frame_scheduler_impl.cc`). `rtc_data_channel.cc` attaches it for `kWebRTC`. The tracked-feature list (`web_scheduler_tracked_feature.mojom`) is the set of features that *can* register; it is not itself a list of opt-outs, and it contains no Native Messaging or extension-port entry. | No Native Messaging code path in these sources attaches the opt-out. |
| What is throttleable at all? | `FrameSchedulerImpl::CreateQueueTraitsForTaskType` makes only timer-type queues (`kJavascriptTimerDelayed*`), idle tasks and content-capture throttleable and intensively-throttleable; immediate timers are not throttled. | Throttling is a property of timer/idle task types. Which task type carries an extension message dispatch was **not** verified in source (inferred only); the probe below measures it. |
| Worker lifetime | Chrome lifecycle documentation: `connectNative()` keeps an MV3 service worker alive (Chrome 105+). | This concerns the **worker**; the design's port lives in a connector **tab**, so it does not carry the design. |

### 1.2 Same-build timer probe (run; non-Bluetooth)
`timer-probe/` (disposable extension, a Python host that only emits an event every 10 s, driver; tests in
`scripts/test_timer_probe_analysis.py`). The driver launches its **own** Chrome in an isolated `--user-data-dir`, registers the host
manifest only inside that profile (the shared manifest directory and the approved signed fixture are untouched), opens a
port tab, a port-less control tab and an ordinary tab, closes every DevTools client for the idle interval and reads the in-page
logs afterwards. macOS console owner was `raiden`, the same account that ran it.

Result (`macos/timer-probe/result-540s.json`, Chrome **156.0.8078.48** as reported over CDP, 540 s idle, 565 s to read-back):
- Port tab and control tab have **identical** timer-gap sequences: about 2 s early, then a 47 s step and gaps of 59.996 to
  60.003 s. The 47 s gap ended at 345.5 s of page time, so throttling began about 5 minutes after hiding on macOS. Intensive throttling engaged in both, with the port open (median 59 997.8 ms) and without it (59 997.75 ms).
- The port tab received **57 of 57** host events, none missing, maximum lag 4 ms, median 1 ms, while its timers fired once a minute.
- Neither tab disconnected; no freeze or discard was observed in that interval (this is an observation, not a freeze/discard exemption claim).

Residuals: no external confirmation of "no DevTools client" was taken during the idle interval (it rests on the driver closing its only browser-level WebSocket before the sleep and
attaching to pages only before the +25 s setup check and after the interval); window visibility/occlusion was not recorded; the driver's pid-scoped kill fallback could not work (no pid was found), so
cleanup relied on `Browser.close` and no after-run process listing exists for the profile.

Limits: one run, macOS, Chrome 156 only (Windows is section 1.6; 155 was not run: source inspection only); both probe tabs were hidden from creation and never visible; only
4 gap samples after throttling began; the browser's own command line was not captured (the pid lookup returned nothing), so the flags are
only those the driver passed (`--user-data-dir`, `--remote-debugging-port`, `--enable-unsafe-extension-debugging`, `--no-first-run`,
`--no-default-browser-check`; no throttling or backgrounding flag); the probe page is not the production connector.

### 1.3 Freeze and discard (separate from timers)
`discard_eligibility_policy.cc` (both tags): for **URGENT** and **PROACTIVE** discard a page whose main-frame URL is not http(s),
`chrome:` or `data:` is protected (`kNotWebOrInternal`), which covers a `chrome-extension://` connector tab. But **EXTERNAL** and
**FROZEN_WITH_GROWING_MEMORY** discards return "Always allow discards" and are not subject to that protection. So the connector tab is
not discard-proof, and the RFC's position (no discard/freeze exemption is claimed; the worker observes `frozen`/`discarded`) stands.
OTP-08 (#90) must still test forced freeze/discard and memory-pressure recovery; freeze policy was not studied in source here.

### 1.4 Effect on the earlier "intrinsic exemption" concern
The source evidence and the probes both indicate that, on Chrome 156.0.8078.48 on macOS and on Windows (sections 1.2 and 1.6), an open Native Messaging port does not exempt a hidden
tab from timer throttling and that host events are delivered without depending on timers. It does not establish that for Chrome 155 (not installed on either machine; covered by the pinned sources only), and it does not by itself change any matrix row. **macOS ST-15 (OS foreground) is not waived or implied:** the current
evaluator still returns BLOCKED for M-09/M-10 because the legacy raws have no foreground evidence. A re-adjudication under the approved clarification (section 1.5) would be a *proposal* for W-11/W-12/M-09/M-10 (which also needs current evidence for macOS ST-15), not an automatic
status change; the evaluators' `formal_otp23_acceptance=false` flag is unchanged by it.

### 1.5 Owner decisions on the background wording (2026-10-27, operator-captured, in order)
**First (earlier): "REQUIRE MORE EVIDENCE FIRST", no wording approved.** The owner answered the background-wording question that way and approved nothing
(verbatim: `evidence/investigations-2026-10-27/owner-approvals/2026-10-27-otp23-background-wording-decision.md`). The sentence drafted earlier in this pass was kept only as an unapproved draft and was not used.

**Second (later): APPROVE CLARIFICATION, OTP-23 only**, after the same-build probe evidence on Chrome 156.0.8078.48 on both macOS (1.2) and Windows (1.6) was presented
(verbatim: `.../owner-approvals/2026-10-27-otp23-background-clarification-approval.md`). The approved text, now in the RFC (Status paragraph and the OTP-23 "Background-criterion clarification" paragraph):

> Event delivery through the architecture’s Native Messaging port is not a timer-throttling exemption. The criterion continues to forbid timer-throttling opt-outs, artificial keepalive, audio, WebRTC, exemption flags and DevTools clients during idle; ordinary timers and the port itself are permitted. If a same-build comparison shows the port tab escapes intensive timer throttling while the port-less control does not, report the criterion unsatisfied.

It is the owner's sentence, not the earlier Opus draft. **Chrome 155 was inspected in source only; no fresh 155 probe was run** (not installed on either machine).
Not changed: cold-idle and prewarmed runs of at least ten minutes on both platforms, the strict-run requirements, foreground evidence (**macOS ST-15 is not waived**), the freeze/discard requirements
(no exemption claimed; OTP-08 owns them), evaluator provenance and `formal_otp23_acceptance=false`, and the NO-GO rule. **No row is promoted automatically:** W-11, W-12, M-09 and M-10 stay BLOCKED and their blockers now say
the criterion text is clarified but the rows are not promoted. OTP-24 is not approved and no gate is GO.

### 1.6 Windows same-build timer probe (run in the logged-in interactive session)
Setup facts (`windows-chrome/interactive-session/`): `query user` shows account `yuriy` on the `console` session 1, state Active; from inside an interactive (`/IT`) scheduled task the input desktop
was accessible (not locked), integrity Medium. The harness's own `ChromeRun` started Chrome there and reported **Chrome/156.0.8078.48** (Program Files install); no Chrome was installed or changed to obtain 155.
The probe driver is the same `timer-probe/run_probe.py`; Windows launches its own Chrome child in an isolated profile and registers the unique probe host name under HKCU (removed afterwards; verified absent).

- **Invalid first run (retained, `windows-chrome/retained-failed-attempts/timer-probe-run1-*`):** both probe tabs stayed `visible` for the whole interval (median gap about 1 011 ms in both), so it did not test the hidden case. Cause: a plain
  `Target.createTarget` did not put the tabs in the background on Windows. The driver now creates the probe tabs with `background: true` in the same window and aborts when a tab is not `hidden` at setup (macOS call unchanged).
- **Valid run (`windows-chrome/timer-probe/result-540s-hidden-tabs.json`):** both tabs `hidden` at the +25 s check; 540 s idle, 565 s to read-back. Port tab and port-less control show the same gap sequence (about 2 s,
  then one 19 s gap ending at 78.3 s of page time, then gaps of 59.99 to 60.00 s). Intensive throttling therefore began about **59 s after the tabs were hidden on Windows**, much earlier
  than the 5-minute rule the analysis assumes (the analysis counts only samples after the 300 s mark, so its verdict is unaffected); median 59 998.9 ms (port) and 59 998.8 ms (control), 5 throttled samples each. The port tab received **57 of 57** host events, max lag 1 ms, no disconnect.
- Limits: one run, Windows 11 and Chrome 156 only (155 covered by pinned sources only); few throttled samples; "no DevTools client during idle" rests on the driver closing its only browser-level WebSocket before the sleep (no external confirmation);
  occlusion was not recorded; the flags are those the driver passed (`launch_flags_as_passed` in the result), the browser command line was not captured.
This is the additional evidence the owner's "require more evidence first" answer asked for; it was followed by the owner's approval of a narrow clarification (section 1.5). It promotes no row.

## 2. Windows W-07 / W-08: the access_denied setup failures

**Scope of what was observed.** Sections 2.1-2.4 are **direct launches of the host from an SSH logon on the existing administrator account**
(synthetic bytes, two physical phones). Section 2.6 adds Chrome-launched runs in the logged-in interactive session on Chrome 156.0.8078.48. Both are the existing
administrator account only; other accounts are not covered.

### 2.1 API status: captured versus inferred
- **Captured** (diagnostic binary, `windows-access-denied/ctl-*`, `diag-binary.txt`): immediately before each denial the host traced
  `services GattCommunicationStatus=0 protocol_error=none` and then `write characteristics GattCommunicationStatus=3 protocol_error=none`
  (3 = AccessDenied, no ATT protocol-error byte). No `os error` line and no HRESULT exist for these denials because the asynchronous
  operation completed normally and carried the status in its result. Passing connects traced `0/0/0` for services, write and notify.
- **Inferred, not OS-captured:** that the status belongs to `GetCharacteristicsForUuidWithCacheModeAsync(write UUID, Uncached)` rests on the
  code line that emits the trace and on the absence of the later `write properties=` line. In the earlier `direct-*`/`sweep-1` runs and in the
  retained Chrome-launched failures (retained binary `6cf72ed0…fcda`, no status tracing) the API was inferred the same way, from stage name,
  `retryable=false` and the missing trace lines; their raw status was not captured. Binary binding (`windows-access-denied/diag-binary.txt`): the retained binary `6cf72ed0…fcda` corresponds to `cab0438`'s `src/winrt.rs`, not to the current worktree; the earlier diagnostic build `186f920c…0a64` came from a pre-`cargo fmt` copy and is not claimed to be the final source;
  the final diagnostic build `e36328d2…2f4d` was built from the final `src/winrt.rs` (sha256 `d348d615…aed1d1`, identical on both machines). The only source change is three `trace!` lines and a helper.

### 2.2 Controlled comparison (earlier diagnostic build `186f920c…0a64`, not the final source; two physical phones; `0838be31` = OnePlus 10 Pro, `4603b134` = OnePlus 6T)
Each case: 45 s idle, then FIRST process connects the listed phone(s) and exits through stdin EOF, then SECOND process after the gap.

| Case | FIRST linked | SECOND connected | Gap | Result of SECOND |
|---|---|---|---|---|
| K1 | 10 Pro | 10 Pro | 1 s | **10 Pro denied** (single phone both times) |
| K2 | 10 Pro | 6T | 1 s | 6T connected |
| K3 | 10 Pro | 6T and 10 Pro | 1 s | 6T connected; **10 Pro denied** in the same process |
| K4 | 10 Pro and 6T | 10 Pro | 1 s | **10 Pro denied** |
| K5 | 10 Pro | 10 Pro | 12 s | connected |

Earlier direct two-phone runs (retained binary, original harness for A1-G, fixed harness for `sweep-1`): after a previous process had linked
**both** phones, a connect started 0-1, 1, 2 and 4 s later was denied for both (4 of 4 attempts); at 10 s (n=1), 15, 60 and 180 s it was not
(see the table in the evidence); every connect with no prior live links passed (6 of 6). Each threshold point is a single sample (one sweep,
4-10 s untested; K5 is the only 12 s point).

### 2.3 What this does and does not show
- **Observed:** in these direct-launch runs the **10 Pro** (K1, K3, K4; the 6T was never the repeated phone in a one-phone control, each control is n=1; the 6T was denied only
  in the earlier two-phone runs) was refused at its first characteristic
  query when a new process reconnected within about 1-4 s after the previous process had linked it, while a different phone (K2, K3) connected in the same second process. The
  earlier "both phones denied at once" cases are explained by both phones having been linked by the previous process. A 10-12 s gap
  did not show the denial. The two denials within one attempt returned about 20-31 ms apart.
- **Not shown (in the direct-launch runs of 2.1-2.4; section 2.6 adds Chrome-launched runs):** whether the refusal comes from Windows (stale per-device session ownership or connection teardown), from the phone's
  GATT server state for that connection, or from both; the controls are per phone, which separates a global state from a per-device
  one but not Windows from phone. They do not establish the cause, a threshold between 4 and 10 s, behaviour for other accounts, or Windows phone-side state; the Chrome-launched path is covered separately in section 2.6 (same pattern, n=1). No standing denial was observed in the direct SSH-launched runs; that
  is all that can be said about standing permission.
- The phone's own connection history (filtered `dumpsys` for the earlier g5/g10 runs; four data points; phone clocks UTC+7 and not
  synchronised) showed a link to Windows persisting about 13 s after a host exit. Raw, unfiltered `dumpsys` for both phones taken after the controls
  is saved separately (`phones/*RAW*`). A live link alone is not the trigger: the 10 s run found the link up and was not denied.
- Runs E, F and G (15, 60, 180 s) followed hosts that were not all cleanly closed: the prior host of F and G had been killed after the harness's
  70 s wait (the false `host_error` described in 2.4), a different exit path from a clean stdin EOF, so they are weaker controls than K5.

### 2.4 Harness findings
1. `DirectHost.reply()` discarded replies belonging to other concurrent requests: a healthy two-phone run waited 70 s and was reported as
   `host_error` (E, F), and the retained 2026-10-27 `LC-stdin-eof` recorded its second reply as `{"type": null}`. Fixed (`reply()` returns an
   already-received frame first); regression `DirectHostReplyOrder` was red before and green after. An out-of-order pair was not seen again
   on hardware.
2. The harness still maps a GATT AccessDenied (stage `access_denied`) to `error_class: permission_denied`. After these controls that label is
   misleading (the denial is not shown to be a permission). It is annotated here and in the matrix; changing the host's stage or the harness class
   is deferred to OTP-24/#87, so no classification code was changed.
3. New control tool `scripts/relaunch_control.py` (label-hash selection of phones; records replies and trace; no acceptance verdict).

### 2.5 Not run
A Chrome-launched comparison on Chrome 155 (not installed); the retained binary `6cf72ed0…fcda` through Chrome in the interactive session (the Chrome-launched runs below used the final diagnostic build, a new host identity that is not adopted for acceptance);
a one-phone control on the 6T alone (the single-phone cases are the 10 Pro, K1; later runs used `any1`/`all` selection and do not name the phone model). W-07 and W-08 stay BLOCKED; a retry is not a fix.

### 2.6 Chrome-launched comparison (interactive session, Chrome 156.0.8078.48, final diagnostic build `e36328d2…2f4d`)
Setup: logged-in console session, interactive `/IT` scheduled task, per-user exact-origin registration of the feasibility host with the final diagnostic binary (`register-host.ps1`), removed afterwards (registry key and install root verified absent).
- **`LC-port-disconnect` with two linked phones, three fresh Chrome runs (C1, C2, C3): all PASS** (`windows-chrome/lifecycle/chrome-lc-C1-C3`): two byte-proven links plus an active scan, teardown mechanism `stdin_closed`, host exit code 0, stdout drained, host and wrapper gone in 0.59-0.67 s, no problems; every traced discovery status was 0. C2 is **not** a rapid relaunch: Chrome start-up and scanning sit between C1's host exit
  and C2's connect (an estimated 9-10 s from the timestamps), outside the 1-4 s window, so its pass is consistent with, not contrary to, section 2.3.
- **Same-Chrome relaunch (`scripts/chrome_relaunch_control.py`): `otp23.closePort()` (the connector's literal `port.disconnect()`), first host exits (code 0, 0.587 s), 1 s gap, second host through the same connector:**
  - R1b: first host linked one phone (`c7de8e9c`); second host asked for both: the new phone (`5d8dd647`) connected, the previously linked `c7de8e9c` was **denied** (`access_denied`; trace: `write characteristics GattCommunicationStatus=3`, services status 0).
  - R2b: first host linked both; second host: **both denied**.
  - Direct-launch repeats with the same binary: F1b (first `5d8dd647`; second both: `c7de8e9c` connected, `5d8dd647` denied), F2b (both denied).
  - Single samples (n=1 each), gap 1 s only, label hashes rotate so no phone-model mapping is claimed for these runs.
  - The configured pause was 1 s; the measured gap from the first host's exit to the second connect request was about 2.4 s (R1b) and 1.6 s (R2b) (result files `first_host_gone_utc` and `connect_started_utc`).
  - **Host-hash binding limits:** the R1b/R2b result files do not record the host hash. For the Chrome-launched runs (C1-C3, R1b, R2b) the binding to `e36328d2…2f4d` rests on `diag-binary.txt` and on the per-user registration of that exact binary with `register-host.ps1` (the installed copy's hash output was not captured, and the registration was removed afterwards). The direct-launch repeats F1b and F2b do record `e36328d2…2f4d` in their `result.json`.
- **Reading:** the per-device refusal after a rapid relaunch is reproduced through Chrome-launched port closure as well as by direct launch, with the raw status captured as 3. That makes the retained Chrome-launched `LC-port-disconnect` failures plausibly the same mechanism
  (they ran back to back) but does not prove it, and it does not localise Windows versus phone-side state. These runs do not change W-07/W-08: they used a new host identity and were not adopted as acceptance evidence; the matrix stays BLOCKED.
- Retained failed attempts (`windows-chrome/retained-failed-attempts/`): three `LC-port-disconnect` FAILs from my own setup error (the connector extension folder was not copied to the probe tree), and one batch whose label-hash selection matched nothing (labels had rotated), so nothing connected.
- Fixture round 3: both fixtures were stopped through the documented marker and their device-side markers and instrumentation process are gone, but the adb client output was not captured (no JUnit `OK` line exists for this round).

**Product implication to carry forward:** a quick close-and-reopen of the bridge can produce `access_denied` for a phone linked moments
earlier although nothing about permissions changed. OTP-24 and OTP-07 need a distinct retryable classification, a bounded reconnect policy and a
physical retest (#87, #88).

## 3. macOS: production-shaped final-ID host, signing, authorization and recovery

**OTP-23 minimum versus OTP-25 (amended 2026-10-27).** The RFC originally required a host fixture "signed directly with CI-provided credentials and the final
bundle identifier". The owner approved, for OTP-23 only, accepting the **existing approved Apple Development-signed final-identifier fixture** as the minimum signing
provenance and deferring CI-provided and distribution signing (Developer ID, notarization, stapling, installer) to OTP-25. This is recorded in the RFC (Status, 2026-10-27),
the matrix (M-03 source/expected/blocker) and `evidence/.../owner-approvals/2026-10-27-otp23-signing-provenance.md` as an **operator-captured approval**, not a GitHub approval.
It does not approve OTP-24 and waives nothing: the observed prompt owner (M-03 expected text and M-04), allow/deny/recovery guidance, the existing-account unsigned dev-host
comparison (M-11) and every background gate (including macOS ST-15) are unchanged. M-03 therefore stays **BLOCKED**, now only on the prompt-owner observation, which
needs a person at the Mac (not available). The local identity is preserved; no new signing, key export or Bluetooth prompt was performed.

**Scopes inspected for context (read-only, no secret value read, no key access); no longer required for the OTP-23 minimum:**
- Repository Actions secret names: `KEYSTORE_BASE64`, `KEYSTORE_PASSWORD`, `KEY_ALIAS`, `KEY_PASSWORD` (Android).
- Environment `github-pages`: no secrets. The owner is a *user* account (no organization secrets; the organization-secrets endpoint
  returned 422).
- Workflow files: no `codesign`, `notarytool` or `stapler` step.
- **Not inspected:** secrets or keychains outside GitHub (for example the owner's own CI or cloud secret stores), repository variables, other repositories.
  So the finding is "no Apple signing credential is visible to this pass in those scopes", not "none exists".

**Designated requirement (read-only readback, `signing/designated-requirement-readback.txt`):**
`identifier "me.nagaev.veles.bridge" and anchor apple generic and certificate leaf[subject.CN] = "Apple Development: yuriy@nagaev.me (ZWYYS8X98S)" and certificate 1[field.1.2.840.113635.100.6.2.1] /* exists */`.
It pins the signing certificate, so a different (for example OTP-25 CI) identity changes the designated requirement; macOS may then treat the
bundle as a new Bluetooth client, which means a new owner-attended grant and a rerun of M-04..M-06. Per the repository's signing rules the approved
key is never exported; a CI identity would be a **new** key and certificate, not a copy.

**Same-state check:** the approved bundle still verifies with the same identifier, team, authority, signed time and executable hash
(`e7c716c5…eb91c`); CDHash `25bc7fc25e48c4470d4c3765b6a5f9b719ff381d` is recorded. No re-signing, no ad-hoc identity, no TCC reset, no uninstall, no
clear-data, no unsigned build, no new Bluetooth prompt. The TCC database was not read; the standing grant is inferred only from the earlier
owner-attended recovery run.

**Prompt ownership** remains unobserved (`unknown`), none was provoked, and **guidance text:** no System Settings/retry string exists in the
feasibility connector, host or drivers; the production owner is #88.

The earlier M-03 owner question is answered by the amendment above. A future CI identity (OTP-25) would be a new key and certificate, change the designated requirement,
and need its own revalidation and a new owner-attended grant.

## 4. Recommendation: a bounded OTP-24 increment (a recommendation only; not an approval and not release readiness)
**Status.** OTP-24 (#87) stays **not authorised**: the RFC decision is NO-GO until every matrix row is `PASS` or carries the account waiver, and #86/#87 say to keep #87 dependent on #86 unless the owner explicitly approves a narrower implementation gate.
Nothing in this pass approves, starts or sizes that work.

**Technically plausible, on the evidence.** Plain-Rust host launched through Native Messaging; two-phone synthetic sweeps 25/25 (Windows) and 26/26 (macOS); the scoped M6 concurrency adjudication; byte-proven lifecycle and teardown on macOS;
Windows `port.disconnect` with two links passing in an interactive session on a diagnostic identity; the Native Messaging port shown not to be a timer-throttling exemption on Chrome 156 (both platforms) and clarified in the RFC; the signing-provenance minimum satisfied under the amendment.
The known engineering finding to carry into the host is the per-device `access_denied` after a quick reconnect (distinct retryable classification and a bounded reconnect policy).

**What a bounded increment would contain, if the owner opens such a gate** (host code only, behind no release and no protected transport): the production headless host of #87 (protocol, handles, bounded state, cleanup), a distinct retryable classification for the recent-link refusal, measured window/endpoint hygiene (W-02/M-08),
connected-phase teardown checks (W-07/W-08) retested with the identity that is then chosen. Excluded: installers and signing pipelines, connector UX, background claims, real OTP data, cryptography.

**Release readiness is separate and not claimed:** OTP-23 is not satisfied, M-03/M-04/M-11, W-02/M-08, W-07/W-08, W-10, A-01 and the Windows strict-background re-adjudication (W-11/W-12; macOS M-09/M-10 were adjudicated 2026-10-27, section 5) remain open, and no protected-transport or real-OTP work is enabled.

**Minimum remaining owner action vs deferred owners (no duplicates; owners are those already in the #86 ownership map):**

| Item | Who acts | Where it is already owned |
|---|---|---|
| **One decision:** approve or decline a narrower OTP-24 implementation gate amendment (this is the only thing that lets host code start) | the owner | #86 (decision), then #87 |
| Prompt-owner observation, Settings/retry guidance (M-03 expected text, M-04) | a person at the Mac | #88 / #89 (early viability stays in #86) |
| Unsigned dev-host comparison on the existing account (M-11), CI/distribution signing (OTP-25) | implementation owner | #89 |
| Connected-phase hygiene (W-02/M-08) and connected teardown (W-07/W-08) | implementation owner | #87 (connector side #88); exit #94, #102, #103 |
| Strict background re-adjudication (W-11/W-12/M-09/M-10), incl. macOS ST-15 | implementation owner | #90; exit #94, #103 |
| Radio-off/adapter-absent states (W-10) | implementation owner | #102 (error paths in #87/#88) |
| Duplicate-callback / CCCD-MTU physical cases (A-01) | implementation owner | #120 -> #102 |

Nothing in this table is a new task or a new owner; the retained-binary-versus-new-identity question is carried by the W-07/W-08 rows in #87, not as a gate here.

## 5. Follow-up integration (2026-10-27, later in the day): Windows awaited-close causal comparison, M-09/M-10 adjudicated PASS, checksum repair
**Decision unchanged: NO-GO.** 18 PASS / 11 BLOCKED / 3 account-waived (W-01, M-01, M-02). Every record below has an evidence folder and the review
reports are copied byte-identically in `native-bridge/feasibility/evidence/integration-2026-10-27/` (with exact source/scope/fingerprints in its
SOURCE-SCOPES.md). Nothing here is a formal final review of the integrated tree: the parent performs that before commit/push, and the R-01 ledger entry
for THIS phase stays pending until then (older R-01 history is not invalidated).

**Windows awaited-close comparison (evidence `windows-cleanup-2026-10-27/`, FINDING.md §1-§6).** The unawaited-close defect (every WinRT disconnect
retired its close onto a detached worker against `std::process::exit`; the bounded shutdown join always saw an empty map) was fixed in source and
physically compared with ONE binary in both arms: first host links both phones and exits; second host reconnects after a gap gated from the common
`disconnect requested` epoch anchor. Awaited arm 3/3 clean (both phones `connected`), legacy arm 3/3 denied (`GattCommunicationStatus=3
access_denied`), measured gaps 1500/1501 ms inside tolerance, first-host close cost only 0-2 ms, no retries and no delay claim. **At the tested
point** (direct launch, this PC, existing administrator account, the two approved phones, 1.5 s anchor-matched gap, n=3) the outcome was decided by
the host's close discipline. **This narrows, and does not erase, section 2:** section 2's "access_denied cause UNPROVEN" narrative stays as written
for everything it covered - the comparison does not prove a general cause for all Windows denials, no threshold, no CCCD involvement (S-2/H-3 are
untouched), the Chrome-launched path is untested on the adopted identity, W-07/W-08 remain BLOCKED pending their own acceptance rerun, and the
crash/kill-restart window still needs the distinct retryable classification + bounded reconnect policy (#87/#88). The retained first attempt
`compare-1` (zero links from a driver selection-token defect) is preserved as a failure record; the strengthened protocol rule and its pure tests are
review fix I-A (`scripts/test_windows_closejoin_compare.py`, 24 tests; exact-2 first-phase connections, both second-phone attempts, measured-gap
tolerance; offline `--recheck-dir`), and the saved raws were re-evaluated with the stricter rule into the NEW derived artifact
`VALIDITY-RECHECK-20261027.json` (compare-1 6/6 invalid, compare-2 6/6 valid) with no raw rewritten. The 6T fixture-account story was corrected
per review I-B (observed: no JUnit result, test process outlived the first force-stop, ended after both force-stops, cause UNPROVEN; the
"four stale remote instrumentation sessions" text was an operator inference from stale LOCAL adb clients, and the app-data uid wording was retracted;
the same 6T passed JUnit in the earlier macOS prewarm round).

**macOS M-09 (cold) / M-10 (prewarm) promoted to PASS under the owner-approved 2026-10-27 background-criterion clarification** (evidence
`post-grant-2026-10-26/strict-mac-20261027/`, adjudications `-v2.json`). Basis: fresh strict runs (cold interval 719.752 s, prewarm 717.76 s, 0 CDP
calls during idle), trusted-platform finalizer (F-1: contradiction between trusted and recorded platform BLOCKS, regression-tested), after-run
read-only provenance capture (F-2: run-tree driver/evaluator byte-compared; registered NM manifest pins executable `e7c716c5…eb91c` = the owner-approved
signed fixture; teardown recorded), wording corrections (F-3: payload identical by design; gate confirmed at poll 1 with no click claimed), an
independent Opus fix verification of F-1/F-2/F-3, and the same-build port-vs-control timer probe on Chrome **156.0.8078.48**. Residuals recorded in
the rows: (a) launch context operator-inferred; (b) phone identity operator-bound, payload identical by design, distinctness = serial binding + two
handles; (c) linear-drift assumption in the ±2.39 s clock bound (margins ≥29 s); (d) the cold 10 Pro JUnit log is partial and nothing is claimed from
it. `formal_otp23_acceptance=false` is script policy; the matrix adjudicates. The v1/14-check and reevaluation-at-head records stay as history.
W-11/W-12 were NOT converted by the clarification and still need their own disposition (their raws predate the session/page guards; the latest
Windows background evidence is from the current 156.0.8078.48-era runs, and the retained-binary-versus-new-identity question is still open).

**Chrome scope note:** all current-phase browser work is on the latest Chrome **156.0.8078.48** (Windows and macOS); Chrome **155** exists on no
machine and is covered by source inspection only; no old-build rerun is planned. **Check repair:** the strict-mac-20261027 folder's checked manifests
are fixed cleanly - v1 is preserved as `SHA256SUMS-before-review.original` (the review fingerprint `3487728c…` with the pre-append README bytes),
`SHA256SUMS-v2` (`66ba8956…`) stays as the review-time snapshot, and the nameless `SHA256SUMS` is regenerated as the active manifest covering every
current file (`SHA256SUMS-NOTES.md` documents the naming). The windows-cleanup folder manifest was refreshed after the I-A/I-B edits and the derived
artifact; every raw file is byte-unchanged (`.gitattributes` `* -text -whitespace` guards both folders and the baseline post-grant manifest verifies
unchanged). **RFC:** no amendment beyond the already-captured recording is needed; the bounded-implementation recommendation (section 4) still needs
its own explicit owner gate amendment and is not a release GO.

## Remaining human actions
1. A person at the Mac to observe the prompt owner (M-03 expected text, M-04) and approve any unsigned-host authorization (M-11); the Mac attendee is not available now.
2. UPDATED 2026-10-27: the macOS half of this item is done - M-09/M-10 were adjudicated PASS with recorded provenance and an independent verification (section 5). Still pending: the W-11/W-12 strict-background disposition on Windows (same clarification; latest Chrome 156.0.8078.48 background evidence; the retained-binary-versus-new-identity question is open).
3. Note (not a new gate): the Chrome-launched Windows passes used the non-adopted diagnostic identity `e36328d2…2f4d`; nothing is promoted on them, and a retained-binary `6cf72ed0…fcda` Chrome run in an interactive session has not been made.
4. Optional: a fresh Chrome 155 probe (not installed anywhere); 155 is covered by source inspection only.
5. NEW (2026-10-27): the parent's final local gates + final review of the integrated tree, commit and push (R-01 new-phase ledger entry pending until then).
(Decided on 2026-10-27: signing provenance, section 3; background criterion clarification, section 1.5. Section 4 recommends, and does not authorise, a bounded OTP-24 increment.)

## Model provenance
`provenance/followup-session-model-provenance.json` was produced by reading the Claude Code transcripts on disk: sessions `52694ac4-…` and
`0631948e-…` (the follow-up continuation, Claude Code 2.1.310 then 2.1.311) show `claude-sonnet-5-5` on every model-produced assistant message
(the continuation also has one harness-generated `<synthetic>` entry). The independent reviewer `2fe20385-…` shows `claude-opus-5-5`. Counts differ
from the earlier `session-model-provenance.json` (234 at its capture) only because transcripts grow.

2026-10-27 phase (section 5): the Windows comparison + review-fix work and the Mac finalize work were done by GLM-5.3-flash sessions (implementer
model set by the owner; opencode-db metadata captures in `native-bridge/feasibility/evidence/integration-2026-10-27/provenance/`, including the
`ses_e8061b402ffe…` integration session that performed this follow-up integration); the Opus minimum-fix reviews and the final M-09/M-10 fix
verification are local Claude Code `claude-opus-5-5` sessions (`2fe20385-…`); the coordinating parent session (gpt-6.1-sol) is not the implementation.

## 6. Package-1 final integration (2026-10-28, evening): W-02/W-07/W-08 promoted, 23 PASS / 6 BLOCKED / 3 waived

The package-1 phase (started 2026-10-28 with the connected-phase and P2 retake `BLOCKED criterion_conflict`
records) is now COMPLETE for the Windows rows, with NO-GO and OTP-24 unauthorised — this section records the
truthful current disposition and points to the NEW evidence; every older section above stays as history of its
time.

- **Rows promoted:** W-02 (separate owner-scoped adjudication of the six-sample connected census under the
  2026-10-28 owner-approved clarification, applied W-02-only), W-07 (literal `port.disconnect()` with two
  byte-proven links on the ADOPTED identity `8521de4d…`, own exit 0, releases 2/3, join 2/2, corrected phone
  bounds +2322/+2406 ms with wholly-in-bound uncertainty intervals), W-08 (the same Chrome-provenance event's
  stdin-EOF semantics + the corrected tab-close recompute +2304/+2310 ms). Final counts: **23 PASS / 6 BLOCKED /
  3 waived**; open rows exactly W-10, M-03, M-04, M-08 (owner-deferred), M-11, A-01; the interim 20/9/3 state
  and the GPT quota block are recorded history, never the final blocker.
- **Independent review chain (actual roles):** GPT windows-strict (`520c2edd…`), GPT P2/B5 (`15a28c80…`), GPT
  fix verification (`ee342efa…`), GPT v3-final recheck (`217e6f2f…`) — all `openai/gpt-5.6-sol high`;
  GPT route quota-blocked mid-day (safe record only). The FOCUSED v4 final verification is the **user-authorised
  Opus 5.5** route (Claude Code `claude-opus-5-5`, session `1b447229-ceaa-4cee-a5be-2181dfdd8838`, report
  `d85d4f57…`: sole remaining exact-branch defect CLOSED at 1001-digit-int scale; validator overflow boundary
  behaves as specified; v4 values re-derived; recommends W-02/W-07/W-08 PASS). NO retroactive Opus authorship
  of the GPT reports; implementation throughout by actual `ollama-cloud/glm-5.3-flash` (0 subagents).
- **Evidence home (published, own manifests):** `native-bridge/feasibility/evidence/package1-integration-20261028/`
  (Opus report byte-identical + final model metadata + provenance map resolving every `/tmp` citation of the
  chain, incl. the v4 record's cited v3-final report, and gate-log copies with exact sources). Derivations:
  `package1-b5-v2/v3/v4-20261028/` (v4 = the promoted record: exact-branch `_num` contract, `numeric_range`
  overflow boundary, fail-closed guards, self-pinned 7-entry code list); raw package evidence
  `package1-p2-20261028/` (34/34) and `package1-connected-2026-10-28/` (181/181 + curated producer snapshot
  238/238, original capture manifest preserved) stay immutable.
- **M-08:** owner-DEFERRED ("Cannot restart Chrome now"; attempts failed at `Extensions.loadUnpacked` before
  BLE with zero peers; first standalone setup capture overwritten — unmeasured, never reconstructed). Not a
  measurement; no Mac action taken. **Decision:** NO-GO; OTP-24 not authorised; release readiness not claimed.
