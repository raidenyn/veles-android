---
trigger: always_on
---

Never regenerate, replace or commit signing keys or their passwords. If a key is missing,
expired or rejected, stop and ask the owner; the node files hold the per-platform details.
Commits are scanned by `.githooks/pre-commit` (gitleaks, `./gradlew installGitleaks`) and pushes by
GitHub push protection; both catch recognised token patterns, not keystores or their passwords, which
stay git-ignored. A finding on a key is a stop, never an allowlist entry.
