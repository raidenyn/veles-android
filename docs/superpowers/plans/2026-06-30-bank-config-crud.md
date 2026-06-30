# Bank Config CRUD UI Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a standalone screen with full CRUD for `BankHandlerConfig` records, accessible via a "Bank Configs" button on the main Permissions screen.

**Architecture:** Two new Compose screens (`BankConfigsScreen` for the list, `BankConfigEditScreen` for create/edit) follow the existing ViewModel + StateFlow + ViewModelFactory pattern. The DAO and Repository gain suspend overloads for CRUD; the original `getAll()` and `insertAll()` are untouched so the `NotificationListener` path is unaffected.

**Tech Stack:** Kotlin, Jetpack Compose, Room (suspend DAO), Compose Navigation, MockK, kotlinx-coroutines-test

## Global Constraints

- minSdk 33, compileSdk 35, JVM target 17
- All new packages under `me.nagaev.veles.otp.config`
- Unit tests are JVM-only (no Android runtime), located in `app/src/test/`
- Test runner: `./gradlew testDebugUnitTest`
- Build runner: `./gradlew assembleDebug`
- Existing `getAll()` / `insertAll()` on DAO and Repository must not be removed or renamed
- Delete requires confirmation dialog (controlled by `deleteTarget` in state)
- Regex validation runs on Save only, using `Regex(pattern)` in a try/catch

---

## File Map

| Action | Path |
|---|---|
| Modify | `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerConfigDao.kt` |
| Modify | `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerRepository.kt` |
| Create | `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsState.kt` |
| Create | `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModel.kt` |
| Create | `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModelFactory.kt` |
| Create | `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditState.kt` |
| Create | `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModel.kt` |
| Create | `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModelFactory.kt` |
| Create | `app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigsScreen.kt` |
| Create | `app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigEditScreen.kt` |
| Modify | `app/src/main/java/me/nagaev/veles/permissions/ui/PermissionsScreen.kt` |
| Modify | `app/src/main/java/me/nagaev/veles/permissions/ui/VelesPermissionsApp.kt` |
| Create | `app/src/test/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModelTest.kt` |
| Create | `app/src/test/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModelTest.kt` |

---

### Task 1: Add suspend CRUD methods to DAO and Repository

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerConfigDao.kt`
- Modify: `app/src/main/java/me/nagaev/veles/otp/config/BankHandlerRepository.kt`

**Interfaces:**
- Produces: `BankHandlerRepository.getAllSuspend()`, `.insert()`, `.update()`, `.delete()` — used by Tasks 2 and 3

- [ ] **Step 1: Add suspend methods to `BankHandlerConfigDao`**

Replace the entire file with:

```kotlin
package me.nagaev.veles.otp.config

import androidx.room.Dao
import androidx.room.Delete
import androidx.room.Insert
import androidx.room.Query
import androidx.room.Update

@Dao
interface BankHandlerConfigDao {
    @Query("SELECT * FROM bank_handler_configs ORDER BY id ASC")
    fun getAll(): List<BankHandlerConfig>

    @Insert
    fun insertAll(vararg configs: BankHandlerConfig)

    @Query("SELECT * FROM bank_handler_configs ORDER BY id ASC")
    suspend fun getAllSuspend(): List<BankHandlerConfig>

    @Insert
    suspend fun insert(config: BankHandlerConfig): Long

    @Update
    suspend fun update(config: BankHandlerConfig)

    @Delete
    suspend fun delete(config: BankHandlerConfig)
}
```

- [ ] **Step 2: Add suspend wrappers to `BankHandlerRepository`**

Replace the entire file with:

```kotlin
package me.nagaev.veles.otp.config

import android.content.Context

class BankHandlerRepository(context: Context) {
    private val dao = BankHandlerDatabase.getInstance(context).bankHandlerConfigDao()

    fun getAll(): List<BankHandlerConfig> = dao.getAll()

    suspend fun getAllSuspend(): List<BankHandlerConfig> = dao.getAllSuspend()

    suspend fun insert(config: BankHandlerConfig): Long = dao.insert(config)

    suspend fun update(config: BankHandlerConfig) = dao.update(config)

