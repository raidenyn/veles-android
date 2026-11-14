---
trigger: always_on
---

Before pushing, find the nodes you touched with
`python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)` and run
the checks listed in the `AGENTS.md` of every node it selects, including nodes selected because
of a shared input such as `gradle/`, `gradlew`, `.java-version` or a workflow file.
