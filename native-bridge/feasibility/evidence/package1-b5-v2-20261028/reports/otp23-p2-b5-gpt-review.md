# OTP-23 package 1 P2 Windows/B5 final independent review

## Review identity and scope

- Independent read-only review authorized by the user. OpenCode session `ses_e7e49d2cbffepV653zHccm8N62`; provider `openai`; model `gpt-5.6-sol`; variant `high`; OpenCode `1.19.4`; session agent/mode `build`.
- Safe metadata captured from `/home/dev/.local/share/opencode/opencode.db` at `2026-10-28T08:59:58Z`: 26 assistant messages at capture, all `(openai, gpt-5.6-sol, high, build)`. No API headers, credentials, cookies, state tokens, error bodies, or transcript content were read or recorded.
- Repository HEAD remained `8f365fce0d31f975610c235085a95b9136ac25e2`; the existing dirty worktree and evidence were not changed. No source edits, hardware, Chrome, adb/homepc, GitHub, commits, canonical-row edits, or subagents were used. The only authored file is this report; test output was temporary/ignored.
- Scope is the Windows P2 connected retake, B5 phone telemetry association, and existing W08 tab-close support. W11/W12 were not redone. M08 is externally deferred under `/tmp/opencode/otp23-package1-mac-deferred.json`; no Mac action was taken.

## Verdict

**CONDITIONAL APPROVAL: no hardware rerun is needed, but the current B5 derived JSON is not publishable as the final analysis. Apply the minimum code fixes below and produce a new, checksummed v2 derived analysis from the immutable raw inputs.**

The physical facts remain decisive after correct clock conversion. W02, W07, and W08 each have a PASS recommendation once that non-hardware repair is reviewed:

| row | recommendation | basis / condition |
|---|---|---|
| W-02 | **PASS by separate adjudication** | Six usable connected-phase samples establish zero host windows in all four kinds and zero host endpoints in all four families. The one wrapper window is exactly one never-visible `ConsoleWindowClass`, with no wrapper endpoints. Apply only the exact owner-approved W02 clarification. Keep the raw `BLOCKED/criterion_conflict` record immutable. Publish corrected v2 sample brackets. |
| W-07 | **PASS after corrected B5 v2** | Literal connector `port.disconnect()` with two byte-proven links and active scan; own exit 0; `stdin_closed`; no shutdown handshake; distinct conns 2/3 released before exit; Windows joined 2/2; process gone; corrected phone releases are safely inside 30 seconds and before fixture stop. |
| W-08 | **PASS after corrected B5 v2** | The same Chrome-provenance event honestly proves the extension stdin pipe closed without a shutdown request; one event may satisfy W07's trigger and W08's resulting EOF semantics. Existing `run-tab-close-2` independently repeats the Chrome EOF lifecycle and two-phone release result. No extra physical repeat is material. |

This review does not authorize OTP-24 or claim package-wide GO.

## Blocking derived-analysis defects

### B5-1. Phone-minus-PC offset is applied in the wrong direction

`pc_phone_clock_probe.py:52-59` defines and records:

```text
offset_phone_minus_pc = phone_epoch - pc_epoch
```

Therefore `pc_epoch = phone_epoch - offset_phone_minus_pc`. Instead, `_telemetry_bursts()` uses `epoch + offset_ms` at `scenario_matrix_core.py:927-955`. The existing offset regression at `test_scenario_matrix.py:1682-1690` encodes the same wrong sign: its prose says a phone clock is ahead, but its expected value rewards addition.

The immutable `phone-burst-association.json` consequently reports wrong PC estimates and gaps (`+3764 ms`, `+2790 ms`). Independent subtraction gives:

| serial / phone | phone DISCONNECTED | median phone-PC offset | corrected PC estimate | corrected gap vs host exit `1793170763088` | uncertainty interval |
|---|---:|---:|---:|---:|---:|
| `2be02e41` / 6T | `1793170766131` | `+721 ms` | `1793170765410` | **`+2322 ms`** | `[+629, +4015] ms` |
| `864a3db0` / 10Pro | `1793170765686` | `+192 ms` | `1793170765494` | **`+2406 ms`** | `[+1482, +3330] ms` |

Both full intervals remain after own exit and far inside the predeclared 30.0-second bound. This is a derived-arithmetic defect, not a physical-data failure.

The old W08 analyzer has the same sign error at `package1_phone_bound.py:44-52`. Recomputing `run-tab-close-2` against its pre-run `clock-offset.json` gives 6T `+2304 ms` (`[+819,+3789]`) and 10Pro `+2310 ms` (`[+1388,+3232]`), still decisively within bound.

