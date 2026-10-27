# Investigations 2026-10-27 (raw evidence index)

Adjudication and reading of these files: `docs/otp-23-investigations.md`. This directory adjudicates nothing; the matrix does.
Files are raw as produced (this directory's `.gitattributes` disables end-of-line conversion); `SHA256SUMS` covers every file.
It is a sibling of `post-grant-2026-10-26/`, whose attested `SHA256SUMS` is not modified.

| Path | What it is |
|---|---|
| `windows-access-denied/ssh-A1` | FAILED probe attempt (environment): host not registered in the SSH user's registry, no `--host-binary`. Retained. |
| `windows-access-denied/direct-A1, -A2, -C, -E, -F, -G` | Direct two-phone `stdin-eof` runs with the **unfixed** `DirectHost.reply` (the 2026-10-27 harness copy `otp23-scenarios-3dbe3db`). A2 = access_denied. E and F = false `host_error` caused by the reply defect (both `connected` frames are in the host trace). |
| `windows-access-denied/sweep-1` | Gap sweep (0/2/5/10 s) with the **fixed** harness copy in `C:\Users\yuriy\otp23-probe\fixed` (scripts from this worktree plus `src/identity.rs`). Same host binary sha256 `6cf72ed0…fcda`. |
| `windows-access-denied/chrome-P1` | FAILED (environment): Chrome did not publish DevToolsActivePort when launched from the SSH session. No Chrome-provenance comparison exists. Retained. |
| `windows-access-denied/ctl-*` | Follow-up one-phone / cross-device relaunch controls (K1-K5) and the two discovery runs, with the **diagnostic** host build (`diag-binary.txt`: hash, build, phone-label mapping). Traces contain the captured `GattCommunicationStatus` lines. |
| `phones/` | Earlier filtered connection-history lines for the first sweep (`grep CONNECTED/DISCONNECTED | sort -u`; covers g5/g10 only) plus **raw** unfiltered `dumpsys bluetooth_manager` for both phones taken after the controls (`*RAW*`), and the instrumentation logs of both fixture rounds (all `OK (1 test)`, stopped via the documented `otp23-stop` marker). Device clocks are UTC+7. |
| `chromium-source/` | Chromium files fetched from googlesource at tags `155.0.8059.85-*` and `156.0.8078.48-*` (scheduler, tracked features, RTC data channel opt-out, Native Messaging port, discard eligibility policy). No `main` snapshots remain. |
| `macos/timer-probe/` | Non-Bluetooth Native Messaging timer probe: 540 s result, host send log, driver output and the 20 s smoke run. Chrome 156.0.8078.48, isolated profile. |
| `signing/` | Read-only designated-requirement readback of the approved bundle. |
| `owner-approvals/` | Operator-captured owner records, verbatim with scope and limits, not GitHub approvals: the OTP-23 signing-provenance amendment (approved); then, in order, the background-wording decision ("require more evidence first"; nothing approved) and the later approved narrow clarification of the background criterion (Chrome 155 source-only; no row promoted). |
| `windows-chrome/` | Interactive-session Windows evidence (Chrome 156.0.8078.48): `interactive-session/` (query user, input-desktop check, harness ChromeRun smoke, Chrome install info), `timer-probe/` (valid hidden-tab 540 s run + 20 s smoke), `lifecycle/` (C1-C3 LC-port-disconnect passes; same-Chrome and direct relaunch controls R1b/R2b/F1b/F2b), `retained-failed-attempts/` (invalid visible-tab probe run, missing-extension-dir failures, stale-label control attempts). Host binaries and their source binding: `windows-access-denied/diag-binary.txt`. |
| `reviews/` | The four independent `claude-opus-5-5` review reports (byte-identical copies, in order, each labelled with the tree it evaluated; report 3 predates the background-clarification approval, report 4 reviews the final amended tree) and the reviewer's transcript-derived model metadata. Report 4 reviews the final amended tree (fingerprint `602a2fbf…7992e4`). |
| `gates/` | Logs of the forced Spotless rerun, the full Gradle gates, both raw Log guards, the harness suites, `cargo fmt/clippy/test`. |
| `issues/` | Full untruncated JSON (body and comments) of GitHub issues #86-#90 as read on 2026-10-27. |
| `macos/signed-bundle-readback-2026-10-27.txt` | Read-only codesign/hash readback of the approved bundle, repo secret names, CI grep result. |
| `provenance/` | `session-model-provenance.json` (first pass) and `followup-session-model-provenance.json` (all three author sessions including the committing session `b0c43fcb`, plus the reviewer), read from the Claude Code transcripts on disk, not self-report. |
| `scripts-used/` | The batch files and helper scripts used, as run. `assemble.py` copied the files above into this directory. |

Not run: anything on Chrome 155 (not installed), the retained host binary through Chrome in the interactive session, a 6T-alone one-phone control, any macOS Bluetooth action or prompt, any APK build or install
(already-installed fixture packages were used, so no signer comparison applied), any unsigned macOS build.

Privacy note: the raw `dumpsys bluetooth_manager` files in `phones/` are unredacted beyond Android's own masking (MAC addresses are already reduced by Android to their last bytes). They still contain
bonded-device names, device model names, app package/uid names and partial MAC suffixes; no account, e-mail, key or token was found.
