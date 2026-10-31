# OTP-23 investigations — independent Opus review (read-only)

Reviewer: Claude Code session 2fe20385-5ef1-4622-bf94-00b8c0c6477e, transcript assistant model `claude-opus-5-5` (163 msgs), CLI 2.1.311, 0 subagents.
Scope: worktree .claude/worktrees/otp23-investigations (HEAD cab0438 + uncommitted diff + evidence/investigations-2026-10-27). No edits, hardware or GitHub actions.
Verdict: NO-GO unchanged. Harness fix correct. No status changes. 0 CRITICAL, 4 IMPORTANT, 6 MINOR.

## Verified
- SHA256SUMS 68/68 OK; matrix check OK (NO-GO); 313 feasibility tests pass.
- DirectHostReplyOrder: 2 of 3 tests ERROR against cab0438 reply(), all green after → red/green claim true.
- Raw Windows table matches lifecycle.json timestamps/traces: denied A2,g0-Y,g2-Y,g5-Y (gaps ~0-1,1,2,4 s); not denied g10-Y,E,F,G; 6/6 no-prior-link passes; fast discovery 124-176 ms.
- API attribution: only gatt_status(3) yields stage=access_denied with retryable=false (wait_failure gives retryable=true), and no "write properties" line → first GetCharacteristicsForUuidWithCacheModeAsync(WRITE) result Status==AccessDenied. Sound code-path inference; status/HRESULT not logged.

## IMPORTANT
I-1 Overclaims in doc §2 / W-07 text: "not phone-specific", "transient Windows refusal", "not a standing permission". Every denied attempt denied BOTH phones after BOTH were linked; no one-phone comparison (prior host links phone P only, then connect P+Q) → cannot separate per-device vs global state nor exclude a phone/server-side contribution. Evidence is direct launch from an SSH logon on the admin account only; Chrome-launched/interactive path untested. Correction: "observed on both phones; localisation (Windows vs phone, per-device vs global) unproven; no standing denial observed under direct SSH-launched runs".
I-2 W-07 matrix text says the intermittent AccessDenied "is characterised" — generalises a direct-launch reproduction to the original Chrome-launched failures. Say "reproduced and characterised for direct launch; retained Chrome-launched failures are consistent but untested". Threshold "~10 s" rests on n=1 per gap (single sweep); 4–10 s untested. Also fix "trigger.No" missing space; "few milliseconds" → ~20-31 ms.
I-3 Source applicability: the decisive scheduler files are `main-` snapshots, not pinned. Pin frame_scheduler_impl.cc (CreateQueueTraitsForTaskType: only timer/idle/content-capture throttleable) at 155.0.8059.85 AND 156.0.8078.48 (M-09/M-10 ran 156). Doc wrongly calls 155 "the version running on the Mac" for the measured runs. The mojom argument holds only via the explicit link that DisableAggressiveThrottling can be attached only to a WebSchedulerTrackedFeature registration — state it; the mojom list is not itself an opt-out list. Missing finding: discard_eligibility_policy.cc@155 protects chrome-extension:// tabs from URGENT/PROACTIVE discard (kNotWebOrInternal); only EXTERNAL and FROZEN_WITH_GROWING_MEMORY discard → doc's "Chrome may discard independently of the port" is inaccurate; flag Gate B/#90 "discard under memory pressure".
I-4 Amendment text not narrow/coherent: "Replace … by adding" contradictory; "registers no timers" bans ordinary production timers (timers are not exemptions) and would break OTP-08; "scheduler-tracked feature" mixes BFCache-tracked features with throttling opt-outs; extends to OTP-08 without scope; framing "not itself counted as an exemption" concedes the false premise. Also doc implies approval unblocks M-09/M-10 — false (HEAD evaluator ST-15 BLOCKED; rerun needed). Replacement (OTP-23 only):
> "Event delivery through the architecture's Native Messaging port is not a timer-throttling exemption. The criterion forbids opt-outs from timer throttling or freezing (audible audio, WebRTC, Web Locks, device connections, throttling/freezing flags, keepalives, DevTools clients); it does not forbid the port or ordinary timers. If a same-build timer probe shows the port tab's timers escape intensive throttling while a port-less control tab's do not, the criterion is unsatisfied and is reported with that evidence."

## MINOR
M-1 M-03 `blocker` still lists "no Developer ID … no notarization" contrary to the doc's own OTP-23 minimum → "no CI-provided signing credential/provenance; prompt owner not observed".
M-2 Signing: only repo-level secret names checked (not org/environment); `codesign -d -r-` (designated requirement) not captured; doc omits that a CI identity changes the DR → TCC re-grant and M-04/05/06 rerun, and the CLAUDE.md no-key-export constraint (new CI key, never export the approved one).
M-3 Phone history filtered with sort -u, raw dumpsys not kept, covers only g5/g10; "~13 s" = 4 points, unsynced clocks.
M-4 Provenance mismatch: doc says 234 assistant msgs, JSON says 241.
M-5 Harness still maps GATT AccessDenied to error_class permission_denied; annotate as misleading (host change deferred).
M-6 E/F prior host was killed after 70 s (no exit line), a different exit path from clean EOF; note it.

## Mac stop justification
Justified for Bluetooth/TCC/prompt/strict-foreground actions (need owner at the active console). Not established for the BLE-free timer probe: a dedicated --user-data-dir Chrome under the bundle owner with the sim backend needs no permission action, only a GUI session of that user. Record console owner (`stat -f%Su /dev/console`) before claiming impossibility. Windows: a scheduled task /IT can launch Chrome in a logged-in desktop; "needs a human" is overstated if the user is logged in.

## Owner question
"Do you accept, for OTP-23 only, the source-pinned determination (Chrome 155.0.8059.85/156.0.8078.48) that Native Messaging event delivery is not a timer-throttling exemption, using the replacement sentence above — or do you require the same-build timer probe first? Separately for M-03: (a) provision a NEW Apple signing identity as CI secrets (accepting TCC re-grant and M-04..M-06 reruns), or (b) approve deferring CI provenance to OTP-25 with the local Apple Development identity as OTP-23 minimum?"
