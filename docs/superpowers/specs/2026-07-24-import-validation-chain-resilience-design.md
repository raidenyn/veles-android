# Import validation and handler-chain resilience

**Date:** 2026-07-24
**Issue:** [#58](https://github.com/raidenyn/veles-android/issues/58)
**Type:** Bug fix and robustness improvement
**Priority:** High - one malformed imported template can disable OTP interception

## Problem

The JSON import flow writes bank templates without applying the validation used by the editor.
An imported malformed regex can therefore enter Room. `HandlerChainReloader` currently builds an
entire handler list inside one `try`, so one `PatternSyntaxException` preserves the previous chain
and prevents every later emission containing that row from taking effect. If the service starts
with the bad row already present, the initial empty chain remains active and all notifications are
filtered.

The editor now validates both regex syntax and required capture-group counts, but that contract is
local to `BankConfigEditViewModel`. Import and runtime reconstruction need the same definition of a
valid template and independent safety boundaries.

## Goals

- Apply one validation contract to editor saves, JSON imports, and runtime chain reconstruction.
- Reject an entire import file when any effective template is invalid, with no database writes.
- Identify every invalid effective template and field without exposing parser internals.
- Ensure one invalid persisted row cannot suppress valid handlers or freeze hot reload.
- Preserve valid import behavior and existing duplicate-name semantics.

## Non-goals

- Changing the import JSON schema.
- Deleting, repairing, or quarantining existing invalid Room rows.
- Changing last-entry-wins handling for duplicate names in an import file.
- Displaying raw `PatternSyntaxException` messages or regex contents.
- Redesigning unrelated import I/O, serialization, or repository failure handling.

## Shared Validation Contract

Add a pure `BankConfigValidator` in the bank-config domain. It accepts a template name and the
three regex strings and returns the set of invalid fields. Field identities cover name, OTP regex,
amount regex, and merchant regex. The validator has no dependency on Android resources or UI
state.

A template is valid when all of these conditions hold:

- The name is not blank.
- The OTP regex compiles and defines at least two capture groups.
- The amount regex compiles and defines at least two capture groups.
- The merchant regex compiles and defines at least one capture group.

Syntax and group-count failures map to the same invalid regex field identity. This preserves the
editor's current error behavior and intentionally avoids exposing Java regex parser diagnostics.

`BankConfigEditViewModel` will call the shared validator and map its field identities to the
existing `UiText` errors. Blank names retain the existing required-name message. Invalid regex
fields retain the existing invalid-regex message. This removes the local validation implementation
so editor and import rules cannot drift.

## Import Analysis And State

Import keeps the existing last-entry-wins deduplication by template name. Validation applies to
the resulting effective entries, not to entries superseded by a later duplicate. This preserves
the data that would actually be imported and avoids rejecting a file because of an overridden
entry.

After JSON parsing and deduplication, import validates every effective entry before creating the
normal insert/overwrite review:

1. If all entries are valid, calculate the existing insert/overwrite diff and show the current
   confirmation dialog unchanged.
2. If any entry is invalid, create a rejected-import review containing every invalid effective
   entry and its invalid fields. Do not create a normal `ImportReview`.
3. `confirmImport()` remains reachable only from a valid normal review, so a rejected file cannot
   perform partial writes.

The rejected-import review is separate state from the generic one-message dialog because it must
carry structured, per-entry validation details. Starting another import or dismissing the dialog
replaces or clears that state through the existing screen flow.

## Rejected-Import UI

`BankConfigsScreen` renders a dedicated informational dialog for rejected imports. The dialog:

- States that nothing was imported and asks the user to fix the listed templates.
- Lists each invalid template by name.
- Uses a localized `Unnamed template` fallback when the name is blank.
- Lists localized field labels: `Name`, `OTP regex`, `Amount regex`, and `Merchant regex`.
- Provides only an `OK` action and never exposes the Import confirmation action.

The UI does not display raw regexes or parser exception text. The existing valid-import review,
success message, malformed-JSON message, and unreadable-file message remain unchanged.

## Handler-Chain Reconstruction

`HandlerChainReloader` will process each persisted config independently. For every emitted list it
will validate each row, omit invalid rows, construct handlers for the valid rows, and atomically
replace `messageHandler` with a `CompositeMessageHandler` containing that valid subset.

Handler construction remains protected at the per-row boundary so an unexpected
`PatternSyntaxException` cannot abort the list rebuild even though validation has already compiled
the patterns. Invalid rows produce warnings containing the config name and invalid field
identities, but not regex contents.

The resulting behavior is explicit:

- A mixed valid/invalid emission activates all valid handlers.
- An all-invalid or empty emission activates an empty composite.
- A later edit or deletion emits a new list and replaces the chain normally.
- Flow-level failures retain the existing collector restart and backoff behavior.
- Coroutine cancellation continues to propagate and is never converted into a skipped row.

This deliberately replaces the current "keep the previous whole chain" behavior for malformed
rows. Keeping stale handlers would hide valid additions, edits, and deletions whenever a bad row
remains in the database.

## Data Flow

```text
Editor save
  -> shared validator
  -> map invalid fields to field-level UiText errors
  -> valid: persist; invalid: remain on editor

JSON import
  -> parse
  -> last-entry-wins deduplication
  -> shared validator for every effective entry
  -> any invalid: rejected-import dialog, no writes
  -> all valid: insert/overwrite review -> confirmation -> writes

Room config emission
  -> shared validator per row
  -> invalid row: warn and skip
  -> valid row: construct RegexMessageHandler
  -> atomically publish composite of valid handlers
```

## Error Handling And Privacy

Validation failures are expected user-data errors, represented as field identities rather than
exceptions. Import reports all invalid effective entries in one pass so the user can fix the file
without repeated import attempts.

The reloader is the final resilience boundary for legacy, externally created, or otherwise corrupt
rows. It logs each skipped config without logging regex values. Existing flow-level exception
handling remains separate because cursor/database failures require collector restart rather than
row omission.

No new broad exception catch is added around message handling or repository writes. Unrelated
programming, notifier, and persistence failures must not be silently reclassified as invalid
configuration.

## Testing

### Shared validator

Unit tests will cover:

- Blank and non-blank names.
- Syntax failures in each regex field.
- OTP and amount patterns with fewer than two groups.
- Merchant patterns with no group.
- Fully valid templates.
- Multiple invalid fields returned together.

### Editor

`BankConfigEditViewModelTest` will verify that the shared result preserves existing required-name
and invalid-regex errors and that valid templates still save.

### Import

Importer and `BankConfigsViewModelTest` coverage will verify:

- A mixed valid/invalid file is rejected atomically.
- Every invalid effective template and field appears in rejection state.
- Blank names use the name-field failure while remaining representable in state.
- No repository insert or update occurs after rejection.
- Last-entry-wins deduplication happens before validation.
- A valid file still produces the existing insert/overwrite review and success count.

Compose/instrumented coverage will verify that the rejection dialog displays template names and
field labels, uses the unnamed fallback, and has no Import action. Existing instrumented import
fixtures will use regexes that satisfy the capture-group contract.

### Handler reloader

`HandlerChainReloaderTest` will verify:

- A mixed initial emission activates valid handlers and skips the invalid row.
- An invalid-only initial emission replaces the placeholder with an empty composite without
  terminating collection.
- A later corrected emission activates its handler without service restart.
- Valid edits and deletions continue to replace the active chain while another bad row remains.
- Existing flow restart, cancellation, start, and stop behavior remains intact.

Verification will run:

- Focused validator, config ViewModel/importer, and reloader unit tests.
- `./gradlew testDebugUnitTest`
- `./gradlew compileDebugAndroidTestKotlin`
- `./gradlew assembleDebug`
- Relevant connected tests when an Android target is available.

## Acceptance Criteria

- Editor saves and JSON imports enforce the same name, regex syntax, and capture-group contract.
- If any effective imported template is invalid, no template from that file is written.
- The rejected-import dialog identifies every invalid effective template and invalid field without
  exposing regex contents or parser diagnostics.
- Valid import files retain the existing review, overwrite, confirmation, and success behavior.
- A persisted invalid row is omitted independently and cannot suppress valid handlers.
- Hot reload applies later additions, edits, and deletions even while an invalid row remains.
- An all-invalid initial emission remains safe and does not terminate or freeze the collector.
- Existing flow-level retry and coroutine cancellation behavior is preserved.
