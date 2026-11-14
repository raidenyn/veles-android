---
trigger: glob
globs:
  - "android/**"
paths:
  - "android/**"
---

Before pushing, run the Android lint gate from `android/`: `./gradlew spotlessCheck detekt lintDebug`.

The `android / lint` CI job also rejects raw `android.util.Log` usage outside `LogSink.kt`.
From the repository root, run its guard and fix anything it prints (use `VelesLog`):

```bash
fail=0
# Catch fully-qualified references and wildcard imports of android.util.Log
if grep -rn "android.util.Log\|import android.util\.\*" android/app/src/main android/app/src/debug --include=*.kt | grep -v "LogSink.kt"; then
  echo "::error::Raw android.util.Log usage or wildcard import found outside LogSink.kt. Use VelesLog instead."
  fail=1
fi
# Catch bare Log.d/w/e/i/v calls (reachable via wildcard import) outside LogSink.kt
if grep -rnE "Log\.[dweiv]\(" android/app/src/main android/app/src/debug --include=*.kt | grep -v "LogSink.kt"; then
  echo "::error::Bare Log.d/w/e/i/v call found outside LogSink.kt. Use VelesLog instead."
  fail=1
fi
exit $fail
```
