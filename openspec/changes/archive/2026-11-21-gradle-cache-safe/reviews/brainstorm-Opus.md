# Review record: brainstorm

- Artifact: brainstorm (`brainstorm.md`)
- Reviewer: Opus — Opus-5.5, effort medium
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: none (brainstorm is first in the order)

## Pass 1

### Round 1 — approve

| Severity | Finding | Disposition |
|---|---|---|
| medium | Key Decision 1: CC becomes the default with `problems=fail`, but only some entry points were probed; CI also runs `protocolCheck`, `rustInstall`, `bridgeHostBuild`, `:app:releaseHookGuard`, `connectedDebugAndroidTest`, `clean`, the Windows/macOS `gradlew.bat` jobs and `verify/check-android-layout.sh`. Needs a full invocation inventory with a probe owner per OS. | Deferred to design (inventory of every Gradle invocation in `.github/workflows/*.yml` and `verify/*.sh`, probe owner per OS). |
| medium | Key Decision 4: the ambient environment variables that refuse caching are not listed, cargo config files (`$CARGO_HOME/config.toml`) are not covered, and the host C linker is not in the `rustHostJni` key. | Deferred to design (exact refusal rule including cargo config files and the host linker). |
| medium | Key Decision 6: how the contract test finds every invocation (including `$(./gradlew …)` and `.\gradlew.bat`) is unspecified, and existing exact-string assertions in `workflow-contracts.test.mjs` (around lines 320, 373, 400–405, 1243) will break. | Deferred to design and tasks (invocation scanner and the existing assertions to update). |
| medium | Goal 3 vs Decision 6: the guard covers workflows only; `verify/generate-sboms.sh:20` runs `gradlew verifyCargoCyclonedx` without flags. | Deferred to design (the guard also covers Gradle invocations in `verify/*.sh`). |
| medium | Key Decision 8 drops the issue's debug APK checksum comparison and the `scriptsTest`/workflow-contracts/`verify-supply-chain.sh` gates. | Deferred to design and tasks (both added to the evidence and gates). |
| low | Key Decision 3: plugin references in `libs.versions.toml:80`, `docs/releasing.md:46`, `docs/android.md:149`, `android/AGENTS.md:32` not named. | Deferred to tasks. |
| low | `pinnedVerificationToolVersion()` and the `ndk-version` `readText` at configuration time are not addressed. | Deferred to design (move to `providers.fileContents`). |
| low | The timing plan does not say which runs pass `--build-cache`. | Deferred to design (timing matrix). |
| low | Open Question 1 (where Android runs) also gates the version harness and tag experiment. | Deferred to tasks/plan (settled there). |

Approved content: brainstorm sha256 08569a637f46e514517ceca16203c206ddbe75b2411cf58281620435d266e8d4
