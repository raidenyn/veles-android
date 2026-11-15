# Retrospective — otp-11-peer-revocation

Written at finalize, after the implementation, two whole-branch reviews, and a real-device run.

## Wins

- The failure paths got the attention: fence release on every branch, delete-first ordering,
  reply-before-close, and fail-closed that never deletes all survived review and hardware.
- The per-task review loop caught real defects that unit tests alone would not have: a
  misclassified malformed key, an episode-transition race, a transport-close abort that skipped
  later generations, and a reset admission window.
- The real-device run paid for itself: it found the recovery-session/FULL-session mismatch that no
  JVM or Node test could show, and proved Chrome → Android mutual removal end to end.

## Misses

- The device run happened after verify rather than before it, so the branch passed verify twice
  with a user-facing contradiction still in the panel.
- The unit tests all used a FULL session fixture; the recovery-only case was structurally
  unreachable in tests until the device run exposed it.
- The enrollment ack race is OTP-10's, but it shaped this change's usability and was only found by
  accident.

## Plan deviations

- `closePeer` has no `excluding` parameter (the plan's own refinement).
- Chrome uses a non-destructive `PairingLink.terminate()` rather than `cancel()`.
- `openspec validate --all --strict` was replaced by the per-change gate, because it fails on
  untouched pre-existing specs.
- After the broad review, the Android overlapping-reset admission finding was fixed in four small
  rounds instead of being parked.

## Skill/workflow compliance

- Superspec review gate: planning files approved by Sol, audit current, a design Pass 2 repaired an
  incomplete upstream set.
- Subagent-driven development throughout: one implementer per task, a task reviewer after each,
  scoped re-reviews for fixes, one fix wave after the broad review.
- Systematic debugging was used for the device finding before any fix was proposed.

## Surprises

- The Chrome extension's Bluetooth transport depends on a Native Messaging host that embeds a
  compile-time extension-origin allowlist; a plain rebuild silently refuses to launch.
- The Android debug screen is `FLAG_SECURE`, so device screenshots are black and observations come
  from the accessibility tree.
- A recovery-only generation is deliberately recovery-only for its whole life (OTP2-STA-010b), so
  "disconnect and reconnect" is the intended path to a FULL session, not a workaround.

## Promote candidates

- Add a loopback scenario that starts from an interrupted activation and asserts the panel's session
  kind, so the recovery/FULL distinction is covered without hardware.
- Make the enrollment ack delivery testable across the teardown boundary (OTP-10, issue #155).
- Consider a device-run gate before verify for changes that touch session lifecycle.
