# OTP-23 phone transport telemetry (debug fixture only)

Records, per central, what the Bluetooth stack actually returned for each synthetic notification, so M6 can
cite real return codes and failure isolation under two centrals. It adds no endpoint and changes no sharing
behavior. Release builds contain none of it: every hook is behind `BuildConfig.DEBUG` and R8 removes
`SyntheticTransportFixture`/`TransportTelemetry` (parent verifies on the release APK).

## Opt-in

```bash
adb shell am instrument -w -e class me.nagaev.veles.sharing.SharingFeasibilityTest \
  -e otp23 true -e otp23_transport_evidence true -e otp23_run_id <RUN_ID> \
  me.nagaev.veles.test/androidx.test.runner.AndroidJUnitRunner
```

`RUN_ID` matches `[A-Za-z0-9._-]{1,64}` and is supplied by the parent. Without
`otp23_transport_evidence true` nothing is recorded, hashed, retained or written.

## Output

`files/otp23-transport-evidence.json` (app-private; written atomically, rewritten while the loop runs, and
always once more after the service stops, with `final: true`). Fields: `kind`, `run_id`, started/finished epoch
ms, `source_package`, `source_version_name/code`, `truncated`, `peers_dropped`, and per peer:
`peer_sha256_prefix` (16 hex; first 8 equal the `otp23-unsolicited.json` prefix), `notify_calls`,
`api_success`/`api_nonzero`, `attempted_bytes`, `callbacks`, `callback_success`/`callback_nonzero`,
`unbound_callbacks`, `connects`, `disconnects`, `subscribes`, `mtu`, `truncated`, and bounded `events`
(`seq`, `kind`, `epoch_ms`, `elapsed_realtime_ms`, raw `code`, optional `label`, `bytes`, `bound_op`).

- `code` is the raw value returned by `notifyCharacteristicChanged` / reported to `onNotificationSent`;
  `label` is set only for codes with a named platform constant, otherwise null.
- `onNotificationSent` has no operation id; `bound_op` binds it FIFO to the oldest API-accepted fragment of
  that peer (the transport keeps one in flight). Unbindable callbacks count in `unbound_callbacks`.
- Limits: 16 peers, 4096 events per peer (sized for 20 records x 9 fragments x notify+callback = 360 events plus lifecycle, with wide margin), 16 pending bindings per peer. Counters stay exact; any dropped
  event or peer sets `truncated` and **forbids a complete-evidence claim**.
- Final snapshot: after `stopService` the test waits (bounded, 5 s) for `SharingService.onDestroy` to release BLE
  plus a 750 ms settle, then writes `final: true`, `final_after_service_stopped` (false if the wait timed out),
  and `clean`. `clean` is true only if the run is final, service-stopped, not truncated, and for every peer:
  `pending_unresolved == 0`, `pending_dropped == 0`, `unbound_callbacks == 0`, `api_nonzero == 0`,
  `callback_nonzero == 0`, `callbacks_seen_nonzero == 0`, `callbacks_after_close == 0`,
  `callbacks_seen == callbacks` and `callbacks == api_success`. An accepted fragment lacking a callback is never clean.
- `callbacks_seen*` / `callbacks_after_close` are raw counts taken when the stack delivers `onNotificationSent`,
  before the main-looper hop, so a status dropped after `close()` is still visible. `pending_dropped` counts
  accepted fragments whose callback was abandoned by a disconnect.
- No MAC, payload, header, raw record or key is stored; only sizes, codes and hashed peer prefixes.

## Not in scope

M2 pending-ingress erasure, full OTP20, production OTP delivery, OTP24 hardening.
