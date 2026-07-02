# Bank Config Export/Import Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add export and import of bank regex templates via JSON files on the `BankConfigsScreen`, with a multi-select export dialog, a confirm-overwrite summary dialog on import, and SAF (Storage Access Framework) for file picking.

**Architecture:** A pure `ConfigSerializer` (kotlinx.serialization) and a pure `ConfigImporter` (diff by name) sit in a new `otp/config/io/` package with no Android dependencies, so they are fully unit-testable on the JVM. The `BankConfigsViewModel` orchestrates them: it holds export/import dialog state in `BankConfigsState`, performs repository writes only on user confirmation, and exposes JSON content + `Uri` handoff via state fields. The `BankConfigsScreen` owns the SAF `ActivityResultLauncher`s (`CreateDocument` for export, `OpenDocument` for import) and bridges `Uri` results back to the ViewModel.

**Tech Stack:** Kotlin, kotlinx.serialization.json (already a dependency), Jetpack Compose, androidx.activity.result (SAF), MockK, kotlinx-coroutines-test

## Global Constraints

- minSdk 33, compileSdk 35, JVM target 17
- detekt `MaxLineLength = 140`; keep lines under 140 chars
- No new gradle dependencies — `kotlinx.serialization.json` and `androidx.activity.compose` are already wired
- Unit tests are JVM-only (no Android runtime), located in `app/src/test/`, run with `./gradlew testDebugUnitTest`
- Instrumented tests live in `app/src/androidTest/`, run with `./gradlew connectedDebugAndroidTest`
- Build verification: `./gradlew assembleDebug`
- Existing `getAll()` / `insertAll()` on DAO and Repository must not be removed or renamed
- Import matches existing configs by `name`; cancel stops the entire import (no writes)
- No DB migration — schema is unchanged

---

## File Map

| Action | Path |
|---|---|
| Create | `app/src/main/java/me/nagaev/veles/otp/config/io/BankConfigJson.kt` |
| Create | `app/src/main/java/me/nagaev/veles/otp/config/io/ConfigSerializer.kt` |
| Create | `app/src/main/java/me/nagaev/veles/otp/config/io/ConfigImporter.kt` |
| Create | `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/ExportSelection.kt` |
| Create | `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/ImportReview.kt` |
| Modify | `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsState.kt` |
| Modify | `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModel.kt` |
| Modify | `app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigsScreen.kt` |
| Modify | `app/src/main/java/me/nagaev/veles/permissions/ui/VelesPermissionsApp.kt` |
| Modify | `app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt` |
| Create | `app/src/test/java/me/nagaev/veles/otp/config/io/ConfigSerializerTest.kt` |
| Create | `app/src/test/java/me/nagaev/veles/otp/config/io/ConfigImporterTest.kt` |
| Modify | `app/src/test/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModelTest.kt` |
| Create | `app/src/androidTest/java/me/nagaev/veles/otp/config/ExportImportFlowTest.kt` |

---

## Task 1: Serialization model and pure serializer

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/otp/config/io/BankConfigJson.kt`
- Create: `app/src/main/java/me/nagaev/veles/otp/config/io/ConfigSerializer.kt`
- Test: `app/src/test/java/me/nagaev/veles/otp/config/io/ConfigSerializerTest.kt`

**Interfaces:**
- Produces: `BankConfigJson` (`@Serializable` data class), `ConfigSerializer.toJson(List<BankHandlerConfig>): String`, `ConfigSerializer.fromJson(String): List<BankConfigJson>`. Used by Task 3 (Importer) and Task 4 (ViewModel).

- [ ] **Step 1: Write the failing test**

Create `app/src/test/java/me/nagaev/veles/otp/config/io/ConfigSerializerTest.kt`:

```kotlin
package me.nagaev.veles.otp.config.io

import kotlinx.serialization.SerializationException
import me.nagaev.veles.otp.config.BankHandlerConfig
import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

class ConfigSerializerTest {
    private val config = BankHandlerConfig(
        id = 1L,
        name = "UOB Thailand",
        otpRegex = """ (\w{4})-(\d{6}) """,
        moneyRegex = """of ([A-Z]{3})(\d+)""",
        merchantRegex = """at (.+)""",
        createdAt = 1000L,
        updatedAt = 2000L,
    )

    @Test
    fun `toJson produces nested regex shape and omits id and timestamps`() {
        val json = ConfigSerializer.toJson(listOf(config))
        assertTrue(json.contains("\"regex\""))
        assertTrue(json.contains("\"otp\""))
        assertTrue(json.contains("\"amount\""))
        assertTrue(json.contains("\"merchant\""))
        assertTrue(json.contains("\"UOB Thailand\""))
        assertTrue(!json.contains("\"id\""))
        assertTrue(!json.contains("\"createdAt\""))
        assertTrue(!json.contains("\"updatedAt\""))
    }

    @Test
    fun `fromJson parses valid JSON into BankConfigJson list`() {
        val json = """
            [{"name":"UOB Thailand","regex":{"otp":"a","amount":"b","merchant":"c"}}]
        """.trimIndent()
        val parsed = ConfigSerializer.fromJson(json)
        assertEquals(1, parsed.size)
        assertEquals("UOB Thailand", parsed[0].name)
        assertEquals("a", parsed[0].regex.otp)
        assertEquals("b", parsed[0].regex.amount)
        assertEquals("c", parsed[0].regex.merchant)
    }

    @Test
    fun `fromJson handles empty array`() {
        val parsed = ConfigSerializer.fromJson("[]")
        assertTrue(parsed.isEmpty())
    }

    @Test(expected = SerializationException::class)
    fun `fromJson throws on malformed JSON`() {
        ConfigSerializer.fromJson("{ not json")
    }

