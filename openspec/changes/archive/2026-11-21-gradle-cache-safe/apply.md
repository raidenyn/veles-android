# Apply Receipt

> Generated at the end of the apply phase to mark code-implementation
> complete and provide verify with the state it needs.
> Overwritten on each apply iteration; iteration counter grows.

**Change**: `gradle-cache-safe`
**Iteration**: `1`
**Applied at**: `2026-11-21 05:17`
**Executor**: `subagent-driven-development`

---

## Workspace

- **Worktree**: `/home/dev/.t3/worktrees/veles-android/t3-699e4a45-1711ce8ea1e8baf4`
- **Branch**: `chore/gradle-cache-safe`

---

## Commits

- **Range**: `fe22068b..5ce5c1c9`
- **Count**: `28`

---

## Tasks

- **Completed**: `48 of 48` checkboxes in tasks.md flipped to `- [x]`
- **Remaining**: `none`

---

## Evidence of note for verify

- Configuration cache is on and strict (`gradle.properties`, `android/gradle.properties`,
  `rust/gradle.properties`); every unguarded entry point reuses the entry, and the strictness probe
  fails a build that captures the script (`evidence/cc-reuse.md`).
- Cacheable build tasks are `rustJni` and `rustHostJni`; `rustWasm` is non-cacheable after its
  relocation proof lost wasm-pack's `pkg/.gitignore` (`evidence/cache-experiments.md`,
  `evidence/cacheability.md`).
- Release and verification invocations pass `--no-build-cache --no-configuration-cache`, enforced by a
  mutation-tested contract test (`verify/test/workflow-contracts.test.mjs`).
- Release APK and `rustPackage` `SHA256SUMS` equal the merge-base baseline; the debug APK differs only
  by the locally regenerated debug keystore (`evidence/apk.md`).
- Version derivation equals plugin 0.4.14 across eight git states (`evidence/version-parity.md`).
- One check is not green: the full `connectedDebugAndroidTest` suite on the attached phones. App source
  is unchanged since the green Task 5 run; the cause is not established. Recorded in
  `evidence/gates.md` and `evidence/pr-summary.md`.
- End-to-end pairing (10.5) is unavailable: the phone is on the PC and the extension's BLE transport
  needs a Native Messaging host on the browser's machine.

---

## Next step

Run `openspec-verify-change` for `gradle-cache-safe`.
