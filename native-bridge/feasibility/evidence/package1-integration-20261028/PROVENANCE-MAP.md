# Provenance map — /tmp citations resolved to published byte-identical counterparts (2026-10-28)

The v4 derivation record (`../package1-b5-v4-20261028/derivation-record.json`) and its artifacts cite
`/tmp` sources for review records and owner decisions; `/tmp` is volatile and NOT exported with the
evidence. This folder is the PUBLICATION home of the missing pieces. Every mapped copy below is
byte-identical (`cmp`-verified at copy time) with identical SHA-256. **The v4 derivation source and
pins are NOT changed by this map** — it documents the capture paths and the published counterparts so
export-time checks resolve provenance; the v4 artifacts remain exactly as pinned (same SHA-256SUMS).

## Review records in the chain

| /tmp capture path | SHA-256 at capture | Published counterpart |
|---|---|---|
| `/tmp/opencode/otp23-package1-windows-strict-gpt-review.md` | `520c2eddbb7a8a8af9aaa0d267c8fd3e09331d6b1b96e947967077386fd52519` | `../package1-b5-v2-20261028/reports/otp23-windows-strict-gpt-review.md` (already published; v2 IMMMUTABLE, listed in its own 15-entry manifest) |
| `/tmp/opencode/otp23-package1-p2-gpt-review.md` | `15a28c80341a5a5e0baa2e6cb91ecb0ae27cafbef8622db90e9f98c7f519ea2f` | `../package1-b5-v2-20261028/reports/otp23-p2-b5-gpt-review.md` (already published; v2 immutable) |
| `/tmp/opencode/otp23-package1-p2-gpt-fix-verification.md` | `ee342efa63de3e27dcc373c8e6944f76879a912ac37f10a2557f22f91b3ff0bd` | `reports/otp23-p2-gpt-fix-verification.md` (NEW, byte-identical) |
| `/tmp/opencode/otp23-package1-p2-gpt-v3-final.md` | `217e6f2f2bcd896a7cda0bb8b6cf484ae3da7151f98f29ca6fb399bf2d40d4d2` | `reports/otp23-p2-gpt-v3-final.md` (NEW, byte-identical) — this is the record the v4 derivation cites at `answers_fix_verification.record.path`; the v4 record pins the SAME sha, so the published counterpart + this row resolve the citation at export time |
| `/tmp/opencode/otp23-package1-p2-opus-v4-final.md` | `d85d4f5784ccda95a3ce85ed1430abee34519f54aa893c81cb3656b5dbe4ff1e` | `reports/otp23-p2-opus-v4-final.md` (PUBLISHED, byte-identical) — the user-authorized Opus 5.5 focused final verification (`claude-opus-5-5`, session `1b447229-ceaa-4cee-a5be-2181dfdd8838`, metadata confirmed): sole remaining defect CLOSED; recommends W-02/W-07/W-08 PASS; applied by the FINAL integration (canonical now 23 PASS / 6 BLOCKED / 3 waived) |
| `/tmp/opencode/otp23-package1-p2-gpt-v4-events.jsonl` | `d02a276afd8bcc6ea512aaf3c32e7121064b0a237125e1874d231ca2a7f1c03f` | NOT PUBLISHED by policy — the failed request events contain error bodies/headers/transcript content; only the safe quota facts are recorded (`../package1-b5-v2-20261028/provenance/v4-review-attempt-quota-state.json`) |
| `/tmp/opencode/otp23-package1-observer-code-gpt-final.md` | `33e8233ee0e26299118afec02fe26b15eb784fab1db7d0caa6eb701c45b53575` | `reports/otp23-package1-observer-code-gpt-final.md` (PUBLISHED, byte-identical) — GPT observer-code final approval for the observer-provenance changes; session `ses_e7fbe5353ffeWU4VTjn6lgYjIL` (openai/gpt-5.6-sol, high) |
| `/tmp/opencode/otp23-package1-observer-code-gpt-verification.md` | `1a14ebd10bad9df349d8295a80d47b1055e2266b10fbe1e180039035c9843dbc` | `reports/otp23-package1-observer-code-gpt-verification.md` (PUBLISHED, byte-identical) — predecessor verification, same GPT session `ses_e7fbe5353ffeWU4VTjn6lgYjIL` |
| `/tmp/opencode/otp23-package1-integrated-opus-review.md` | `cc0b6225c25ccd0ce3ce49875c73bafdd1b8ea05bce22d83f762a08728935654` | `reports/otp23-package1-integrated-opus-review.md` (PUBLISHED, byte-identical) — the FINAL INTEGRATED review (Opus 5.5, approved for commit pending clean export + CI; whole-session metadata 161 real + 1 synthetic, effort high — `provenance/integrated-opus-review-model-metadata-final.json`, measured; the preserved 69-entry snapshots are correct for the v4 part only) |
| `/tmp/opencode/otp23-package1-integration-result.md` | `9a6c4117528e85e8db019d84a857eb5db70bdb6517ce49f97a6f1376db880f8e` | `phase-records/otp23-package1-integration-result.md` (PUBLISHED, byte-identical) — GLM integration-phase historical record; resolves the citation in `gates/SOURCES.md` and in the frozen Opus v4 report |
| `/tmp/opencode/otp23-package1-final-integration-result.md` | `72e874f6b607c9699ae1bd28a73c47d2f4c4afa9850824091b53b0d5b34a1a48` | `phase-records/otp23-package1-final-integration-result.md` (PUBLISHED, byte-identical) — GLM final-integration-phase historical record |

