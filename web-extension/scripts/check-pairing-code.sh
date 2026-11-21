#!/bin/sh
# Guard against pairing-code logging in the Chrome debug surface (the `web-extension / lint` CI guard).
#
# Run from the repository root, with no arguments. Exit 0 when clean, 1 on a match. The grep, its
# `::error::` line and the exit status are the inline CI block's, moved here verbatim.
set -u

fail=0
if grep -rnE "(console\.[a-z]+|DiagnosticRing|diagnostics\.push|textContent\s*=|\bresult\s*\(|\blog\s*\()[^;]*(PairingCode|\.code\b|codeBytes|\bdigits\b|PairingDebugState|\bcode\b)" web-extension/src/debug --include=*.ts; then
  echo "::error::The pairing code reaches a log, DiagnosticRing or rendered text in the Chrome debug surface."
  fail=1
fi
exit $fail
