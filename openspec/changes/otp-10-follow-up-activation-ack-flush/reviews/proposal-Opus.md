# Review record: proposal

- Artifact: proposal.md
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: brainstorm.md sha256 88e635325ad8eb2c63f5e752e642dd0cbcb3f32ca22ee22c7ad1407948755d7e

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| medium | Capabilities paraphrased the modified requirement instead of naming the real header (`Protected enroll_identity records`, `openspec/specs/spake2-enrollment-exchange/spec.md:71/78`), and did not say whether to modify it or add a separate requirement | fixed: the proposal now adds a requirement `Enrollment completion hold` inside the capability, keeps the existing sentence unchanged and says so |
| medium | Impact said "two test doubles" but four implement `PairingTransport`, and the new method's required/optional status was unstated | fixed: the method is required on the interface and all four doubles are listed by path and line |
| medium | Verification dropped agreed acceptance checks (phone `ACTIVE` before the close, native-port trace with a phone `disconnected` event and no client `disconnect` request, "consecutive", and the unit-only statement for grace expiry) | fixed: all restored and matched to the brainstorm |
| low | "Fixing it now, before Gate B" was an unsourced rationale | fixed: removed; the reason rests on the recorded device run |
| low | "A peer close during the hold schedules no recovery" read as new behaviour | fixed: reworded as keeping the existing invariant |
| low | Erasure was not stated as unchanged | fixed: the teardown block says the keys are wiped and the core handle closed at the ack (OTP2-CRY-019e) |

### Round 2 — approve

| Severity | Finding | Disposition |
| --- | --- | --- |
| low | Verification asks for cancel, tab close and revocation "while the link is held" on the device, which may be impossible to trigger by hand in a normal run; only grace expiry had the unit-only escape | deferred to `design.md`: those edge paths get the same unit-only escape hatch, and the PR note must say which evidence was device-run |
| low | Brainstorm step 9 (a port close or reset that drops every handle must also end a held link) is not mentioned in the proposal | deferred to `design.md`, which carries the port-close check forward |

Approved content: proposal sha256 d6811482c82a688b6c3c13cb18d587093f5a431888d110de7352d3e60e80fde2