    suspend fun delete(config: BankHandlerConfig) = dao.delete(config)
}
```

- [ ] **Step 3: Build to verify no compilation errors**

```
./gradlew assembleDebug
```

Expected: `BUILD SUCCESSFUL`

- [ ] **Step 4: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/config/BankHandlerConfigDao.kt
git add app/src/main/java/me/nagaev/veles/otp/config/BankHandlerRepository.kt
git commit -m "feat: add suspend CRUD methods to DAO and Repository"
```

---

### Task 2: BankConfigsViewModel — list state and logic

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsState.kt`
- Create: `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModel.kt`
- Create: `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModelFactory.kt`
- Test: `app/src/test/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModelTest.kt`

**Interfaces:**
- Consumes: `BankHandlerRepository.getAllSuspend()`, `.delete()` from Task 1
- Produces: `BankConfigsViewModel(repository)`, `BankConfigsState`, `BankConfigsViewModelFactory(context)` — consumed by Task 4 (screen) and Task 6 (nav wiring)

- [ ] **Step 1: Write the failing tests**

Create `app/src/test/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModelTest.kt`:

```kotlin
package me.nagaev.veles.otp.config.viewmodel

import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import me.nagaev.veles.otp.config.BankHandlerConfig
import me.nagaev.veles.otp.config.BankHandlerRepository
import org.junit.After
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class BankConfigsViewModelTest {

    private val repository = mockk<BankHandlerRepository>()
    private val config = BankHandlerConfig(
        id = 1L,
        name = "Test Bank",
        otpRegex = """\d{6}""",
        moneyRegex = """([A-Z]{3})(\d+)""",
        merchantRegex = """at (.+)""",
        createdAt = 1000L,
        updatedAt = 1000L
    )

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
        coEvery { repository.getAllSuspend() } returns listOf(config)
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun `loads configs on init`() {
        val vm = BankConfigsViewModel(repository)
        assert(vm.state.value.configs == listOf(config))
        assert(!vm.state.value.isLoading)
    }

    @Test
    fun `requestDelete sets deleteTarget`() {
        val vm = BankConfigsViewModel(repository)
        vm.requestDelete(config)
        assert(vm.state.value.deleteTarget == config)
    }

    @Test
    fun `cancelDelete clears deleteTarget`() {
        val vm = BankConfigsViewModel(repository)
        vm.requestDelete(config)
        vm.cancelDelete()
        assert(vm.state.value.deleteTarget == null)
    }

    @Test
    fun `confirmDelete deletes config and reloads list`() {
        coEvery { repository.delete(config) } returns Unit
        val vm = BankConfigsViewModel(repository)
        vm.requestDelete(config)
        vm.confirmDelete()
        coVerify { repository.delete(config) }
        coVerify(exactly = 2) { repository.getAllSuspend() }
        assert(vm.state.value.deleteTarget == null)
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

```
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.viewmodel.BankConfigsViewModelTest"
```

Expected: compilation failure — `BankConfigsViewModel` does not exist yet.

- [ ] **Step 3: Create `BankConfigsState`**

Create `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsState.kt`:

```kotlin
package me.nagaev.veles.otp.config.viewmodel

import me.nagaev.veles.otp.config.BankHandlerConfig

data class BankConfigsState(
    val configs: List<BankHandlerConfig> = emptyList(),
    val isLoading: Boolean = false,
    val deleteTarget: BankHandlerConfig? = null
)
```

- [ ] **Step 4: Create `BankConfigsViewModel`**

Create `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModel.kt`:

```kotlin
package me.nagaev.veles.otp.config.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import me.nagaev.veles.otp.config.BankHandlerConfig
import me.nagaev.veles.otp.config.BankHandlerRepository

class BankConfigsViewModel(
    private val repository: BankHandlerRepository
) : ViewModel() {

    private val _state = MutableStateFlow(BankConfigsState())
    val state: StateFlow<BankConfigsState> = _state

    init {
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

    private suspend fun reload() {
        _state.update { it.copy(isLoading = true) }
        val configs = repository.getAllSuspend()
        _state.update { it.copy(configs = configs, isLoading = false) }
    }
}
```

- [ ] **Step 5: Create `BankConfigsViewModelFactory`**

Create `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModelFactory.kt`:

```kotlin
package me.nagaev.veles.otp.config.viewmodel

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import me.nagaev.veles.otp.config.BankHandlerRepository

class BankConfigsViewModelFactory(context: Context) : ViewModelProvider.Factory {
    private val repository = BankHandlerRepository(context)

    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T =
        BankConfigsViewModel(repository) as T
}
```

- [ ] **Step 6: Run tests to verify they pass**

```
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.viewmodel.BankConfigsViewModelTest"
```

Expected: `BUILD SUCCESSFUL` with 4 tests passing.

- [ ] **Step 7: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsState.kt
git add app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModel.kt
git add app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModelFactory.kt
git add app/src/test/java/me/nagaev/veles/otp/config/viewmodel/BankConfigsViewModelTest.kt
git commit -m "feat: add BankConfigsViewModel with load and delete logic"
```

---

### Task 3: BankConfigEditViewModel — create/edit state and logic

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditState.kt`
- Create: `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModel.kt`
- Create: `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModelFactory.kt`
- Test: `app/src/test/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModelTest.kt`

**Interfaces:**
- Consumes: `BankHandlerRepository.getAllSuspend()`, `.insert()`, `.update()` from Task 1
- Produces: `BankConfigEditViewModel(repository, configId)`, `BankConfigEditState`, `BankConfigEditViewModelFactory(context, configId)` — consumed by Tasks 5 and 6

- [ ] **Step 1: Write the failing tests**

Create `app/src/test/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModelTest.kt`:

```kotlin
package me.nagaev.veles.otp.config.viewmodel

import io.mockk.coEvery
import io.mockk.coVerify
import io.mockk.mockk
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.ExperimentalCoroutinesApi
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.setMain
import me.nagaev.veles.otp.config.BankHandlerConfig
import me.nagaev.veles.otp.config.BankHandlerRepository
import org.junit.After
import org.junit.Before
import org.junit.Test

@OptIn(ExperimentalCoroutinesApi::class)
class BankConfigEditViewModelTest {

    private val repository = mockk<BankHandlerRepository>()
    private val existingConfig = BankHandlerConfig(
        id = 42L,
        name = "Test Bank",
        otpRegex = """\d{6}""",
        moneyRegex = """([A-Z]{3})(\d+)""",
        merchantRegex = """at (.+)""",
        createdAt = 1000L,
        updatedAt = 2000L
    )

    @Before
    fun setUp() {
        Dispatchers.setMain(UnconfinedTestDispatcher())
    }

    @After
    fun tearDown() {
        Dispatchers.resetMain()
    }

    @Test
    fun `new config starts with empty fields`() {
        val vm = BankConfigEditViewModel(repository, configId = null)
        val state = vm.state.value
        assert(state.name == "")
        assert(state.otpRegex == "")
        assert(state.moneyRegex == "")
        assert(state.merchantRegex == "")
        assert(!state.savedSuccessfully)
    }

    @Test
    fun `existing config loads fields on init`() {
        coEvery { repository.getAllSuspend() } returns listOf(existingConfig)
        val vm = BankConfigEditViewModel(repository, configId = 42L)
        val state = vm.state.value
        assert(state.name == "Test Bank")
        assert(state.otpRegex == """\d{6}""")
        assert(state.moneyRegex == """([A-Z]{3})(\d+)""")
        assert(state.merchantRegex == """at (.+)""")
        assert(state.originalCreatedAt == 1000L)
    }

    @Test
    fun `save with blank name sets nameError`() {
        val vm = BankConfigEditViewModel(repository, configId = null)
        vm.onOtpRegexChanged("""\d{6}""")
        vm.onMoneyRegexChanged("""([A-Z]{3})(\d+)""")
        vm.onMerchantRegexChanged("""at (.+)""")
        vm.save()
        assert(vm.state.value.nameError != null)
        assert(!vm.state.value.savedSuccessfully)
    }

    @Test
    fun `save with invalid OTP regex sets otpRegexError`() {
        val vm = BankConfigEditViewModel(repository, configId = null)
        vm.onNameChanged("My Bank")
        vm.onOtpRegexChanged("[invalid")
        vm.onMoneyRegexChanged("""([A-Z]{3})(\d+)""")
        vm.onMerchantRegexChanged("""at (.+)""")
        vm.save()
        assert(vm.state.value.otpRegexError != null)
        assert(!vm.state.value.savedSuccessfully)
    }

    @Test
    fun `save with blank regex sets error`() {
        val vm = BankConfigEditViewModel(repository, configId = null)
        vm.onNameChanged("My Bank")
        vm.save()
        assert(vm.state.value.otpRegexError != null)
        assert(vm.state.value.moneyRegexError != null)
        assert(vm.state.value.merchantRegexError != null)
    }

    @Test
    fun `save valid new config calls insert and sets savedSuccessfully`() {
        coEvery { repository.insert(any()) } returns 1L
        val vm = BankConfigEditViewModel(repository, configId = null)
        vm.onNameChanged("My Bank")
        vm.onOtpRegexChanged("""\d{6}""")
        vm.onMoneyRegexChanged("""([A-Z]{3})(\d+)""")
        vm.onMerchantRegexChanged("""at (.+)""")
        vm.save()
        coVerify { repository.insert(any()) }
        assert(vm.state.value.savedSuccessfully)
    }

    @Test
    fun `save valid existing config calls update and sets savedSuccessfully`() {
        coEvery { repository.getAllSuspend() } returns listOf(existingConfig)
        coEvery { repository.update(any()) } returns Unit
        val vm = BankConfigEditViewModel(repository, configId = 42L)
        vm.save()
        coVerify { repository.update(any()) }
        assert(vm.state.value.savedSuccessfully)
    }

    @Test
    fun `changing field clears its error`() {
        val vm = BankConfigEditViewModel(repository, configId = null)
        vm.save()
        assert(vm.state.value.nameError != null)
        vm.onNameChanged("My Bank")
        assert(vm.state.value.nameError == null)
    }
}
```

- [ ] **Step 2: Run tests to verify they fail**

```
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.viewmodel.BankConfigEditViewModelTest"
```

Expected: compilation failure — `BankConfigEditViewModel` does not exist yet.

- [ ] **Step 3: Create `BankConfigEditState`**

Create `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditState.kt`:

```kotlin
package me.nagaev.veles.otp.config.viewmodel

data class BankConfigEditState(
    val name: String = "",
    val otpRegex: String = "",
    val moneyRegex: String = "",
    val merchantRegex: String = "",
    val originalCreatedAt: Long? = null,
    val nameError: String? = null,
    val otpRegexError: String? = null,
    val moneyRegexError: String? = null,
    val merchantRegexError: String? = null,
    val isSaving: Boolean = false,
    val savedSuccessfully: Boolean = false
)
```

- [ ] **Step 4: Create `BankConfigEditViewModel`**

Create `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModel.kt`:

```kotlin
package me.nagaev.veles.otp.config.viewmodel

import androidx.lifecycle.ViewModel
import androidx.lifecycle.viewModelScope
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch
import me.nagaev.veles.otp.config.BankHandlerConfig
import me.nagaev.veles.otp.config.BankHandlerRepository

class BankConfigEditViewModel(
    private val repository: BankHandlerRepository,
    private val configId: Long?
) : ViewModel() {

    private val _state = MutableStateFlow(BankConfigEditState())
    val state: StateFlow<BankConfigEditState> = _state

    init {
        if (configId != null) {
            viewModelScope.launch {
                val config = repository.getAllSuspend().find { it.id == configId }
                if (config != null) {
                    _state.update {
                        it.copy(
                            name = config.name,
                            otpRegex = config.otpRegex,
                            moneyRegex = config.moneyRegex,
                            merchantRegex = config.merchantRegex,
                            originalCreatedAt = config.createdAt
                        )
                    }
                }
            }
        }
    }

    fun onNameChanged(value: String) = _state.update { it.copy(name = value, nameError = null) }
    fun onOtpRegexChanged(value: String) = _state.update { it.copy(otpRegex = value, otpRegexError = null) }
    fun onMoneyRegexChanged(value: String) = _state.update { it.copy(moneyRegex = value, moneyRegexError = null) }
    fun onMerchantRegexChanged(value: String) = _state.update { it.copy(merchantRegex = value, merchantRegexError = null) }

    fun save() {
        val s = _state.value
        val nameError = if (s.name.isBlank()) "Name is required" else null
        val otpRegexError = validateRegex(s.otpRegex)
        val moneyRegexError = validateRegex(s.moneyRegex)
        val merchantRegexError = validateRegex(s.merchantRegex)

        if (nameError != null || otpRegexError != null || moneyRegexError != null || merchantRegexError != null) {
            _state.update {
                it.copy(
                    nameError = nameError,
                    otpRegexError = otpRegexError,
                    moneyRegexError = moneyRegexError,
                    merchantRegexError = merchantRegexError
                )
            }
            return
        }

        _state.update { it.copy(isSaving = true) }
        viewModelScope.launch {
            val now = System.currentTimeMillis()
            if (configId != null) {
                repository.update(
                    BankHandlerConfig(
                        id = configId,
                        name = s.name,
                        otpRegex = s.otpRegex,
                        moneyRegex = s.moneyRegex,
                        merchantRegex = s.merchantRegex,
                        createdAt = s.originalCreatedAt ?: now,
                        updatedAt = now
                    )
                )
            } else {
                repository.insert(
                    BankHandlerConfig(
                        name = s.name,
                        otpRegex = s.otpRegex,
                        moneyRegex = s.moneyRegex,
                        merchantRegex = s.merchantRegex,
                        createdAt = now,
                        updatedAt = now
                    )
                )
            }
            _state.update { it.copy(isSaving = false, savedSuccessfully = true) }
        }
    }

    private fun validateRegex(pattern: String): String? = if (pattern.isBlank()) {
        "Required"
    } else {
        try {
            Regex(pattern)
            null
        } catch (e: Exception) {
            "Invalid regex"
        }
    }
}
```

- [ ] **Step 5: Create `BankConfigEditViewModelFactory`**

Create `app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModelFactory.kt`:

```kotlin
package me.nagaev.veles.otp.config.viewmodel

import android.content.Context
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import me.nagaev.veles.otp.config.BankHandlerRepository

class BankConfigEditViewModelFactory(
    context: Context,
    private val configId: Long?
) : ViewModelProvider.Factory {
    private val repository = BankHandlerRepository(context)

    @Suppress("UNCHECKED_CAST")
    override fun <T : ViewModel> create(modelClass: Class<T>): T =
        BankConfigEditViewModel(repository, configId) as T
}
```

- [ ] **Step 6: Run tests to verify they pass**

```
./gradlew testDebugUnitTest --tests "me.nagaev.veles.otp.config.viewmodel.BankConfigEditViewModelTest"
```

Expected: `BUILD SUCCESSFUL` with 7 tests passing.

- [ ] **Step 7: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditState.kt
git add app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModel.kt
git add app/src/main/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModelFactory.kt
git add app/src/test/java/me/nagaev/veles/otp/config/viewmodel/BankConfigEditViewModelTest.kt
git commit -m "feat: add BankConfigEditViewModel with create/edit/validate logic"
```

---

### Task 4: BankConfigsScreen — list UI

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigsScreen.kt`

**Interfaces:**
- Consumes: `BankConfigsState`, `BankHandlerConfig` from Tasks 2 and 1
- Produces: `BankConfigsScreen` composable — wired in Task 6

- [ ] **Step 1: Create `BankConfigsScreen`**

Create `app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigsScreen.kt`:

```kotlin
package me.nagaev.veles.otp.config.ui

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
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.Icon
import androidx.compose.material3.IconButton
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import me.nagaev.veles.otp.config.BankHandlerConfig
import me.nagaev.veles.otp.config.viewmodel.BankConfigsState

@Composable
fun BankConfigsScreen(
    state: BankConfigsState,
    onAdd: () -> Unit,
    onEdit: (Long) -> Unit,
    onRequestDelete: (BankHandlerConfig) -> Unit,
    onCancelDelete: () -> Unit,
    onConfirmDelete: () -> Unit,
    modifier: Modifier = Modifier
) {
    Box(modifier = modifier.fillMaxSize()) {
        Column(
            modifier = Modifier
                .fillMaxSize()
                .padding(horizontal = 16.dp)
                .statusBarsPadding()
        ) {
            Text(
                text = "Bank Configs",
                style = MaterialTheme.typography.titleLarge,
                color = MaterialTheme.colorScheme.primary,
                modifier = Modifier.padding(vertical = 10.dp)
            )
            TextButton(onClick = onAdd) {
                Text("Add")
            }
            if (state.isLoading) {
                CircularProgressIndicator(
                    modifier = Modifier
                        .align(Alignment.CenterHorizontally)
                        .padding(top = 32.dp)
                )
            } else {
                LazyColumn {
                    items(state.configs, key = { it.id }) { config ->
                        BankConfigRow(
                            config = config,
                            onEdit = { onEdit(config.id) },
                            onDelete = { onRequestDelete(config) }
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
                }
            )
        }
    }
}

@Composable
private fun BankConfigRow(
    config: BankHandlerConfig,
    onEdit: () -> Unit,
    onDelete: () -> Unit
) {
    Row(
        modifier = Modifier
            .fillMaxWidth()
            .padding(vertical = 4.dp),
        verticalAlignment = Alignment.CenterVertically
    ) {
        Text(
            text = config.name,
            style = MaterialTheme.typography.bodyLarge,
            modifier = Modifier.weight(1f)
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
        updatedAt = 0L
    )
    BankConfigsScreen(
        state = BankConfigsState(configs = listOf(config)),
        onAdd = {},
        onEdit = {},
        onRequestDelete = {},
        onCancelDelete = {},
        onConfirmDelete = {}
    )
}
```

- [ ] **Step 2: Build to verify compilation**

```
./gradlew assembleDebug
```

Expected: `BUILD SUCCESSFUL`

- [ ] **Step 3: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigsScreen.kt
git commit -m "feat: add BankConfigsScreen list UI"
```

---

### Task 5: BankConfigEditScreen — create/edit form UI

**Files:**
- Create: `app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigEditScreen.kt`

**Interfaces:**
- Consumes: `BankConfigEditState` from Task 3
- Produces: `BankConfigEditScreen` composable — wired in Task 6

- [ ] **Step 1: Create `BankConfigEditScreen`**

Create `app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigEditScreen.kt`:

```kotlin
package me.nagaev.veles.otp.config.ui

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.ui.Modifier
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import me.nagaev.veles.otp.config.viewmodel.BankConfigEditState

@Composable
fun BankConfigEditScreen(
    state: BankConfigEditState,
    isNew: Boolean,
    onNameChanged: (String) -> Unit,
    onOtpRegexChanged: (String) -> Unit,
    onMoneyRegexChanged: (String) -> Unit,
    onMerchantRegexChanged: (String) -> Unit,
    onSave: () -> Unit,
    onNavigateBack: () -> Unit,
    modifier: Modifier = Modifier
) {
    LaunchedEffect(state.savedSuccessfully) {
        if (state.savedSuccessfully) onNavigateBack()
    }

    Column(
        modifier = modifier
            .fillMaxSize()
            .padding(horizontal = 16.dp)
            .statusBarsPadding()
            .verticalScroll(rememberScrollState())
    ) {
        Text(
            text = if (isNew) "New Bank Config" else "Edit Bank Config",
            style = MaterialTheme.typography.titleLarge,
            color = MaterialTheme.colorScheme.primary,
            modifier = Modifier.padding(vertical = 10.dp)
        )
        OutlinedTextField(
            value = state.name,
            onValueChange = onNameChanged,
            label = { Text("Name") },
            isError = state.nameError != null,
            supportingText = state.nameError?.let { error -> { Text(error) } },
            modifier = Modifier.fillMaxWidth()
        )
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = state.otpRegex,
            onValueChange = onOtpRegexChanged,
            label = { Text("OTP Regex") },
            isError = state.otpRegexError != null,
            supportingText = state.otpRegexError?.let { error -> { Text(error) } },
            modifier = Modifier.fillMaxWidth()
        )
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = state.moneyRegex,
            onValueChange = onMoneyRegexChanged,
            label = { Text("Money Regex") },
            isError = state.moneyRegexError != null,
            supportingText = state.moneyRegexError?.let { error -> { Text(error) } },
            modifier = Modifier.fillMaxWidth()
        )
        Spacer(Modifier.height(8.dp))
        OutlinedTextField(
            value = state.merchantRegex,
            onValueChange = onMerchantRegexChanged,
            label = { Text("Merchant Regex") },
            isError = state.merchantRegexError != null,
            supportingText = state.merchantRegexError?.let { error -> { Text(error) } },
            modifier = Modifier.fillMaxWidth()
        )
        Spacer(Modifier.height(16.dp))
        Button(
            onClick = onSave,
            enabled = !state.isSaving,
            modifier = Modifier.fillMaxWidth()
        ) {
            Text("Save")
        }
    }
}

