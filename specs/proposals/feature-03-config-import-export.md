# Feature: Bank config import/export (JSON)

**Type:** New feature
**Priority:** Medium-high
**Effort:** Small-medium (~1–2 days)

## Motivation

Bank configs are painstaking to get right and live only inside one device's Room DB. Users can't
back them up, move to a new phone, or share a working config for their bank with someone else
(or with the project as a candidate seed/template). `kotlinx-serialization` is already a
dependency, so the marginal cost is low.

## Design

1. **Format.** Versioned JSON envelope:
   ```json
   {
     "velesConfigVersion": 1,
     "configs": [
       {"name": "UOB Thailand", "otpRegex": "...", "moneyRegex": "...", "merchantRegex": "..."}
     ]
   }
   ```
   `@Serializable` DTO distinct from the Room entity (no ids/timestamps — those are local).
2. **Export.** Action on `BankConfigsScreen` (overflow menu): serialize all configs and hand off
   via `Intent.ACTION_CREATE_DOCUMENT` (SAF) to write a `.json` file, plus a share sheet option
   (`ACTION_SEND`) for a single selected config.
3. **Import.** `ACTION_OPEN_DOCUMENT` picker → parse → show a review list (name + regexes,
   collision detection by name) with per-item checkboxes → insert selected. Never overwrite
   silently: on name collision offer "keep both (renamed)" or "replace".
4. **Validation.** Reuse the write-time validation from `tech-04` (regexes must compile, group
   counts correct) and reject entries that fail, listing them in the review UI.
5. Also register an intent filter for `.json`/`application/json` `VIEW`/`SEND` so a config file
   received in a messenger can be opened directly with Veles (nice-to-have, phase 2).

## Testing

- Round-trip serialization tests (export → import yields equal configs).
- Import validation tests: malformed JSON, unknown version, invalid regex entries.
- ViewModel test for collision handling paths.
