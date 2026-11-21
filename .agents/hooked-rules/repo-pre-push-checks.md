---
pointer: "`git push` is denied until `python3 -I scripts/verify.py --branch` passed for HEAD; delivered at `openspec instructions finalize` and at a denied push; read this file then if it was not."
commands:
  - "\\bopenspec\\s+instructions\\s+finalize\\b"
gates:
  - "\\bgit\\b[^;&|\\n]*?\\spush\\b"
requires: ["branch-pass"]
exempt:
  - "\\bgit\\b[^;&|\\n]*?\\spush\\b[^;&|\\n]*?(?:\\s--delete(?:\\s|$)|\\s-d(?:\\s|$))"
  - "\\bgit\\b[^;&|\\n]*?\\spush\\b(?:\\s+-[^\\s;&|]+)*\\s+[^\\s;&|]+\\s+(?:\\+?:[^\\s;&|]+)(?:\\s+\\+?:[^\\s;&|]+)*\\s*$"
---

Commit everything first: the branch pass needs a clean tree, including new untracked change files.

Then run

```bash
python3 -I scripts/verify.py --branch
```

in the background and wait for it: it takes about 15 minutes, and a foreground run exceeds the
shell tools' limits. Fix every `FAIL` and every unexpected `NOT RUN` (its fix text is the path),
then push.
