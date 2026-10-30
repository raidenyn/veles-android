# M-03 signed-host prompt observation — 2026-10-30

## Candidate disposition: M-03 PASS, subject to independent Kimi K3.1 Pro review

This is a **fresh observation on the existing administrator account**, not recovery of
the original historical prompt. The user explicitly approved an app-specific Bluetooth
permission reset and then requested a restart after accidentally closing Chrome.
`owner-record.json` records that scope and the exact replies as captured by the operator.

The only reset command was:

```text
/usr/bin/tccutil reset BluetoothAlways me.nagaev.veles.bridge
```

It succeeded once per attempt, with the bundle ID supplied. No global reset, signing
change, new account, or M-04 permission-toggle run was performed. The approved executable
remained SHA-256 `e7c716c5d8c9c05ac8aaa838d5bdbdcc0918f352c270cb330d8edb5befeeb91c`,
identifier `me.nagaev.veles.bridge`, Team `7T39M26Z99`, and CDHash
`25bc7fc25e48c4470d4c3765b6a5f9b719ff381d`; strict verification passed before and after.
Both Bluetooth usage descriptions are in `restart1/bundle-info.json`.

## Preserved attempt history

- `interrupted/result.json`: first attempt, **ERROR**, not credited. Its initial scan
  returned `access_denied`. The user subsequently reported closing Chrome and requested
  a restart; the final trace shows the host exiting. The cause of the first denial is
  not established. The injected abort answer is explicitly not an observation, and the
  resulting CDP session-not-found exception and cleanup records are retained.
- `restart1/result.json`: Chrome `156.0.8078.74`, fresh disposable profile, host PID
  **15918** directly parented by Chrome PID **15893**, hello ready. Host trace records
  `not_determined`, then `permission_pending` at its ten-second budget. The host and
  port were retained while waiting for the attendee; no late response was rewritten
  into the initial scan result.
- User report, bound into the waiting capture at **02:26:04Z**, was exactly
  **“ValesOtp23PoC. Clicked allowed”**. The spelling is preserved, not silently corrected.
  The bundle's separate readback says `CFBundleName = VelesOtp23PoC`.
- On the same port, the next scan started, stopped, and returned `shutdown_complete`.
  That trace does not itself print an `allowed_always` transition. A separate fresh
  Chrome/host launch, `restart1/verify-grant.json` and `.trace.log`, directly records
  **`authorization ... allowed_always`**, scan started/stopped and clean exit 0.

## Independent OS channel

`restart1/signed-prompt-chain.ndjson` contains nine OS records plus the log tool's footer,
selected by `tccd` and exact request-ID string `15918.2` within the recorded time window:

- **02:23:37.553060Z:** `AUTHREQ_PROMPTING`, BluetoothAlways, subject and responsible
  signed-host identity **`me.nagaev.veles.bridge`**, PID **15918**, unchanged executable path.
- **02:25:37.324777Z:** `AUTHREQ_RESULT`, `authValue=2`, `authReason=2`, error null.

The subject-filtered query additionally records `Allowed (User Consent)` for this host.
Query commands and return codes are preserved; local timestamps are **+0700**, confirmed
in `final-readback.json`. The UI name is the attendee's report; OS attribution corroborates
the host identity, not a fabricated screenshot or a different observation of UI spelling.

## Scope and cleanup

This supports **the signed host, rather than Chrome, as the observed prompt subject for
this run** on the existing account. It makes no universal macOS attribution claim and
does not identify the original historical prompt. No phones advertised and no discovery
or record-transfer result is claimed.

Owned Chrome/profile and temporary native registration were removed. All eight recorded
driver/Chrome/host PIDs from the interrupted, restarted and grant-verification runs were
absent at final readback; the restored grant was left allowed. The Mac session was ended.

**M-04 stays BLOCKED:** this M-03-approved reset does not substitute for its exact displayed
Settings/retry guidance and coordinated denial/recovery without a reset. W-10 and A-01
also remain BLOCKED. Candidate totals are **26 PASS / 3 BLOCKED / 3 waived NOT RUN**,
still **NO-GO for OTP-24**; no new waiver or requirement change.

Raw files and operational source copies are byte-identical to the listed sources in
`provenance.json`. The operator was the parent `openai/gpt-6.1-sol`; the independent
reviewer must be the user-selected **`kimi-code-plan-global/k3.1-pro`**. No review approval
is claimed by this pre-review finding.
