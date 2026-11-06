#!/usr/bin/env bash
# OTP-25 Task 11: assemble the UNSIGNED, deterministic macOS installer input tree and its disk-image
# layout from already-built, already-verified inputs.
#
#   package-macos.sh --inputs <input-dir> --output <new-output-dir>
#
# This script only copies, substitutes one version string, normalises modes/timestamps and writes
# checksums. It never builds, signs, notarizes, staples, mounts or creates a disk image, holds no
# credential and touches nothing outside <output-dir>. Signing the nested code inside-out, building
# the DMG and notarizing it are Task 13 (protected jobs, credentials, no rebuild); the
# `dmg-root/` directory below is exactly the folder that job hands to the image builder.
#
# <input-dir> must hold EXACTLY these regular files (no symlinks, no sub-directories, nothing else):
#   VERSION                    one line `MAJOR.MINOR.PATCH` + newline
#   VelesBridgeInstaller       the arm64 SwiftUI frontend executable
#   veles-bridge-maintenance   the engine executable (becomes carrier.app's executable)
#   veles-native-host          the production Bluetooth host executable
#   veles-policy-carrier       the policy carrier document (carrier.app/Contents/Resources)
#   descriptor.json            the authenticated release descriptor
#   inventory.json             the canonical payload inventory (its digest is the payload id)
#   sealed-inventory.json      OPTIONAL: signed-byte inventory, present only when signing changed bytes
# Info.plist, CarrierInfo.plist and HostInfo.plist come from ../macos in this repository.
#
# <output-dir> (must not exist) receives:
#   dmg-root/Veles Bridge Installer.app/...   the unsigned application tree (the only DMG content)
#   SHA256SUMS                                digest of every file below dmg-root, byte-order sorted
#   manifest.tsv                              path <TAB> mode <TAB> size <TAB> sha256, same order
#
# Determinism: no timestamp, owner, absolute path, xattr or symlink survives. Directories are 0755,
# the three executables 0755, every other file 0644, every mtime is 2000-01-01T00:00:00Z, and the
# result does not depend on the umask, TZ, locale or the location of the inputs/output. No
# /Applications alias is created: this is a per-user installer and must not suggest a system path.
set -euo pipefail

usage() {
  echo "usage: package-macos.sh --inputs <input-dir> --output <new-output-dir>" >&2
  exit 2
}

fail() {
  echo "package-macos: $*" >&2
  exit 1
}

inputs=""
output=""
while [ "$#" -gt 0 ]; do
  case "$1" in
    --inputs)
      [ "$#" -ge 2 ] || usage
      inputs="$2"
      shift 2
      ;;
    --output)
      [ "$#" -ge 2 ] || usage
      output="$2"
      shift 2
      ;;
    *) usage ;;
  esac
done
[ -n "$inputs" ] && [ -n "$output" ] || usage

export LC_ALL=C
export TZ=UTC
STAMP="200001010000.00"
APP_NAME="Veles Bridge Installer.app"
HOST_APP_NAME="Veles Native Bridge.app"

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
macos_dir="$script_dir/../macos"

sha256_of() {
  if command -v sha256sum >/dev/null 2>&1; then
    sha256sum "$1" | cut -d' ' -f1
  else
    shasum -a 256 "$1" | cut -d' ' -f1
  fi
}

size_of() {
  wc -c <"$1" | tr -d ' '
}

# ---- validate everything before creating anything ----------------------------------------------