### B5-2. Three declared completeness guards fail open

Direct semantic probes started from a synthetic baseline that returns PASS. Each of these malformed variants also returned `PASS []`:

```text
missing_one_phone_run_id PASS []
missing_stop_pc PASS []
false_final_after_service_stopped PASS []
```

The causes are:

- `scenario_matrix_core.py:794-807` establishes one non-`None` shared run id but never requires every phone document to carry that same non-empty id.
- `scenario_matrix_core.py:895-903` checks the full fixture-hold window only when `epoch_pc_ms` is numeric; a missing PC stop epoch silently skips the guard and can still PASS.
- `final_after_service_stopped` is not validated, despite the completeness claim and the actual raw field.

The actual P2 raw documents do satisfy all three conditions: both have the exact run id `p2pd1-portdisconnect-20261028T0651Z`, `final_after_service_stopped=true`, and both stop markers contain phone and PC epochs. These are code false-pass risks, not contradictions in this run.

### B5-3. Connected-sample trace brackets use the wrong zero point

`Session.start()` records `host_launch_wall_ms` before `otp23.connect()` (`scenario_matrix.py:294-295`), while the host trace begins only after Chrome starts the native host. `probe_connected()` treats launch initiation as trace zero (`scenario_matrix.py:470-476`). In P2, every saved `trace_offset_ms_estimate` is 1718 ms late, so the raw `trace_bracket` annotations incorrectly place all samples after the final host output.

Using the trace's own absolute disconnect anchor (`1793170763078` at trace `9958 ms`) gives the correct trace origin `1793170753120` and corrected sample positions:

```text
linked-idle-active-scan  9371 ms  (rescan started at 9370)
send-in-flight           9473 ms  (sent 9472, message 9572)
echo-verified            9584 ms
send-in-flight           9686 ms  (sent 9685, message 9940)
echo-verified            9944 ms
after-probes-active-scan 9951 ms  (disconnect requested 9958)
```

Thus the samples do cover active scan, both 4096-byte transfers in flight, and both verified echoes after the two distinct 16-byte link proofs. The raw wall times and trace anchor are sufficient; only the derived bracket labels are wrong. Correct the correlation algorithm and emit corrected brackets in v2 without rewriting the raw JSON.

### B5-4. Stage/analyzer provenance needs a v2 binding

The run self-records the actual loaded acceptance bytes: `scenario_matrix.py=f8284c98...`, `scenario_matrix_core.py=527de610...`, and `chrome_validate.py=78d53ca9...`; these match the current reviewed files. That is adequate for the W02/W07 runtime path.

However, `p2-stage-manifest.txt` lists older core/test hashes (`scenario_matrix_core.py=b33b015b...`, `test_scenario_matrix.py=2183b847...`) rather than the run/current hashes (`527de610...`, `0ca7ff96...`). It also does not list `package1_phone_burst_telemetry.py`. Therefore the broad claim that all staged source and the B5 analyzer are bound by that manifest is not supported. The mismatch does not erase the run's self-hashed runtime source, but the final v2 derivation must pin its own analyzer and helper bytes explicitly.

## Minimum repair, no physical rerun

1. Change phone-to-PC conversion in `_telemetry_bursts()` and the older `row_epoch_ms()` analyzer to subtract a field named `offset_phone_minus_pc_ms_median`.
2. Replace the offset regression with a nonzero measured-offset test whose hand-computed expected PC epoch and gap prove the direction. Include positive and negative offsets.
3. Require every expected phone document to carry the same non-empty run id; require `final_after_service_stopped is True`; require both numeric fixture-stop epochs. Add one failing test per omission/mismatch.
4. Prefer a conservative bound assertion that records the entire uncertainty interval; for these inputs, assert its upper endpoint is below 30 seconds and its lower endpoint is after own exit.
5. Correct sample correlation by deriving trace zero from a trace absolute epoch anchor, or add a trace-start epoch. Add a regression with a nonzero native-host startup delay.
6. Generate new files such as `phone-burst-association-v2.json` and `tab-close-phone-bound-v2.json` from unchanged raw inputs. Record SHA-256 for the analyzer, core helper, clock probe/source contract, every input, and each output. Do not replace the old derived files.
7. Refresh the stage/source manifest claim so it distinguishes runtime self-pins from test/analyzer pins. Preserve the existing mismatching manifest as historical evidence.

