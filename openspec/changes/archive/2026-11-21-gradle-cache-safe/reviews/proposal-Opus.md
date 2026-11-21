# Review record: proposal

- Artifact: proposal (`proposal.md`)
- Reviewer: Opus — Opus-5.5, effort medium
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes:
  - brainstorm.md sha256 08569a637f46e514517ceca16203c206ddbe75b2411cf58281620435d266e8d4

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | New capability `android-version-derivation` duplicates `component-versioning`'s *Android version from tags*. | Fixed: listed as Modified Capability `component-versioning`; the new capability removed. |
| high | "Every invocation runs with CC" contradicts the guard's `--no-configuration-cache`; the guarded workflows also run on PRs (`ci.yml:159`, `build-pr-installers.yml:70,82`). | Fixed: CC scope excludes the guarded invocations; PR use stated; CC compatibility of the guarded graphs proven by local/`homepc` probes. |
| medium | The blanket `verify/*.sh` guard catches `verify/check-android-layout.sh`. | Fixed: only artifact-producing/verifying scripts (`verify/generate-sboms.sh`) are guarded; layout checks keep CC on. |
| medium | "A new tag invalidates" is too narrow. | Fixed: any git state that changes the computed name or code. |
| low | `:app:androidGitVersion` comes from the removed plugin. | Fixed: a repository task replaces it with the same output under `-q`. |
| low | `rust/` has no `gradle.properties`. | Fixed: new `rust/gradle.properties`. |
| low | No evidence item. | Fixed: "Evidence in the PR" section added. |

### Round 2 — approve

| Severity | Finding | Disposition |
|---|---|---|
| low | The proposal extends the brainstorm's guard scope (`verify/generate-sboms.sh`) and CC files (`rust/gradle.properties`) without the brainstorm being updated. | Deferred: both extensions came from review findings recorded in reviews/brainstorm-Opus.md; the design is the binding detail and restates them. |
| low | The evidence list is vaguer than brainstorm decision 8 (the pinned input, the setup). | Deferred: the design and tasks carry the exact experiment setup. |

Approved content: proposal sha256 19c95bd691411d2b70d1cf69c825915f5a2603a396e198bccffaeee4f74ff273

Re-point after a non-triggering edit (1 minor fix, from reviews/design-Opus.md round 2): the CC-probe example changed from `bridgeInstallerUnsigned`/`assembleRelease` to `assembleRelease`/`rustPackage`, and the installer packaging pipeline opt-out (design D2) is named.

Approved content: proposal sha256 25a769528cb93da164101cf41b20b68e06d68bde72a603d303a3fe0b398bfdc4 (re-pointed: probe example corrected to match design D2/D6)
