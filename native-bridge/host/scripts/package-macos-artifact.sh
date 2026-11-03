#!/usr/bin/env bash
# Packages the OTP-24 production host for artifact upload: a mode-preserving tar.gz holding ONLY the
# staged `veles-native-host` executable and its `SHA256SUMS`, plus an archive sidecar checksum.
# actions/upload-artifact drops the executable bit of bare files; the tar keeps it. Usage:
#   package-macos-artifact.sh <build/native-bridge/host> <build/native-bridge/host-archive>
# Fails (never substitutes) if the executable or a matching checksum is missing.
set -euo pipefail

if [ "$#" -ne 2 ]; then
  echo "usage: package-macos-artifact.sh <host-dir> <archive-dir>" >&2
  exit 2
fi
host_dir="$1"
archive_dir="$2"
name="veles-native-host-macos.tar.gz"

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | cut -d' ' -f1; else shasum -a 256 "$1" | cut -d' ' -f1; fi
}

[ -f "$host_dir/veles-native-host" ] || { echo "ERROR: production executable missing in $host_dir" >&2; exit 1; }
[ -f "$host_dir/SHA256SUMS" ] || { echo "ERROR: SHA256SUMS missing in $host_dir" >&2; exit 1; }
[ "$(cut -d' ' -f1 "$host_dir/SHA256SUMS")" = "$(sha256_of "$host_dir/veles-native-host")" ] \
  || { echo "ERROR: SHA256SUMS does not match veles-native-host" >&2; exit 1; }
chmod 755 "$host_dir/veles-native-host"

rm -rf "$archive_dir"
mkdir -p "$archive_dir"
# Explicit member list: nothing else from the directory (e.g. a test driver) can enter the archive.
COPYFILE_DISABLE=1 tar -C "$host_dir" -czf "$archive_dir/$name" veles-native-host SHA256SUMS
printf '%s  %s\n' "$(sha256_of "$archive_dir/$name")" "$name" > "$archive_dir/$name.sha256"
