---
pointer: "Before `git push`, run the checks of every node `scripts/components.py changed` selects; delivered at `openspec instructions finalize` and before the push; read this file then if it was not."
commands:
  - "\\bopenspec\\s+instructions\\s+finalize\\b"
gates:
  - "\\bgit\\b[^;&|\\n]*?\\spush\\b"
---

Before pushing, find the nodes you touched with
`python3 -I scripts/components.py changed --base $(git merge-base origin/master HEAD)` and run
the checks listed in the `AGENTS.md` of every node it selects, including nodes selected because
of a shared input such as `gradle/`, `gradlew`, `.java-version` or a workflow file.
