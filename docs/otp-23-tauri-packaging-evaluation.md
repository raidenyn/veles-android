# OTP-23: Tauri as a packaging-only dependency — current evidence

**Conclusion: not established.** The runtime question is settled for this branch
(plain Rust; see [`otp-23-contract-alignment.md`](otp-23-contract-alignment.md)). Whether
Tauri is worth keeping **only for OTP-25 packaging** needs current build and packaging
evidence that this Linux session cannot produce. Nothing below claims the choice is
justified, and no installer was implemented. Matrix row D-01 therefore stays `BLOCKED`.

## Kept strictly separate: three bodies of evidence

| Evidence | Where | What it is | Not |
|---|---|---|---|
| **Historical no-go**, commit `1a34191` | `native-bridge/feasibility/evidence/cost.json`, `sbom/*.cdx.json`, charts, `tauri-exit-probe.txt`, `chrome-tauri*.json` | Windows x64 builds of the old tree: baseline 717,824 B and 22 crates; Tauri linked-but-unstarted 755,712 B and 275 crates; Tauri runtime 7,862,784 B, 285 crates, 1.5 s exit after shutdown; visible helper window; forced exit | Current. Costs were measured on those binaries and are not relabelled |
| **Historical later successes**, `d5b6f2d` (Windows), `fb224e8` (macOS) | `poc-*.json`, `macos-poc-*.json`, traces | Plain-Rust host liveness/transfer, administrator account, CDP attached | Production acceptance |
| **Current** (this document) | `native-bridge/feasibility/evidence/current-dependency-cost.json` (`scripts/dependency_cost.py`) | Dependency graphs from today's manifests and `Cargo.lock` with Rust 1.98.0 | Size, build time, behavior, installer proof |

## Current dependency graphs

`cargo metadata --locked --filter-platform TARGET`, crates reachable from the root through
normal and build edges (dev excluded), the same metric as the historical `cost_report.py`.

| Variant | Target | Direct | Unique crates | Proc-macro | Build-script | `links` |
|---|---|---|---|---|---|---|
| Plain-Rust host (`feasibility`, no features) | Windows | 4 | 22 | 3 | 5 | 0 |
| Plain-Rust host (`feasibility`, no features) | macOS | 6 | 46 | 3 | 9 | 0 |
| Same host + `tauri-linked` (Tauri compiled and linked, never started) | Windows | 6 | 276 | 24 | 36 | 1 |
| Same host + `tauri-linked` (Tauri compiled and linked, never started) | macOS | 8 | 275 | 23 | 36 | 1 |
| Same host + `tauri-runtime` (windowless Tauri shell) | Windows | 6 | 286 | 25 | 39 | 1 |
| Same host + `tauri-runtime` (windowless Tauri shell) | macOS | 8 | 295 | 24 | 39 | 2 |
| Production stub `native-bridge/src-tauri`, no `tauri-runtime` feature (still pulls `tauri-build`) | Windows | 3 | 211 | 20 | 28 | 0 |
| Production stub `native-bridge/src-tauri`, no `tauri-runtime` feature (still pulls `tauri-build`) | macOS | 3 | 206 | 20 | 27 | 0 |
| Production stub with `tauri-runtime` | Windows | 4 | 283 | 27 | 38 | 1 |
| Production stub with `tauri-runtime` | macOS | 4 | 271 | 24 | 37 | 1 |

Reading it (graph facts only):

* The plain host is 22 crates on Windows (the same count as at `1a34191`) and 46 on macOS
  (`bluest`, CoreBluetooth bindings, a small Tokio).
* Linking Tauri, even without ever starting it, multiplies the graph about 12× on Windows
  (22 → 276). The Tauri runtime adds ten more.
* The committed production stub (`native-bridge/src-tauri`) already carries about 210 crates
  **without** the runtime feature, because `tauri-build` is an unconditional build dependency.
  Whatever the packaging decision, that stub is not the lean host the RFC describes.
* Tauri-family crates remain in the review surface for any variant that uses them (3 for the
  build-only stub, 7–10 otherwise).

## What would justify, or reject, retaining Tauri for packaging

Not measured and required (coordinating agent, on the Windows and macOS builders):

1. **Binary size and build time** of the unmodified plain-Rust host versus the artifact the
   Tauri bundler produces around it, from clean builds with pinned toolchains (the `1a34191`
   method is in `scripts/cost_report.py`).
2. **SBOMs** (`cargo cyclonedx`) of the packaging path actually shipped, with licenses; the
   Tauri **CLI** is a build-tool dependency, not a crate in the host's graph, so the host SBOM
   alone does not capture it.
3. A **packaging proof**: the Tauri bundler wrapping the plain host (runtime never started)
   produces a per-user Windows installer and macOS bundle with the exact-origin manifest, and
   its signed payload corresponds byte-for-byte to the reproducible unsigned inputs (OTP-25
   clauses).
4. The **alternatives' cost**: a standalone signing/notarization pipeline (for example
   `signtool`/`makensis`/WiX and `codesign`/`notarytool`) written and maintained in-repo.

Until those exist, the RFC records Tauri only as an open packaging choice. A packaging result
cannot repair a failed runtime requirement, and a packaging-only retention must never
reintroduce the Tauri event loop into the host (the helper window and exit fallback are the
reasons it was removed).

## Reproduce the current numbers

```sh
cd native-bridge/feasibility
rustup target add x86_64-pc-windows-msvc aarch64-apple-darwin   # metadata only, nothing is built
python3 scripts/dependency_cost.py --out evidence/current-dependency-cost.json
```
