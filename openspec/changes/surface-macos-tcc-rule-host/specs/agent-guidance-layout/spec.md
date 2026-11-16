## MODIFIED Requirements

### Requirement: Platform facts live in one canonical folder
macOS signing identity and TCC rules SHALL live only in the macOS node: `native-bridge/installer/macos/AGENTS.md` owns the canonical identity facts, and the macOS TCC rule it points at is `.agents/rules/native-bridge-test-app-tcc.md`. Windows facts SHALL live only in `native-bridge/installer/windows/AGENTS.md`, and Android signing and architecture only in `android/AGENTS.md`. Cross-platform nodes such as `native-bridge/host/` MUST point to the platform file instead of copying it, and MAY keep the CI artifact names the build-contract gate requires. A macOS TCC rule consumed by both the macOS node and another node under `native-bridge/` MUST be named `native-bridge-<topic>.md`, scoped to the narrowest common parent `native-bridge/**` so it loads in every consuming node, and listed in `native-bridge/AGENTS.md`; a macOS-only rule stays `macos-<topic>.md`, scoped to `native-bridge/installer/macos/**` and listed by that node. The macOS node's `AGENTS.md` MUST still point at the rule.

#### Scenario: Host pointer
- **WHEN** `native-bridge/host/AGENTS.md` mentions macOS signing
- **THEN** it is a one-line pointer to `native-bridge/installer/macos/AGENTS.md` and names `.agents/rules/native-bridge-test-app-tcc.md`, which loads under `native-bridge/host/` through the `native-bridge/**` scope without a by-hand read instruction; the rule text is not copied

#### Scenario: Script citation
- **WHEN** `native-bridge/host/scripts/macos-test-bundle.sh` cites its signing rules
- **THEN** it cites `native-bridge/installer/macos/AGENTS.md`

#### Scenario: macOS rule lives in the node
- **WHEN** a macOS TCC rule file is added or rescoped
- **THEN** it is named for the narrowest scope that loads in every consuming node (`macos-<topic>.md` scoped to `native-bridge/installer/macos/**` when only that node consumes it, `native-bridge-<topic>.md` scoped to `native-bridge/**` when the host also consumes it), listed by that scope's `AGENTS.md`, and the root rules gain no macOS content
