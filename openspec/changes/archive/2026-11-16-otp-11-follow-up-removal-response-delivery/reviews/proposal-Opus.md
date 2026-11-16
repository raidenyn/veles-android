# Review record: proposal

- **Artifact**: `proposal.md`
- **Reviewer**: Opus (Opus-5.5, medium)
- **Session preset**: [Opus]
- **Cap**: 5 rounds
- **Upstream**: brainstorm.md sha256 `d95c221184f9475e95e94a369245fecc303d15fd7067234105fdfd7f859c1e09`

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| medium | The Why and the responder block stated the response was "dropped at teardown, not delayed" as established; the issue only says it appears to be dropped and the run is intermittent. | fixed — the proposal now says "appears to be dropped" and that the diagnosis is inferred, and the Verification bullet records the unit-test fallback if the device run cannot reproduce the loss. |
| medium | The proposal had no cleanup-paths block and did not say why `peer-revocation` and `trust-store-failure-recovery` are left unchanged. | fixed — a cleanup-paths From/To block was added, and the Capabilities section explains that both capabilities remain true because logical termination does not depend on the transport close and an existing held link is still finished by an explicit reset or revocation. |
| low | "the requester's close is the only application-level delivery evidence" overstated the evidence. | fixed — the normal case is stated as the requester's close following its validated deletion, and a close for another reason is bounded by the same hold. |
| low | `close-peer.test.ts` is named in the Impact but not in design D7. | deferred — a design-side alignment item carried into tasks/plan; the file's existing immediate-disconnect assertions must be updated for the hold. |
| low | Ten runs in each direction was ambiguous. | fixed — the Verification bullet says ten Android → Chrome and ten Chrome → Android. |
| low | The recovery-skip To line omitted the start-only marker and listener-order detail. | deferred — proposals do not carry that level of detail; design D4 does. |

### Round 2 — approve

| Severity | Finding | Disposition |
|---|---|---|
| low | Port-close cleanup (`abortAll` clears holds without a request) is missing from the cleanup block. | deferred — design D6 covers it; the proposal block names only the destructive reset/revocation paths. |
| low | Design D7 still does not name `close-peer.test.ts`. | deferred — design-side alignment item carried into tasks/plan. |

Reviewed hash: `8c447272e7419f88407e4f868d382f8b1f8950d082bbb0b57d788016ff0205e8`.

Approved content: proposal sha256 8c447272e7419f88407e4f868d382f8b1f8950d082bbb0b57d788016ff0205e8
