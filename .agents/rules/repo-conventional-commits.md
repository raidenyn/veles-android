---
trigger: always_on
---

Write every commit subject as a Conventional Commit: `type(scope): description`.
Release tooling derives versions and changelogs from these subjects, so a non-conforming
subject breaks a release.
