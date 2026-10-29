# M-11 controller execution

The owner confirmed **"Ready at the Mac"** after approving the distinct developer-host test.

Claude Code's fresh physical-operator attempt (`c401a87c-93bc-4498-9de6-f3106797baa4`) was API-quota-blocked before tools or device actions. The parent controller is executing the **unchanged, independently approved scripts** directly. This is operational execution, not new implementation or an independent review.

- Capture implementation: Claude Code Opus 5.5, session `752e1974-0617-470b-a425-dfbda97ac728`.
- Independent execution approval: Claude Code Opus 5.5, session `7b4ee6ca-e48e-407b-93d0-43738aa7238b`, `/tmp/opencode/otp23-mac-m11-loop-verification-opus.md`.
- Controller: current OpenCode parent, `gpt-6.1-sol`.
- Reviewed capture SHA256: `976f61cf038a3d46543bf03a80e8ae3b55a83b9516519d631e31e50f7a40e02d`.
- Required operator conditions: actual per-user distinct dev registration; atomic nonce-bound owner JSON answer; classify actual final scan reply rather than stale post-init authorization; approved signed fixture unchanged; no permission choices automated.
- Owner permission decisions have not yet been supplied. M-04 toggles remain unauthorized.

## Setup observations

- `package-dev.sh` is not executable in the staged copy; direct `./package-dev.sh` exited 126 before running. Executing the same bytes with `bash package-dev.sh source-raw/otp23-host` succeeded. No source/signing change was made.
- Attempt 1 used the reviewed runbook's `--stage .`. Its captured manifest has `path: target-dev/VelesOtp23Dev.app/Contents/MacOS/otp23-host`, a relative executable path; Chrome on macOS requires an absolute path. `hello` returned synthesized `port_closed`; scan/prompt-watch was not reached. Attempt 1 is retained verbatim.
- Attempt 2 corrects only the operational `--stage` argument to `/Users/raiden/homepc/work/otp23-m11-dev` and uses a fresh output directory. It reuses the exact packaged developer binary. This is not a script edit or a signing change.
- No phone fixture was started for the comparison. The existing signed baseline includes discovery of both phones; this developer trial records authorization and scan-start results, without claiming a fresh discovery comparison when advertisements are absent.

## Attempt 2 owner observation and deadline

The owner reported the actual Bluetooth dialog name **VelesOtp23Dev**, then confirmed **Clicked Allow** for nonce `ac6b44f7-2857-416d-bc00-75e2aa44139d`. The parent wrote the documented answer JSON atomically, but the capture had already ended by its 720-second deadline. Its `owner_answer` remains absent and `pending_at_host_budget_not_denial` / `ended_by: deadline` are retained as measured. The late answer file is not represented as accepted by that run.

Attempt 3 is a fresh post-Allow observation of the exact same developer bundle and binary. The operational `--prompt-wait-s` is explicitly increased to 3600 seconds to allow owner confirmation; code, identity, registration scope and scan semantics are unchanged. A new prompt is not requested or manufactured.

## Attempt 3 completion and external owner report

Attempt 3 started at 06:21:17 UTC and ended at 07:21:19 UTC. Its actual native reply is `scan_state/status: started`, and its classification is `authorized_started`, while the coordination fields correctly remain `ended_by: deadline`, `owner_dialog_observed: null`, with a deadline problem.

The owner reported **No dialog visible** and **Still enabled, unchanged** for the original VelesOtp23PoC entry; those confirmations were received later. The proposed answer JSON was uploaded as a temporary file, but the guard `test ! -e m11-capture.json` prevented the rename after the capture had finished. Thus no answer file was accepted or inserted for attempt 3. The temporary payload and the local owner record are external, unaccepted observations; they are not represented as an in-run acknowledgement or proof of no dialog during the active window.

The read-only observation at 10:45:35 UTC verifies the approved executable hash is still `e7c716c5…`, strict codesign succeeds, and the distinct dev Native Messaging manifest is absent. All three capture JSONs retain their original statuses and problems. Independent review must adjudicate the actual launch/authorization/scan facts separately from the coordination-timeout outcome.
