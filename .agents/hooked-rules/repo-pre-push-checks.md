---
pointer: "`git push` is denied until `python3 -I scripts/verify.py` passed for HEAD; delivered at `openspec instructions finalize` and at a denied push; read this file then if it was not."
commands:
  - "\\bopenspec\\s+instructions\\s+finalize\\b"
gates:
  - "\\bgit\\b[^;&|\\n]*?\\spush\\b"
requires: ["branch-pass"]
exempt:
  - "\\bgit\\b[^<>$`;&|#\\n()]*?\\spush\\b[^<>$`;&|#\\n()]*?(?:\\s(?<!-o )(?<!--push-option )(?<!--receive-pack )(?:--delete|-d)(?:\\s|$))"
  - "\\bgit\\b[^<>$`;&|#\\n]*?\\spush\\b(?:\\s+-[^\\s<>$`;&|#]+)*\\s+[^\\s<>$`;&|#]+\\s+(?<!-o )(?<!--push-option )(?<!--receive-pack )(?:\\+?:[^\\s<>$`;&|#]+)(?:\\s+\\+?:[^\\s<>$`;&|#]+)*\\s*$"
---

Commit everything first: the pass needs a clean tree, including new untracked change files.

Then run

```bash
python3 -I scripts/verify.py
```

in the background and wait for it: the Android checks and related tests can take minutes, and a
foreground run can exceed the shell tools' limits. Fix every `FAIL` and every unexpected `NOT RUN`
(its fix text is the path), then push.

`python3 -I scripts/verify.py --branch` is the optional full local run and satisfies the gate as
well; CI runs every test on the pull request.
