#!/bin/sh
# Guard against raw android.util.Log usage outside LogSink.kt (the `android / lint` CI guard).
#
# Run from the repository root, with no arguments. Exit 0 when clean, 1 on a match. The two greps,
# their `::error::` lines and the exit status are the inline CI block's, moved here verbatim.
set -u

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
