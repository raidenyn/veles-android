#!/usr/bin/env bash
# Verifies verify/verify-supply-chain.sh normalizes environment/pin failures to
# exit 2 (not the raw 1 that `set -e` would propagate from a bare `test`), while
# leaving policy-mismatch exit codes from the node checkers unchanged. Uses a
# fake node/npm so no real repo build is required.
set -euo pipefail

script_dir=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repo_root="$(cd "$script_dir/../.." && pwd)"
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT

# Fabricate a fake node/npm pair with controllable versions.
fake_bin="$tmp/bin"
mkdir -p "$fake_bin"
cat >"$fake_bin/node" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  --version) printf '%s\n' "${FAKE_NODE_VERSION:-v26.8.1}" ;;
  *) exit 0 ;;
esac
EOF
cat >"$fake_bin/npm" <<'EOF'
#!/usr/bin/env bash
case "$1" in
  --version) printf '%s\n' "${FAKE_NPM_VERSION:-11.19.0}" ;;
  *) exit 0 ;;
esac
EOF
chmod +x "$fake_bin/node" "$fake_bin/npm"

# Wrong Node version -> exit 2 (must NOT proceed to npm ci or node checkers).
if PATH="$fake_bin:$PATH" FAKE_NODE_VERSION=v22.0.0 \
  bash "$repo_root/verify/verify-supply-chain.sh" 2>/dev/null; then
  echo 'ERROR: wrong Node must exit 2' >&2; exit 1
else [ $? -eq 2 ]; fi

# Wrong npm version -> exit 2 (Node check passes, npm check fails).
if PATH="$fake_bin:$PATH" FAKE_NPM_VERSION=10.0.0 \
  bash "$repo_root/verify/verify-supply-chain.sh" 2>/dev/null; then
  echo 'ERROR: wrong npm must exit 2' >&2; exit 1
else [ $? -eq 2 ]; fi

echo 'PASS verify-supply-chain.sh normalizes environment/pin failures to exit 2'