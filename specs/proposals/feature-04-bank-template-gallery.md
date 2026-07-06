# Feature: Built-in bank template gallery

**Type:** New feature
**Priority:** Medium
**Effort:** Medium (~2 days initial, ongoing template curation)

## Motivation

Today the app ships one hard-coded seed (UOB Thailand, embedded as raw SQL in
`BankHandlerDatabase.SeedCallback` and `MIGRATION_1_2`). Every other user must author regexes
from scratch — the steepest part of the funnel. A curated, in-app gallery of known bank formats
("KBank TH", "SCB TH", "Bangkok Bank", "Krungsri", …) turns setup into a two-tap flow and gives
the community a place to contribute (via `feature-03`'s JSON format checked into the repo).

## Design

1. **Template source.** Bundle templates as a JSON asset (`assets/bank_templates.json`) using the
   same DTO/format as import/export (`feature-03`). Each template additionally carries
   `sampleMessage` (anonymized, e.g. digits replaced) and optional `notes` ("only card purchases,
   not transfers").
2. **UI.** "Add from template" entry on `BankConfigsScreen` alongside the existing FAB → a
   searchable list of templates → tapping one opens the normal `BankConfigEditScreen` pre-filled
   (user can rename/tweak before saving). With `feature-02` in place, the bundled
   `sampleMessage` immediately shows green match previews — instant confidence.
3. **Seeding refactor.** Replace the raw-SQL seed inserts with "on first run, import the
   default template(s) through the same repository path". This removes the duplicated SQL between
   `SeedCallback` and migrations and guarantees seeds pass the same validation as user input.
   (Note: current code seeds *two* overlapping UOB rows on fresh install — SeedCallback inserts
   both "UOB Thailand" and "UOB Thai" — the refactor should clean this up.)
4. **Community pipeline.** `CONTRIBUTING.md` section: submit a template as a PR editing the JSON
   asset + a redacted sample message; CI validates the file (regexes compile, groups correct,
   sample matches) with a small JVM test that loads the asset.

## Testing

- JVM test: every bundled template's regexes compile, have required group counts, and match the
  bundled `sampleMessage` end-to-end via the shared extractor.
- UI test: template list → prefilled editor → save → appears in configs list.
