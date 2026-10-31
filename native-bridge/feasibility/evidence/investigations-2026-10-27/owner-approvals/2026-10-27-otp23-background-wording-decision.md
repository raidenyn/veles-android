# Owner decision record: OTP-23 background wording (2026-10-27) — NOT APPROVED, more evidence first

**Kind:** operator-captured owner decision, relayed to this working session as an instruction in the session conversation. Not made on GitHub; no issue or
pull-request comment records it; this file does not invent one.

**Operator's words, verbatim** (the exact wording relayed; no longer or fuller owner text was provided to this session):

> Owner answered background question REQUIRE MORE EVIDENCE FIRST; explicitly NOT approve background wording. Record that.

**Effect:**
- The proposed sentence in `docs/otp-23-investigations.md` section 1.5 is **not approved** and is **not** inserted into the RFC. The RFC's background criterion, the
  OTP-08 wording and every background gate stay exactly as they were.
- W-11, W-12, M-09 and M-10 are unchanged (BLOCKED). No waiver, no re-adjudication, no status change. macOS ST-15 is not waived.
- The macOS / Chrome 156 timer probe already run (`macos/timer-probe/`) is **not** described as satisfying the owner's request; it is one piece of evidence.
- The evidence the owner asked for first, as understood by this pass (the owner did not list it; this is an interpretation): the same-build timer probe on **Windows Chrome**
  (installed build only; Windows Chrome is 156.0.8078.48, so 155 remains covered by pinned sources alone) and a Chrome-launched Windows lifecycle comparison. Their results are
  recorded in `windows-chrome/` when run; a later owner decision is still required before any wording changes.

---
Later record (does not alter the text above): the owner subsequently approved a narrow clarification of the criterion text after the both-platform Chrome 156.0.8078.48 probe evidence; see `2026-10-27-otp23-background-clarification-approval.md`.
