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

## Build & Release Documentation

- Refer to [BUILD.md](BUILD.md) for local compile and test instructions.
- Refer to [docs/ci.md](docs/ci.md) for continuous integration workflows.
- Refer to [docs/releasing.md](docs/releasing.md) for versioning rules and release runbooks.
