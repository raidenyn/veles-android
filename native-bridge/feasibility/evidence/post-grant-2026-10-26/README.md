# Permission-granted physical scenarios — 2026-10-26/27

This evidence supplements, and never relabels, the earlier `fresh-2026-10-26/`
NO-GO runs. Both phones' Bluetooth permissions were verified granted. The user
reported enabling **VelesOtp23PoC** in macOS Bluetooth privacy while Chrome was
already enabled. That is an observed Settings entry, not a fabricated TCC
database result.

Build identities remain stable: the original approved Android debug certificate
SHA-256 is `2081d17da5ec1a400e710720eba4a16939db774da54cdce936be30f4a848506e`.
The new fixture APKs use that same certificate; no app uninstall/data clearing
or replacement signing key was used. The Mac uses the same Apple Development
identity/team, `me.nagaev.veles.bridge` bundle and existing signed executable.
No ad-hoc substitution, key export or TCC reset occurred.

## Recorded scope

- Two-phone size matrices passed on Windows and macOS. The completed sweeps
  include 177/178/179, 507/508/509, 1015/1016/1017, 4095/4096 and repeated
  concurrent maximum records, stale handles, survivor delivery and shutdown.
- Mac cold and prewarmed observational background runs completed without any
  connector or browser-level CDP client during the interval. Separate offline
  adjudications bind distinct current-run phone files and clocks to the receipts.
- Windows visibility/foreground setup failures, a locked-desktop diagnosis and
  focus-interrupted cold/prewarm runs are retained. Later valid retries are
  separate artifacts, not retroactive PASS labels.
- A timed-out human-controlled permission-recovery attempt is retained as FAIL;
  no deny/recovery success is inferred from the restored grant alone.

`formal_otp23_acceptance` remains false in the background tools. Their observed
checks do not claim to exclude Chrome's possible intrinsic Native Messaging
exemption. No CI/distribution-signing provenance or new-account evidence is
inferred from these local fixtures. The approved account waiver remains limited
to tests requiring new accounts.

Final requirement dispositions, source/component hashes and review findings are
recorded in the accompanying scenario report and the authoritative matrix.
