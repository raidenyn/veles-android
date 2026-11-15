# Review record: specs

- Artifact: specs (five delta spec files, judged separately)
- Reviewer: Sol (GPT-6.1-Sol, medium)
- Session preset: [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: brainstorm.md sha256 4b4463045b9a541eada0adab14f893891adf1c61e70af3d14470ec4613492983; proposal.md sha256 47323e92d7693f47187fa07f59bd21998e8eb736b9f6899b2bcedaff65e60d4c; design.md sha256 555e091665837b759c16541d6fc2dbfcef0689491dd33300ae1c5c16a5f9280b

## Pass 1

### Round 1 — changes-required (peer-revocation, protected-record-session); approve with notes (the other three)

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | peer-revocation: unconditional "no other session terminated" conflicts with store-failure escalation | fixed: isolation applies while storage is healthy; failed delete escalates to fail-closed |
| medium | peer-revocation: fence/write atomicity and duplicate-commit case | fixed: shared-boundary scenario and duplicate-commit scenario |
| medium | mutual-peer-removal: response timeout undefined | fixed: `REMOVAL_RESPONSE_TIMEOUT_MS` = 10 000 ms on an injected clock, deadline scenario |
| medium | mutual-peer-removal: "only those" vs store failure | fixed: healthy-storage qualifier, responder-delete-failure scenario |
| medium | trust-store-failure-recovery: "episode" undefined | fixed: begin and end defined, new-episode scenario |
| low | trust-store-failure-recovery: interruption after both deletions | fixed: scenario added |
| high | protected-record-session: teardown said reset deletes first, contradicting reset-terminates-first | fixed: teardown never modifies trust; per-operation orderings |
| medium | protected-record-session: revocation assumed successful deletion | fixed: terminates even if deletion fails |
| medium | pairing-debug-harness: Reset scope | fixed: endpoint-wide Reset requirement |
| low | pairing-debug-harness: confirmation stronger than design | fixed: recorded as an intentional refinement |

### Round 2 — approve (all five files)

| Severity | Finding | Disposition |
| --- | --- | --- |
| medium | peer-revocation: overlapping revocations of one peer could release each other's fence | deferred: not a spec contradiction; tasks require a reference-counted fence per peer id and an overlapping-revocation test |
| low | mutual-peer-removal: no scenario for deletion throwing in the handler-driven removal | deferred: covered by the existing failure scenarios; tasks add the handler-driven delete-failure test |
| low | trust-store-failure-recovery: asynchronous host-scope dispatch of the fail-closed action not stated | deferred: an implementation constraint already in design D6; tasks add a handler-originated store-failure test |

Approved content: specs/peer-revocation/spec.md sha256 0aadb96061c36eb7079528aaaea4fc9f7c4f6d38b5750291221bcb4b5bda9c33
Approved content: specs/mutual-peer-removal/spec.md sha256 be9f127bf9d8bfc5da0354bbccc07cb602dad0b764fd021ccbeaecfe440a570e
Approved content: specs/trust-store-failure-recovery/spec.md sha256 bfce1d1a981936d11bbd545088d522ab764f1ee2ca612db11d841758ea06c81d
Approved content: specs/protected-record-session/spec.md sha256 6b8391acb08e3bec97a4b68473561fb93ccd6d97255ab362c059350ad844b03f
Approved content: specs/pairing-debug-harness/spec.md sha256 0fb6b55aa66c3258268ba9010a0c657e61bfad25a5a6a047b883a25f73e279aa
