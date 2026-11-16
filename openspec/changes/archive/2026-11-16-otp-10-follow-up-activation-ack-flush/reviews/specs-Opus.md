# Review record: specs

- Artifact: specs
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: brainstorm.md sha256 88e635325ad8eb2c63f5e752e642dd0cbcb3f32ca22ee22c7ad1407948755d7e;
  proposal.md sha256 d6811482c82a688b6c3c13cb18d587093f5a431888d110de7352d3e60e80fde2;
  design.md sha256 b7a0e4acea41c0c49c188c4f6670c89505d7502445b651c20b8d18ffc07ee9ea

## Spec file: specs/spake2-enrollment-exchange/spec.md

### Pass 1

#### Round 1 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | The first requirement's unconditional ban on disconnect requests conflicted with the second requirement and D4, which require revocation, fail-closed, reset and port death to close a held link | fixed: the ban now covers only the link's own finishing path, and the hold's exits are listed |
| medium | The approved proposal adds exactly one requirement; the delta had two | fixed: merged into one `Enrollment completion hold` requirement |
| medium | The failed-send rule covered only a send that failed because the phone closed first | fixed: the rule and its scenario now say "for any cause" |
| medium | Cancel, dispose and connector-context loss during the hold had no scenario, so the mirror's fate was unstated | fixed: a scenario now keeps the mirror, ends in `idle` without a failure and closes immediately |
| low | "may report the enrollment as failed" was not normative | fixed: the requirement uses MUST NOT |
| low | "as it did before this requirement" only made sense against the old code | fixed: the behaviour is stated directly |
| low | No scenario for the peer-closed latch; the expiry scenario did not state the mirror | fixed: a latch scenario was added, and expiry keeps the mirror |
| low | `openspec validate` warns that the requirement exceeds 500 characters | rejected: the existing capability spec already carries requirements of 684 and 625 characters, so this matches the repository's style |

#### Round 2 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | The mirror-preservation rule was too broad: revocation and identity reset delete the record by their own trust-store write, so the reset scenario could not pass | fixed: the link MUST NOT delete the mirror, and a revocation or reset removes records only through its own write |
| medium | The hold's exits were not listed and "that finishing path" had no referent | fixed: the three exits are named explicitly |
| low | The failed-send scenario did not say whether the disconnect request is still sent | fixed: it keeps the request so no handle leaks |
| low | The cancel scenario did not say the phase reads `idle` | fixed |

#### Round 3 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| medium | The first sentence still conflicted with the bound-expiry and other-owner exits | fixed: "until the hold ends in one of the ways below" |
| low | The trust-store carve-out listed fail-closed recovery, which deletes nothing | fixed: only revocation and reset are named |

#### Round 4 — approve

| Severity | Finding | Disposition |
| --- | --- | --- |
| low | "After the client sends `activation_ack`" could be read as "tries to send" | deferred: wording only; the failed-send scenario and design D4 already state the successful-send meaning, and the change does not edit an approved file for it |

Approved content: specs/spake2-enrollment-exchange/spec.md sha256 77be0a6e187e67df03d2a5ce420ca8a7125639e24f5a497ccc2e7d35e2f1919f
