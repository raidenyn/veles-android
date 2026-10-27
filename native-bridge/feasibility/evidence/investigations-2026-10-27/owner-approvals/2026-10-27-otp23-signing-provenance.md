# Owner approval record: OTP-23 signing provenance (2026-10-27)

**Kind:** operator-captured approval. The text below was relayed to this working session by the operator as an instruction in the session
conversation ("Explicit owner approval just received via question"). It was **not** made on GitHub, no issue or pull-request comment records it, and this
file does not invent one. Its only authority is the operator's relay; the owner may restate or revoke it.

**Operator's words, verbatim (the part that states the approval and its limits):**

> Explicit owner approval just received via question: for OTP23 ONLY accept existing approved Apple Development-signed final-ID fixture as minimum signing provenance; defer CI/distribution signing to OTP25. Other permission and evidence requirements remain. Implement exact narrow dated amendment in authoritative RFC, matrix source/expected/blocker and docs consistent with this approval. This does NOT approve OTP24 or waive prompt ownership M04 M11 or any background gate. M03 may remain BLOCKED if prompt owner requirement persists. Preserve local identity no new signing. Record owner answer as operator-captured approval, not invented GitHub approval. Owner still unavailable Mac prompts.

**What it changes (and where it is implemented):**
- RFC `docs/rfcs/2026-08-21-otp-external-devices.md`: Status paragraph "Amended on 2026-10-27", the OTP-23 acceptance sentence on the signed fixture, a
  "Signing-provenance revision" scope paragraph, and an OTP-25 carry-over note.
- Matrix `acceptance/otp23-matrix.json`: M-03 `source`, `expected`, `actual` and `blocker`, and a `sources` entry; `scripts/matrix.py` header; regenerated `docs/otp-23-requirements-matrix.md`.
- `docs/otp-23-investigations.md` section 3 and `docs/otp-23-contract-alignment.md` section 16.

**What it does not change:** OTP-24 stays NO-GO and unapproved; the prompt-owner observation (M-04 and the M-03 expected text), allow/deny/recovery guidance, the
unsigned dev-host comparison on the existing account (M-11) and every background gate (W-11, W-12, M-09, M-10, including macOS ST-15) are unchanged; no waiver is
added (only W-01, M-01, M-02 remain waived); M-03 remains BLOCKED on the retained prompt-owner observation.

**Identity preserved:** the approved bundle `me.nagaev.veles.bridge` (Team 7T39M26Z99, Apple Development leaf, executable sha256 `e7c716c5…eb91c`, CDHash
`25bc7fc25e48c4470d4c3765b6a5f9b719ff381d`) was read back only (`macos/signed-bundle-readback-2026-10-27.txt`, `signing/designated-requirement-readback.txt`). No new signing, re-signing,
key export, ad-hoc identity, TCC reset or Bluetooth prompt was performed for this amendment.
