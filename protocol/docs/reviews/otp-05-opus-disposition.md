# OTP-05 final review disposition

Reviewer: Claude Code 2.1.310, explicitly selected **`claude-opus-5-5`**,
session `7438629e-c299-49b4-8880-9e0948464fd8`, 2026-10-25.
Read-only review of the entire change, including new files, against issue #84,
the RFC, protocol §5.4 and the implementation plan. No reviewer subagents.

The review verdict was **With fixes**. This records the findings and their
disposition; it does not claim a second reviewer approval after the fixes.

## Blocking / important findings

| Finding | Disposition and verification |
|---|---|
| C1: `@playwright/test` adds an unreviewed nested `fsevents@2.3.2` install script | Replaced with pinned `playwright-core@1.58.2`, which has no transitive dependencies. Browser runner and CI use its CLI. Exact-pin npm install-script, license and remote-code checks pass. |
| I1: A rejected first operation leaves a key without its metadata snapshot | Persist the empty identity-bound snapshot before evaluating the first operation. A regression test for first-operation `forgetHint` failure was observed failing, then passing. An interrupted cross-store save still conservatively requires explicit reset. |
| I2: Downloaded browser executes in the release artifact producer | Removed browser execution from `build-web-extension.yml`; browser tests run only in PR/master CI, which does not publish extension release artifacts. |
| I3: Isolation immediately after browser restart was unproven | Reordered the real-browser scenario to load a content-script page BEFORE any extension page/repository call after restart. Chromium preserves the access restriction; the test passes. Manifest now requires Chrome 102, the floor for `setAccessLevel`. |
| I4: All infrastructure failures incorrectly request destructive re-pairing | Added exported `TrustUnavailableError` for retryable infrastructure failures, keeping `TrustError` for malformed/inconsistent trust and rejected authentication. Regression test observed RED→GREEN. Also tested a null peer so malformed snapshots remain corruption errors rather than incidental TypeErrors. |

## Deferred minors

These do not authorize untrusted peers or expose secrets. They remain explicit
follow-up items rather than being silently counted as fixed.

- M1: Broaden boundary/fault-injection coverage: two-context simultaneous writes,
  concurrent first initialization, identity binding corruption, stage-order
  rejection, verifier-copy mutation, confirm/hint write failure, the 257th peer,
  interrupted reset, more clock-metadata tampering, sync emptiness and actual
  extension-uninstall automation. Current tests cover the core paths and profile
  deletion; they do not claim this entire additional matrix.
- M2: Explicit IndexedDB strict durability (current default can lose the latest
  identity write on power loss; cross-store mismatch still fails closed).
- M3: Close an IndexedDB open connection that succeeds after `onblocked` already
  rejected (the document/browser eventually closes it).
- M4: Validate hints before invoking the protocol verifier and document its
  exclusive-lock/non-reentrant contract. Current behavior rejects malformed
  hints before persistence, but can consume a verifier attempt first.
- M5: Owner-side proactive pending cleanup timer. Current pruning is lazy on
  access; foreign observers can show expired metadata, but cannot activate it
  or reconstruct its clock. Owner activation rechecks expiry.
- M6: Consistent unknown-peer semantics for `forgetHint` versus `revoke`.
- M7: Avoid unchanged snapshot writes on read operations (currently a write
  failure makes the read unavailable, without publishing trust).
- M8: Tighten the worker readiness comment/API; the effective security gate is
  every repository operation awaiting `restrict`, not use of the exported worker
  readiness promise by an as-yet-unimplemented connector.
- M9: Fixture adapter duplication is retained; the evidence now explicitly says
  only the repository is imported from the production bundle. Browser corruption
  helpers bundle source identity/model/storage adapters separately.

## Boundaries the reviewer declined to judge

- Protocol MAC/session/ET binding belongs to OTP-09/10. Ruling: keep the default
  reject verifier and injectable callback; a future caller must supply actual
  authenticated proof verification. Incorrect integration would authorize peers.
- Monotonic-clock behavior during system sleep is prescribed by §5.4. Ruling:
  follow the frozen mapping, without inventing wall-clock expiry; sleep-related
  retention behavior inherits the protocol's platform constraint.
- An attacker already able to write extension storage is outside this store's
  corruption-detection scope. Ruling: fingerprints are consistency checks, not
  authenticated at-rest metadata; a compromised trusted context is not defended
  by a local hash.
- The reviewer did not execute license/SBOM/remote-code checks. Ruling: execute
  those locally rather than infer supply-chain success from review. Targeted npm
  checks pass; the aggregate verifier fails on missing Rust SBOM component
  `pkg:cargo/itoa@1.0.18`. The Rust lockfile/generator are unchanged by OTP-05.
- Android tests were outside the review's source scope. Ruling: run the required
  local lint gate and raw-Log guard independently; no Android implementation is
  changed here.
