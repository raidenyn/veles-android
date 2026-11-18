## ADDED Requirements

### Requirement: Stopped transport line
When the sharing preference is enabled and the published availability is `Disabled`, the Device
sharing card MUST show exactly one status line below its title (its buttons are not status lines and
MUST NOT change): the stopped-transport line. It MUST NOT show the `Disabled` availability line or
the "enabled, but not running" line in that state.

#### Scenario: Preference left on after a reinstall
- **WHEN** the app is replaced in place with sharing enabled, the new process has not started the
  service, and the card is shown with availability `Disabled`
- **THEN** the card shows only "Sharing is on, but the Bluetooth service is not running. This can
  happen after an app update, a restart or the system stopping the app. Tap Enable transport to
  start it again, or Stop sharing to turn it off."
- **AND** no line contains "does not share OTPs yet"

#### Scenario: Sharing was never enabled
- **WHEN** the sharing preference is not enabled and the availability is `Disabled`
- **THEN** the card shows only "Disabled. This transport does not share OTPs yet."

### Requirement: Other card states keep their text
For every combination of the sharing preference and the published availability other than an
enabled preference with `Disabled`, the card MUST show the lines it showed before this change, in
the same order, and its buttons MUST NOT change. Each availability's own line is fixed by the
scenario "Availability lines".

#### Scenario: Availability lines
- **WHEN** the card shows the availability's own line
- **THEN** that line is, by availability: `Disabled` "Disabled. This transport does not share OTPs
  yet."; `Starting` "Starting Bluetooth service…"; `Advertising` "Available over Bluetooth. OTP
  sharing is not enabled."; `Unsupported` "Bluetooth LE peripheral advertising is unavailable on this
  device."; `PermissionRequired` "Nearby devices permission is required. Allow it in app settings if
  denied."; `BluetoothOff` "Turn on Bluetooth, then enable transport again."; `Failed` "Bluetooth
  service stopped. Try enabling transport again."

#### Scenario: Enabled and the start failed
- **WHEN** the preference is enabled and the availability is `Unsupported`, `PermissionRequired`,
  `BluetoothOff` or `Failed`
- **THEN** the card shows "Sharing is enabled, but the transport is not running. Enable transport to
  retry, or stop sharing." followed by that availability's own line

#### Scenario: Enabled and running
- **WHEN** the preference is enabled and the availability is `Starting` or `Advertising`
- **THEN** the card shows only that availability's own line

#### Scenario: Not enabled
- **WHEN** the preference is not enabled and the availability is any value other than `Disabled`
- **THEN** the card shows only that availability's own line

#### Scenario: All combinations
- **WHEN** the line mapping is evaluated for each of the 2 preference values and 7 availability values
- **THEN** exactly the combination of an enabled preference with `Disabled` yields the
  stopped-transport line, and the other 13 yield the lines described above
