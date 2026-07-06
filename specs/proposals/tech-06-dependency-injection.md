# Tech: Introduce a real dependency-injection setup (Hilt)

**Type:** Technical improvement (architecture)
**Priority:** Medium
**Effort:** Medium (~1–2 days)

## Problem

Wiring is currently ad-hoc and duplicated:

- Four hand-written `ViewModelProvider.Factory` classes (`TestViewModelFactory`,
  `PermissionsViewModelFactory`, `BankConfigsViewModelFactory`, `BankConfigEditViewModelFactory`)
  that all do the same "grab a `Context`, build a repository" dance.
- `BankHandlerDatabase.getInstance()` is a hand-rolled double-checked-locking singleton;
  `TestResultFlow`/`RedactionStateFlow` are global singletons reset by convention
  (`onCleared()`), which is easy to get wrong and already needs dedicated tests.
- `NotificationListener` uses nullable constructor parameters with production fallbacks — a
  pragmatic seam, but it mixes construction logic into the service and only supports one level of
  substitution.

Every new screen or service repeats this boilerplate, and test setup diverges from production
wiring.

## Proposal

Adopt **Hilt** (the Android-standard choice; Koin is a lighter alternative if avoiding kapt/ksp
processors is preferred):

1. `@HiltAndroidApp` on `VelesApplication`.
2. A `DatabaseModule` providing `BankHandlerDatabase`, `BankHandlerConfigDao`,
   `BankHandlerRepository` as `@Singleton`s — deleting the hand-rolled singleton.
3. `@HiltViewModel` on all four ViewModels; delete the four factories and the
   `remember { ...Factory(context) }` blocks in `VelesPermissionsApp` (use `hiltViewModel()`).
4. `@AndroidEntryPoint` on `NotificationListener` with `@Inject lateinit var` fields; keep the
   constructor-injection seam for the existing instrumented tests or migrate them to Hilt test
   rules (`@BindValue`).
5. Scope `TestResultFlow`/`RedactionStateFlow` as injected `@Singleton` classes instead of
   `object`s, making reset behaviour explicit and testable.

## Payoff

- New screens need zero wiring boilerplate.
- Unit/instrumented tests swap implementations via Hilt test modules instead of nullable
  constructor params and `mockkStatic`.
- One place to see the whole object graph.

## Migration order

Database module → ViewModels (screen by screen) → service → flows. Each step compiles and ships
independently.
