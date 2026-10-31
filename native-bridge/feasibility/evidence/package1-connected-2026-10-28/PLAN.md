# OTP-23 package1 connected-phase batch — pre-declared plan (2026-10-28, written before any measured run)

Owner task: OTP-23 blocked package1 rows W-02, W-07, W-08 (Windows connected-phase lifecycle/hygiene).
Model: ollama-cloud/glm-5.3-flash (implementation), independent Opus review by parent, per RFC.
Repo state at declaration: branch `contract/otp23-desktop-alignment`, HEAD `8f365fce0d31f975610c235085a95b9136ac25e2`, clean.
Chrome: only the latest installed stable on the PC (Program Files install), exact version recorded per run from
CDP `Browser.getVersion` (`chrome_version_actual`) — no older Chrome is used and no launch flags are added for
acceptance runs (Windows direct-exe NM launch is DISABLED by default in Chrome 156; the default cmd path is
the production shape).

## Pre-declared observables and bounds (chosen BEFORE the runs, never post hoc)

1. **Link-release bound (W-02/W-07/W-08 teardown).** For every teardown trigger, the phone-side
   `dumpsys bluetooth_manager` ConnectionState history must show `DISCONNECTED` for the central's masked
   address **within 30.0 s** of the host's own traced `exit code=0` line (PC epoch clock, host trace
   `disconnect requested epoch_ms`). Rationale recorded up front: prior direct-launch observation showed the
   Windows LE link surviving ~13 s after object close; 30 s is ~2.3× that margin plus clock-uncertainty
   margin. The phone↔PC clock offset is measured BEFORE the runs (`pc_phone_clock_probe.py`, ≥9 samples);
   its median offset and stated uncertainty are recorded per phone. A DISCONNECTED later than the bound is
   recorded as an actual and fails the pre-declared bound — it is not renegotiated afterwards. The host side
   is additionally required to trace every linked connection `released` before `exit`, with the host trace
   explicitly noting link-down as unconfirmed (objects closed; link down is proven by the phone history).
2. **W-02 window/endpoints observation (connected phase, not only at ready).** During the connected phase —
   after both links are byte-proven (distinct 16-byte proofs), while 4096-byte records are in flight and
   echoed, and with an active scan — the driver samples host/wrapper windows and endpoints. The observation
   mode is **sampled** (labelled `sampled`; no continuity claim). Each sample carries mandatory positive
   controls from the SAME enumeration: (a) the owned Chrome pid's visible top-level window(s), (b) the owned
   Chrome pid's CDP listener endpoint in the same TCP table read. A sample whose controls are missing makes
   the absence **unverifiable, never zero**. Fields that cannot be measured are recorded `unavailable`,
   never fabricated as 0. Counts are by kind: top-level visible, top-level hidden, message-only,
   children; endpoints TCP/UDP IPv4/IPv6 with owner pids. Session identity (observer/Chrome/wrapper/host
   session ids + active console session id) is recorded so the enumeration context is auditable.
3. **W-02 wrapper window criterion — owner clarification required before promotion.** Chrome 156 launches
   NM hosts via `%COMSPEC%` (`LaunchNativeHostViaCmd`, direct-exe feature DISABLED by default), so the
   `cmd.exe` wrapper may own ONE chrome-created never-visible console window ("historical wrapper hidden 1").
   The driver enforces: host owns zero windows of ANY kind; wrapper owns zero VISIBLE windows; host owns zero
   endpoints. A wrapper hidden console window is recorded. Because matrix row W-02 says windows are absent
   "owned by the host and its wrapper", a measured never-visible wrapper console window is a conflict to be
   **resolved by the owner** with the proposed wording: *"The plain host owns no top-level window (visible or
   hidden) and no network endpoint; the Chrome-created cmd.exe wrapper of Chrome's default Windows launch may
   own one never-visible console window; any visible window fails."* A pass under the driver rules does NOT
   silently promote W-02; the proposal goes in the package1 result report.
4. **W-07/W-08 own-exit criterion.** Chrome kills a lingering NM host ~2 s after port close. Host-gone alone
   is therefore invalid: the host's own trace must show `exit code=0`, every linked connection
   `released` before exit, the bounded `shutdown joined N/N` line (N = N) before exit, and no frames after
   closure with stdout ending at a frame boundary. `VELES_OTP23_DETACHED_CLOSE` must be unset (recorded and
   asserted per run). EOF-path provenance: Chrome-launched port.close and tab-remove are the Chrome-provenance
   EOF paths; the direct-process stdin-EOF runs are supplementary and are labelled non-Chrome provenance.
   Triggers are separately named (shutdown / port-disconnect / tab-close / failure-isolation / stdin-eof).
5. **Retained evidence.** Every failed or partial setup is retained as evidence; nothing is retried into a
   pass. Old wrapper-window results remain history (untouched evidence directories).
6. **Registration scope.** The fixed host binary `otp23-host.exe` sha256
   `8521de4d58bdba0858e717e3dca3bcc0850b272ba3c1544bda158afb9e60ae33` is registered per-user with a scoped
   manifest write (HKCU before/after recorded). No installer, no autostart, no network changes, no machine-wide
   writes. Registration is removed after the batch.
7. **Phones.** The two approved fixtures (OnePlus 6T `2be02e41`, OnePlus 10 Pro `864a3db0`) with the
   pre-approved APKs (hashes re-verified from the installed files before start; no reinstall unless a hash
   mismatch is proven). Phone binding is by serial; per-phone dumpsys captures are named by serial.

## What would be recorded as environment-only (never PASS)

- Console-session launch context: all runs launch Chrome and the host from the logged-in console session via
  an interactive (`/IT`) scheduled task; the observer session id and the active console session id are part
  of every sample. An SSH-context launch would make window enumeration non-auditable and is not used for
  these runs.
- Clock: phone↔PC offset measured per batch; PC host trace carries PC epoch ms; the two are correlated only
  through the measured offset (the M6 one-clock rule is respected: no desktop clock claims beyond this batch's
  measured offset file).