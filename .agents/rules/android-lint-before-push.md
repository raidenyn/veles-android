---
trigger: glob
globs:
  - "android/**"
paths:
  - "android/**"
---

Before pushing, run the Android lint gate.

Run from `android/`:

```bash
./gradlew spotlessCheck detekt lintDebug
```

From the repository root, run the source guards and fix anything they print:

```bash
android/scripts/check-raw-log.sh                                   # raw android.util.Log outside LogSink.kt (use VelesLog)
android/scripts/check-pairing-code.sh                              # pairing-code logging in the Kotlin debug surface
node scripts/check-mac-comparisons.mjs --scan android/app/src/main  # direct MAC comparisons outside the constant-time helpers
```
