# Desktop BLE PoC — macOS review disposition

Claude Code read-only final gate used explicit **`claude-opus-5-5`**, session
`58001f71-b4af-4571-a5be-72818e6202c6`; returned modelUsage confirms that model.
No review subagents or device calls. Scope was the Mac delta against Windows
checkpoint `d5b6f2d`, not a repeat review of the unchanged prerequisite harness.

## Assessment

No critical findings. Mergeable as an experimental PoC conditional on the full
Mac background measurement passing with committed evidence, README/provenance
updates, required local lint and a green `macos-26` CI job. Formal OTP-23
acceptance remains false.

The full Mac measurement subsequently passed with committed JSON and a clean
46-line native trace: two byte-exact 4096-byte replies after more than 610 seconds
hidden, present at first read after 630 seconds detached, and no surviving host.
An initial passing run lacked a native trace because its output path was relative;
local Sonnet 5.5 resolved trace paths before launch and added a regression, then
the complete measurement was repeated rather than substituting a short run.

The reviewer independently checked bluest's maximum-write API and flow control,
cap arithmetic, authorization classification, cancel/connect cleanup, target-only
dependency changes and the physical evidence's 40 sends versus 39 replies.

## Important findings addressed

1. **Mac handoff missing:** README now includes build → package → register →
   diagnostic/matrix/background → unregister, GUI-context signing, TCC recovery
   and inferred-MTU semantics. The packaging script points to that README.
2. **Source revision unavailable:** archives intentionally do not include `.git`.
   Historical JSON remains unchanged; the signed host hash is the provenance
   anchor. `macos-poc-build-identity.json` adds Rust/cargo/Xcode/SDK identity,
   Cargo.lock and transport-source hashes independently checked on both machines.
   New driver runs report structured git-unavailable stage/reason instead of
   silently returning null, with unit coverage for missing git/non-repo/errors.

## Implementation model steering

The initial Mac backend and scripts used the previously requested
`ollama-cloud/glm-5.3-flash`. After the user's new instruction, implementation
tasks ran locally with **`claude-sonnet-5-5`**, including:

- Compiler warning/lifetime cleanup: `da86dd0c-5496-4a43-9978-9c43982d7e33`.
- Disposable-profile manifest discovery: `d713e3d5-eead-4090-95e6-56f78617c01e`.
- Authorization diagnostics/classification: `c21c6752-5d33-423e-8abf-ed84f1153c29`.
- Shared Mac background adapter: `c2357488-caaa-4ae6-9c78-731968bdffc3`.
- Three-platform CI: `9efc1f88-27ac-468a-a00e-6f2904a7301c`.
- Explicit provenance result: `4c355716-5bc0-4a5a-b9a9-9ea7356c1d08`.
- Absolute trace path: `1d6a2088-8c1d-4173-ad6e-0bce4476103b`.
- Explicit CI interpreters: `9e0f3358-a1cb-428b-9445-958338f266b6`.

CLI result metadata confirms Sonnet 5.5. Mac signing and command execution used
the GUI/Aqua context because SSH lacked private-key access; no source changes
were delegated to that execution-only session.

## Deferred experimental notes

- Duplicate late `LinkLost` is ignored by the core, but can be emitted twice.
- Mac notifications may overwrite in bluest's 16-event stream while writes are
  awaited; heavier-load handling needs a concurrent reader/overflow strategy.
- Cancellation is not biased ahead of a simultaneously ready write; discovery
  steps can take their setup deadline to cancel. Shutdown relies partly on exit
  for tasks removed from the join maps.
- Restricted/MDM authorization should not be labeled retryable in production.
- CoreGraphics window flags/comment, process command matching and Chrome final
  reaping can be tightened. The tested host itself owns no observed windows.
- Re-registering an owned manifest backs it up; unregister reports whether it
  restored that backup. Garbage manifests should fail more clearly.
- Explicit ad-hoc identity input is not yet rejected by the packaging CLI,
  although all measured runs use the verified Apple Development identity.
- The background shim's identity-error exit path and test hint naming can be
  made more uniform. These do not establish production readiness.

Declined-to-judge items remain bounded by the evidence: no inferred fresh-account
TCC attribution, no claim to exclude every Chrome throttling exemption, and no
untested hardware/account/platform result. CI must verify interpreter availability
on its actual `macos-26` image; real Mac driver execution used Python 3.9.6.
