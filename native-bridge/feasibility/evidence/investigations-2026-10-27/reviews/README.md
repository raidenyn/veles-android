# Independent review reports (actual `claude-opus-5-5`), copied unchanged from `/tmp/opencode/`

One reviewer session (`2fe20385-5ef1-4622-bf94-00b8c0c6477e`, Claude Code 2.1.310/2.1.311, no subagents) produced three read-only reports, in this order. Model identity is read from the transcript on disk
(`reviewer-session-model-metadata.json`: every model-produced assistant message is `claude-opus-5-5`), not from the reviewer's or the author's self-report. The reports also state their own capture-time metadata.

| Order | File (copy, byte-identical to the original) | sha256 | Tree it evaluated |
|---|---|---|---|
| 1 | `opus-review-1-initial-pre-signing-amendment.md` | `8d68bcf6…c53967` | the first investigation pass, **before** the signing-provenance amendment and before any Windows interactive evidence |
| 2 | `opus-review-2-rereview-pre-background-approval.md` | `c54b38dc…63ac0` | fingerprint `853f7d1f…908b5`, **before** the background-wording decisions and before the Windows interactive evidence existed |
| 3 | `opus-review-3-final-PRE-BACKGROUND-APPROVAL.md` | `fb731c50…e9821` | fingerprint `5b8b5679…b379ca0`, **before the owner's approval of the narrow background clarification** |

**Report 3 predates the approval of the background clarification.** Its statements that the background wording is "NOT approved", that "the RFC does not contain the sentence (grep count 0)" and that W-11/W-12/M-09/M-10 are blocked by an unapproved wording describe that
earlier tree and are superseded by the later operator-captured approval (`../owner-approvals/2026-10-27-otp23-background-clarification-approval.md`, RFC Status and OTP-23 section, matrix `sources`). Its three wording corrections (C-1 W-07/W-08 blockers, C-2 scoped direct-launch caveat and the Windows onset,
C-3 host-hash binding limits) were applied afterwards. **A final review of the amended tree (clarification approval plus those corrections) has not happened yet and will follow;** until then no review report covers the final fingerprint recorded in `ready-for-commit`.