No phone, Windows, Chrome, host binary, Rust runtime, APK, or fixture rerun is needed if the corrected v2 outputs reproduce the values above and the new regressions pass.

## Physical evidence assessment

### W02 connected census

- Chrome is recorded by CDP as `Chrome/156.0.8078.73`; the pinned driver launches no direct-native-host feature flag or policy override. The observed Chrome-created `cmd.exe` wrapper is the default launch shape. `VELES_OTP23_DETACHED_CLOSE` is recorded unset.
- Both distinct links passed distinct 16-byte byte proofs before the six samples. Active scan was restarted and remained active through the samples.
- All six samples are `mode=sampled`, usable, and have no probe or session problems. Observer, Chrome, host, and wrapper are all session 1, matching active console session 1.
- Every sample has host counts `top_visible=0`, `top_hidden=0`, `message_only=0`, `children=0`; all `tcp4/tcp6/udp4/udp6` slices are empty. Every sample has all endpoint families available, sees Chrome's visible top-level window, and sees its CDP listener from the same endpoint-table snapshot.
- The wrapper has exactly one hidden, never-visible `ConsoleWindowClass`, no other window kind, and no endpoint in any family. This exactly satisfies the approved text in owner record SHA-256 `89d255383b21272590f7c3b3cc3370e7b8f1e688b2445a65dba022d529f23fd4`.
- Recommendation: create a separate adjudication citing the immutable raw status and exact owner text. Do not turn the raw scenario itself into PASS and do not apply this clarification outside W02.

### W07/W08 lifecycle

Independent parsing of P2 `port-disconnect.host-trace.log` returns:

```text
mechanism stdin_closed
teardown problems []
released conn 2 @9960, conn 3 @9960
joined 2/2 within 10 ms @9968
exit code=0 stdout_drained=true @9968
shutdown/shutdown_complete frames []
```

The two release ids are distinct and precede exit. The driver records owned host/wrapper gone in 0.653 s, after-teardown host and wrapper `pid_alive=false`, no cleanup problems, and no surviving owned host. The event is both literal W07 `port.disconnect()` and the W08 no-shutdown stdin-EOF path; that is one observed trigger with two relevant semantics, not two claimed runs.

Existing `run-tab-close-2` independently has the same clean mechanism: distinct conns 2/3 released, joined 2/2, own exit 0, no shutdown frames, and host/wrapper gone. Its producer snapshot was captured post hoc, but its driver/core hashes match the run's self-recorded hashes and the snapshot includes `chrome_validate.py=5b258924...`. W08 does not rely on the old observer's endpoint census. Corrected old phone timing remains well within 30 seconds, so no extra tab-close run is material.

### B5 telemetry causal bound

Observed, not inferred:

- Each serial-bound retrieval has exactly one peer, one CONNECTED event, and one DISCONNECTED event; both peer prefixes are `c3a3dc8495dd5bc2`; both documents share the exact run id and fixture package.
- Both documents are `final=true`, `final_after_service_stopped=true`, `clean=true`, `truncated=false`, `peers_dropped=0`.
- Corrected releases occur about 2.3-2.4 seconds after host own exit. Each uncertainty interval is wholly after exit and below 30 seconds.
- Phone disconnect precedes its phone-clock stop marker by 39,614 ms (6T) and 44,863 ms (10Pro). PC stop occurs 42,851 ms and 48,166 ms after own exit, exceeding the required `30 s + uncertainty` margins of 31,693 ms and 30,924 ms.
- The fixture records no second peer/central. The host trace records object release as `link-down unconfirmed`; the phone telemetry supplies the later link-down observation.

Inference, scoped honestly: the run-id, serial retrieval paths, same peer prefix, pre-exit connects, post-exit disconnects, absence of another peer, and much-later fixture stops support association of both releases with this host teardown. The telemetry does not identify a Windows API call or server-stop reason as the cause, reveal UI state, or prove host payload bytes. Do not label the DISCONNECTED event itself as an observed `serverStop` or host-cleanup callback.

## Preservation and scope notes

