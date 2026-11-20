# Review record: design

- Artifact: design (`design.md`)
- Reviewer: Opus — Opus-5.5, effort medium
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes:
  - brainstorm.md sha256 08569a637f46e514517ceca16203c206ddbe75b2411cf58281620435d266e8d4
  - proposal.md sha256 25a769528cb93da164101cf41b20b68e06d68bde72a603d303a3fe0b398bfdc4 (re-pointed from 19c95bd691411d2b70d1cf69c825915f5a2603a396e198bccffaeee4f74ff273 after the non-triggering probe-example fix noted in reviews/proposal-Opus.md)

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | D4/D2: `Exec` `executable`/`args` hold absolute paths, so the worktree path enters the cache key and worktree B never hits. | Fixed: `CargoBuildTask` (`@CacheableTask` DefaultTask + ExecOperations) with path-free `@Input` arguments, `@Internal` paths, `@OutputDirectory`. |
| medium | D8: before/after APK checksum on "the same commit" is ill-defined; version comes from git state. | Fixed: both sides pinned with the same local tag `android-v9.9.9`; unsigned release APK must match; debug compared and reported. |
| medium | D3: the harness cannot re-run once the plugin is removed; owner and gate unclear. | Fixed: two throwaway builds (plugin from the portal vs `android/git-version.gradle.kts`), android node, run during apply, documented as the re-proof. |
| medium | D6/D8: unguarded Windows/macOS jobs probed only by CI; installer pipeline had no class design. | Fixed: `homepc` probes before push; installer producer pipeline opted out with `notCompatibleWithConfigurationCache`. |
| medium | D4: a cached `rustWasm` may lack `.gitignore`. | Fixed: relocation proof compares the full file list including dotfiles; `doNotCacheIf` fallback. |
| low | D6: imprecise breaking-assertion list, `test_build_contract.py:571`, command end. | Fixed. |
| low | Context: inaccurate facts (root `ndkVersion`, bindgen `Cargo.lock`). | Fixed. |
| low | D4: incomplete environment denylist. | Fixed: extended; allowlist rejected with reason. |
| low | Lazy wiring not stated. | Fixed. |
| low | Scratch-copy builds without `gradle.properties`; `test-ndk-lookup.sh` missing. | Fixed. |
| low | `cacheIf` vs `@CacheableTask` open. | Fixed: `@CacheableTask`. |
| low | `VELES_KEYSTORE_*` `System.getenv` at configuration time. | Fixed: providers; local signed builds documented with `--no-configuration-cache`. |

### Round 2 — approve

| Severity | Finding | Disposition |
|---|---|---|
| medium | D2/D6 opt out the installer producer graphs, but the approved proposal named `bridgeInstallerUnsigned` as a probed example. | Fixed (non-triggering): the proposal's example corrected to `assembleRelease`/`rustPackage` with the opt-out named; D6 states the opt-out graphs are not probed. |
| low | D6: `bridgeHostBuild` does not call `installerSourceCommit()`. | Fixed (non-triggering): clause removed. |
| low | D4: absolute `--output-dir`/`--out-dir` cannot simply be appended. | Fixed (non-triggering): `preArguments`/`postArguments` with the insertion point named. |
| low | D4: mixed `@InputFiles` with RELATIVE sensitivity collides `rust/Cargo.toml` and `veles-crypto/Cargo.toml`. | Fixed (non-triggering): one `fileTree(rust/)` with includes. |

Approved content: design sha256 d5e8bb74414cb10e505f0ec0503af7d4004907b1a0c2aa6fbb2d3bdcfcce6784 (4 non-triggering minor fixes after round 2: probe-example deviation, bridgeHostBuild clause, argument insertion point, Rust file-tree sensitivity)
