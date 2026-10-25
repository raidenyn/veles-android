# OTP-23 Windows final review and disposition

Claude Code was invoked locally with explicit **`--model claude-opus-5-5`**.
Session `df745987-751f-4834-bca7-5c72b3f36e12`; returned `modelUsage` confirms
`claude-opus-5-5`, firstParty, no subagents. Read-only review covered all untracked
harness files, Android changes, CI and documentation against base `6e083e0`.

Initial verdict: **not ready as-is; mergeable as a diagnostic NO-GO with fixes**.
The protocol correction itself, macOS and incomplete physical cases were not
required to pass; they remain explicit gates rather than acceptance claims.

## Addressed findings

| Finding | Disposition and verification |
|---|---|
| C1: existing bridge Prettier/ESLint traverses the new experiment | Isolate disposable subtree in production tool ignore lists; separate CI runs harness tests. `./gradlew bridgeFormat bridgeLint` now passes |
| I1: debugger-attached background artifact incorrectly says PASS | Script and annotated JSON now say DIAGNOSTIC, debugger attached, source scan_cap, acceptance false. Remove fabricated websocket observations and compare actual PIDs in future runs; historical PID assertion is unknown |
| I2: physical sizes miss actual MTU-517 attribute/fragment edges | Correct historical wording; next-run script adds 508/509, 510/511 and 1020/1021. Historical 4095 crash is not represented as a minimum-failure measurement |
| I3: nonexistent `otp23.disconnect` method | Use `disconnectDevice`; real connector API test observed failing (`missing disconnect`) then passing after correction |
| I4: stale WinRT link slots accumulate through churn | Remove cancelled slots and join finished workers, detach pending cancel-aware workers. Windows regression observed failing before helper implementation; final Windows suite 151 passed and Clippy clean |

Required Android `spotlessCheck detekt lintDebug` and JVM suite passed. Raw Log
guard is run again before PR creation. Local runtime/device failures remain
documented failures; they are not defects hidden by green deterministic tests.

## Deferred minor notes

- M1: watcher can abort between Started observation and ScanStarted emission,
  leaving stale scan state until its bounded cap. Keep this experimental race
  explicit; production OTP-24 adapter needs a regression and ordering fix.
- M2: MTU-185 simulation does not establish MTU-517 Android behavior (now explicit
  in the physical matrix).
- M3: ignored Python bytecode must not be staged (not part of the PR).
- M4: explicit workflow `permissions: contents: read` would improve consistency
  with other builds; existing repository defaults apply in this experiment.

## Review boundaries / executor rulings

No unmeasured WinRT radio timing, SBOM rebuild identity, macOS TCC, standard
non-admin-account result, denied-radio matrix, or complete real-link lifecycle
result is inferred. Synthetic-only in-memory event clones are not a payload
persistence claim. Release workflows are unchanged, and the synthetic responder
remains debug-gated. The cost artifacts predate the service-lifetime fix and are
labeled accordingly. The frozen profile correction is a separate reviewed RFC
change; NO-GO is the result of this Windows investigation.