@Preview(showBackground = true)
@Composable
fun BankConfigEditScreenPreview() {
    BankConfigEditScreen(
        state = BankConfigEditState(
            name = "UOB Thailand",
            otpRegex = """ (\w{4})-(\d{6}) """,
            moneyRegex = """of ([A-Z]{3})(\d+)""",
            merchantRegex = """at (.+) expiring"""
        ),
        isNew = false,
        onNameChanged = {},
        onOtpRegexChanged = {},
        onMoneyRegexChanged = {},
        onMerchantRegexChanged = {},
        onSave = {},
        onNavigateBack = {}
    )
}
```

- [ ] **Step 2: Build to verify compilation**

```
./gradlew assembleDebug
```

Expected: `BUILD SUCCESSFUL`

- [ ] **Step 3: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/otp/config/ui/BankConfigEditScreen.kt
git commit -m "feat: add BankConfigEditScreen form UI"
```

---

### Task 6: Wire navigation — PermissionsScreen button + NavHost routes

**Files:**
- Modify: `app/src/main/java/me/nagaev/veles/permissions/ui/PermissionsScreen.kt`
- Modify: `app/src/main/java/me/nagaev/veles/permissions/ui/VelesPermissionsApp.kt`

**Interfaces:**
- Consumes: `BankConfigsScreen`, `BankConfigsViewModel`, `BankConfigsViewModelFactory` from Tasks 2/4; `BankConfigEditScreen`, `BankConfigEditViewModel`, `BankConfigEditViewModelFactory` from Tasks 3/5

