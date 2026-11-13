#!/bin/sh
# Acceptance checks for the PR 2 Gradle layout (design D4): `android/` is an independent
# Gradle build that owns the Android editor configuration, the root build only orchestrates
# the included Android and Rust builds, and every documented entry point still resolves.
#
# Run from anywhere: sh verify/check-android-layout.sh
#
# Every check is reported as `ok N`/`not ok N`; all of them run so one failing check cannot
# hide another. Exit 0 when every check passes, 1 when any check fails. Check 2 builds the
# Android app from a throw-away copy of the working tree whose root build files are removed,
# which is what proves the independent build does not read the root build.
set -u

root="$(cd "$(dirname "$0")/.." && pwd)" || exit 1
cd "$root" || exit 1
[ -x ./gradlew ] || { echo "ERROR: ./gradlew is not executable" >&2; exit 1; }

workdir="$(mktemp -d "${TMPDIR:-/tmp}/veles-android-layout.XXXXXX")" || {
    echo "ERROR: cannot create a temporary directory" >&2
    exit 1
}
trap 'rm -rf "$workdir"' EXIT
trap 'exit 1' HUP INT TERM

failures=0
problems="$workdir/problems"

begin_check() {
    : > "$problems"
}

add_problem() {
    printf '%s\n' "$1" >> "$problems"
}

report() {
    # report <number> <description>
    if [ -s "$problems" ]; then
        printf 'not ok %s - %s\n' "$1" "$2"
        sed 's/^/  /' "$problems"
        failures=$((failures + 1))
    else
        printf 'ok %s - %s\n' "$1" "$2"
    fi
}

tail_log() {
    # tail_log <file> <lines>
    printf '  last %s lines of %s:\n' "$2" "$1"
    tail -n "$2" "$1" | sed 's/^/  /'
}

# 1. Editor configuration ownership ------------------------------------------------------
# android/.editorconfig is the ktlint/IDE configuration of the Android build and the root
# file no longer carries a Kotlin section.
begin_check
if [ ! -f android/.editorconfig ]; then
    add_problem "android/.editorconfig does not exist"