    @Test(expected = SerializationException::class)
    fun `fromJson throws when a required regex field is missing`() {
        val json = """[{"name":"X","regex":{"otp":"a","amount":"b"}}]"""
        ConfigSerializer.fromJson(json)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.io.ConfigSerializerTest"`
Expected: FAIL with unresolved reference `ConfigSerializer` / `BankConfigJson`.

- [ ] **Step 3: Create `BankConfigJson`**

Create `app/src/main/java/me/nagaev/veles/otp/config/io/BankConfigJson.kt`:

```kotlin
package me.nagaev.veles.otp.config.io

import kotlinx.serialization.Serializable

@Serializable
data class BankConfigJson(
    val name: String,
    val regex: RegexJson,
)

@Serializable
data class RegexJson(
    val otp: String,
    val amount: String,
    val merchant: String,
)
```

- [ ] **Step 4: Create `ConfigSerializer`**

Create `app/src/main/java/me/nagaev/veles/otp/config/io/ConfigSerializer.kt`:

```kotlin
package me.nagaev.veles.otp.config.io

import kotlinx.serialization.builtins.ListSerializer
import kotlinx.serialization.json.Json
import me.nagaev.veles.otp.config.BankHandlerConfig

object ConfigSerializer {
    private val json = Json {
        prettyPrint = true
        ignoreUnknownKeys = false
        encodeDefaults = true
    }
    private val listSerializer = ListSerializer(BankConfigJson.serializer())

    fun toJson(configs: List<BankHandlerConfig>): String {
        val payload = configs.map {
            BankConfigJson(
                name = it.name,
                regex = RegexJson(
                    otp = it.otpRegex,
                    amount = it.moneyRegex,
                    merchant = it.merchantRegex,
                ),
            )
        }
        return json.encodeToString(listSerializer, payload)
    }

    fun fromJson(text: String): List<BankConfigJson> =
        json.decodeFromString(listSerializer, text)
}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.io.ConfigSerializerTest"`
Expected: PASS (all 5 tests).

- [ ] **Step 6: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/config/io/ \
    app/src/test/java/me/nagaev/veles/otp/config/io/ConfigSerializerTest.kt
git commit -m "feat: add ConfigSerializer for bank config JSON export/import"
```

---

## Task 2: Pure import diff logic

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/otp/config/io/ConfigImporter.kt`
- Test: `app/src/test/java/me/nagaev/veles/otp/config/io/ConfigImporterTest.kt`

**Interfaces:**
- Consumes: `BankConfigJson` (Task 1), `BankHandlerConfig`
- Produces: `ConfigImporter.Diff(toInsert: List<BankConfigJson>, toOverwrite: List<Pair<BankHandlerConfig, BankConfigJson>>)`. Used by Task 4 (ViewModel).

- [ ] **Step 1: Write the failing test**

Create `app/src/test/java/me/nagaev/veles/otp/config/io/ConfigImporterTest.kt`:

```kotlin
package me.nagaev.veles.otp.config.io

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test
import me.nagaev.veles.otp.config.BankHandlerConfig

class ConfigImporterTest {
    private val existing = BankHandlerConfig(
        id = 5L,
        name = "UOB Thailand",
        otpRegex = "old-otp",
        moneyRegex = "old-amount",
        merchantRegex = "old-merchant",
        createdAt = 1000L,
        updatedAt = 1000L,
    )

    private fun json(name: String, otp: String = "x") =
        BankConfigJson(name, RegexJson(otp, "amt", "mer"))

    @Test
    fun `diff classifies names not present locally as toInsert`() {
        val parsed = listOf(json("New Bank"))
        val diff = ConfigImporter.diff(parsed, listOf(existing))
        assertEquals(1, diff.toInsert.size)
        assertEquals("New Bank", diff.toInsert[0].name)
        assertTrue(diff.toOverwrite.isEmpty())
    }

    @Test
    fun `diff classifies matching names as toOverwrite preserving existing id`() {
        val parsed = listOf(json("UOB Thailand", otp = "new-otp"))
        val diff = ConfigImporter.diff(parsed, listOf(existing))
        assertTrue(diff.toInsert.isEmpty())
        assertEquals(1, diff.toOverwrite.size)
        val (existingRow, incoming) = diff.toOverwrite[0]
        assertEquals(5L, existingRow.id)
        assertEquals("new-otp", incoming.regex.otp)
    }

    @Test
    fun `diff ignores local configs not present in import`() {
        val parsed = listOf(json("Other Bank"))
        val diff = ConfigImporter.diff(parsed, listOf(existing))
        assertEquals(1, diff.toInsert.size)
        assertTrue(diff.toOverwrite.isEmpty())
    }

    @Test
    fun `diff de-duplicates duplicate names in import keeping the last entry`() {
        val parsed = listOf(json("Dup", "first"), json("Dup", "second"))
        val diff = ConfigImporter.diff(parsed, emptyList())
        assertEquals(1, diff.toInsert.size)
        assertEquals("second", diff.toInsert[0].regex.otp)
    }

    @Test
    fun `diff matches first existing row when local has duplicate names`() {
        val existingDup = existing.copy(id = 9L, name = "UOB Thailand")
        val parsed = listOf(json("UOB Thailand", "new"))
        val diff = ConfigImporter.diff(parsed, listOf(existing, existingDup))
        assertEquals(1, diff.toOverwrite.size)
        assertEquals(5L, diff.toOverwrite[0].first.id)
    }
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.io.ConfigImporterTest"`
Expected: FAIL with unresolved reference `ConfigImporter`.

- [ ] **Step 3: Create `ConfigImporter`**

Create `app/src/main/java/me/nagaev/veles/otp/config/io/ConfigImporter.kt`:

```kotlin
package me.nagaev.veles.otp.config.io

import me.nagaev.veles.otp.config.BankHandlerConfig

object ConfigImporter {
    data class Diff(
        val toInsert: List<BankConfigJson>,
        val toOverwrite: List<Pair<BankHandlerConfig, BankConfigJson>>,
    )

    fun diff(parsed: List<BankConfigJson>, existing: List<BankHandlerConfig>): Diff {
        val deduped = LinkedHashMap<String, BankConfigJson>()
        for (entry in parsed) {
            deduped[entry.name] = entry
        }
        val toInsert = mutableListOf<BankConfigJson>()
        val toOverwrite = mutableListOf<Pair<BankHandlerConfig, BankConfigJson>>()
        for (incoming in deduped.values) {
            val match = existing.firstOrNull { it.name == incoming.name }
            if (match == null) {
                toInsert.add(incoming)
            } else {
                toOverwrite.add(match to incoming)
            }
        }
        return Diff(toInsert, toOverwrite)
    }
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.io.ConfigImporterTest"`
Expected: PASS (all 5 tests).

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/config/io/ConfigImporter.kt \
    app/src/test/java/me/nagaev/veles/otp/config/io/ConfigImporterTest.kt
git commit -m "feat: add ConfigImporter diff-by-name logic"
```

---

## Task 3: ViewModel export/import state and handlers

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/ExportSelection.kt`
- Create: `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/ImportReview.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsState.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModel.kt`
- Test: `app/src/test/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModelTest.kt` (extend)

**Interfaces:**
- Consumes: `ConfigSerializer` (Task 1), `ConfigImporter` (Task 2), `BankHandlerRepository`
- Produces: `BankConfigsViewModel.onExportRequested()`, `.toggleExportItem(name)`, `.confirmExportSelection()`, `.writeExportToUri(context, uri)`, `.onImportUri(context, uri)`, `.confirmImport()`, `.cancelImport()`, `.dismissMessage()`. State fields `exportSelection`, `pendingExportJson`, `importReview`, `message`. Used by Task 4 (Screen).

- [ ] **Step 1: Add the failing tests**

Append to `app/src/test/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModelTest.kt`. Add these imports at the top of the file (keeping the existing imports):

```kotlin
import android.content.Context
import io.mockk.coJustRun
import io.mockk.every
import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import me.nagaev.veles.otp.config.io.ConfigSerializer
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
```

Append these test methods inside the class (after the existing tests):

```kotlin
    @Test
    fun `onExportRequested opens selection dialog with all names checked`() {
        val vm = BankConfigsViewModel(repository)
        vm.onExportRequested()
        val sel = vm.state.value.exportSelection
        assertNotNull(sel)
        assertEquals(setOf("Test Bank"), sel!!.checked)
        assertEquals(listOf("Test Bank"), sel.items)
    }

    @Test
    fun `toggleExportItem toggles checked set`() {
        val vm = BankConfigsViewModel(repository)
        vm.onExportRequested()
        vm.toggleExportItem("Test Bank")
        assertTrue(vm.state.value.exportSelection!!.checked.isEmpty())
        vm.toggleExportItem("Test Bank")
        assertEquals(setOf("Test Bank"), vm.state.value.exportSelection!!.checked)
    }

    @Test
    fun `confirmExportSelection with empty selection sets message and stays in dialog`() {
        val vm = BankConfigsViewModel(repository)
        vm.onExportRequested()
        vm.toggleExportItem("Test Bank")
        vm.confirmExportSelection()
        assertEquals("Select at least one config", vm.state.value.message)
        assertNotNull(vm.state.value.exportSelection)
        assertNull(vm.state.value.pendingExportJson)
    }

    @Test
    fun `confirmExportSelection serializes checked configs and clears selection`() {
        val vm = BankConfigsViewModel(repository)
        vm.onExportRequested()
        vm.confirmExportSelection()
        val json = vm.state.value.pendingExportJson
        assertNotNull(json)
        assertTrue(json!!.contains("Test Bank"))
        assertTrue(json.contains("\"regex\""))
        assertNull(vm.state.value.exportSelection)
    }

    @Test
    fun `writeExportToUri writes pending json and clears it`() {
        val context = mockk<Context>()
        val out = ByteArrayOutputStream()
        every { context.contentResolver.openOutputStream(any()) } returns out
        val vm = BankConfigsViewModel(repository)
        vm.onExportRequested()
        vm.confirmExportSelection()
        vm.writeExportToUri(context, android.net.Uri.parse("content://x/y"))
        assertTrue(out.toString().contains("Test Bank"))
        assertNull(vm.state.value.pendingExportJson)
    }

    @Test
    fun `onImportUri parses and sets importReview with insert and overwrite split`() {
        val context = mockk<Context>()
        val json = ConfigSerializer.toJson(
            listOf(
                config.copy(name = "New Bank"),
                config,
            ),
        )
        every { context.contentResolver.openInputStream(any()) }
            .returns(ByteArrayInputStream(json.toByteArray()))
        val vm = BankConfigsViewModel(repository)
        vm.onImportUri(context, android.net.Uri.parse("content://x/y"))
        val review = vm.state.value.importReview
        assertNotNull(review)
        assertEquals(1, review!!.toInsert.size)
        assertEquals("New Bank", review.toInsert[0].name)
        assertEquals(1, review.toOverwrite.size)
        assertEquals("Test Bank", review.toOverwrite[0].name)
    }

    @Test
    fun `onImportUri with malformed json sets message and no review`() {
        val context = mockk<Context>()
        every { context.contentResolver.openInputStream(any()) }
            .returns(ByteArrayInputStream("{bad".toByteArray()))
        val vm = BankConfigsViewModel(repository)
        vm.onImportUri(context, android.net.Uri.parse("content://x/y"))
        assertNull(vm.state.value.importReview)
        assertNotNull(vm.state.value.message)
    }

    @Test
    fun `confirmImport inserts new and updates overwrite rows then clears review`() {
        coJustRun { repository.insert(any()) }
        coEvery { repository.update(any()) } returns Unit
        val context = mockk<Context>()
        val json = ConfigSerializer.toJson(
            listOf(
                config.copy(name = "New Bank"),
                config,
            ),
        )
        every { context.contentResolver.openInputStream(any()) }
            .returns(ByteArrayInputStream(json.toByteArray()))
        val vm = BankConfigsViewModel(repository)
        vm.onImportUri(context, android.net.Uri.parse("content://x/y"))
        vm.confirmImport()
        coVerify(exactly = 1) { repository.insert(any()) }
        coVerify(exactly = 1) { repository.update(any()) }
        assertNull(vm.state.value.importReview)
    }

    @Test
    fun `cancelImport clears review with no repository writes`() {
        coJustRun { repository.insert(any()) }
        coEvery { repository.update(any()) } returns Unit
        val context = mockk<Context>()
        val json = ConfigSerializer.toJson(
            listOf(
                config.copy(name = "New Bank"),
                config,
            ),
        )
        every { context.contentResolver.openInputStream(any()) }
            .returns(ByteArrayInputStream(json.toByteArray()))
        val vm = BankConfigsViewModel(repository)
        vm.onImportUri(context, android.net.Uri.parse("content://x/y"))
        vm.cancelImport()
        assertNull(vm.state.value.importReview)
        coVerify(exactly = 0) { repository.insert(any()) }
        coVerify(exactly = 0) { repository.update(any()) }
    }
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.viewmodel.BankConfigsViewModelTest"`
Expected: FAIL with unresolved references `onExportRequested`, `exportSelection`, etc.

- [ ] **Step 3: Create `ExportSelection` and `ImportReview`**

Create `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/ExportSelection.kt`:

```kotlin
package me.nagaev.veles.otp.config.viewmodel

data class ExportSelection(
    val items: List<String>,
    val checked: Set<String>,
)
```

Create `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/ImportReview.kt`:

```kotlin
package me.nagaev.veles.otp.config.viewmodel

import me.nagaev.veles.otp.config.BankHandlerConfig
import me.nagaev.veles.otp.config.io.BankConfigJson
import me.nagaev.veles.otp.config.io.ConfigImporter

data class ImportReview(
    val toInsert: List<BankConfigJson>,
    val toOverwrite: List<Pair<BankHandlerConfig, BankConfigJson>>,
) {
    val totalConfigs: Int get() = toInsert.size + toOverwrite.size

    companion object {
        fun from(diff: ConfigImporter.Diff): ImportReview =
            ImportReview(diff.toInsert, diff.toOverwrite)
    }
}
```

- [ ] **Step 4: Extend `BankConfigsState`**

Replace `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsState.kt` with:

```kotlin
package me.nagaev.veles.otp.config.viewmodel

import me.nagaev.veles.otp.config.BankHandlerConfig

data class BankConfigsState(
    val configs: List<BankHandlerConfig> = emptyList(),
    val isLoading: Boolean = false,
    val deleteTarget: BankHandlerConfig? = null,
    val exportSelection: ExportSelection? = null,
    val pendingExportJson: String? = null,
    val importReview: ImportReview? = null,
    val message: String? = null,
)
```

- [ ] **Step 5: Extend `BankConfigsViewModel`**

Replace `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModel.kt` with:

```kotlin
package me.nagaev.veles.otp.config.viewmodel

import android.content.Context
import android.net.Uri
import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlinx.coroutines.Dispatchers
import me.nagaev.veles.otp.config.BankHandlerConfig
import me.nagaev.veles.otp.config.BankHandlerRepository
import me.nagaev.veles.otp.config.io.ConfigImporter
import me.nagaev.veles.otp.config.io.ConfigSerializer

class BankConfigsViewModel(
    private val repository: BankHandlerRepository,
) : ViewModel() {

    private val _state = MutableStateFlow(BankConfigsState())
    val state: StateFlow<BankConfigsState> = _state

    init {
        refresh()
    }

    fun refresh() {
        viewModelScope.launch { reload() }
    }

    fun requestDelete(config: BankHandlerConfig) {
        _state.update { it.copy(deleteTarget = config) }
    }

    fun cancelDelete() {
        _state.update { it.copy(deleteTarget = null) }
    }

    fun confirmDelete() {
        val target = _state.value.deleteTarget ?: return
        _state.update { it.copy(deleteTarget = null) }
        viewModelScope.launch {
            repository.delete(target)
            reload()
        }
    }

    fun onExportRequested() {
        val names = _state.value.configs.map { it.name }
        _state.update {
            it.copy(exportSelection = ExportSelection(items = names, checked = names.toSet()))
        }
    }

    fun toggleExportItem(name: String) {
        _state.update { current ->
            val sel = current.exportSelection ?: return@update current
            val next = if (name in sel.checked) sel.checked - name else sel.checked + name
            current.copy(exportSelection = sel.copy(checked = next))
        }
    }

    fun cancelExportSelection() {
        _state.update { it.copy(exportSelection = null) }
    }

    fun confirmExportSelection() {
        val sel = _state.value.exportSelection ?: return
        if (sel.checked.isEmpty()) {
            _state.update { it.copy(message = "Select at least one config") }
            return
        }
        val selected = _state.value.configs.filter { it.name in sel.checked }
        val json = ConfigSerializer.toJson(selected)
        _state.update {
            it.copy(exportSelection = null, pendingExportJson = json)
        }
    }

    fun writeExportToUri(context: Context, uri: Uri) {
        val json = _state.value.pendingExportJson ?: return
        viewModelScope.launch {
            withContext(Dispatchers.IO) {
                context.contentResolver.openOutputStream(uri)?.use { out ->
                    out.write(json.toByteArray())
                }
            }
            _state.update {
                it.copy(
                    pendingExportJson = null,
                    message = "Exported ${it.configs.count { c -> c.name in (it.configs.map { c -> c.name }) }} configs",
                )
            }
        }
    }

    fun onImportUri(context: Context, uri: Uri) {
        viewModelScope.launch {
            val text = withContext(Dispatchers.IO) {
                context.contentResolver.openInputStream(uri)?.use { it.readBytes().toString(Charsets.UTF_8) }
            } ?: run {
                _state.update { it.copy(message = "Import failed: cannot read file") }
                return@launch
            }
            try {
                val parsed = ConfigSerializer.fromJson(text)
                if (parsed.isEmpty()) {
                    _state.update { it.copy(message = "Nothing to import") }
                    return@launch
                }
                val diff = ConfigImporter.diff(parsed, _state.value.configs)
                _state.update {
                    it.copy(importReview = ImportReview.from(diff))
                }
            } catch (e: Exception) {
                _state.update { it.copy(message = "Import failed: invalid file") }
            }
        }
    }

    fun confirmImport() {
        val review = _state.value.importReview ?: return
        viewModelScope.launch {
            val now = System.currentTimeMillis()
            for (incoming in review.toInsert) {
                repository.insert(
                    BankHandlerConfig(
                        id = 0L,
                        name = incoming.name,
                        otpRegex = incoming.regex.otp,
                        moneyRegex = incoming.regex.amount,
                        merchantRegex = incoming.regex.merchant,
                        createdAt = now,
                        updatedAt = now,
                    ),
                )
            }
            for ((existing, incoming) in review.toOverwrite) {
                repository.update(
                    existing.copy(
                        otpRegex = incoming.regex.otp,
                        moneyRegex = incoming.regex.amount,
                        merchantRegex = incoming.regex.merchant,
                        updatedAt = now,
                    ),
                )
            }
            _state.update {
                it.copy(
                    importReview = null,
                    message = "Imported ${review.totalConfigs} configs",
                )
            }
            reload()
        }
    }

    fun cancelImport() {
        _state.update { it.copy(importReview = null) }
    }

    fun dismissMessage() {
        _state.update { it.copy(message = null) }
    }

    private suspend fun reload() {
        _state.update { it.copy(isLoading = true) }
        val configs = repository.getAllSuspend()
        _state.update { it.copy(configs = configs, isLoading = false) }
    }
}
```

Note: the "Exported N configs" message uses the number of configs that were selected. Since `pendingExportJson` is cleared in the same update, capture the count before clearing. The implementation above references `it.configs` for the count — to keep it accurate, replace the count line with a pre-captured value. Concretely, change `writeExportToUri` to capture the count locally:

```kotlin
    fun writeExportToUri(context: Context, uri: Uri) {
        val json = _state.value.pendingExportJson ?: return
        val exportedCount = _state.value.configs.count { it.name in (_state.value.exportSelection?.checked ?: emptySet()) }
        viewModelScope.launch {
            withContext(Dispatchers.IO) {
                context.contentResolver.openOutputStream(uri)?.use { out ->
                    out.write(json.toByteArray())
                }
            }
            _state.update {
                it.copy(
                    pendingExportJson = null,
                    message = "Exported $exportedCount configs",
                )
            }
        }
    }
```

(The second version above is the canonical one — use it. `exportSelection` is already null by the time `writeExportToUri` is called, so capture the count from `pendingExportJson` parse instead. Simplest correct approach: count the selected names from `pendingExportJson` by re-parsing. To keep this task self-contained and avoid fragile re-parsing, capture the count in `confirmExportSelection` and stash it in state.)

To resolve this cleanly, add a `pendingExportCount: Int?` field. Apply this final version of the three pieces instead of the earlier drafts:

`BankConfigsState` (final):

```kotlin
package me.nagaev.veles.otp.config.viewmodel

import me.nagaev.veles.otp.config.BankHandlerConfig

data class BankConfigsState(
    val configs: List<BankHandlerConfig> = emptyList(),
    val isLoading: Boolean = false,
    val deleteTarget: BankHandlerConfig? = null,
    val exportSelection: ExportSelection? = null,
    val pendingExportJson: String? = null,
    val pendingExportCount: Int? = null,
    val importReview: ImportReview? = null,
    val message: String? = null,
)
```

`confirmExportSelection` (final):

```kotlin
    fun confirmExportSelection() {
        val sel = _state.value.exportSelection ?: return
        if (sel.checked.isEmpty()) {
            _state.update { it.copy(message = "Select at least one config") }
            return
        }
        val selected = _state.value.configs.filter { it.name in sel.checked }
        val json = ConfigSerializer.toJson(selected)
        _state.update {
            it.copy(
                exportSelection = null,
                pendingExportJson = json,
                pendingExportCount = selected.size,
            )
        }
    }
```

`writeExportToUri` (final):

```kotlin
    fun writeExportToUri(context: Context, uri: Uri) {
        val json = _state.value.pendingExportJson ?: return
        val count = _state.value.pendingExportCount ?: 0
        viewModelScope.launch {
            withContext(Dispatchers.IO) {
                context.contentResolver.openOutputStream(uri)?.use { out ->
                    out.write(json.toByteArray())
                }
            }
            _state.update {
                it.copy(
                    pendingExportJson = null,
                    pendingExportCount = null,
                    message = "Exported $count configs",
                )
            }
        }
    }
```

(Add `coJustRun` is already imported in the test step; the existing `coEvery { repository.getAllSuspend() } returns listOf(config)` covers reload calls.)

- [ ] **Step 6: Run tests to verify they pass**

Run: `./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.viewmodel.BankConfigsViewModelTest"`
Expected: PASS (all tests, old + new).

- [ ] **Step 7: Build to verify no compilation errors**

Run: `./gradlew assembleDebug`
Expected: `BUILD SUCCESSFUL`.

- [ ] **Step 8: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/config/viewmodel/ \
    app/src/test/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModelTest.kt
git commit -m "feat: add export/import state and handlers to BankConfigsViewModel"
```

---

## Task 4: Screen UI — Export/Import buttons, dialogs, SAF launchers

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigsScreen.kt`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/ui/VelesPermissionsApp.kt`

**Interfaces:**
- Consumes: `BankConfigsViewModel` callbacks (Task 3), `BankConfigsState` dialog fields
- Produces: A `BankConfigsScreen` with new parameters `onExport`, `onImport`, plus the SAF wiring in `VelesPermissionsApp`.

- [ ] **Step 1: Add test tags**

Replace `app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt` with:

```kotlin
package me.nagaev.veles.common.ui

import me.nagaev.veles.permissions.services.PermissionType

object TestTags {
    const val NOTIFICATION_LISTENER_STATUS = "notification_listener_status"
    const val TEST_INPUT = "test_input"
    const val TEST_SEND_BUTTON = "test_send_button"
    const val TEST_RESULT = "test_result"
    const val REDACTION_OPEN_SETTINGS = "redaction_open_settings"
    val PERMISSION_STATUS = { state: PermissionType -> "permission_status_$state" }

    const val BANK_CONFIG_EXPORT_BUTTON = "bank_config_export_button"
    const val BANK_CONFIG_IMPORT_BUTTON = "bank_config_import_button"
    const val BANK_CONFIG_EXPORT_DIALOG = "bank_config_export_dialog"
    const val BANK_CONFIG_IMPORT_DIALOG = "bank_config_import_dialog"
    const val BANK_CONFIG_IMPORT_CONFIRM = "bank_config_import_confirm"
    const val BANK_CONFIG_IMPORT_CANCEL = "bank_config_import_cancel"
    const val BANK_CONFIG_EXPORT_CONFIRM = "bank_config_export_confirm"
}
```

- [ ] **Step 2: Update `BankConfigsScreen`**

Replace `app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigsScreen.kt` with:

```kotlin
package me.nagaev.veles.otp.config.ui

import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.filled.Delete
import androidx.compose.material.icons.filled.Edit
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.otp.config.BankHandlerConfig
import me.nagaev.veles.otp.config.viewmodel.BankConfigsState
import me.nagaev.veles.otp.config.viewmodel.ExportSelection
import me.nagaev.veles.otp.config.viewmodel.ImportReview

@Composable
fun BankConfigsScreen(
    state: BankConfigsState,
    onAdd: () -> Unit,
    onEdit: (Long) -> Unit,
    onRequestDelete: (BankHandlerConfig) -> Unit,
    onCancelDelete: () -> Unit,
    onConfirmDelete: () -> Unit,
    onExport: () -> Unit,
    onToggleExportItem: (String) -> Unit,
    onCancelExportSelection: () -> Unit,
    onConfirmExportSelection: () -> Unit,
    onImport: () -> Unit,
    onConfirmImport: () -> Unit,
    onCancelImport: () -> Unit,
    onDismissMessage: () -> Unit,
    modifier: Modifier = Modifier,
) {
    Box(modifier = modifier.fillMaxSize()) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 16.dp)
                .statusBarsPadding(),
        ) {
            Text(
                text = "Bank Configs",
                style = MaterialTheme.typography.titleLarge,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.padding(vertical = 10.dp),
            )
            Row(
                modifier = Modifier.fillMaxWidth(),
                horizontalArrangement = Arrangement.spacedBy(8.dp),
            ) {
                TextButton(onClick = onAdd) { Text("Add") }
                TextButton(
                    onClick = onExport,
                    modifier = Modifier.testTag(TestTags.BANK_CONFIG_EXPORT_BUTTON),
                ) { Text("Export") }
                TextButton(
                    onClick = onImport,
                    modifier = Modifier.testTag(TestTags.BANK_CONFIG_IMPORT_BUTTON),
                ) { Text("Import") }
            }
            if (state.isLoading) {
                CircularProgressIndicator(
                    modifier = Modifier
                        .align(Alignment.CenterHorizontally)
                        .padding(top = 32.dp),
                )
            } else {
                LazyColumn {
                    items(state.configs, key = { it.id }) { config ->
                        BankConfigRow(
                            config = config,
                            onEdit = { onEdit(config.id) },
                            onDelete = { onRequestDelete(config) },
                        )
                    }
                }
            }
        }

