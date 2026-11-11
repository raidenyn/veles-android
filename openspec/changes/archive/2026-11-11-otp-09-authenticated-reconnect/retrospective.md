# Retrospective — otp-09-authenticated-reconnect

Evidence first, opinion second.

## Wins

- The D0 vector defect was fixed and isolated before any engine code depended on it
  (`f27ec2e`); every later conformance test and both engines consume the corrected fixture.
- Both platform engines were held to the same committed vectors, and the loopback drove the
  real Kotlin and TypeScript engines rather than test doubles.
- Task reviews caught real security boundaries: pre-auth type allowlists, handler-reply
  nonce reuse, post-await expiry revival, sequence reservation before sealing, proof
  immutability and per-generation metadata fencing.
- The final review found the last callback-accounting hole in the GATT fence; the fix keeps
  cardinality of stale callbacks and is fail-closed.
- The physical run passed all seven planned steps on Windows 11 and macOS with the fixed
  builds, and the evidence note records hashes, fingerprints and deviations.

## Misses

- The apply aggregate gate hit an AGP lint/KSP race when release assembly ran in the same
  invocation; the pieces passed when run separately.
- The fix wave introduced a one-line `setResultData` crash in the adb export that neither the
  scoped re-review nor the unit tests caught; the physical run did, and it was fixed in
  `6a1faa1`.
- The first physical evidence draft reported the heartbeat step too strongly; the refreshed
  note marks it as indirect evidence.
- Sol's subscription limit interrupted two reviews (Task 9 round 3, the final branch review);
  the user chose controller verification for the first and Kimi K3.1 Pro for the second.

## Plan deviations

- The Android fixture package is `me.nagaev.veles.otp23` (the repository's OTP-23 fixture
  suffix), not the plan's expected `.debug`; the evidence uses the actual package.
- `connector/reconnect.ts` needed no change: recovery renewal already happened only on
  handler success once `attemptRecovery` returned false until authentication.
- The Chrome deadline driver added an injectable timer seam that the briefed constructor did
  not list, because the 15 s tick is required by the plan's Global Constraints.
- `st-rec-heartbeat-rate-terminates` is recorded as a receive-side engine-effect (the
  OTP2-REC-034 rule is receive-side), with real emission assertions instead of an input flag.
- The final GATT fencing is permanently fail-closed for an address whose stale callback never
  arrives; the reviewer accepted this as the only safe behavior because the callback carries
  no notification identity.

## Skill/workflow compliance

- Tasks ran through subagent-driven development with a fresh implementer and a task review
  per group; fix rounds were scoped and re-reviewed.
- The final review used the code-reviewer template and a single fix wave plus one scoped
  re-review.
- The physical run used the homepc skill and the project's fixture signing and probe surface.

## Surprises

- `onNotificationSent` identity is the binding constraint on the whole GATT completion
  design; every alternative either completes early or stalls.
- The adb `EXPORT_IDENTITY` result must go through the `goAsync()` pending result, not the
  receiver instance; the failure mode is an app crash, not an error return.
- The native host validates a build-time origin allowlist, so each development machine needs
  its own build with its extension id.

## Promote candidates

- Add a real ordered-broadcast smoke to the debug probe tests (the pending-result wiring is
  currently only exercised by the physical run).
- Add an async bootstrap for `SharingService` to remove the parked main-thread `runBlocking`.
- Turn the deferred scenario ids' owners (OTP-10/11/13-15) into inventory entries with tests
  as those changes land.
