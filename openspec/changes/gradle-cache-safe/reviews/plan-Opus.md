# Review record: plan

- Artifact: plan (`plan.md`)
- Reviewer: Opus — Opus-5.5, effort medium
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes:
  - brainstorm.md sha256 08569a637f46e514517ceca16203c206ddbe75b2411cf58281620435d266e8d4
  - proposal.md sha256 25a769528cb93da164101cf41b20b68e06d68bde72a603d303a3fe0b398bfdc4
  - design.md sha256 d5e8bb74414cb10e505f0ec0503af7d4004907b1a0c2aa6fbb2d3bdcfcce6784
  - specs/gradle-build-caching/spec.md sha256 61f399e10d7a86f314334873f30525e815693d12085825d21a814a7703dd867e
  - specs/component-versioning/spec.md sha256 91972bfa3d988df0127e83c127bb7be4463d3c43e3106914f58cdc05e97f7a0e
  - tasks.md sha256 e99124297163786df4e1345af0797178ecb2ad2f687bc76c151f11b269ede03b

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | Top-level `.kts` helper `fun`s are script members; calling them from tasks or lambdas captures the script. | Fixed: `object RustBuildHelpers` with plain-value helpers. |
| high | `CargoBuildTask` lacked the NDK lookup, `PATH`, delete and copy hooks; the empty `outputFlag` contradicted the action. | Fixed: properties, services and a five-step action with a conditional flag. |
| high | Windows refusal for `rustHostJni` missing. | Fixed: `doNotCacheIf` plus the constant `hostIdentity`. |
| medium | `connectedDebugAndroidTest` missing. | Fixed: Task 5 Step 3. |
| medium | Lazy-pin check ran after the revert. | Fixed: Task 7 Step 2. |
| medium | `GRADLE_USER_HOME` seed path wrong. | Fixed. |
| medium | Task 2 Step 6 compared against a tagged baseline. | Fixed: plugin output captured in place before the edit. |
| medium | Harness settings, dirty tree and `repoDir`. | Fixed. |
| medium | `ndkLookup` type. | Fixed: `NdkLookup` Serializable data class. |
| medium | `hostIdentity` composition. | Fixed. |
| low | Task 1 Step 5 worktree removed. | Fixed. |
| low | Working directories. | Fixed. |
| low | `apply(from)` `.asFile` and the `gitVersion` declaration. | Fixed. |
| low | `ccProbe` expected text. | Fixed. |
| low | `ndk-version` message path. | Fixed. |
| low | Relocation worktrees, directories and commit type. | Fixed. |
| low | Contract test: `rust` undefined, trimming. | Fixed. |
| low | Audit grep: `tasks.named` and generated names. | Fixed. |
| low | Review Focus 5: SDK not hidden. | Fixed. |
| low | Test OTP method. | Fixed. |

### Round 2 — approve

| Severity | Finding | Disposition |
|---|---|---|
| medium | Execution-time lambdas may name script-level `val`s and capture the script. | Fixed (non-triggering): capture rule added for every registration in Tasks 3–4. |
| low | `gradleCommands` quote state at the match (`"$root/gradlew"`). | Fixed (non-triggering). |
| nit | `local.properties` read eagerly with `.orNull`. | Fixed (non-triggering): wired lazily into `Property<NdkLookup>`. |

Approved content: plan sha256 274330b9034d321a268c36b7cc9b0a641e78a49e34d540a390a89155be70c218 (re-pointed after 3 non-triggering fixes from round 2: lambda capture rule, scanner quote state, lazy local.properties wiring)