        if (state.deleteTarget != null) {
            AlertDialog(
                onDismissRequest = onCancelDelete,
                title = { Text("Delete \"${state.deleteTarget.name}\"?") },
                text = { Text("This bank config will be permanently removed.") },
                confirmButton = {
                    TextButton(onClick = onConfirmDelete) { Text("Delete") }
                },
                dismissButton = {
                    TextButton(onClick = onCancelDelete) { Text("Cancel") }
                },
            )
        }

        if (state.exportSelection != null) {
            ExportSelectionDialog(
                selection = state.exportSelection,
                onToggle = onToggleExportItem,
                onConfirm = onConfirmExportSelection,
                onDismiss = onCancelExportSelection,
            )
        }

        if (state.importReview != null) {
            ImportReviewDialog(
                review = state.importReview!!,
                onConfirm = onConfirmImport,
                onDismiss = onCancelImport,
            )
        }

        if (state.message != null) {
            AlertDialog(
                onDismissRequest = onDismissMessage,
                confirmButton = {
                    TextButton(onClick = onDismissMessage) { Text("OK") }
                },
                title = { Text("Veles") },
                text = { Text(state.message!!) },
            )
        }
    }
}

@Composable
private fun ExportSelectionDialog(
    selection: ExportSelection,
    onToggle: (String) -> Unit,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        modifier = Modifier.testTag(TestTags.BANK_CONFIG_EXPORT_DIALOG),
        title = { Text("Export configs") },
        text = {
            Column {
                selection.items.forEach { name ->
                    Row(
                        modifier = Modifier
                            .fillMaxWidth()
                            .padding(vertical = 2.dp),
                        verticalAlignment = Alignment.CenterVertically,
                    ) {
                        Checkbox(
                            checked = name in selection.checked,
                            onCheckedChange = { onToggle(name) },
                        )
                        Text(name, style = MaterialTheme.typography.bodyLarge)
                    }
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = onConfirm,
                modifier = Modifier.testTag(TestTags.BANK_CONFIG_EXPORT_CONFIRM),
            ) { Text("Export") }
        },
        dismissButton = {
            TextButton(onClick = onDismiss) { Text("Cancel") }
        },
    )
}

