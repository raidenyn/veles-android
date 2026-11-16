## MODIFIED Requirements

### Requirement: Platform facts live in one canonical folder

macOS signing identity and TCC rules SHALL live only in the macOS node:
`native-bridge/installer/macos/AGENTS.md` and the `.agents/rules/macos-*` rule files that file lists.
Windows facts SHALL live only in `native-bridge/installer/windows/AGENTS.md`, and Android signing and
architecture only in `android/AGENTS.md`. Cross-platform nodes such as `native-bridge/host/` MUST
point to the platform file instead of copying it, and MAY keep the CI artifact names the
build-contract gate requires. When a macOS rule is path-scoped and therefore does not load in a
cross-platform node that consumes it, that node's `AGENTS.md` MUST tell the agent to read the rule by
hand before the relevant run.

#### Scenario: Host pointer

- **WHEN** `native-bridge/host/AGENTS.md` mentions macOS signing
- **THEN** it is a one-line pointer to `native-bridge/installer/macos/AGENTS.md` and, for a macOS
  Bluetooth E2E run, an instruction to read `.agents/rules/macos-test-app-tcc.md` by hand; the rule
  text is not copied

#### Scenario: Script citation

- **WHEN** `native-bridge/host/scripts/macos-test-bundle.sh` cites its signing rules
- **THEN** it cites `native-bridge/installer/macos/AGENTS.md`

#### Scenario: macOS rule lives in the node

- **WHEN** a macOS TCC rule file is added
- **THEN** it is named `macos-<topic>.md`, scoped to `native-bridge/installer/macos/**` and listed by
  that node's `AGENTS.md`, and the root rules gain no macOS content
