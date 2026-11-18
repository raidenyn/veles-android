## Design Summary

Issue raidenyn/veles-android#169. After the app is replaced in place (`adb install -r`, or an ordinary
app update) the persisted "sharing enabled" preference stays `true`, but `SharingService` is not running
and the in-process `SharingState.availability` starts as `Disabled`. `SharingCard` then shows two lines:
the "Sharing is enabled, but the transport is not running…" line and the `Disabled` string
"Disabled. This transport does not share OTPs yet.". The second line reads like a capability refusal. It
was misread as `Unsupported` during the OTP-12 Gate B run, which led to the follow-up change
`relax-ble-advertising-prerequisite` (that change is a safe relaxation, not a fix for an observed failure:
its apply notes, now archived at
`openspec/changes/archive/2026-11-18-relax-ble-advertising-prerequisite/apply.md`, record that master
advertises on the OnePlus 6T and that Enable transport then works).

The change is copy only. When sharing is enabled but the transport is not running **and** the published
availability is `Disabled`, the card shows one line that says plainly that the transport stopped (for
example after an update) and how to start it again; the `Disabled` line is not shown in that state. Every
other state, enabled or not, renders exactly as today.

Findings from reading the code and the issue:

- `SharingState.enabled` is loaded from the `sharing` preferences by `SharingCard`; `availability` is an
  in-memory flow that starts as `Disabled` in a fresh process. `SharingCard` renders
  `if (enabled && !active)` the "enabled, not running" line, then the availability line chosen by `state`.
- A restart after process death comes only from `START_STICKY` in `SharingService.onStartCommand`. The only
  `MY_PACKAGE_REPLACED` receiver (`otp.PackageReplacedReceiver`) re-binds the notification listener and
  never touches sharing.
- `docs/otp-06-transport.md` already states that force-stop, reboot and an explicit failure promise no
  automatic restart; the RFC calls restoration after package replacement best effort.
- Other enabled-but-not-running states (`Unsupported`, `PermissionRequired`, `BluetoothOff`, `Failed`)
  already carry an explanatory line of their own next to the "not running" line and stay as they are.
- `openspec/specs/ble-sharing-availability` covers prerequisites and advertising results; it does not cover
  what the card shows, so the new behaviour needs its own requirement.

## Alternatives Considered

### Alternative A: Copy only
- **Approach**: One card line when sharing is enabled, not running and `availability == Disabled`; keep the
  manual Enable transport restart.
- **Pros**: pure UI change; no permission change; no new background start path; verifiable by a unit test
  and one real-phone reinstall; keeps the debug/release boundary untouched.
- **Cons**: after every update or reinstall sharing stays off until the user taps Enable transport.
- **Why not chosen**: chosen (see Agreed Approach).

### Alternative B: Copy plus restart after package replacement
- **Approach**: Add a `MY_PACKAGE_REPLACED` receiver that starts `SharingService` when the preference is on.
- **Pros**: sharing survives an app update without user action; needs no new permission.
- **Cons**: a foreground-service start from a broadcast is only allowed in some cases and the
  `connectedDevice` rules differ by Android version; this was not confirmed for Android 15 and needs real
  device proof; adds a background start path to an offline, privacy-focused app; a failed start would still
  need the new copy.
- **Why not chosen**: the owner chose A; the card still says OTP sharing is not enabled yet, so a manual
  restart costs little now. B is not filed; it can be raised when sharing ships.

### Alternative C: Copy plus restart after reboot
- **Approach**: Also start the service from a boot receiver.
- **Pros**: sharing also survives a reboot.
- **Cons**: requires `RECEIVE_BOOT_COMPLETED`, which changes the permission set the issue says to keep
  unchanged.
- **Why not chosen**: breaks a stated constraint.

## Agreed Approach

Alternative A, chosen by the owner on 2026-11-18. Restoration after a package replacement or a reboot is
out of scope; the manual restart stays and the copy now says so.

## Key Decisions

- **One message, no `Disabled` line.** The new text replaces both lines only for
  `enabled && !active && availability == Disabled`. It is a new string resource `sharing_stopped`; the
  existing `sharing_enabled_unavailable` stays for the other enabled-but-not-running states. Proposed text
  (the builder's call, to be fixed in the design): "Sharing is on, but the Bluetooth service is not
  running. This can happen after an app update, a restart or the system stopping the app. Tap Enable
  transport to start it again, or Stop sharing to turn it off." It names the action with the two buttons
  the card already shows in this state.
- **The state is wider than an update.** The same state appears after a package replacement, a reboot, a
  force-stop, a system stop of the app or any cold start with the preference still on, and when
  `SharingService.onDestroy` resets `Starting`/`Advertising` to `Disabled` without `fail`. The copy and the
  doc sentence therefore must not claim it means "after an update" only. A user Stop through Task Manager
  also lands here; the RFC's wish for an explicit unavailable state for that case is accepted as not met by
  this change and gets the same generic line.
- **Decision as a pure function.** The mapping from (`enabled`, `availability`) to the card's text lines is
  extracted so a JVM unit test covers all 14 combinations (2 `enabled` values × 7 availabilities) without a
  Compose runtime, proving that only the one `Disabled`-while-enabled combination changes and the other 13
  render as today; `SharingCard` calls it. The function returns string resource ids. This is the builder's
  structural call, not an owner decision.
- **No behaviour change outside the card.** No change to `SharingState`, `SharingService`, the manifest, the
  permission set or the debug/release boundary.
- **Docs.** `docs/otp-06-transport.md` gains one sentence that the card states the stopped transport after a
  package replacement and that the restart is manual.
- **Spec.** A new requirement on the card text goes in the existing `ble-sharing-availability` capability.
- **Real-device check.** On a real phone: install, enable sharing, reinstall over it with `-r`, read the
  card (one line, no "does not share OTPs yet") and confirm with `dumpsys activity services` that
  `SharingService` is not running, press Enable transport and see it reach `Advertising` with the service
  running in the foreground; stop sharing and see the plain `Disabled` card unchanged.

## Open Questions

None. Restoration after a package replacement is deliberately out of scope and no follow-up issue is filed
for it.
