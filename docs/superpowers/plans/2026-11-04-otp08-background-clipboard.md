# OTP-08 Background and Clipboard Implementation Plan

> **For agentic workers:** Use executing-plans to implement task-by-task. Steps use checkbox syntax. Owner requested inline execution with remaining design choices delegated (2026-11-04).

**Goal:** Keep synthetic event delivery independent of short timers, add clipboard-only offscreen copying, and invalidate frozen/discarded connector generations.

**Architecture:** The connector retains its native port. A worker-side lifecycle authority stores document-bound random generation tokens in session storage and rejects obsolete work, including after worker restart. A serialized, bounded clipboard coordinator validates the connector sender and generation before dispatching an immediate six-digit synthetic copy to a transient offscreen document. Native callbacks pass through a bounded, ordered generation gate; freeze closes locally and resume reloads into a new host.

**Tech Stack:** Existing TypeScript, MV3, Vitest, Vite; Chrome offscreen clipboard API. Chrome 132 minimum for the required frozen tab transition API.

**Spec:** `docs/rfcs/2026-08-21-otp-external-devices.md`, OTP-08; issue #90.

## Global Constraints

- No real OTP, automatic-copy preference, protected sessions, helper history, trust, native ports or durable clipboard state.
- Clipboard permission and offscreen permission only in addition to existing storage/nativeMessaging; CSP unchanged.
- Never replay a send or carry old handles into recovery. Preserve existing queue limits.
- Physical acceptance: separate Windows/macOS cold/prewarm >=600-second runs, synthetic message/copy after minute seven, no DevTools or artificial throttling exemption during idle; force freeze/discard/removal separately.
- No commits/push/PR without an explicit request. Preserve signing identities and private evidence.

## Review Focus

- Content-script or wrong-page messages must not copy or activate a generation (Task 1/2 sender tests).
- Worker restart must retain invalidation (Task 1 session-store test).
- A lifecycle transition while validation/copy is pending must prevent stale delivery (Task 1/2 races).
- Helper creation failure, clipboard denial and teardown failure must be visible without closing transport (Task 2).
- Queued native callbacks after freeze must not mutate the new generation; queues remain bounded (Task 3).

### Task 1: Worker lifecycle authority

Files: create `web-extension/src/background/lifecycle.ts`, `web-extension/test/background-lifecycle.test.ts`; modify `src/background.ts`.

Interfaces: `LifecycleCoordinator.handle(message, sender): Promise<unknown>` for activate/check/status; `invalidate(tabId): Promise<void>` for tab transitions. Tokens bind tab ID, browser document ID, extension origin and exact connector page. Session storage survives worker restart; tab state is rechecked before accepting work. Badge updates are payload-free, tab-scoped and serialized with invalidation.

- [ ] Write sender, restart, freeze/discard/removal, in-flight validation and status regressions; run focused Vitest and observe missing implementation failure.
- [ ] Implement authority with injected Chrome APIs, register worker listeners, reject unknown fields and stale tokens.
- [ ] Run focused tests and full `npm test`.

### Task 2: Clipboard-only helper

Files: create `src/background/clipboard.ts`, `src/offscreen.ts`, `offscreen.html`, `test/clipboard.test.ts`; modify manifest, Vite inputs, package test list and bundle guards.

Interfaces: `ClipboardCoordinator.copy(token, code, sender): Promise<{ok:boolean}>`; exact six ASCII digits only for this synthetic task, <=16 pending operations, no replay. Worker validates lifecycle, serializes create/copy/close and authenticates helper response. Helper validates exact worker message and sender; uses a temporary textarea plus `execCommand('copy')`, erases and removes it in finally.

- [ ] Add failing tests for forged senders, invalid text, bounded concurrent requests, creation/write/close failures, stale queued generations and sequential helper reuse/cleanup.
- [ ] Implement helper and worker coordination, add offscreen/clipboardWrite permissions and Chrome 132 floor; update guards explicitly.
- [ ] Run tests, typecheck, lint and build/bundle checks.

### Task 3: Connector generation gate and synthetic controls

Files: create `src/connector/lifecycle.ts`, `test/connector/lifecycle.test.ts`; modify `src/connector.ts`, `connector.html`.

Interfaces: ordered generation-gated `NativePortLike` adapter (128 callbacks / 262144 encoded bytes); `invalidate()` synchronously drops queued work and disconnects. Worker token activation precedes native creation. Freeze/pagehide invalidate before disposal; resume reloads for fresh ownership/host. Synthetic copy button reports success/failure, with no setting or automatic interpretation of raw records.

- [ ] Write failing tests for delayed authorization, stale generations, ordered events, overflow cleanup and copy failure isolation.
- [ ] Wire lifecycle activation, status reporting, native event gate, freeze/resume/pagehide handling and six-digit synthetic copy control.
- [ ] Run all source tests, format, lint, typecheck, build, bundle and packaged-browser tests.

### Task 4: Acceptance and evidence

Files: create `docs/otp-08-background-clipboard.md` with implementation and per-platform verification ledger.

- [ ] Inspect available home devices and browser tooling; run physical checks possible with the approved fixture identities.
- [ ] Record exact tested source/build identities, outcomes and blocked prerequisites. Do not inherit OTP-23 or OTP-07 physical acceptance.
- [ ] Review changes for stale-event races, helper payload retention and permission expansion; rerun affected checks after fixes.

## Baseline

2026-11-04: `npm ci --ignore-scripts` and full `npm test` succeeded: 16 files, 212 tests. npm reports 12 moderate dependency advisories in the existing locked graph. Working branch `feat/otp08-background-clipboard` in the existing checkout; no separate worktree was created.
