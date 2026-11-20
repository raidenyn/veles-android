---
pointer: "Behaviour changes need a real device, emulator or Chrome run before done, stated in the PR or report; delivered at planning, apply, verify and before `gh pr create`; read this file then if it was not."
skills:
  - "openspec-ff-change"
  - "opsx:ff"
  - "openspec-propose"
  - "opsx:propose"
  - "openspec-continue-change"
  - "opsx:continue"
  - "openspec-apply-change"
  - "opsx:apply"
  - "openspec-verify-change"
  - "opsx:verify"
commands:
  - "\\bopenspec\\s+instructions\\s+(tasks|plan|finalize)\\b"
gates:
  - "\\bgh\\s+pr\\s+create\\b"
---

Unit tests are not enough: plan real-environment tests with the change and run them before calling it done.

- **Android logic** (code in `android/`, not docs, comments or formatting): run it on a real device or an emulator.
- **Web-extension logic**: run it in a real Chrome with the extension loaded.
- **Shared logic** (`rust/`, `protocol/`, `native-bridge/`): run it through each Android or Chrome consumer it affects.
- **More than one component affected**: also cover the end-to-end scenario across them.
- **Exempt**: docs, comments, formatting and other edits with no behaviour change.

State what ran and on what in the PR note, or the completion report if there is no PR. If a device or browser is unavailable, say so and ask; never report the change as tested.
