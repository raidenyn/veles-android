# Report copies — exact sources, scope and fingerprints (2026-10-28 finish phase)

All files in `reports/` are byte-identical (`cmp` verified) copies of the session reports in
`/tmp/opencode/` at capture time (2026-10-28 UTC, this GLM-5.3-flash finish session). They are REVIEW
RECORDS, not new claims: the review opinions and their fingerprints belong to their authors and are
unchanged here.

| Copy (reports/) | Author session / model (opencode db, safe metadata only) | Scope |
|---|---|---|
| otp23-windows-strict-gpt-review.md | `ses_e7ef53f61ffesIPmRxLtwZ1efE`, openai gpt-5.6-sol, variant high, OpenCode 1.19.4, agent build (63 assistant messages incl. 1 compaction at db capture; preflight was the separate session `ses_e7fbe5353ffeWU4VTjn6lgYjIL`, same model — a different review role, retained as its own count) | independent FINAL recommendation: W-11 COLD / W-12 PREWARM PASS under the 2026-10-27 background clarification, no hardware rerun |
| otp23-p2-b5-gpt-review.md | `ses_e7e49d2cbffepV653zHccm8N62`, openai gpt-5.6-sol, variant high, OpenCode 1.19.4, agent build (31 assistant messages at db capture) | independent CONDITIONAL APPROVAL of the P2 connected retake + B5: recommends W-02/W-07/W-08 PASS after the four source-only B5 fixes and v2 derivation; NO hardware rerun; M-08 externally deferred |

Model provenance: `provenance/finish-phase-model-metadata.json` holds the opencode-db capture of the
actual session records (assistant/user message counts, per-message modelID/providerID/variant, agent,
opencode version) for this finish session, both review sessions, the strict-review preflight, the
coordinating parent session (`ses_e8393db6bffeMzWgsvMaGsH3pI`, gpt-6.1-sol — coordinating only, never
the implementation) and the earlier package-1 implementer session. Captured from
`/home/dev/.local/share/opencode/opencode.db` with metadata-only queries: no message text, no API
headers, no cookies, no auth state was read or stored. The counts are as of each capture moment and
may grow afterwards; they are evidence of identity, not of size. Earlier phases additionally used local
Claude Code Opus (`claude-opus-5-5`) review/preflight sessions (see
`../integration-2026-10-27/provenance/`): the Opus preflight role is retained there as a different
review role count; no Opus authored anything in this folder.

Nothing here authorises OTP-24, waives any row, or changes the NO-GO decision.