@Composable
private fun ImportReviewDialog(
    review: ImportReview,
    onConfirm: () -> Unit,
    onDismiss: () -> Unit,
) {
    AlertDialog(
        onDismissRequest = onDismiss,
        modifier = Modifier.testTag(TestTags.BANK_CONFIG_IMPORT_DIALOG),
        title = { Text("Import ${review.totalConfigs} configs?") },
        text = {
            Column {
                if (review.toInsert.isNotEmpty()) {
                    Text("New:", style = MaterialTheme.typography.titleSmall)
                    review.toInsert.forEach { Text("- ${it.name}") }
                }
                if (review.toOverwrite.isNotEmpty()) {
                    Text("Will replace:", style = MaterialTheme.typography.titleSmall)
                    review.toOverwrite.forEach { (existing, _) ->
                        Text("- ${existing.name}")
                    }
                }
            }
        },
        confirmButton = {
            TextButton(
                onClick = onConfirm,
                modifier = Modifier.testTag(TestTags.BANK_CONFIG_IMPORT_CONFIRM),
            ) { Text("Import") }
        },
        dismissButton = {
            TextButton(
                onClick = onDismiss,
                modifier = Modifier.testTag(TestTags.BANK_CONFIG_IMPORT_CANCEL),
            ) { Text("Cancel") }
        },
    )
}