else
    grep -qE '^root[[:space:]]*=[[:space:]]*true[[:space:]]*$' android/.editorconfig ||
        add_problem "android/.editorconfig does not declare 'root = true'"
    if ! grep -qF '[*.{kt,kts}]' android/.editorconfig; then
        add_problem "android/.editorconfig has no [*.{kt,kts}] section"
    else
        # Only the rules inside that section count: a setting elsewhere would not reach ktlint.
        ktlint_section="$(awk '
            /^[[:space:]]*\[/ { section = ($0 ~ /\[\*\.\{kt,kts\}\]/); next }
            section { print }
        ' android/.editorconfig)"
        for rule in \
            ktlint_standard_function-naming \
            ktlint_standard_backing-property-naming \
            ktlint_standard_property-naming \
            ktlint_standard_max-line-length; do
            printf '%s\n' "$ktlint_section" |
                grep -qE "^[[:space:]]*${rule}[[:space:]]*=[[:space:]]*disabled[[:space:]]*$" ||
                add_problem "android/.editorconfig does not disable $rule in [*.{kt,kts}]"
        done
    fi
    grep -qE '^\[.*\]' android/.editorconfig || add_problem "android/.editorconfig has no sections"
fi
if grep -qF '[*.{kt,kts}]' .editorconfig; then
    add_problem "the root .editorconfig still has a [*.{kt,kts}] section"
fi
if grep -q 'ktlint_' .editorconfig; then
    add_problem "the root .editorconfig still carries ktlint settings"
fi
report 1 "android/.editorconfig owns the Kotlin/ktlint configuration and the root .editorconfig has no Kotlin section"

# 2. The Android build is independent ----------------------------------------------------
# Build the app inside a copy of the working tree with the root build.gradle.kts,
# settings.gradle.kts and gradle.properties renamed, so any read of the root build fails.
begin_check
scratch="$workdir/tree"
log="$workdir/android-standalone.log"
mkdir -p "$scratch"
# Caches and build outputs are not part of the working tree; everything else is copied, so
# the scratch build sees exactly these sources and the shared Gradle user home.
if ! (cd "$root" && tar \
        --exclude=./.git \
        --exclude=./.gradle \
        --exclude=./.kotlin \
        --exclude=./build \
        --exclude='*/build' \
        --exclude='*/.gradle' \
        --exclude='*/.kotlin' \
        --exclude='*/node_modules' \
        --exclude='*/target' \
        --exclude='*/dist' \
        -cf - .) | tar -xf - -C "$scratch"; then
    add_problem "copying the working tree to $scratch failed"
else
    renamed=0
    for name in build.gradle.kts settings.gradle.kts gradle.properties; do
        if mv "$scratch/$name" "$scratch/$name.acceptance-removed"; then
            renamed=$((renamed + 1))
        else
            add_problem "cannot rename the root $name in the scratch copy"
        fi
    done
    if [ "$renamed" -eq 3 ]; then
        if ! (cd "$scratch/android" && ./gradlew assembleDebug --console=plain) >"$log" 2>&1; then
            add_problem "cd android && ./gradlew assembleDebug failed with the root build files removed"
            tail_log "$log" 40 >>"$problems"
        fi
    fi
    # Restore what was renamed, then drop the scratch copy: the check never changes the tree.
    for name in build.gradle.kts settings.gradle.kts gradle.properties; do
        if [ -f "$scratch/$name.acceptance-removed" ]; then
            mv "$scratch/$name.acceptance-removed" "$scratch/$name" || true
        fi
    done
    rm -rf "$scratch"
fi
report 2 "android builds with the root build.gradle.kts, settings.gradle.kts and gradle.properties removed"

# 3. The Rust build configures alone -----------------------------------------------------
begin_check
log="$workdir/rust-help.log"
if ! (cd "$root/rust" && ../gradlew help --console=plain) >"$log" 2>&1; then
    add_problem "cd rust && ../gradlew help failed"
    tail_log "$log" 40 >>"$problems"
fi
report 3 "cd rust && ../gradlew help configures the Rust build alone"

# 4. Each shared Rust task executes once -------------------------------------------------
# The root aliases and the Android build both reach into the included Rust build; one root
# invocation must list every included Rust task exactly once in its task graph.
begin_check
log="$workdir/root-rust-graph.log"
if ! ./gradlew assembleDebug testDebugUnitTest rustHostJni rustJni --console=plain --dry-run >"$log" 2>&1; then
    add_problem "the root dry run of assembleDebug testDebugUnitTest rustHostJni rustJni failed"
    tail_log "$log" 40 >>"$problems"
else
    rust_tasks="$(sed -n 's/^\(:rust:[^ ]*\) .*/\1/p' "$log" | sort)"
    if [ -z "$rust_tasks" ]; then
        add_problem "the root dry run lists no :rust: task"
    else
        duplicates="$(printf '%s\n' "$rust_tasks" | uniq -d)"
        if [ -n "$duplicates" ]; then
            add_problem "the root dry run lists shared Rust tasks more than once:"
            printf '%s\n' "$duplicates" | sed 's/^/  /' >>"$problems"
        fi
        for task in :rust:rustHostJni :rust:rustJni; do
            printf '%s\n' "$rust_tasks" | grep -qx "$task" ||
                add_problem "the root dry run does not execute $task"
        done
    fi
fi
report 4 "one root invocation lists each shared Rust task execution exactly once"

# 5. The Android wrapper is the root wrapper ---------------------------------------------
begin_check
log="$workdir/android-version.log"
if ! (cd "$root/android" && ./gradlew --version --console=plain) >"$log" 2>&1; then
    add_problem "cd android && ./gradlew --version failed"
    tail_log "$log" 40 >>"$problems"
else
    reported="$(sed -n 's/^Gradle \([0-9][0-9.]*\)$/\1/p' "$log" | head -n 1)"
    [ "$reported" = "9.5.0" ] || add_problem "the Android wrapper reports Gradle '${reported}' instead of 9.5.0"
fi
grep -qF '../gradlew' android/gradlew ||
    add_problem "android/gradlew does not exec the root wrapper"
report 5 "cd android && ./gradlew --version reports the root wrapper's Gradle 9.5.0"

# 6. The documented root aliases resolve -------------------------------------------------
begin_check
log="$workdir/root-aliases.log"
if ! ./gradlew --dry-run --console=plain \
        assembleDebug assembleRelease testDebugUnitTest lintDebug spotlessCheck detekt \
        connectedDebugAndroidTest protocolCheck >"$log" 2>&1; then
    add_problem "a documented root alias does not resolve with --dry-run"
    tail_log "$log" 40 >>"$problems"
fi
report 6 "the root aliases resolve with --dry-run"

if [ "$failures" -eq 0 ]; then
    echo "PASS: the Android layout acceptance checks all passed"
    exit 0
fi
printf 'FAIL: %s acceptance check(s) failed\n' "$failures"
exit 1