[ -d "$inputs" ] && [ ! -L "$inputs" ] || fail "inputs is not a directory"
inputs="$(cd "$inputs" && pwd -P)"
output_parent="$(dirname "$output")"
[ -d "$output_parent" ] || fail "the output's parent directory does not exist"
output="$(cd "$output_parent" && pwd -P)/$(basename "$output")"
[ ! -e "$output" ] && [ ! -L "$output" ] || fail "the output already exists; it is never overwritten"
case "$output/" in
  "$inputs"/*) fail "the output may not be inside the inputs" ;;
esac
case "$inputs/" in
  "$output"/*) fail "the inputs may not be inside the output" ;;
esac

for plist in Info.plist CarrierInfo.plist HostInfo.plist; do
  [ -f "$macos_dir/$plist" ] && [ ! -L "$macos_dir/$plist" ] || fail "missing bundle input $plist"
done

required="VERSION VelesBridgeInstaller veles-bridge-maintenance veles-native-host veles-policy-carrier descriptor.json inventory.json"
allowed="$required sealed-inventory.json"

while IFS= read -r -d '' entry; do
  name="$(basename "$entry")"
  case " $allowed " in
    *" $name "*) ;;
    *) fail "unexpected input entry" ;;
  esac
  [ ! -L "$entry" ] || fail "input $name is a symbolic link"
  [ -f "$entry" ] || fail "input $name is not a regular file"
done < <(find "$inputs" -mindepth 1 -maxdepth 1 -print0)

for name in $required; do
  [ -f "$inputs/$name" ] && [ ! -L "$inputs/$name" ] || fail "missing input $name"
  [ -s "$inputs/$name" ] || fail "input $name is empty"
done

version_line=""
IFS= read -r version_line <"$inputs/VERSION" || true
version_re='^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$'
[[ "$version_line" =~ $version_re ]] || fail "VERSION is not MAJOR.MINOR.PATCH"
[ "$(size_of "$inputs/VERSION")" -eq "$((${#version_line} + 1))" ] \
  || fail "VERSION must be exactly one line"
version="$version_line"

# ---- build --------------------------------------------------------------------------------------

created=0
cleanup() {
  if [ "$created" -eq 1 ] && [ "${finished:-0}" -ne 1 ]; then
    rm -rf "$output"
  fi
}
trap cleanup EXIT

mkdir "$output"
created=1

app="$output/dmg-root/$APP_NAME"
carrier="$app/Contents/Resources/package/carrier.app"
host_app="$app/Contents/Resources/package/payload/$HOST_APP_NAME"
mkdir -p \
  "$app/Contents/MacOS" \
  "$app/Contents/Resources/package" \
  "$carrier/Contents/MacOS" \
  "$carrier/Contents/Resources" \
  "$host_app/Contents/MacOS"

put() {
  cat "$1" >"$2"
}

put_plist() {
  sed "s/@VERSION@/$version/g" "$1" >"$2"
}

put_plist "$macos_dir/Info.plist" "$app/Contents/Info.plist"
put "$inputs/VelesBridgeInstaller" "$app/Contents/MacOS/VelesBridgeInstaller"
put "$inputs/descriptor.json" "$app/Contents/Resources/package/descriptor.json"
put "$inputs/inventory.json" "$app/Contents/Resources/package/inventory.json"
if [ -f "$inputs/sealed-inventory.json" ]; then
  put "$inputs/sealed-inventory.json" "$app/Contents/Resources/package/sealed-inventory.json"
fi
put_plist "$macos_dir/CarrierInfo.plist" "$carrier/Contents/Info.plist"
put "$inputs/veles-bridge-maintenance" "$carrier/Contents/MacOS/veles-bridge-maintenance"
put "$inputs/veles-policy-carrier" "$carrier/Contents/Resources/veles-policy-carrier"
put_plist "$macos_dir/HostInfo.plist" "$host_app/Contents/Info.plist"
put "$inputs/veles-native-host" "$host_app/Contents/MacOS/veles-native-host"

# Normalise modes: nothing inherits the umask or the input modes.
find "$output" -type d -exec chmod 755 {} +
find "$output" -type f -exec chmod 644 {} +
chmod 755 \
  "$app/Contents/MacOS/VelesBridgeInstaller" \
  "$carrier/Contents/MacOS/veles-bridge-maintenance" \
  "$host_app/Contents/MacOS/veles-native-host"

# Checksums and manifest over the final bytes and modes, byte-order sorted, relative paths only.
sums="$output/SHA256SUMS"
manifest="$output/manifest.tsv"
: >"$sums"
: >"$manifest"
while IFS= read -r -d '' relative; do
  digest="$(sha256_of "$output/$relative")"
  if [ -x "$output/$relative" ]; then mode=755; else mode=644; fi
  printf '%s  %s\n' "$digest" "$relative" >>"$sums"
  printf '%s\t%s\t%s\t%s\n' "$relative" "$mode" "$(size_of "$output/$relative")" "$digest" >>"$manifest"
done < <(cd "$output" && find dmg-root -type f -print0 | sort -z)
chmod 644 "$sums" "$manifest"

# Timestamps last: nothing may be created or written after this.
find "$output" -exec touch -h -t "$STAMP" {} +

finished=1
echo "package-macos: unsigned input tree for $version written (not signed, not notarized)" >&2
