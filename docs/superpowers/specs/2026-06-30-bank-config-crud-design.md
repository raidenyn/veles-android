# Bank Config CRUD UI — Design Spec

**Date:** 2026-06-30  
**Status:** Approved

## Overview

Add a standalone screen for managing `BankHandlerConfig` records stored in the local Room database. Users can create, read, update, and delete bank regex templates. A "Bank Configs" button on the main Permissions screen navigates to this feature.

---

## Data Layer

### Changes to `BankHandlerConfigDao`

Existing `getAll()` and `insertAll()` are untouched (used by `NotificationListener` path). Four new suspend methods are added alongside:

```kotlin
@Query("SELECT * FROM bank_handler_configs ORDER BY id ASC")
suspend fun getAllSuspend(): List<BankHandlerConfig>

@Insert
suspend fun insert(config: BankHandlerConfig): Long

@Update
suspend fun update(config: BankHandlerConfig)

@Delete
suspend fun delete(config: BankHandlerConfig)
```

### Changes to `BankHandlerRepository`

Add suspend wrapper methods for each new DAO operation:

```kotlin
suspend fun getAllSuspend(): List<BankHandlerConfig>
suspend fun insert(config: BankHandlerConfig): Long
suspend fun update(config: BankHandlerConfig)
suspend fun delete(config: BankHandlerConfig)
```

**No DB migration required** — schema is unchanged.

---

## Navigation

Two new routes added to the `NavHost` in `VelesPermissionsApp`:

| Route | Screen |
|---|---|
| `bank-configs` | List of all bank configs |
| `bank-config-edit?id={id}` | Create/edit form (`id` absent = new, present = edit) |

`PermissionsScreen` receives a new `onNavigateToBankConfigs: () -> Unit` parameter. A `TextButton("Bank Configs")` is added next to the existing "Test" button.

---

## Package Structure

New files under `otp/config/`:

```
otp/config/
  ui/
    BankConfigsScreen.kt
    BankConfigEditScreen.kt
  viewmodel/
    BankConfigsState.kt
    BankConfigsViewModel.kt
    BankConfigsViewModelFactory.kt
    BankConfigEditState.kt
    BankConfigEditViewModel.kt
    BankConfigEditViewModelFactory.kt
```

---

## Screen: BankConfigsScreen (List)

**Layout:**
- Title "Bank Configs"
- `TextButton("Add")` below the title (matches style of existing "Test" button)
- `LazyColumn` — each row: bank name + Edit icon button (pencil) + Delete icon button (trash)
- `AlertDialog` for delete confirmation (controlled by `deleteTarget` in state)

**State:**

```kotlin
data class BankConfigsState(
    val configs: List<BankHandlerConfig> = emptyList(),
    val isLoading: Boolean = false,
    val deleteTarget: BankHandlerConfig? = null
)
```

**ViewModel behaviour:**
- Loads list on init via `viewModelScope.launch(Dispatchers.IO)`
- `requestDelete(config)` sets `deleteTarget` to show the confirmation dialog
- `cancelDelete()` clears `deleteTarget`
- `confirmDelete()` deletes the target and reloads the list, then clears `deleteTarget`

---

## Screen: BankConfigEditScreen (Create / Edit)

**Layout:**
- Title: "New Bank Config" (no id) or "Edit Bank Config" (id present)
- `OutlinedTextField` for each of: Name, OTP Regex, Money Regex, Merchant Regex
- Inline error text below each field when validation fails
- `Button("Save")` — disabled while `isSaving` is true

**State:**

```kotlin
data class BankConfigEditState(
    val name: String = "",
    val otpRegex: String = "",
    val moneyRegex: String = "",
    val merchantRegex: String = "",
    val nameError: String? = null,
    val otpRegexError: String? = null,
    val moneyRegexError: String? = null,
    val merchantRegexError: String? = null,
    val isSaving: Boolean = false,
    val savedSuccessfully: Boolean = false
)
```

**ViewModel behaviour:**
- If `id != null`: loads existing config on init via `Dispatchers.IO` and populates state fields
- `save()`: validates all fields, sets error messages on invalid fields, aborts if any error; on pass sets `isSaving = true`, persists via `insert` or `update`, then sets `savedSuccessfully = true`
- Screen observes `savedSuccessfully` via `LaunchedEffect` and calls `navController.popBackStack()` on true

**Validation:**
- Name: must not be blank
- OTP Regex, Money Regex, Merchant Regex: must be syntactically valid — checked with `Regex(pattern)` in a try/catch on `PatternSyntaxException`
- Validation runs on Save tap only (not on every keystroke)

---

## Files Modified

| File | Change |
|---|---|
| `BankHandlerConfigDao.kt` | Add 4 suspend methods |
| `BankHandlerRepository.kt` | Add 4 suspend wrappers |
| `VelesPermissionsApp.kt` | Add 2 new `composable` routes |
| `PermissionsScreen.kt` | Add `onNavigateToBankConfigs` param + TextButton |