- [ ] **Step 1: Add `onNavigateToBankConfigs` to `PermissionsScreen`**

Replace the entire `PermissionsScreen.kt` with:

```kotlin
package me.nagaev.veles.permissions.ui

import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.statusBarsPadding
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.Text
import androidx.compose.material3.TextButton
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.testTag
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.tooling.preview.Preview
import androidx.compose.ui.unit.dp
import me.nagaev.veles.R
import me.nagaev.veles.common.ui.TestTags
import me.nagaev.veles.permissions.ui.components.PermissionsList
import me.nagaev.veles.permissions.viewmodal.PermissionsActions
import me.nagaev.veles.permissions.viewmodal.PermissionsState

@Composable
fun PermissionsScreen(
    state: PermissionsState,
    actions: PermissionsActions,
    onNavigateToTest: () -> Unit,
    onNavigateToBankConfigs: () -> Unit,
    modifier: Modifier = Modifier
) {
    Box(modifier = modifier.fillMaxSize()) {
        Column {
            Text(
                modifier = Modifier.padding(10.dp).statusBarsPadding(),
                text = stringResource(id = R.string.permissions),
                style = MaterialTheme.typography.titleLarge,
                color = MaterialTheme.colorScheme.primary
            )
            Text(
                modifier = Modifier.padding(10.dp).testTag(TestTags.NOTIFICATION_LISTENER_STATUS),
                text =
                    if (state.notificationListenerEnabled)
                        "Notification listener enabled"
                    else
                        "Notification listener disabled",
                style = MaterialTheme.typography.bodyMedium,
                color = MaterialTheme.colorScheme.secondary
            )
            TextButton(
                onClick = onNavigateToTest,
                modifier = Modifier.padding(horizontal = 10.dp)
            ) {
                Text("Test")
            }
            TextButton(
                onClick = onNavigateToBankConfigs,
                modifier = Modifier.padding(horizontal = 10.dp)
            ) {
                Text("Bank Configs")
            }
            PermissionsList(
                permissions = state.permissions,
                actions = actions,
                modifier = Modifier.fillMaxSize(),
            )
        }
    }
}

@Preview(showBackground = true)
@Composable
fun PermissionsScreenPreview() {
    PermissionsScreen(
        state = PermissionsState.Mocked,
        actions = PermissionsActions.Mocked,
        onNavigateToTest = {},
        onNavigateToBankConfigs = {},
    )
}
```

