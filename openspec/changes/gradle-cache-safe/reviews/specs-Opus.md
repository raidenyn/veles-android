# Review record: specs

- Artifact: specs (`specs/gradle-build-caching/spec.md`, `specs/component-versioning/spec.md`)
- Reviewer: Opus — Opus-5.5, effort medium
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes:
  - brainstorm.md sha256 08569a637f46e514517ceca16203c206ddbe75b2411cf58281620435d266e8d4
  - proposal.md sha256 25a769528cb93da164101cf41b20b68e06d68bde72a603d303a3fe0b398bfdc4
  - design.md sha256 d5e8bb74414cb10e505f0ec0503af7d4004907b1a0c2aa6fbb2d3bdcfcce6784

## Pass 1

### specs/gradle-build-caching/spec.md

#### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | Scenario "Pinned file change invalidates the entry" contradicts D2's lazy wiring. | Fixed: config-time scenario uses `rust/ndk-version` (read for AGP); new "A lazily read pin is never stale" scenario for `toolchain-tools.toml`. |
| medium | The entry-point list is narrower than every unguarded inventory invocation. | Fixed: the rule covers every unguarded invocation; missing ones named. |
| medium | Platform scope unstated. | Fixed: each OS CI runs it on. |
| medium | Refusal rule ambiguous (inherited vs task-set env). | Fixed: inherited Gradle process environment; task overrides stated. |
| medium | No fallback when relocation/dotfile proof fails. | Fixed: non-cacheable with the failure recorded. |
| medium | Toolchain check scenario not testable via `--info`. | Fixed: runs twice, never `FROM-CACHE`. |
| low | Sync task mechanism. | Fixed. |
| low | Vague crate sources / linker identity. | Fixed. |
| low | `rust/gradle.properties` jvmargs. | Fixed. |
| low | `Properties.load` / `test-ndk-lookup.sh` limit. | Fixed. |

#### Round 2 — approve

| Severity | Finding | Disposition |
|---|---|---|
| low | `-q` hides the reuse message for `-q rustNdkVersion`. | Deferred to tasks: the reuse check runs the second call without `-q`. |
| low | Unnamed invocations (`test-jni-conformance.sh`, `--dry-run` calls, the `check-android-layout.sh:128` fresh-copy build, which cannot show reuse). | Deferred to tasks: covered by "every"; the fresh-copy build is checked for no problems only. |
| low | The two invalidation scenarios need real alternative values. | Deferred to tasks: the ndk-version change only needs the recalculation message; the pin change uses a real published version. |
| nit | "Wrapper set" needs sccache installed. | Deferred to tasks: a pass-through wrapper script is used. |

Approved content: specs/gradle-build-caching/spec.md sha256 61f399e10d7a86f314334873f30525e815693d12085825d21a814a7703dd867e

### specs/component-versioning/spec.md

#### Round 1 — approve

| Severity | Finding | Disposition |
|---|---|---|
| medium | Scenarios lack preconditions (clean tree, detached HEAD or `master`, no other `android-v*` tag on `HEAD`). | Fixed (non-triggering). |
| medium | The D3 note on *Initial versions without a tag* is not carried over. | Fixed (non-triggering): raw-value comparison and the planner-vs-fallback note added. |
| low | "New commit after the tag" is only checkable through the harness. | Fixed (non-triggering): points to the harness-pinned value. |
| low | The harness compared only name and code, the task requirement all lines. | Fixed (non-triggering): full `-q` output compared. |
| low | Raw code vs `maxOf(code, 1)` unclear; pre-release state omitted. | Fixed (non-triggering) for the raw/maxOf part; the pre-release state is deferred to the harness (design D3: "if the plugin accepts one"). |

Approved content: specs/component-versioning/spec.md sha256 91972bfa3d988df0127e83c127bb7be4463d3c43e3106914f58cdc05e97f7a0e (re-pointed after 5 non-triggering minor fixes listed in round 1: preconditions, planner-vs-fallback note, harness-pinned commit state, full-output harness, raw-code comparison)
