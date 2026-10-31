# OTP-23 M-11 read-only TCC log supplement (2026-10-29): FINDING

**Label: post-run, read-only, operator-captured.** The parent controller (OpenCode `gpt-6.1-sol`)
ran two `log show` queries over SSH on the existing Mac account (uid 501) after the M-11 curation was
frozen. The goal was to look for a retained record of the ORIGINAL signed-fixture prompt without any
TCC reset. No permission, TCC database or Settings state was changed. This folder does not modify
`../mac-m11-dev-2026-10-29/` (25 entries, `SHA256SUMS 071d14f1…`), and it does not fill any captured
`null`.

## Files (`raw/`, byte-identical to the operator files; see `provenance.json`)

| file | sha256 | content |
|---|---|---|
| `raw/scoped-tcc-history.ndjson` | `a71f932d…91c6` | `process == "tccd" AND eventMessage CONTAINS "me.nagaev.veles.bridge"`, last 4 days: 90 events plus footer, first 2026-10-28 22:44:17 +0700, last 2026-10-29 13:21:18 +0700 |
| `raw/dev-prompt-tcc-chain.ndjson` | `09d680b6…96b4` | `process == "tccd" AND eventMessage CONTAINS "95540.2"`: the 7-record chain of the developer request plus footer |
| `raw/tcc-log-supplement.json` | `6fb570ae…8767` | the operator's command list, raw numbers and limits |

Both stderr captures were 0 bytes. They are recorded by hash in `provenance.json` and not published.

## What it shows

**Developer prompt, corroborated through a separate OS channel.** msgID `95540.2`:
- service `kTCCServiceBluetoothAlways`
- subject `me.nagaev.veles.bridge.dev`
- requester `otp23_host-b86638eeb61c91b4`, pid 95540, the exact dev binary path

The chain has these records:
- `AUTHREQ_PROMPTING` at **05:29:58.733307Z**, inside M-11 attempt 2 (05:29:57–05:42:00Z)
- `AUTHREQ_RESULT` at **06:17:25.367351Z**, with `authValue=2, authReason=2, authVersion=1,
  desired_auth=0, error=(null)`
- `REPLY`

The result came 35 min after attempt 2's deadline and about 1 min before the owner's answer file
(06:18:28Z). That is consistent with the owner's reported "VelesOtp23Dev" / Allow. The values are kept
as logged; this is not a host trace, and no CoreBluetooth enum is derived from them.

**Original signed prompt: not found.** The only `AUTHREQ_PROMPTING` record returned is the developer
one. Records for the signed subject `me.nagaev.veles.bridge` are later authorization checks
(`AUTHREQ_ATTRIBUTION`/`AUTHREQ_SUBJECT`/`staticCode`), and they are **not** observations of the
original prompt. The returned data begins 2026-10-28 22:44 +0700, after the original grant. No
statement is made about older retention, and no universal retention claim is made.

## Consequences (no inference)

- **M-11:** supplementary corroboration of the developer prompt and the OS decision. The M-11 PASS
  rests on the manual adjudication (`../mac-integration-2026-10-29/adjudications/m11-final-adjudication.md`),
  not on this supplement, and the raw M-11 statuses stay as captured.
- **M-03 / M-04: unchanged, BLOCKED.** The original signed prompt owner is still unobserved. The
  distinct developer prompt and the current Settings entry do not discharge them, and no M-04 off/on
  toggle was approved or done.

## Privacy

The records are scoped to our own app:
- our identifiers
- the dev binary's code identifier
- Apple's `bluetoothd`
- the SSH session's responsible process

The paths are the already-published fixture and dev-host paths. There are no third-party app names,
other users, API headers, cookies or transcripts. Details are in `provenance.json`.
