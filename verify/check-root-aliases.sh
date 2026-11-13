#!/bin/sh
# Acceptance check for the documented repository-root Gradle aliases (design D4/D8).
#
# `android/` is an independent build, so its commands run from android/ in CI; the root build keeps a
# thin alias for every documented entry point so `./gradlew <alias>` and the docs still work. The
# aliases are not part of the Android node's dependency chain, so this check resolves them on every
# root-build edit instead.
#
# Usage:
#   sh verify/check-root-aliases.sh               # resolve every alias with a Gradle dry run
#   sh verify/check-root-aliases.sh --self-test   # prove an alias to a missing task exits non-zero
#
# Exit 0 when every documented alias resolves (or the self-test proves the check can fail), 1 when an
# alias does not resolve, 2 on a usage error.
set -u

root="$(cd "$(dirname "$0")/.." && pwd)" || exit 1
cd "$root" || exit 1

# The aliases pinned by the Android layout acceptance check and the docs. `clean` and `spotlessApply`
# are not here: this list is the CI scenario the alias contract names.
aliases="assembleDebug assembleRelease testDebugUnitTest lintDebug spotlessCheck detekt connectedDebugAndroidTest protocolCheck"

workdir="$(mktemp -d "${TMPDIR:-/tmp}/veles-root-aliases.XXXXXX")" || {
    echo "ERROR: cannot create a temporary directory" >&2
    exit 1
}
trap 'rm -rf "$workdir"' EXIT
trap 'exit 1' HUP INT TERM

[ -x ./gradlew ] || {
    echo "ERROR: ./gradlew is not executable" >&2
    exit 1
}

check_aliases() {
    log="$workdir/dry-run.log"
    # A dry run configures the full task graph and names every task it would execute, without running
    # anything: a missing or misnamed alias fails here instead of in the Android node. The alias names
    # are spelled out so the workflow-contract test can compare them with the documented set.
    if ! ./gradlew --dry-run --console=plain assembleDebug assembleRelease testDebugUnitTest lintDebug \
            spotlessCheck detekt connectedDebugAndroidTest protocolCheck >"$log" 2>&1; then
        echo "ERROR: ./gradlew --dry-run $aliases failed; a root alias does not resolve" >&2
        tail -n 40 "$log" >&2
        return 1
    fi
    failed=0
    for alias in $aliases; do
        # Plain-console dry-run output prints one `:task SKIPPED` line per resolved task, so a missing
        # line means the alias did not resolve even if Gradle exited zero.
        if ! grep -q "^:${alias} " "$log"; then
            echo "ERROR: the root alias :$alias did not resolve in the dry run" >&2
            failed=1
        fi
    done
    if [ "$failed" -ne 0 ]; then
        tail -n 40 "$log" >&2
        return 1
    fi
    echo "PASS: every root alias resolves with --dry-run ($aliases)"
    return 0
}

self_test() {
    # A scratch build whose only alias targets a task that does not exist. The dry run must exit
    # non-zero; a zero exit would mean this check cannot detect a broken alias.
    scratch="$workdir/scratch"
    mkdir -p "$scratch" || return 1
    cat >"$scratch/settings.gradle.kts" <<'EOF'
rootProject.name = "veles-root-alias-self-test"
EOF
    cat >"$scratch/build.gradle.kts" <<'EOF'
// The alias of this scratch build targets taskThatDoesNotExist. Gradle must refuse to configure it.
tasks.register("brokenAlias") {
    dependsOn("taskThatDoesNotExist")
}
EOF
    log="$workdir/self-test.log"
    if ./gradlew -p "$scratch" --dry-run --console=plain brokenAlias >"$log" 2>&1; then
        echo "ERROR: the self-test alias to a missing task resolved, so the alias check cannot fail" >&2
        tail -n 20 "$log" >&2
        return 1
    fi
    echo "PASS: an alias to a missing task exits non-zero"
    return 0
}

case "${1:-}" in
    "")
        check_aliases
        ;;
    --self-test)
        self_test
        ;;
    *)
        echo "usage: sh verify/check-root-aliases.sh [--self-test]" >&2
        exit 2
        ;;
esac
