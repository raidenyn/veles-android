#!/usr/bin/env bash
# VERIFICATION ONLY (OTP-24 Task 9). Records the property run and one bounded smoke run per fuzz
# target, with tool/source identity, seeds, budgets, exit status and findings, under
# build/native-bridge/host/verification/. Bounded runs are not proof of absence.
#
#   native-bridge/host/fuzz/run-smoke.sh [SECONDS=60]
set -u -o pipefail
SECONDS_PER_TARGET="${1:-60}"
HERE="$(cd "$(dirname "$0")" && pwd)"
HOST="$(cd "$HERE/.." && pwd)"
ROOT="$(cd "$HOST/../.." && pwd)"
OUT="${VELES_VERIFICATION_OUT:-$ROOT/build/native-bridge/host/verification}"
mkdir -p "$OUT"
export CARGO_TARGET_DIR="$ROOT/build/native-bridge/host/target"
FUZZ_TARGET_DIR="$ROOT/build/native-bridge/host/fuzz-target"

declare -A SEEDS=([strict_envelope]=24001 [fragment_stream]=24002 [core_sequence]=24003)
TARGETS=(strict_envelope fragment_stream core_sequence)

now() { date +%s.%N; }
elapsed() { python3 -c "print(f'{float('$2') - float('$1'):.1f}')"; }
{
  echo "started_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "rustc: $(cd "$HOST" && rustc -Vv | tr '\n' ';')"
  echo "cargo: $(cd "$HOST" && cargo -V)"
  echo "kernel: $(uname -srm)"
  echo "git_head=$(git -C "$ROOT" rev-parse HEAD) dirty_files=$(git -C "$ROOT" status --porcelain | wc -l)"
  echo "cargo_fuzz: $(cargo fuzz --version 2>&1 | head -1)"
  echo "nightly_toolchain: $(rustup toolchain list | grep -c nightly) installed"
  echo "budget_seconds_per_target=$SECONDS_PER_TARGET"
} > "$OUT/identity.txt"

# Source identity: sha256 of every file that defines the properties, targets and drivers.
( cd "$HOST" && find src tests fuzz Cargo.toml Cargo.lock rust-toolchain.toml vendor/bluest/VELES-MANIFEST.sha256 \
    -type f -not -path 'fuzz/target/*' -not -name '*.pyc' | LC_ALL=C sort | xargs sha256sum ) > "$OUT/source-manifest.sha256"
sha256sum "$OUT/source-manifest.sha256" | tee -a "$OUT/identity.txt"

# 1. deterministic properties
t0=$(now)
# VELES_PROPERTIES_CMD overrides the command ONLY for the harness negative test (a failing property
# run must fail the aggregate while all other evidence is still collected).
PROPERTIES_CMD="${VELES_PROPERTIES_CMD:-cargo test --manifest-path native-bridge/host/Cargo.toml --locked --test properties}"
( cd "$ROOT" && eval "$PROPERTIES_CMD" ) > "$OUT/properties.log" 2>&1
rc=$?; t1=$(now)
status=0
prop_rc=$rc
[ "$rc" -eq 0 ] || status=1
printf 'properties exit=%s duration_s=%s cmd=%s\n' "$rc" "$(elapsed "$t0" "$t1")" \
  "$PROPERTIES_CMD" | tee "$OUT/properties.result"
grep -E '^test result|^test .*FAILED' "$OUT/properties.log" | tee -a "$OUT/properties.result"

# 2. build the smoke driver once
( cd "$HERE" && CARGO_TARGET_DIR="$FUZZ_TARGET_DIR" cargo build --locked --release --bin smoke ) > "$OUT/smoke-build.log" 2>&1 || { echo "smoke build failed" >&2; echo "finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) properties_exit=$prop_rc overall_status=2 (smoke build failed)" >> "$OUT/identity.txt"; exit 2; }
SMOKE="$FUZZ_TARGET_DIR/release/smoke"
sha256sum "$SMOKE" > "$OUT/smoke-binary.sha256"

# 3. bounded smoke per target
: > "$OUT/smoke-results.jsonl"
for t in "${TARGETS[@]}"; do
  seed="${SEEDS[$t]}"
  t0=$(now)
  line=$("$SMOKE" --target "$t" --seed "$seed" --seconds "$SECONDS_PER_TARGET" 2> "$OUT/smoke-$t.stderr")
  rc=$?; t1=$(now)
  printf '{"cmd":"smoke --target %s --seed %s --seconds %s","exit":%s,"wall_s":%s,"result":%s}\n' \
    "$t" "$seed" "$SECONDS_PER_TARGET" "$rc" "$(elapsed "$t0" "$t1")" "${line:-null}" | tee -a "$OUT/smoke-results.jsonl"
  [ "$rc" -eq 0 ] || status=1
done
echo "finished_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) properties_exit=$prop_rc overall_status=$status" >> "$OUT/identity.txt"
exit $status
