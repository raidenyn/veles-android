#!/bin/sh
# Guard against pairing-code logging in the Kotlin debug surface (the `android / lint` CI guard).
#
# Run from the repository root, with no arguments. Exit 0 when clean, 1 on a match. The grep, its
# `::error::` line and the exit status are the inline CI block's, moved here verbatim.
#
# The pairing code may exist only as the on-screen readout and the in-process StateFlow. These greps
# mirror the Vitest negative controls: a sink combined with a code-bearing value is a leak even when
# the line never names PairingCode. The Chrome debug surface is guarded by web-extension / lint.
set -u

fail=0
if grep -rnE "(VelesLog|logger\.[dweiv]|\blogger?\b|Log\.[dweiv]|println|print\s*\(|setResultData|\bresult\s*\(|Notification|putExtra)" android/app/src/debug --include=*.kt | grep -E "(PairingCode|\.code\b|codeBytes|\bdigits\b|PairingDebugState|\bcode\b)"; then
  echo "::error::A pairing-code value reaches a logger, broadcast result or notification in the Android debug surface."
  fail=1
fi
exit $fail
