# Owner approval record: OTP-23 background-criterion clarification (2026-10-27) — APPROVED (narrow), later than the "require more evidence first" decision

**Order of the two same-day background records:** (1) `2026-10-27-otp23-background-wording-decision.md`: the owner answered "REQUIRE MORE EVIDENCE FIRST" and approved no wording; (2) this record: after the
both-platform Chrome 156.0.8078.48 probe evidence was presented, the owner approved the clarification below. Record (1) is preserved unchanged; this one is later and refines it for the criterion text only.

**Kind:** operator-captured owner approval, relayed to this working session as an instruction in the session conversation. Not made on GitHub; no issue or pull-request comment records it; this file does not invent one.
Only the operator's relayed wording is available; the full question text and its answer options were not provided to this session beyond what is quoted here.

**Operator's words, verbatim:**

> New explicit owner approval after both-platform Chrome156 probe evidence: owner selected APPROVE CLARIFICATION. Apply exact OTP23-only text proposed in question (not earlier Opus draft): "Event delivery through the architecture’s Native Messaging port is not a timer-throttling exemption. The criterion continues to forbid timer-throttling opt-outs, artificial keepalive, audio, WebRTC, exemption flags and DevTools clients during idle; ordinary timers and the port itself are permitted. If a same-build comparison shows the port tab escapes intensive timer throttling while the port-less control does not, report the criterion unsatisfied." Scope: all cold/prewarm, foreground evidence and freeze/discard requirements remain; this does NOT approve OTP24. Chrome155 source inspection only no fresh probe, clearly record. Preserve earlier owner REQUIRE MORE EVIDENCE FIRST decision chronologically and record this subsequent approval operator-captured exact question/answer. Update RFC authoritative OTP23 narrow clarification matrix sources/blocker wording and docs without automatic status promotions or mac ST15 waiver, no gate GO.

**Applied as:** the quoted sentence, verbatim (including the typographic apostrophe), in the RFC (Status paragraph and the OTP-23 "Background-criterion clarification" paragraph, with a pointer in the OTP-23 acceptance sentence),
in the matrix `sources`, the regenerated matrix document header and `docs/otp-23-investigations.md` section 1.5. The earlier Opus-drafted sentence is **not** used.

**Evidence basis (as stated in the RFC paragraph):** source inspection of Chromium 155.0.8059.85 and 156.0.8078.48; same-build timer probes (port tab vs port-less control, no DevTools client during idle) on Chrome 156.0.8078.48 on Windows
(`windows-chrome/timer-probe/`) and macOS (`macos/timer-probe/`). **Chrome 155 was inspected in source only; no fresh 155 probe was run** (155 was not installed on either machine).

**What does not change:** cold-idle and prewarmed runs of at least ten minutes on both platforms, strict-run requirements, foreground evidence (macOS ST-15 is NOT waived), freeze/discard requirements (no exemption claimed), evaluator provenance
and `formal_otp23_acceptance`, the NO-GO rule. **No matrix row is promoted** (W-11, W-12, M-09, M-10 stay BLOCKED; their blockers were reworded to say the criterion text is clarified but the rows are not promoted). OTP-24 is not approved; no gate is GO.
Host and source identities are as recorded in `windows-access-denied/diag-binary.txt`; no new physical run was made for this change.
