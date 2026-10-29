# OTP-23 M-11 unsigned developer host (2026-10-29): FINDING (raw published; adjudication DRAFT)

**Criterion (M-11):** the unsigned dev-registered binary is launched by Chrome from a disposable
profile; its TCC authorization and scan result are recorded and compared with M-03..M-06; account
class is recorded truthfully.

**Status:** the raw captures are published with their original statuses and problems unchanged. The
manual adjudication is a **draft** (`adjudication-draft.md`) awaiting independent review. No matrix
row changed (23/6/3, NO-GO).

## Who ran what

- **Capture script:** `m11_dev_capture.py 976f61cf…`, written by the Claude Code Opus implementer
  `752e1974` (M11-1 loop fix) and independently approved by Opus `7b4ee6ca`.
- **Operator:** the coordinating parent **gpt-6.1-sol** (controller). It ran the **unchanged** script
  because the fresh Claude Code physical-operator attempt `c401a87c` hit an API quota (429) **before
  any tool**. No Opus model operated M-11.
- **Script hash:** the hash in the capture is controller-**attested**, not self-recorded by the
  capture (`provenance.json`).
- **Data review:** fresh Opus `e30e474d`.

## Identities

| | approved signed fixture (unchanged) | M-11 developer host |
|---|---|---|
| binary | `e7c716c5…`, Apple Development, Team `7T39M26Z99`, `codesign --verify --strict` OK before and after every attempt | `c36894dc…`, the raw unsigned counterpart of the same build. Only the automatic **linker ad-hoc** signature (`adhoc,linker-signed`, TeamIdentifier not set). No Developer ID or Apple Development signing. |
| bundle / NM name | `me.nagaev.veles.bridge` / `me.nagaev.veles.otp23.plain` | `me.nagaev.veles.bridge.dev` / `me.nagaev.veles.otp23.dev`, `CFBundleName VelesOtp23Dev` |

## Attempts (raw: `attempts/`; timings: `controller/OPERATOR.md`, `controller/observation-timeline.json`)

| attempt | UTC | result as captured (unchanged) | reading |
|---|---|---|---|
| 1 | 05:26:31–05:26:32 | `hello` = synthesized `port_closed`; no scan | **Setup FAIL.** The reviewed runbook's `--stage .` produced a **relative** manifest `path`; Chrome on macOS needs an absolute one. Not an OS rejection of the unsigned host. |
| 2 | 05:29:57–05:42:00 | `hello ready` (Chrome-launched, disposable profile); scan reply `scan_failed / permission_pending` at +10.1 s; `pending_at_host_budget_not_denial`, `ended_by: deadline` (720.9 s), no answer accepted | The host emits `permission_pending` only when `CBManager.authorization == NotDetermined` at its 10 s budget. |
| 3 | 06:21:17–07:21:19 | `hello ready`; scan reply `scan_state started` at +1.0 s; `authorized_started`, `ended_by: deadline` (3600 s), no answer accepted | Authorized, **inferred** from the actual reply (scan start needs PoweredOn). There is no explicit enum readback: the dev host trace is absent. |

**Process defects (runbook only; the as-run bytes are kept immutable):**
- `package-dev.sh` was not executable: `./package-dev.sh` exited 126, and the same bytes then ran
  via `bash`.
- The `--stage .` relative-path defect above.

## Owner observations (external, post-run, never accepted by a run)

- **Attempt 2.** The owner reported that the dialog named **VelesOtp23Dev** and that they chose
  **Allow** (nonce `ac6b44f7…`). The answer file was written at 06:18:28Z, **36 min after** the 720 s
  deadline, and is not accepted. Published as
  `attempts/m11-attempt-2/…answer.json`, labelled post-run.
- **Attempt 3.** The owner reported "No dialog visible" (nonce `8a055ccc…`) about 3 h 17 min after
  the run ended. The controller's guard refused the rename, so it remains
  `attempts/m11-attempt-3/.owner-answer-8a055ccc.tmp`. It is **not** a during-run observation.
- **Original grant.** The owner read the original `VelesOtp23PoC` Settings entry read-only: "Still
  enabled, unchanged" (`owner-records/original-grant-owner-check.json`). This is not an observation
  of the original prompt.

## Account class

`owner-records/account-class.txt` (sha256 `a2bdc40c…`), captured 2026-10-29T11:01:19Z: read-only,
post-run, same account. `raiden`, uid 501 (the captures record uid 501), `id -Gn` includes `admin`,
`dseditgroup`: "yes raiden is a member of admin".

## Comparison and limits

| | signed fixture | developer host |
|---|---|---|
| launch | Chrome, disposable profile | same (attempts 2–3) |
| initial authorization | historic grant | `not_determined` |
| prompt | original prompt unobserved (M-03) | owner-reported "VelesOtp23Dev", Allow |
| after grant | `allowed_always` (traced) + `scan_state started` | authorized ⇒ `scan_state started` (reply; untraced) |
| discovery | `device_found` | **not measured**: no phone advertised; **not claimed** |

- **Dev grant:** it persists on the account; no TCC reset or toggle was done.
- **Cleanup:** only the owned dev manifest was registered, and it was unregistered (`all_veles_named
  []` after every attempt). Disposable profiles were removed.
- **M-03 / M-04:** untouched. The dev dialog says nothing about the original prompt's owner, and no
  M-04 off/on toggle was approved or done.

## Notes for any future M-11-style run (not applied to the as-run bytes)

- Pass **absolute** `--stage` and `--out-dir`. The relative paths also probably caused the missing
  host trace (`VELES_OTP23_TRACE` was relative).
- Run the packager via `bash package-dev.sh …` or `chmod +x`.
- Take the owner's dialog / no-dialog answer before the run window closes, and allow
  `authorized_started` to end the watch after a bounded grace.
- Self-record the script sha in the capture.

## Files

- `attempts/`: exact bytes. Answer files in it are post-run.
- `controller/`: `OPERATOR.md` and `observation-timeline.json`, exact.
- `owner-records/`: account class, original-grant check, M-11 owner approval.
- `as-run-code/`: script, packager, test, README and extension, from local copies matching the
  attested hashes.
- `provenance.json`
- `curation-map.json`
- `adjudication-draft.md`
- `SHA256SUMS`
