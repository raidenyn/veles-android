## Why

After a package replacement, a reboot, a force-stop or a system stop of the app, the saved "sharing on" preference stays true while the service is not running and the in-memory availability is `Disabled`. The Device sharing card then adds "Disabled. This transport does not share OTPs yet." under its "enabled, but not running" line. That reads like a capability refusal; it was misread as `Unsupported` during the OTP-12 Gate B run and led to a follow-up built on the wrong cause (issue raidenyn/veles-android#169). Plain wording removes the misreading.

## What Changes

**Device sharing card, enabled but not running while availability is `Disabled`**
- From: two lines, "Sharing is enabled, but the transport is not running…" followed by "Disabled. This transport does not share OTPs yet."
- To: one line saying that sharing is on but the Bluetooth service is not running, that this can follow an app update, a restart or a system stop, and that Enable transport restarts it (or Stop sharing turns it off).
- Reason: the `Disabled` string describes a transport that was never turned on; here the transport was turned on and stopped.
- Impact: non-breaking text change for the one state; every other state renders as today.

Also: the mapping from (enabled preference, availability) to the card's text lines becomes a pure function covered by a JVM test, and `docs/otp-06-transport.md` gains one sentence (exact wording in the design) saying that after a restart that leaves the preference on the card names the stopped transport and the restart stays manual. No service, manifest, permission or debug/release change; restoring sharing after a package replacement or reboot is out of scope.

## Capabilities

### New Capabilities
- `sharing-card-copy`: which text lines the Device sharing card shows for each combination of the enabled preference and the published availability, including the stopped-transport line. This is a deliberate departure from the approved brainstorm, which put the requirement into `ble-sharing-availability`: that capability's Purpose (prerequisites and advertising results) does not cover the card, and a delta cannot widen a Purpose.

### Modified Capabilities

## Impact

- `android/app/src/main/java/me/nagaev/veles/sharing/SharingCard.kt`: calls the new mapping function instead of choosing strings inline.
- A new mapping function in `android/app/src/main/java/me/nagaev/veles/sharing/SharingCardText.kt` and a new string resource `sharing_stopped` in `android/app/src/main/res/values/sharing.xml`.
- A new JVM unit test for the 14 (enabled × availability) combinations.
- `docs/otp-06-transport.md`: one sentence.
- A new main spec `openspec/specs/sharing-card-copy/spec.md` after the closeout sync.
- Real-phone check: with sharing enabled and `Advertising`, reinstall over the installed app with `adb install -r`, then read the card and the service state.