@Composable
private fun BankConfigRow(
    config: BankHandlerConfig,
    onEdit: () -> Unit,
    onDelete: () -> Unit,
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically,
    ) {
        Text(
            text = config.name,
            style = MaterialTheme.typography.bodyLarge,
            modifier = Modifier.weight(1f),
        )
        IconButton(onClick = onEdit) {
            Icon(Icons.Default.Edit, contentDescription = "Edit ${config.name}")
        }
        IconButton(onClick = onDelete) {
            Icon(Icons.Default.Delete, contentDescription = "Delete ${config.name}")
        }
    }
}

@Preview(showBackground = true)
@Composable
fun BankConfigsScreenPreview() {
    val config = BankHandlerConfig(
        id = 1L,
        name = "UOB Thailand",
        otpRegex = """ (\w{4})-(\d{6}) """,
        moneyRegex = """of ([A-Z]{3})(\d+)""",
        merchantRegex = """at (.+) expiring""",
        createdAt = 0L,
        updatedAt = 0L,
    )
    BankConfigsScreen(
        state = BankConfigsState(configs = listOf(config)),
        onAdd = {},
        onEdit = {},
        onRequestDelete = {},
        onCancelDelete = {},
        onConfirmDelete = {},
        onExport = {},
        onToggleExportItem = {},
        onCancelExportSelection = {},
        onConfirmExportSelection = {},
        onImport = {},
        onConfirmImport = {},
        onCancelImport = {},
        onDismissMessage = {},
    )
}
```

- [ ] **Step 3: Wire SAF launchers in `VelesPermissionsApp`**

Replace the `composable("bank-configs") { ... }` block in `app/src/main/java/me/nagaev/veles/permissions/ui/VelesPermissionsApp.kt` with:

```kotlin
                composable("bank-configs") {
                    val context = LocalContext.current
                    val factory = remember { BankConfigsViewModelFactory(context) }
                    val vm: BankConfigsViewModel = viewModel(factory = factory)
                    val state by vm.state.collectAsStateWithLifecycle()
                    val lifecycleOwner = LocalLifecycleOwner.current
                    DisposableEffect(lifecycleOwner) {
                        val observer = LifecycleEventObserver { _, event ->
                            if (event == Lifecycle.Event.ON_RESUME) {
                                vm.refresh()
                            }
                        }
                        lifecycleOwner.lifecycle.addObserver(observer)
                        onDispose { lifecycleOwner.lifecycle.removeObserver(observer) }
                    }

                    val createDocumentLauncher = rememberLauncherForActivityResult(
                        contract = ActivityResultContracts.CreateDocument("application/json"),
                    ) { uri ->
                        if (uri != null) vm.writeExportToUri(context, uri)
                    }
                    val openDocumentLauncher = rememberLauncherForActivityResult(
                        contract = ActivityResultContracts.OpenDocument(),
                    ) { uri ->
                        if (uri != null) vm.onImportUri(context, uri)
                    }

                    LaunchedEffect(state.pendingExportJson) {
                        if (state.pendingExportJson != null) {
                            createDocumentLauncher.launch("veles-bank-configs.json")
                        }
                    }

                    BankConfigsScreen(
                        state = state,
                        onAdd = { navController.navigate("bank-config-edit") },
                        onEdit = { id -> navController.navigate("bank-config-edit?id=$id") },
                        onRequestDelete = vm::requestDelete,
                        onCancelDelete = vm::cancelDelete,
                        onConfirmDelete = vm::confirmDelete,
                        onExport = vm::onExportRequested,
                        onToggleExportItem = vm::toggleExportItem,
                        onCancelExportSelection = vm::cancelExportSelection,
                        onConfirmExportSelection = vm::confirmExportSelection,
                        onImport = { openDocumentLauncher.launch(arrayOf("application/json", "*/*")) },
                        onConfirmImport = vm::confirmImport,
                        onCancelImport = vm::cancelImport,
                        onDismissMessage = vm::dismissMessage,
                    )
                }