## Owner decision/authorization records

| /tmp source | SHA-256 | Published counterpart |
|---|---|---|
| `/tmp/opencode/otp23-package1-owner-approval.json` | `89d255383b21272590f7c3b3cc3370e7b8f1e688b2445a65dba022d529f23fd4` | `../package1-b5-v2-20261028/owner-approval-w02-2026-10-28.json` (already listed in the v2 manifest; the v3 and v4 folders hold their own byte-identical copies) |
| `/tmp/opencode/otp23-package1-mac-deferred.json` | `ace5e31e99c25ee21b4d634208ef006af02624121624952e0c40238505086e99` | `../package1-b5-v2-20261028/mac-deferral-2026-10-28.json` (published; byte-identical copies in v3/v4) |
| `/tmp/opencode/otp23-package1-opus-reviewer-approval.json` | `9211bbf87a4d114279b547bb99bc89c5e24884cc15a6e22df6001ddb943a6313` | `owner-records/opus-reviewer-approval-2026-10-28.json` (NEW, byte-identical; operator-captured owner instruction: "Use Opus 5.5 via Claude Code"; earlier GPT reports keep actual authorship; no Kimi) |

## Session-model metadata for the two newly published GPT reports

Both reports were authored by the same authorized review session
`ses_e7e49d2cbffepV653zHccm8N62` (provider `openai`, model `gpt-5.6-sol`, variant `high`, OpenCode
1.19.4, agent build), whose actual counts are captured in
`../package1-b5-v2-20261028/provenance/finish-phase-model-metadata.json` (31 assistant messages at
that earlier capture; the session later grew — 59 assistant records by the v3-final recheck per that
report's own safe-metadata line). No new DB metadata capture is needed for provenance: the two new
copies carry the same author session, and no content, headers or errors were read.

## Preserved-manifest honesty (v2 15-entry manifest + the unlisted addition)

`../package1-b5-v2-20261028/SHA256SUMS` covers EXACTLY the 15 candidate files as frozen; the quota
record `../package1-b5-v2-20261028/provenance/v4-review-attempt-quota-state.json`
(`03a1454d0f9c87717b7492b0901ba0dad0e46dc1a975a2d4272a6ce0ae3f3923`) was added AFTER freezing and is
NOT listed there (all old manifests stay byte-unchanged). This folder's active `SHA256SUMS` includes
that addition BY RELATIVE PATH, so the published set resolves it without touching the frozen v2
manifest:
`../package1-b5-v2-20261028/provenance/v4-review-attempt-quota-state.json  <sha above>`
(verify with `sha256sum -c SHA256SUMS` from THIS folder).

## Label rules (unchanged)

Earlier GPT reports remain actually GPT; the quota block is historical; the CURRENT v4 review is the
user-authorized Opus route (Claude Code `claude-opus-5-5`, session
`1b447229-ceaa-4cee-a5be-2181dfdd8838`, metadata confirmed in BOTH preserved snapshots), now
DELIVERED and applied by the final integration; no Kimi is involved anywhere; implementation is
ollama-cloud/glm-5.3-flash with 0 subagents.