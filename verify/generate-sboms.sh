#!/usr/bin/env bash
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
output="$root/build/sbom"
bin="$root/verify/node_modules/.bin/cyclonedx-npm"
mkdir -p "$output"
test -x "$bin"

generate_npm() {
  local project="$1" destination="$2" temporary
  temporary="$(mktemp "$output/.${destination}.XXXXXX")"
  "$bin" --package-lock-only --output-reproducible --validate --output-file "$temporary" "$root/$project/package.json"
  mv -f "$temporary" "$output/$destination"
}

generate_cargo() {
  local manifest="$1" package_directory="$2" destination="$3" prefix
  prefix=".${destination}.$$"
  "$root/gradlew" verifyCargoCyclonedx >/dev/null
  "$root/build/verify-tools/cargo-cyclonedx/bin/cargo-cyclonedx" cyclonedx --manifest-path "$root/$manifest" --format json --target all --all-features --override-filename "$prefix"
  mv -f "$root/$package_directory/$prefix.json" "$output/$destination"
}

generate_npm web-extension web-extension.cdx.json
generate_cargo rust/Cargo.toml rust/veles-crypto rust.cdx.json
generate_cargo native-bridge/src-tauri/Cargo.toml native-bridge/src-tauri native-bridge.cdx.json