- P2 `SHA256SUMS` verifies all 34 listed files now. Its own SHA-256 is `addf32b2f68474fc65447ddadf1ae38b86dbd8bf24b05f1b740aa177d529697d`.
- The P2 evidence package does not retain the real-Windows `WindowsMachineSmoke` transcript. The source contains the test-only `PROCESS_TERMINATE | SYNCHRONIZE` correction, and the producer reports 5/5 on Windows, but this review can independently claim only the source fix and the live P2 observer results, not reconstitute the missing smoke output. This does not block the rows because the measured run exercised the relevant real APIs successfully.
- M08 remains deferred by owner decision SHA-256 `ace5e31e99c25ee21b4d634208ef006af02624121624952e0c40238505086e99`. The retained attempts establish an extension-loading/browser-process failure before BLE and zero peers, not a macOS lifecycle measurement. The first setup record was overwritten with `--overwrite`; retain that as a transparent preservation gap and do not recreate bytes.
- The Mac evidence supports only the scoped observation that `Extensions.loadUnpacked` closed the tested Chrome sessions, including a minimal invalid-manifest reproduction. It does not prove a universal unpacked-extension defect, that `.49` caused the regression, or that an update will fix it. The approved signed `e7c716c5...` fixture and grant remain unchanged.

## Verification performed

- `python3 -m unittest test_scenario_matrix test_macos_poc`: **169 tests OK, 5 skipped**.
- `python3 -m unittest discover -s . -p 'test*.py'`: **444 tests OK, 5 skipped**.
- `python3 -m py_compile` on the eight scoped source/test files: PASS.
- Scoped `git diff --check`: PASS.
- P2 `sha256sum -c SHA256SUMS`: **34/34 OK**.
- Independent current-core trace probes: P2 port-disconnect and old tab-close both return no teardown problems.
- Semantic negative probes expose the three B5 fail-open guards above. The full green suite therefore does not validate the current B5 acceptance semantics.

## Fingerprints

### Current reviewed source

| file | sha256 |
|---|---|
| `chrome_validate.py` | `78d53ca98c839e78bccf404beab5fb978f421f485f7945e5578d09a8b71a62f6` |
| `macos_poc.py` | `c2a6a20c738720da3bd683c2a1eaf77839d410266f067dd52ee29ba1d3a35cd7` |
| `scenario_matrix.py` | `f8284c98b9dc200bf8ec83e09b4d040643a725e4ca637d5854b09494fc31ffc1` |
| `scenario_matrix_core.py` | `527de61027811319a3d72990b2cef5546c617e1fc0e151684e3e688ebd1d0a02` |
| `test_scenario_matrix.py` | `0ca7ff963b5a6270d85c479a4dad084ceac8f2fdd355aadafe1947870aff2704` |
| `test_macos_poc.py` | `3ac96dd4af0359903efae8578bdbd4fe24d237004a35105da258cb2a4994aac1` |
| `package1_phone_burst_telemetry.py` | `c6fcfd6eab1e90e9861232fa6d06ef2d8166e39b2b613e7e37fd77e12296663d` |
| `pc_phone_clock_probe.py` | `06f0de1b64775615f30abcf2582d04113601632da2006aad98b317a01637b629` |

### Principal evidence

| file | sha256 |
|---|---|
| P2 `port-disconnect.json` | `967f4c04af8ccd16086eb159e4323962bfc0dffd3574057b042e10941719b9c9` |
| P2 host trace | `22e0aecd49e12d987806d2ffbb4de563ba8713c62725a3d2d9052f921ac0ed7d` |
| P2 current derived phone association | `615c4558fed6ebf2b926afe45cd8c95c126b917b805057d358275004e2c9878d` |
| P2 clock probe | `1f675d4365cc5a46a5d566d9aa4ba51944d3b3965cc3962b1f764451db06700e` |
| P2 stage manifest | `fca6faac598e970b8beb86e1d106722e55d0bb31e315d0d737f17309cdcf763c` |
| old W08 `tab-close.json` | `ac8a2bad9b74150da644aeaf6d0d292810582f1f236bb4bf60fde573988038c5` |
| old W08 host trace | `53ec2e4746399e91d99a1c6cbb5fbcd87e40d7ab7250064789595422c3fc561e` |
| old W08 6T retry capture | `5b81ce49de95a06cb0fa0b00aed502a1b8843fa240b14d6c70b02d9cd634f869` |
| old W08 10Pro retry capture | `d596ddf3123f4d557f9b7620866d90ccd92c56292580715a7059acad2a33604f` |
| old W08 pre-run clock probe | `75ffac51d045ca3fc71c1ee448f8e09fce47810c46e35ce4181606df602a0d1e` |
| old producer snapshot manifest | `14b7aa71fab8046ded5ac52c0f0c4062e244df8a5b0c5f8c78e01e731676667d` |

## Final boundary

The correct next step is a source-only B5/correlation repair plus v2 derivation and independent rereview. Do not rerun hardware, edit canonical rows yet, rewrite historical JSON, broaden archiving, touch M08, or infer OTP-24 eligibility from this review.