- [ ] **Step 2: Add two new routes to `VelesPermissionsApp`**

Replace the entire `VelesPermissionsApp.kt` with:

```kotlin
package me.nagaev.veles.permissions.ui

import androidx.compose.material3.Surface
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.remember
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.tooling.preview.Preview
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import androidx.lifecycle.viewmodel.compose.viewModel
import androidx.navigation.NavType
import androidx.navigation.compose.NavHost
import androidx.navigation.compose.composable
import androidx.navigation.compose.rememberNavController
import androidx.navigation.navArgument
import me.nagaev.veles.common.ui.theme.VelesTheme
import me.nagaev.veles.otp.config.ui.BankConfigEditScreen
import me.nagaev.veles.otp.config.ui.BankConfigsScreen
import me.nagaev.veles.otp.config.viewmodel.BankConfigEditViewModel
import me.nagaev.veles.otp.config.viewmodel.BankConfigEditViewModelFactory
import me.nagaev.veles.otp.config.viewmodel.BankConfigsViewModel
import me.nagaev.veles.otp.config.viewmodel.BankConfigsViewModelFactory
import me.nagaev.veles.permissions.viewmodal.PermissionsActions
import me.nagaev.veles.permissions.viewmodal.PermissionsState
import me.nagaev.veles.testing.ui.TestScreen
import me.nagaev.veles.testing.viewmodel.TestViewModel
import me.nagaev.veles.testing.viewmodel.TestViewModelFactory

@Composable
fun VelesPermissionsApp(
    permissionsState: PermissionsState,
    permissionsActions: PermissionsActions,
) {
    VelesTheme {
        Surface {
            val navController = rememberNavController()
            NavHost(navController = navController, startDestination = "permissions") {
                composable("permissions") {
                    PermissionsScreen(
                        state = permissionsState,
                        actions = permissionsActions,
                        onNavigateToTest = { navController.navigate("test") },
                        onNavigateToBankConfigs = { navController.navigate("bank-configs") }
                    )
                }
                composable("test") {
                    val context = LocalContext.current
                    val factory = remember { TestViewModelFactory(context) }
                    val testViewModel: TestViewModel = viewModel(factory = factory)
                    val testState by testViewModel.uiState.collectAsStateWithLifecycle()
                    TestScreen(
                        state = testState,
                        onTextChanged = testViewModel::onTextChanged,
                        onSend = testViewModel::send
                    )
                }
                composable("bank-configs") {
                    val context = LocalContext.current
                    val factory = remember { BankConfigsViewModelFactory(context) }
                    val vm: BankConfigsViewModel = viewModel(factory = factory)
                    val state by vm.state.collectAsStateWithLifecycle()
                    BankConfigsScreen(
                        state = state,
                        onAdd = { navController.navigate("bank-config-edit") },
                        onEdit = { id -> navController.navigate("bank-config-edit?id=$id") },
                        onRequestDelete = vm::requestDelete,
                        onCancelDelete = vm::cancelDelete,
                        onConfirmDelete = vm::confirmDelete
                    )
                }
                composable(
                    route = "bank-config-edit?id={id}",
                    arguments = listOf(navArgument("id") {
                        type = NavType.LongType
                        defaultValue = -1L
                    })
                ) { backStackEntry ->
                    val rawId = backStackEntry.arguments?.getLong("id") ?: -1L
                    val configId: Long? = if (rawId == -1L) null else rawId
                    val context = LocalContext.current
                    val factory = remember(configId) { BankConfigEditViewModelFactory(context, configId) }
                    val vm: BankConfigEditViewModel = viewModel(factory = factory)
                    val state by vm.state.collectAsStateWithLifecycle()
                    BankConfigEditScreen(
                        state = state,
                        isNew = configId == null,
                        onNameChanged = vm::onNameChanged,
                        onOtpRegexChanged = vm::onOtpRegexChanged,
                        onMoneyRegexChanged = vm::onMoneyRegexChanged,
                        onMerchantRegexChanged = vm::onMerchantRegexChanged,
                        onSave = vm::save,
                        onNavigateBack = { navController.popBackStack() }
                    )
                }
            }
        }
    }
}

@Preview(showBackground = true)
@Composable
fun VelesAppPreview() {
    VelesPermissionsApp(
        permissionsState = PermissionsState.Mocked,
        permissionsActions = PermissionsActions.Mocked,
    )
}
```

- [ ] **Step 3: Build to verify everything compiles**

```
./gradlew assembleDebug
```

Expected: `BUILD SUCCESSFUL`

- [ ] **Step 4: Run all unit tests to verify nothing regressed**

```
./gradlew testDebugUnitTest
```

Expected: `BUILD SUCCESSFUL` — all tests pass.

- [ ] **Step 5: Commit**

```bash
git add app/src/main/java/me/nagaev/veles/permissions/ui/PermissionsScreen.kt
git add app/src/main/java/me/nagaev/veles/permissions/ui/VelesPermissionsApp.kt
git commit -m "feat: wire Bank Configs navigation into NavHost and PermissionsScreen"
```