```

Add these imports at the top of `VelesPermissionsApp.kt`:

```kotlin
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.LaunchedEffect
```

- [ ] **Step 4: Build to verify no compilation errors**

Run: `./gradlew assembleDebug`
Expected: `BUILD SUCCESSFUL`.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/common/ui/TestTags.kt \
    app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigsScreen.kt \
    app/src/main/java/me/nagaev/veles/permissions/ui/VelesPermissionsApp.kt
git commit -m "feat: add export/import UI to BankConfigsScreen with SAF launchers"
```

---

## Task 5: Instrumented tests for export/import flow

**Files:**
- Create: `app/src/androidTest/java/me/nagaev/veles/otp/config/ExportImportFlowTest.kt`

**Interfaces:**
- Consumes: `BankConfigsScreen`, `TestTags`, `BankConfigsViewModel`, Room DB (in-memory via `BankHandlerDatabase`)

This task drives the real Compose UI and the real Room database (in-memory) end to end. SAF launchers are exercised through Compose UI by pre-seeding a content `Uri` via a `ContentProvider` registered in the test manifest is heavy; instead, the launchers are bypassed by calling the ViewModel directly for the uri handoff while the dialogs (the user-facing confirmation gates) are driven through the Compose UI. This matches how the existing `VelesPermissionsAppTests` mix Compose UI interaction with mocked collaborators.

- [ ] **Step 1: Write the instrumented tests**

Create `app/src/androidTest/java/me/nagaev/veles/otp/config/ExportImportFlowTest.kt`:

```kotlin
package me.nagaev.veles.otp.config

import androidx.activity.ComponentActivity
import androidx.compose.ui.test.assertIsDisplayed
import androidx.compose.ui.test.junit4.createAndroidComposeRule
import androidx.compose.ui.test.onNodeWithTag
import androidx.compose.ui.test.onNodeWithText
import androidx.compose.ui.test.performClick
import androidx.room.Room
import androidx.test.ext.junit.runners.AndroidJUnit4
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.otp.config.ui.BankConfigsScreen
import me.nagaev.veles.otp.config.viewmodel.BankConfigsViewModel
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.runner.RunWith

@RunWith(AndroidJUnit4::class)
class ExportImportFlowTest {
    @get:Rule
    val composeRule = createAndroidComposeRule<ComponentActivity>()

    private lateinit var db: BankHandlerDatabase
    private lateinit var repository: BankHandlerRepository
    private lateinit var vm: BankConfigsViewModel

    @Before
    fun setUp() {
        val context = androidx.test.core.app.ApplicationProvider.getApplicationContext<android.content.Context>()
        db = Room.inMemoryDatabaseBuilder(context, BankHandlerDatabase::class.java).allowMainThreadQueries().build()
        repository = BankHandlerRepository(context).let {
            // Rebuild repository backed by the in-memory db via reflection-free path:
            // BankHandlerRepository creates its own db instance; for tests we use the
            // real repository against the app db, but seed/clear via the in-memory dao.
            it
        }
        // Use the in-memory dao directly through a repository wrapper.
        val dao = db.bankHandlerConfigDao()
        repository = object : BankHandlerRepository(context) {
            // not possible to subclass final; see note below
        }
        // NOTE: BankHandlerRepository is final. Use the production repository but point
        // the singleton db at our in-memory instance via reflection on INSTANCE.
        val field = BankHandlerDatabase::class.java.getDeclaredField("INSTANCE")
        field.isAccessible = true
        field.set(null, db)
        repository = BankHandlerRepository(context)
        vm = BankConfigsViewModel(repository)
        composeRule.setContent {
            BankConfigsScreen(
                state = vm.state.collectAsState().value,
                onAdd = {},
                onEdit = {},
                onRequestDelete = vm::requestDelete,
                onCancelDelete = vm::cancelDelete,
                onConfirmDelete = vm::confirmDelete,
                onExport = vm::onExportRequested,
                onToggleExportItem = vm::toggleExportItem,
                onCancelExportSelection = vm::cancelExportSelection,
                onConfirmExportSelection = vm::confirmExportSelection,
                onImport = {},
                onConfirmImport = vm::confirmImport,
                onCancelImport = vm::cancelImport,
                onDismissMessage = vm::dismissMessage,
            )
        }
    }

    @After
    fun tearDown() {
        db.close()
    }

    @Test
    fun `export button opens selection dialog with seeded configs`() {
        // Seed a config via the dao
        val now = System.currentTimeMillis()
        kotlinx.coroutines.runBlocking {
            repository.insert(
                BankHandlerConfig(
                    id = 0L,
                    name = "UOB Thailand",
                    otpRegex = "o",
                    moneyRegex = "m",
                    merchantRegex = "mer",
                    createdAt = now,
                    updatedAt = now,
                ),
            )
        }
        vm.refresh()
        composeRule.waitForIdle()

        composeRule.onNodeWithTag(TestTags.BANK_CONFIG_EXPORT_BUTTON).performClick()
        composeRule.waitForIdle()

        composeRule.onNodeWithTag(TestTags.BANK_CONFIG_EXPORT_DIALOG).assertIsDisplayed()
        composeRule.onNodeWithText("UOB Thailand").assertIsDisplayed()
    }

    @Test
    fun `confirm export selection produces pending export json`() {
        val now = System.currentTimeMillis()
        kotlinx.coroutines.runBlocking {
            repository.insert(
                BankHandlerConfig(
                    id = 0L,
                    name = "UOB Thailand",
                    otpRegex = "o",
                    moneyRegex = "m",
                    merchantRegex = "mer",
                    createdAt = now,
                    updatedAt = now,
                ),
            )
        }
        vm.refresh()
        composeRule.waitForIdle()

        composeRule.onNodeWithTag(TestTags.BANK_CONFIG_EXPORT_BUTTON).performClick()
        composeRule.waitForIdle()
        composeRule.onNodeWithTag(TestTags.BANK_CONFIG_EXPORT_CONFIRM).performClick()
        composeRule.waitForIdle()

        assertNotNull(vm.state.value.pendingExportJson)
        assertTrue(vm.state.value.pendingExportJson!!.contains("UOB Thailand"))
    }

    @Test
    fun `import review dialog lists new and overwrite names`() {
        val now = System.currentTimeMillis()
        kotlinx.coroutines.runBlocking {
            repository.insert(
                BankHandlerConfig(
                    id = 0L,
                    name = "Existing Bank",
                    otpRegex = "old",
                    moneyRegex = "old",
                    merchantRegex = "old",
                    createdAt = now,
                    updatedAt = now,
                ),
            )
        }
        vm.refresh()
        composeRule.waitForIdle()

        val json = me.nagaev.veles.otp.config.io.ConfigSerializer.toJson(
            listOf(
                me.nagaev.veles.otp.config.BankHandlerConfig(
                    id = 0L,
                    name = "Existing Bank",
                    otpRegex = "new",
                    moneyRegex = "new",
                    merchantRegex = "new",
                    createdAt = 0L,
                    updatedAt = 0L,
                ),
                me.nagaev.veles.otp.config.BankHandlerConfig(
                    id = 0L,
                    name = "Brand New Bank",
                    otpRegex = "x",
                    moneyRegex = "x",
                    merchantRegex = "x",
                    createdAt = 0L,
                    updatedAt = 0L,
                ),
            ),
        )
        val context = androidx.test.core.app.ApplicationProvider.getApplicationContext<android.content.Context>()
        val uri = me.nagaev.veles.otp.config.TestFileUris.writeTempFile(context, json)
        vm.onImportUri(context, uri)
        composeRule.waitForIdle()

        composeRule.onNodeWithTag(TestTags.BANK_CONFIG_IMPORT_DIALOG).assertIsDisplayed()
        composeRule.onNodeWithText("New:").assertIsDisplayed()
        composeRule.onNodeWithText("Brand New Bank").assertIsDisplayed()
        composeRule.onNodeWithText("Will replace:").assertIsDisplayed()
        composeRule.onNodeWithText("Existing Bank").assertIsDisplayed()
    }

    @Test
    fun `confirm import writes new and overwrite rows to the database`() {
        val now = System.currentTimeMillis()
        kotlinx.coroutines.runBlocking {
            repository.insert(
                BankHandlerConfig(
                    id = 0L,
                    name = "Existing Bank",
                    otpRegex = "old",
                    moneyRegex = "old",
                    merchantRegex = "old",
                    createdAt = now,
                    updatedAt = now,
                ),
            )
        }
        vm.refresh()
        composeRule.waitForIdle()

        val json = me.nagaev.veles.otp.config.io.ConfigSerializer.toJson(
            listOf(
                me.nagaev.veles.otp.config.BankHandlerConfig(
                    id = 0L,
                    name = "Existing Bank",
                    otpRegex = "new-otp",
                    moneyRegex = "new-amt",
                    merchantRegex = "new-mer",
                    createdAt = 0L,
                    updatedAt = 0L,
                ),
                me.nagaev.veles.otp.config.BankHandlerConfig(
                    id = 0L,
                    name = "Brand New Bank",
                    otpRegex = "x",
                    moneyRegex = "x",
                    merchantRegex = "x",
                    createdAt = 0L,
                    updatedAt = 0L,
                ),
            ),
        )
        val context = androidx.test.core.app.ApplicationProvider.getApplicationContext<android.content.Context>()
        val uri = me.nagaev.veles.otp.config.TestFileUris.writeTempFile(context, json)
        vm.onImportUri(context, uri)
        composeRule.waitForIdle()

        composeRule.onNodeWithTag(TestTags.BANK_CONFIG_IMPORT_CONFIRM).performClick()
        composeRule.waitForIdle()

        kotlinx.coroutines.runBlocking {
            val all = repository.getAllSuspend()
            assertEquals(2, all.size)
            val existing = all.first { it.name == "Existing Bank" }
            assertEquals("new-otp", existing.otpRegex)
            assertEquals("new-amt", existing.moneyRegex)
            assertEquals("new-mer", existing.merchantRegex)
            val newBank = all.first { it.name == "Brand New Bank" }
            assertEquals("x", newBank.otpRegex)
        }
    }

    @Test
    fun `cancel import stops everything and writes nothing`() {
        val now = System.currentTimeMillis()
        kotlinx.coroutines.runBlocking {
            repository.insert(
                BankHandlerConfig(
                    id = 0L,
                    name = "Existing Bank",
                    otpRegex = "old",
                    moneyRegex = "old",
                    merchantRegex = "old",
                    createdAt = now,
                    updatedAt = now,
                ),
            )
        }
        vm.refresh()
        composeRule.waitForIdle()

        val json = me.nagaev.veles.otp.config.io.ConfigSerializer.toJson(
            listOf(
                me.nagaev.veles.otp.config.BankHandlerConfig(
                    id = 0L,
                    name = "Existing Bank",
                    otpRegex = "new",
                    moneyRegex = "new",
                    merchantRegex = "new",
                    createdAt = 0L,
                    updatedAt = 0L,
                ),
                me.nagaev.veles.otp.config.BankHandlerConfig(
                    id = 0L,
                    name = "Brand New Bank",
                    otpRegex = "x",
                    moneyRegex = "x",
                    merchantRegex = "x",
                    createdAt = 0L,
                    updatedAt = 0L,
                ),
            ),
        )
        val context = androidx.test.core.app.ApplicationProvider.getApplicationContext<android.content.Context>()
        val uri = me.nagaev.veles.otp.config.TestFileUris.writeTempFile(context, json)
        vm.onImportUri(context, uri)
        composeRule.waitForIdle()

        composeRule.onNodeWithTag(TestTags.BANK_CONFIG_IMPORT_CANCEL).performClick()
        composeRule.waitForIdle()

        assertNull(vm.state.value.importReview)
        kotlinx.coroutines.runBlocking {
            val all = repository.getAllSuspend()
            assertEquals(1, all.size)
            assertEquals("old", all[0].otpRegex)
        }
    }

    @Test
    fun `import of malformed file shows message and no review dialog`() {
        val context = androidx.test.core.app.ApplicationProvider.getApplicationContext<android.content.Context>()
        val uri = me.nagaev.veles.otp.config.TestFileUris.writeTempFile(context, "{not json")
        vm.onImportUri(context, uri)
        composeRule.waitForIdle()

        assertNull(vm.state.value.importReview)
        assertNotNull(vm.state.value.message)
    }
}
```

