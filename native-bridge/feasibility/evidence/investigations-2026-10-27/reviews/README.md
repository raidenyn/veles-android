# Independent review reports (actual `claude-opus-5-5`), copied unchanged from `/tmp/opencode/`

One reviewer session (`2fe20385-5ef1-4622-bf94-00b8c0c6477e`, Claude Code 2.1.310/2.1.311, no subagents) produced four read-only reports, in this order. Model identity is read from the transcript on disk
(`reviewer-session-model-metadata.json`: every model-produced assistant message is `claude-opus-5-5`), not from the reviewer's or the author's self-report. The reports also state their own capture-time metadata.

| Order | File (copy, byte-identical to the original) | sha256 | Tree it evaluated |
|---|---|---|---|
| 1 | `opus-review-1-initial-pre-signing-amendment.md` | `8d68bcf6…c53967` | the first investigation pass, **before** the signing-provenance amendment and before any Windows interactive evidence |
| 2 | `opus-review-2-rereview-pre-background-approval.md` | `c54b38dc…63ac0` | fingerprint `853f7d1f…908b5`, **before** the background-wording decisions and before the Windows interactive evidence existed |
| 3 | `opus-review-3-final-PRE-BACKGROUND-APPROVAL.md` | `fb731c50…e9821` | fingerprint `5b8b5679…b379ca0`, **before the owner's approval of the narrow background clarification** |
| 4 | `opus-review-4-final-amendment.md` | `0266d303…dc0ad1` | fingerprint `602a2fbf…7992e4`: the tree **after** the background-clarification approval and corrections C-1 to C-3; this is the tree that commits 1-3 contain (verified file by file) |

**Report 3 predates the approval of the background clarification.** Its statements that the background wording is "NOT approved", that "the RFC does not contain the sentence (grep count 0)" and that W-11/W-12/M-09/M-10 are blocked by an unapproved wording describe that
earlier tree and are superseded by the later operator-captured approval (`../owner-approvals/2026-10-27-otp23-background-clarification-approval.md`, RFC Status and OTP-23 section, matrix `sources`). Its three wording corrections (C-1 W-07/W-08 blockers, C-2 scoped direct-launch caveat and the Windows onset,
C-3 host-hash binding limits) were applied afterwards. **Report 4 is the review of the amended tree** (verdict: publishable investigation package, no CRITICAL or IMPORTANT findings; OTP-23 not satisfied, NO-GO, OTP-24 not authorised). It evaluated fingerprint `602a2fbf8e2271efff6f306100b8efc739922526a12f2af976271faa0d7992e4`, the uncommitted tree that was then committed as commits 1-3 (harness/tools, governance/findings, raw evidence). The attestation commit that added this report, the refreshed model metadata, this README and the regenerated `SHA256SUMS` changed no source file and is outside that fingerprint; the review report is not re-edited and its scope is that fingerprint only. Report 3 remains the PRE-background-approval review and is kept as such.
