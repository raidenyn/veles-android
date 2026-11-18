# Contributing to Veles

Thank you for your interest in contributing to Veles! We welcome contributions, from adding support for new bank message templates to refining existing components and extending external-device toolchains.

---

## Core Privacy Constraint

Veles is an on-device utility for handling sensitive financial OTPs. Any contribution must strictly preserve our privacy guarantees:
- **Zero network access:** The Android app does not request or use the internet permission.
- **Zero telemetry or analytics:** No tracking, crash reporting, or external logging SDKs.
- **Local storage only:** Message configs and bank templates reside solely in the local Room database; notification bodies are processed in-memory and discarded.

---

## How Veles Works (Architecture)

```
Incoming notification
  → NotificationListener (NotificationListenerService)
      → CompositeMessageHandler
          → RegexMessageHandler (one per bank, configs loaded from local Room DB)
              → UserNotifierOtpMessageHandler (posts a simplified notification)
                  → CopyDataReceiver (BroadcastReceiver — copies the OTP on tap)
```

Each bank is represented by a row in the local Room database (`bank_handler_configs`) containing three regular expressions:

- **`otpRegex`**: Group 1 = OTP id/prefix, Group 2 = OTP value.
- **`moneyRegex`**: Group 1 = Currency code, Group 2 = Amount.
- **`merchantRegex`**: Group 1 = Merchant name.

All three regexes must match for an incoming notification to be accepted. When a handler returns `ACCEPTED`, the original notification is cancelled and the concise notification is posted.

---

## Adding Support for a New Bank

1. You can test new regex patterns directly using the **in-app Test Screen** in Veles: enter sample notification text, post it, and verify that the handler parses all three fields accurately.
2. Default bank seeds (such as UOB Thailand) are managed in Room migrations and database pre-population. You can contribute new presets via a PR modifying the database seed configs.

---

## Commit Guidelines & Git Workflow

We use [Conventional Commits](https://www.conventionalcommits.org/) (`feat:`, `fix:`, `docs:`, `refactor:`, `test:`, etc.) for commit messages and pull request titles. 

Pull requests are squash-merged, so your PR title and description become the final commit on `main`.

### Git Hooks

Install the local `commit-msg` hook to validate commit messages before committing:

```bash
./gradlew installGitHooks
```

*(This hook also runs automatically as part of `:app:preBuild`, and pull request titles are verified by the required `pr-title` CI check).*

---

## AI agent tooling

Agent guidance is tool-neutral: the root `AGENTS.md` and one `AGENTS.md` per node give that folder's
purpose, commands, constraints and rules, and link the `docs/` pages that hold the architecture and
procedures; `.agents/rules/` holds the scoped rule files. The agent-file check
(`docs/checks/check_agent_files.py`) enforces the layout, including the 40-line cap on every
`AGENTS.md`; `docs/agent-guidance.md` explains the policy and how to add a rule or a node.

- **Start at the repository root.** Scoped rules are delivered only when the agent is started at the
  root; started inside a node folder you get that folder's `AGENTS.md` (Claude Code also loads the
  parent chain; OpenCode does not), but you must read that folder's listed rule files by hand.
- **Claude Code** needs ≥ 2.1.277 (≥ 2.1.281 for Bedrock and telemetry-off sessions). A `CLAUDE.md`
  or `CLAUDE.local.md` in any parent folder hides `AGENTS.md`, so do not create one — and do not run
  `/init`, which can.
- **OpenCode** needs the `opencode-rules` plugin at the range pinned in `opencode.json`; without it
  scoped rules are not delivered automatically. Install and troubleshooting steps are in
  `docs/opencode-rules.md`. Never repeat the version range in another file.
- **Antigravity** reads `AGENTS.md`; it does not load nested node files on its own, so follow the
  node map in the root file and open each node's `AGENTS.md` yourself. Scoped `.agents/rules`
  delivery in the IDE is unverified, so read the node's listed rule files as well.
- **Checkout on Windows** requires `core.symlinks=true` *and* Developer Mode, or `.claude/rules` and
  the skill links are checked out as plain text and Claude Code sees no rules or skills (the check
  warns explicitly). On Linux and macOS the committed symlinks resolve as-is.
- **OpenSpec** CLI `>= 2.0.0, < 3` (2.0.0 last verified; 1.x is unsupported), run on Linux or macOS
  only. Use the update procedure in `docs/openspec-workflow.md`; generated `openspec-*` skills must
  never be hand-edited.
- **Reviewing an untrusted checkout:** read `opencode.json` and `.claude/settings.json` before
  launching an agent, or start outside the checkout / disable the project's plugin configuration,
  because repository configuration can install and run code.

---

## Build & Release Documentation

- Refer to [BUILD.md](https://github.com/raidenyn/veles-android/blob/master/BUILD.md) for local compile and test instructions.
- Refer to [docs/ci.md](https://github.com/raidenyn/veles-android/blob/master/docs/ci.md) for continuous integration workflows.
- Refer to [docs/releasing.md](https://github.com/raidenyn/veles-android/blob/master/docs/releasing.md) for versioning rules and release runbooks.
