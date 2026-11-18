# Review record: specs — Opus

- Artifact: specs (`specs/sharing-card-copy/spec.md`)
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream: brainstorm sha256 a1cf3d73b6f5cfa15b1e49d067453ad7ec9795b9ea66654f529fd2054522f6d9; proposal sha256 f9bcdc7a2631c03203ea3f7aec1271c44f92cb64beebcb66dee80d173b5a3da0; design sha256 0f1bdc0aa9a8a92bfaf76de8823d5ad2f77e7c33234fc5580996f835dc7c0f13 (re-pointed, see `design-Opus.md`)

## Pass 1

### specs/sharing-card-copy/spec.md

#### Round 1 — approve

Reviewed together with `tasks.md` in one reviewer run; the reviewed hash was not computed before the fixes below. The reviewer confirmed the quoted strings match `sharing.xml` and that the scenarios agree with the design's rule for all 14 combinations.

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | medium | "the lines it shows today" and "that availability's own line" have no anchor once synced into the main spec; no scenario names the 7 availability strings. | fixed: the requirement says "before this change" and a new scenario "Availability lines" fixes all 7 strings. |
| 2 | low | "exactly one text line below its title" could be read to include the buttons. | fixed: "one status line … (its buttons are not status lines)". |
| 3 | low | The buttons-unchanged clause sits only in Requirement 2, not in the stopped state. | fixed: added to Requirement 1. |
| 4 | low | The "Sharing was never enabled" scenario sits under Requirement 1 though it covers the not-enabled case. | rebutted (minor): it is a contrast case that pins what the new line replaces; no change. |

Approved content: specs/sharing-card-copy/spec.md sha256 056f083de15cd2de00556f62ee2b2450e94c68d9dc2783fc7e86fb8ec493ab64 — after 3 non-triggering minor fixes (findings 1–3 above).
