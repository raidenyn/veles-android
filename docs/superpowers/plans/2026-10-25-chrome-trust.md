# Chrome Installation Identity and Trust Storage Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox syntax for tracking.

**Goal:** Implement OTP-05 (#84): non-extractable Chrome identity and fail-closed local phone trust.

**Architecture:** A connector-owned repository serializes read/validate/replace operations across trusted extension contexts with Web Locks. IndexedDB holds a non-extractable key pair and installation metadata atomically; restricted chrome.storage.local holds one complete identity-bound trust snapshot. Protocol authentication is supplied through verifier callbacks, never persisted booleans.

**Tech Stack:** TypeScript, Web Crypto, IndexedDB, Chrome MV3 storage, Vitest, playwright-core Chromium.

**Spec:** docs/rfcs/2026-08-21-otp-external-devices.md (OTP-05), docs/protocol/veles-v1.md §5.4.

## Global Constraints

- Random 128-bit installation ID; P-256 ECDSA private key is non-extractable.
- Peer metadata is local, non-sync, restricted to trusted extension contexts; no content-script bridge.
- `pending` expires after 86 400 seconds in its original connector's monotonic context, or on context loss. Both `active_activation_pending` and `active` survive restart.
- Connection hints never select trust and are writable only after authenticated pairing/reconnect.
- Missing/corrupt/partial identity or trust fails closed; explicit reset requires re-pairing. No secret logging.

## Review Focus

- Concurrent connector operations must not lose peer updates or replace identity.
- Partial initialization or deletion of one store must not silently create a new identity.
- Pending expiry must not remove durable activation mirrors after restart.
- Tampered key material, fingerprints and timestamps must reject the whole snapshot.
- Failed authentication and storage writes must publish no trust or hint changes.

### Task 1: Identity and storage adapters

**Files:** `web-extension/src/trust/identity.ts`, `storage.ts`; `test/trust.test.ts`.

**Interfaces:** `IdentityStore.read(): Promise<StoredIdentity | undefined>`, `create(): Promise<StoredIdentity>`; `LocalStore.read(): Promise<unknown>`, `write(snapshot): Promise<void>`, `restrict(): Promise<void>`.

- [x] Test private-key non-exportability, P-256 sign/verify, missing/partial state and storage failures.
- [x] Implement atomic IndexedDB creation, identity self-check and local trusted-context access restriction.
- [x] Run focused tests and TypeScript checks.

### Task 2: Trust repository

**Files:** `web-extension/src/trust/repository.ts`, `model.ts`; `test/trust.test.ts`.

**Interfaces:** `ChromeTrustRepository.identity`, `peers`, `activePeers`, `addPending`, `confirm`, `authenticated`, `forgetHint`, `revoke`, `reset`. Verifiers consume immutable peer copies plus proof bytes; state and hint changes are serialized inside the same lock.

- [x] Test duplicate IDs, malformed points/records, fingerprint binding, authenticated-only transitions, expiry/context loss, durable mirrors, revocation, hint changes and concurrent writes.
- [x] Implement strict schema validation and atomic whole-snapshot replacement, with identity binding and explicit recovery.
- [x] Run focused tests, lint and format checks.

### Task 3: Production wiring and real-browser evidence

**Files:** `web-extension/src/background.ts`, `manifest.ts`, `package.json`; browser test runner and fixture; CI extension test workflow; evidence document.

- [x] Add storage permission and initialize trusted-only local storage at worker startup; no message handlers exposing trust.
- [x] Run actual Chromium tests for CryptoKey structured cloning/restart, storage deletion, restricted content scripts and profile removal.
- [x] Run extension tests/build/bundle plus `./gradlew spotlessCheck detekt lintDebug` and CI raw-Log guard.
- [x] Run Claude Code with `claude-opus-5-5` as final independent review; resolve findings and repeat affected checks.
- [ ] Commit, push, create PR closing #84, and monitor checks/review feedback to completion.
