# Review record: brainstorm

- Artifact: brainstorm.md
- Reviewer: Sol (GPT-6.1-Sol, medium per the roster; executed at the environment's reviewer pin, high effort)
- Session preset: [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: none

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | Rule activation does not cover its principal consumers: the rule matches only `native-bridge/installer/macos/**`, while the packaging script and production host live under `native-bridge/host/`; a pointer is not a mandatory handoff | fixed: the host node gets an explicit read-by-hand-before-macOS-E2E instruction and the rationale for the scope is stated |
| high | The required read-only authorization preflight has no established execution mechanism; the getter is private and startup performs no Bluetooth work | fixed: the preflight is now the existing signature checks plus the host's ordinary first `start_scan` result (`started` vs `permission_pending`); no new probe is required |
| high | The two permitted launch routes lack an identity-preserving E2E contract; `open` cannot feed a headless stdio host and Chrome could launch a different bundle | fixed: the Chrome Native Messaging manifest `path` inside the signed bundle is the primary route; `open` is limited to the app/driver case; test identity vs production distribution identity is stated |
| medium | The SSH attribution evidence is generalized to every terminal launch | fixed: limited to the one measured SSH case; other non-LaunchServices contexts are declared unverified |
| medium | Identity lifecycle claims exceed the evidence: ad-hoc churn does not prove the old grant is deleted, and certificate renewal does not prove a new identity | fixed: ad-hoc means the stored grant no longer matches; renewal is a compare-the-DR-and-revalidate step, not an automatic identity change |

The artifact was rewritten in response to these findings and sent for round 2.

### Round 2 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | A successful first scan does not prove reuse without a prompt: authorization may become determined during the scan wait, and `permission_pending` is a timeout classification, not a pre-scan check | fixed: the scan result is now only an authorization observation; the separate reuse evidence is the unified TCC log (no `AUTHREQ_PROMPTING`/`CFUserNotification`, no `Create`/`Modify` grant event), required for unattended runs and reported by the operator in attended runs |
| medium | The renewal example still contradicted the conditional identity rule | fixed: a replaced or renewed certificate/team requires a new Allow only if the DR comparison shows it changed |

The artifact was revised again and sent for round 3.

### Round 3 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | Missing log events were treated as proof without establishing observation coverage, correlation with the responsible process, capture boundaries or an inconclusive outcome | fixed: the log window is now validated by start/end bounds, `--info`, filters on both the bundle identifier and the responsible process/executable path, and a positive control (the expected `tccd` access records must be present); an empty, unreadable, wrongly filtered or verbosity-limited capture is INCONCLUSIVE/BLOCKED, never "no prompt"; where visibility is unavailable, the attended operator observation is used and reuse is not claimed |

The artifact was revised again and sent for round 4.

### Round 4 — approve

No critical, high, medium or low findings; the log-coverage requirement is consistent and the
remaining ambiguities are deferred to the design, which must specify the exact predicates and
positive-control records.

Approved content: brainstorm sha256 c23498c533380384c936caba135d106fc980570aec4717aa6f0a0bb3b6034d25

## Pass 2 — rename amendment

### Round 1 — approve

The app-name rename to `VelesOtpDebug.app`, the expanded scope (the test-bundle script and its
tests) and the unchanged identity/baseline are coherent at the intent level. The downstream
acceptance-driver consumer had to be covered by the proposal/design/tasks/plan, which it is.

No findings for this artifact.

Approved content: brainstorm sha256 c7826d38283fb95d1e5a1f2d8e6ac45c7e316a27c5582eb64eb54aa95e517903

## Pass 3 — identifier migration to me.nagaev.veles.debug

### Round 1 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | The summary still described an unchanged identifier and signing requirement | fixed: the summary and Key Decisions now state the move from `VelesOtp23PoC.app`/`me.nagaev.veles.otp23.poc` to `VelesOtpDebug.app`/`me.nagaev.veles.debug`, the retirement, the fresh grant and the reused certificate/team |

### Round 2 — approve

No findings. The certificate/team reuse is clearly separated from the identity migration, retirement
and owner-approved fresh grant.

Approved content: brainstorm sha256 01670f07cf77f34f048604305d5e26ec96b4867d92a67cdb9f017ef472da6c7d
