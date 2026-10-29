# OTP-23 Mac M-08 retake (2026-10-29): FINDING

Scope: the reviewed M-08 retake, approved by the independent micro-verification
(`otp23-mac-m08-fix-verification-opus.md`). It consists of the lsof listener self-test, then three
separate fresh Chrome-owned scenarios (shutdown, port-disconnect, tab-close). Each scenario ran in
a new unique attempt dir with its own phone run id on both fixtures, using the approved signed
fixture. Operator: Claude Code / `claude-opus-5-5` (see `provenance.json` for all roles).
Nothing here changes `acceptance/otp23-matrix.json`.

## Result

| scenario | driver (host side) | acceptance read-out (20 checks) | phone release proof (`mac_release_bound.py` a49b3450…) |
|---|---|---|---|
| shutdown-1 | PASS | all pass | **PASS** both phones, `target_evidence` |
| port-disconnect-1 | PASS | all pass | **FAIL**: 10Pro `release_after_last_exchange=false` |
| tab-close-1 | PASS | all pass | **FAIL**: 6T `release_after_last_exchange=false` |

So M-08 acceptance holds for shutdown only. Port-disconnect and tab-close do not have the required
external release proof. Every other check passed for all three runs:

- 6/6 usable connected-phase samples.
- Chrome positive controls held in every sample: 14 visible top-level windows, plus the CDP
  listener.
- The host owned 0 windows at any layer, and `tcp4=[]`.
- Host and Chrome were alive in every sample, and the context audit was usable
  (`launchctl_managername=Background`, ssh).
- Two distinct 16-byte link proofs, with an active scan.
- 14 lsof diagnostics lines per run, all `benign` (the foreign-nullfs warning) or `clean_stderr`.
- Teardown per mechanism:
  - shutdown: `shutdown_complete` last, exit 0, stdout drained.
  - port/tab: `stdin_closed`, no shutdown frames, exit 0, drained.
- Owned host gone in 0.004 / 0.004 / 0.113 s, with no survivors, no wrappers and no cleanup
  problems.
- Mac trace released count recorded as 0 (P0-A: not gated on Mac).
- Phone telemetry: final, after-stop, clean, not truncated, 0 dropped, exactly one peer
  (`576d2120c83dc839`), and one connect/disconnect per phone per run.

## Why the two release proofs fail (measured, not a host defect claim)

The analyzer requires each phone's mapped release interval to start at or after the host trace's
**global** last exchange message, meaning the last echoed record on either link. The host exits
only 77–81 ms after that message, while the in-band anchor intervals are 119–142 ms wide:

| run | last exchange (ms) | exit (ms) | gap | failing phone DIS interval | overlap |
|---|---|---|---|---|---|
| shutdown | 4059 | 4139 | 80 | none (margins +21 / +94) | none |
| port-disconnect | 5951 (handle c710…) | 6032 | 81 | 10Pro [5934, 6053] | −17 ms |
| tab-close | 5113 (handle 7185…) | 5190 | 77 | 6T [5085, 5227] | −28 ms |

The check therefore cannot resolve a release that happens within one anchor width of the final
exchange, and the outcome depends on where the interval falls (shutdown passed by +21 ms). The
reviewed analyzer has no handle↔phone attribution, so it cannot use the failing phone's own link's
last exchange, which was about 500 ms earlier. The result is recorded as FAIL. It was not retried,
and the analyzer was not changed. The other bound checks (within exit + 30 s, stop after disconnect,
stop ≥ bound + uncertainty) passed for all six phone records.

## Listener self-test

The test ran in a fresh process with its own `127.0.0.1:0` listener:

- **DIAG set:** `lsof_endpoints` returned exactly that port (`LISTEN`) while the foreign-nullfs
  warning was present. One verbatim diagnostics line was written, with mount snapshot
  `328d66e8…`, sanitized and with no account names.
- **DIAG unset (warning still present):** returned `None`.
- No `-w` was used and there were no mount actions.

## Files

- `stage/` holds the exact staged bytes and the provenance record.
- `helpers/` holds the selftest, the read-out and the runner.
- `listener-selftest/` holds the self-test outputs.
- `registration/` holds only the own plain manifest. It was created for the runs and removed after,
  and the directory's file set equals the pre-state.
- `<scenario>-1/` holds the Mac driver output, the argv samples, the phone docs and stop brackets,
  the acceptance read-out and the release-bound result.
- `curation/curation-map.json` lists the privately kept exclusions by hash.
- `SHA256SUMS` covers every published file.
