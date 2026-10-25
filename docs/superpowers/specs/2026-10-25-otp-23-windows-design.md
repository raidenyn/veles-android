# OTP-23 Windows feasibility harness

Approved scope: implement and validate Windows first; macOS follows separately.

## Intent

Prove Chrome-owned Native Messaging can carry synthetic whole records through
WinRT BLE to the OTP-06 Android service. This disposable harness must not become
the production OTP-24 bridge, handle real OTPs, or activate trust.

## Approach

Use a separate Rust harness and small unpacked Chrome connector alongside the
existing native bridge. Use native WinRT APIs, existing protocol UUIDs and
fragment format, bounded requests/events, and one owner for framed stdout.
Compare plain-Rust and Tauri-feature builds for dependency and size cost.
Reusing the production bridge skeleton would blur the explicitly disposable
boundary; implementing both platforms now conflicts with the approved staging.

## Verification

Test framing, malformed input, fragmentation boundaries, handle isolation and
cleanup deterministically. Validate stable Chrome launch, physical BLE round
trips, ten-minute background delivery and termination on the Windows PC. Record
exact hardware/build identity and expected versus actual cases. A second phone
and standard non-admin account are acceptance requirements, not assumptions.
Unavailable cases remain pending. macOS and the overall OTP-24 gate stay pending.

Run Claude Code Opus 5.5 review and required repository lint gates before PR;
monitor PR checks and address actionable feedback.
