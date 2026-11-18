## ADDED Requirements

### Requirement: Sharing start prerequisite
Before the Android sharing transport starts, the phone MUST check, in order: the Bluetooth LE system
feature (`Unsupported`), the `BLUETOOTH_CONNECT` and `BLUETOOTH_ADVERTISE` permissions
(`PermissionRequired`), an adapter (`Unsupported`) and the adapter being on (`BluetoothOff`). The
first failing check MUST decide the published availability, and a `SecurityException` while reading
the adapter MUST map to `PermissionRequired`. When every check passes, the transport MUST be allowed
to start.

#### Scenario: Each prerequisite failure, with the earlier checks passing
- **WHEN** exactly one check fails and every earlier check passes: no Bluetooth LE, a missing
  permission, no adapter, or the adapter off
- **THEN** the published availability is `Unsupported`, `PermissionRequired`, `Unsupported` or
  `BluetoothOff` respectively, and the service does not start

#### Scenario: Precedence when several checks fail
- **WHEN** a permission is missing and the adapter is also off
- **THEN** the published availability is `PermissionRequired`

#### Scenario: Adapter read is refused
- **WHEN** reading the adapter state throws `SecurityException`
- **THEN** the published availability is `PermissionRequired`

### Requirement: No multi-advertisement requirement
The adapter's multiple-advertisement flag MUST NOT affect whether the sharing transport may start,
because the transport starts exactly one advertisement.

#### Scenario: Single-advertisement phone may start
- **WHEN** the user requests Start, the phone has Bluetooth LE, both permissions are granted, the
  adapter is on, the adapter reports no support for multiple advertisements, and no other startup
  failure occurs
- **THEN** the prerequisite passes, and the sharing service starts, publishes `Starting` and runs in
  the foreground

### Requirement: Advertising result decides capability
The real BLE advertising attempt of the current, still-open GATT server MUST decide whether
advertising is possible. A successful start
MUST publish `Advertising`. A start failure with `ADVERTISE_FAILED_FEATURE_UNSUPPORTED` MUST publish
`Unsupported`. Every other start-failure code, including unknown codes, MUST publish `Failed`. When
the adapter provides no LE advertiser, the phone MUST publish `Unsupported`.

#### Scenario: Controller cannot advertise
- **WHEN** the advertising start fails with `ADVERTISE_FAILED_FEATURE_UNSUPPORTED`
- **THEN** the availability is `Unsupported`

#### Scenario: Non-capability advertising failure
- **WHEN** the advertising start fails with `ADVERTISE_FAILED_DATA_TOO_LARGE`,
  `ADVERTISE_FAILED_TOO_MANY_ADVERTISERS`, `ADVERTISE_FAILED_ALREADY_STARTED`,
  `ADVERTISE_FAILED_INTERNAL_ERROR` or an unknown code
- **THEN** the availability is `Failed`

#### Scenario: Advertising starts
- **WHEN** the advertising start succeeds
- **THEN** the availability is `Advertising`

#### Scenario: Callback after the server closed
- **WHEN** an advertising success or failure callback arrives after the GATT server was closed
- **THEN** it is ignored and the published availability does not change

#### Scenario: No advertiser
- **WHEN** the GATT service is added but the adapter returns no LE advertiser
- **THEN** the availability is `Unsupported`

### Requirement: Advertising failure ends the service and allows a retry
Any availability other than `Advertising` reported by the GATT server MUST tear the sharing service
down: close the GATT server, shut the sessions down, publish the state, remove the foreground
notification and stop the service. A later user Start MUST run the prerequisite and start the
service again, without leftover state from the failed attempt.

#### Scenario: Failure then retry
- **WHEN** a started sharing service receives `Unsupported` from the GATT server, and the user then
  requests Start again while every prerequisite still passes and no other startup failure occurs
- **THEN** the first service publishes `Unsupported` and stops, the new start publishes `Starting`
  in the foreground, and a later successful advertising start publishes `Advertising`
