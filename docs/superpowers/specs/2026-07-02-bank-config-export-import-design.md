# Bank Config Export/Import — Design

## Goal

Add export and import functionality for saved bank regex templates. Users can
select which configs to export or export all. Import appends new configs and
overwrites existing ones with the same name, with a confirmation step before
overwriting. Rejecting the import stops the entire import. Uses a simple JSON
file format. Export and Import buttons live on the bank template list screen.

## JSON Format

Simple, portable shape — no id, no timestamps. `createdAt`/`updatedAt` are
regenerated on import (set to `now`).

```json
[
  {
    "name": "UOB Thailand",
    "regex": {
      "otp": "...",
      "amount": "...",
      "merchant": "..."
    }
  }
]
```

Field mapping to `BankHandlerConfig`:
- `regex.otp` → `otpRegex`
- `regex.amount` → `moneyRegex`
- `regex.merchant` → `merchantRegex`

Import matches by `name`.

## Architecture & Components

New package `otp/config/io/` isolates serialization and import-diff logic from
the repository. The existing `BankHandlerRepository` / `BankHandlerConfigDao`
are reused (no schema change); only an `upsert`-by-name path is exercised
through existing `insert` / `update`.

Components:
- **`BankConfigJson`** — on-the-wire shape: `{ name, regex: { otp, amount, merchant } }`.
- **`ConfigSerializer`** — pure functions: `toJson(configs): String`,
  `fromJson(json): List<BankConfigJson>`. No Android dependencies; fully
  unit-testable.
- **`ConfigImporter`** — pure function: given parsed JSON + existing configs,
  computes a diff (`toInsert`, `toOverwrite`). The ViewModel performs the actual
  repository writes in `confirmImport()` (so cancel can skip them entirely).
- **`BankConfigsViewModel`** — new event handlers and dialog state in
  `BankConfigsState`.
- **`BankConfigsScreen`** — adds Export/Import buttons, a multi-select export
  dialog, and an import-confirm dialog. SAF launchers
  (`rememberLauncherForActivityResult` with `CreateDocument` / `OpenDocument`)
  live in the screen and bridge to the ViewModel via callbacks.

## Data Flow — Export

1. User taps **Export** on `BankConfigsScreen`. ViewModel sets
   `state.exportSelection = ExportSelection(items = all names, checked = all
   names)` (defaults to all checked).
2. User toggles checkboxes via `toggleExportItem(name)` and confirms with
   `confirmExportSelection()`. ViewModel serializes the selected configs via
   `ConfigSerializer.toJson` and sets `state.pendingExportJson`.
3. The screen observes `pendingExportJson`; when non-null, it fires the SAF
   `CreateDocument` launcher with a suggested filename like
   `veles-bank-configs.json`.
4. The launcher callback returns a `uri`. The screen calls
   `ViewModel.writeExportToUri(uri)`.
5. ViewModel writes the JSON string to the `uri`'s `OutputStream`, clears
   `pendingExportJson`, and sets `state.message = "Exported N configs"`.

## Data Flow — Import

1. User taps **Import** on `BankConfigsScreen`. The screen fires the SAF
   `OpenDocument` launcher (mime `application/json` / `*/*`).
2. The launcher callback receives a `uri`, passed to
   `ViewModel.onImportUri(uri)`.
3. ViewModel reads the `uri`'s `InputStream` and parses via
   `ConfigSerializer.fromJson`.
4. ViewModel diffs parsed configs against existing configs **by name**:
   - `toInsert`: names not present locally.
   - `toOverwrite`: names present locally (existing config objects retained so
     updates can preserve `id`).
5. ViewModel sets `state.importReview = ImportReview(toInsert, toOverwrite,
   parsed)`. The screen renders a summary `AlertDialog`:
   - Title: "Import N configs?"
   - Body lists "New: ..." and "Will replace: ..." names.
   - Confirm → `confirmImport()`: inserts new configs and updates existing ones
     via repository; clears `importReview`; sets
     `state.message = "Imported N configs"`.
   - Cancel → `cancelImport()`: clears `importReview`, stops the entire import
     (nothing is written).

Edge cases:
- Malformed JSON → `message = "Import failed: invalid file"`, no dialog.
- Empty array or no names → `message = "Nothing to import"`.
- All names already exist and user cancels → nothing changes, matching "reject
  = stop entire import".

## State Changes