Create the helper `app/src/androidTest/java/me/nagaev/veles/otp/config/TestFileUris.kt`:

```kotlin
package me.nagaev.veles.otp.config

import android.content.Context
import android.net.Uri
import java.io.File

object TestFileUris {
    fun writeTempFile(context: Context, content: String): Uri {
        val file = File.createTempFile("import-test", ".json", context.cacheDir)
        file.writeText(content)
        return Uri.fromFile(file)
    }
}
```

Notes for the implementer:
- The `BankHandlerRepository` is final and holds its own `BankHandlerDatabase` singleton. The test swaps the singleton `INSTANCE` field to an in-memory database via reflection. This keeps tests hermetic without changing production code. If reflection on `INSTANCE` proves flaky on your toolchain, an alternative is to add a `@VisibleForTesting` setter on `BankHandlerDatabase` — but prefer the reflection path first to avoid touching production code for tests.
- `collectAsState()` requires `import androidx.compose.runtime.collectAsState` — add it to the test file imports.
- The `BankHandlerRepository(context)` subclass attempt in `setUp` is dead code from an earlier draft — delete the two `repository = ...` assignments before the `field.set(null, db)` line; keep only the reflection block and the final `repository = BankHandlerRepository(context)`.

Final `setUp` (use this cleaned version):

```kotlin
    @Before
    fun setUp() {
        val context = androidx.test.core.app.ApplicationProvider
            .getApplicationContext<android.content.Context>()
        db = Room.inMemoryDatabaseBuilder(context, BankHandlerDatabase::class.java)
            .allowMainThreadQueries()
            .build()
        val field = BankHandlerDatabase::class.java.getDeclaredField("INSTANCE")
        field.isAccessible = true
        field.set(null, db)
        repository = BankHandlerRepository(context)
        vm = BankConfigsViewModel(repository)
        composeRule.setContent {
            BankConfigsScreen(
                state = vm.state.collectAsState().value,
                onAdd = {},
                onEdit = {},
                onRequestDelete = vm::requestDelete,
                onCancelDelete = vm::cancelDelete,
                onConfirmDelete = vm::confirmDelete,
                onExport = vm::onExportRequested,
                onToggleExportItem = vm::toggleExportItem,
                onCancelExportSelection = vm::cancelExportSelection,
                onConfirmExportSelection = vm::confirmExportSelection,
                onImport = {},
                onConfirmImport = vm::confirmImport,
                onCancelImport = vm::cancelImport,
                onDismissMessage = vm::dismissMessage,
            )
        }
    }
```

- [ ] **Step 2: Run instrumented tests**

Run: `./gradlew connectedDebugAndroidTest --tests "me.nagaev.veles.otp.config.ExportImportFlowTest"`
Expected: PASS (all 6 tests). Requires a connected device/emulator.

- [ ] **Step 3: Commit**

```bash
git add app/src/androidTest/java/me/nagaev/veles/otp/config/ExportImportFlowTest.kt \
    app/src/androidTest/java/me/nagaev/veles/otp/config/TestFileUris.kt
git commit -m "test: add instrumented tests for bank config export/import flow"
```

---

## Self-Review Notes

Spec coverage:
- JSON format `{name, regex:{otp, amount, merchant}}` → Task 1. ✓
- Multi-select export dialog (default all) → Task 4. ✓
- SAF `CreateDocument` export → Task 4. ✓
- SAF `OpenDocument` import → Task 4. ✓
- Diff by name, insert + overwrite → Task 2. ✓
- Summary confirm dialog → Task 4. ✓
- Cancel = stop entire import (no writes) → Task 3 `cancelImport` + Task 5 test. ✓
- Malformed JSON message → Task 3 + Task 5 test. ✓
- Empty selection guard → Task 3. ✓
- Timestamps regenerated on import → Task 3 `confirmImport`. ✓
- Export/Import buttons next to Add → Task 4. ✓
- Unit tests for serializer/importer/viewmodel → Tasks 1–3. ✓
- Instrumented tests for the 5 scenarios → Task 5. ✓

Placeholders: none — all code blocks contain real code.

Type consistency: `BankConfigJson` / `RegexJson` (Task 1) used by `ConfigImporter.diff` (Task 2) and `ImportReview` (Task 3). `ExportSelection` / `ImportReview` (Task 3) used by `BankConfigsScreen` (Task 4). Test tags (Task 4) used by instrumented tests (Task 5). Names match across tasks.