```kotlin
data class BankConfigsState(
    val configs: List<BankHandlerConfig> = emptyList(),
    val isLoading: Boolean = false,
    val deleteTarget: BankHandlerConfig? = null,
    // NEW
    val exportSelection: ExportSelection? = null,
    val pendingExportJson: String? = null,
    val importReview: ImportReview? = null,
    val message: String? = null,  // transient feedback for snackbar/toast
)

data class ExportSelection(
    val items: List<String>,        // names
    val checked: Set<String>,      // selected names
)

data class ImportReview(
    val toInsert: List<BankConfigJson>,
    val toOverwrite: List<BankHandlerConfig>,  // existing rows to replace
    val parsed: List<BankConfigJson>,         // full parsed payload (for the write step)
)
```

`BankConfigsViewModel` new functions:
- `onExportRequested()` — opens selection dialog (pre-checked = all names).
- `toggleExportItem(name)` — toggles a checkbox.
- `confirmExportSelection()` — serializes selected configs, sets
  `pendingExportJson`.
- `writeExportToUri(uri)` — writes `pendingExportJson` to the uri, clears it.
- `onImportUri(uri)` — reads, parses, diffs, sets `importReview` (or `message`
  on error).
- `confirmImport()` — writes the diff, clears `importReview`, sets success
  `message`.
- `cancelImport()` — clears `importReview`.
- `dismissMessage()` — clears `message`.

## UI Placement

`BankConfigsScreen` header row currently has just "Add". Add `Export` and
`Import` `TextButton`s alongside it, ordered `[Add] [Export] [Import]`. The
screen renders three dialogs conditionally: export multi-select, import review,
plus the existing delete dialog. SAF launchers are created with
`rememberLauncherForActivityResult` and invoked in response to state changes.

## Error Handling & Edge Cases

- **File write failure** (export): `writeExportToUri` catches `IOException`,
  sets `message = "Export failed"`, clears `pendingExportJson`.
- **File read failure** (import): `onImportUri` catches `IOException`, sets
  `message = "Import failed: cannot read file"`.
- **Parse failure**: caught, sets `message = "Import failed: invalid file"`,
  no dialog.
- **Empty selection on export**: `confirmExportSelection` with nothing checked
  → sets `message = "Select at least one config"`, stays in selection dialog.
- **Duplicate names within a single import file**: de-dup by name during
  parse, keeping the final entry; the diff then compares against local.
- **Name collision where local has multiple same-named rows**: importer
  matches the first existing row by name and overwrites it.

## Testing

### Unit tests (JVM)

- **`ConfigSerializerTest`**
  - `toJson` round-trips a config list to the expected JSON shape
    `{name, regex:{otp, amount, merchant}}`.
  - `fromJson` parses valid JSON into `BankConfigJson` list.
  - `fromJson` throws on malformed JSON / missing required fields.
  - `fromJson` handles empty array → empty list.
- **`ConfigImporterTest`**
  - Diff: names in import but not local → `toInsert`.
  - Diff: names in both → `toOverwrite` with the correct existing id for
    updates.
  - Diff: names only local → ignored.
  - `confirmImport` (via ViewModel) calls repository `insert` for new and
    `update` for overwrites with correct field mapping and regenerated
    timestamps.
- **`BankConfigsViewModelTest`** (extend existing)
  - Export: `confirmExportSelection` serializes only checked configs and sets
    `pendingExportJson`.
  - Import: `onImportUri` with mock content sets `importReview` with correct
    insert/overwrite split.
  - Import cancel clears state with no repository writes (verifies "reject =
    stop entire import").
  - Import confirm calls insert/update the expected number of times.
  - Malformed input sets `message`, leaves `importReview` null.

### Instrumented tests (androidTest)

Added to `VelesPermissionsAppTests` (Compose UI) and a new
`ExportImportFlowTest`:

1. **Export happy path** — seed a config, tap Export, confirm selection, grant
   SAF via a stubbed `CreateDocument` returning a temp file uri, assert the
   file contains valid JSON with the expected shape.
2. **Import happy path** — pre-place a JSON file, tap Import, pick it, confirm
   the review dialog shows the right new/overwrite names, tap Confirm, assert
   the list updates via repository.
3. **Import overwrite confirm** — seed a config, import a file with the same
   name but different regexes, assert the review dialog lists it under "Will
   replace", confirm, assert the row's regexes changed in the DB.
4. **Import cancel stops everything** — seed a config, import a file with new
   + overwrite entries, tap Cancel, assert no repository writes occurred (DB
   unchanged).
5. **Import malformed file** — pick an invalid JSON file, assert no review
   dialog appears and a message is shown.

For SAF, use `mockkStatic` on `ContextCompat`/content resolver where needed,
mirroring the existing `NotificationListener` instrumented tests, or drive the
real picker via Compose UI + a stubbed `ActivityResultLauncher`.