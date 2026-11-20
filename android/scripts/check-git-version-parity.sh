#!/usr/bin/env bash
# Parity harness for the Android version derivation (gradle-cache-safe, design D3).
#
# Plugin 0.4.14 and `android/git-version.gradle.kts` must report the same
# `androidGitVersion.name` / `androidGitVersion.code` lines in every repository state the change
# cares about. Both derivations run in throwaway builds against throwaway git repositories, through
# the repository wrapper, and the full `-q` output of the two `androidGitVersion` tasks is diffed.
#
# The plugin build must live inside the repository it reads (the plugin discovers the repository
# from its own project directory), so it is created as `<repo>/.gv-plugin/` and that path is added
# to the repository's `.git/info/exclude`, which keeps the tree clean. The script build lives
# outside the repository and receives it as `-PgitRepoDir=<path>`.
#
# Needs network access to the Gradle plugin portal on a cold Gradle home, so it is not a CI gate.
# Run it with `bash android/scripts/check-git-version-parity.sh`; it exits 1 on any mismatch.
set -euo pipefail

REPO_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)
GRADLEW="$REPO_ROOT/gradlew"
VERSION_SCRIPT="$REPO_ROOT/android/git-version.gradle.kts"

WORK=$(mktemp -d "${TMPDIR:-/tmp}/git-version-parity.XXXXXX")
cleanup() {
    chmod -R u+w "$WORK" 2>/dev/null || true
    rm -rf "$WORK"
}
trap cleanup EXIT

REPO="$WORK/repo"
PLUGIN_BUILD="$REPO/.gv-plugin"
SCRIPT_BUILD="$WORK/builds/script"
mkdir -p "$PLUGIN_BUILD" "$SCRIPT_BUILD"

# Deterministic history: fixed author/committer dates, incremented per commit, so no clock is read
# and the walk order never depends on when the harness ran.
commit_clock=0
GIT_AUTHOR_NAME=parity
GIT_AUTHOR_EMAIL=parity@example.invalid
GIT_COMMITTER_NAME=parity
GIT_COMMITTER_EMAIL=parity@example.invalid
export GIT_AUTHOR_NAME GIT_AUTHOR_EMAIL GIT_COMMITTER_NAME GIT_COMMITTER_EMAIL

commit() {
    local subject=$1
    local stamp
    stamp=$(printf '2026-01-01T%02d:%02d:00+0000' $((commit_clock / 60)) $((commit_clock % 60)))
    commit_clock=$((commit_clock + 1))
    printf '%s\n' "$subject" >"$REPO/file-$commit_clock.txt"
    git -C "$REPO" add -A
    GIT_AUTHOR_DATE=$stamp GIT_COMMITTER_DATE=$stamp \
        git -C "$REPO" commit -q -m "$subject"
}

git -C "$REPO" init -q -b master
printf '.gv-plugin/\n' >>"$REPO/.git/info/exclude"

cat >"$PLUGIN_BUILD/settings.gradle.kts" <<'EOF'
rootProject.name = "git-version-parity-plugin"
EOF
cat >"$PLUGIN_BUILD/build.gradle.kts" <<'EOF'
plugins {
    id("com.gladed.androidgitversion") version "0.4.14"
}

androidGitVersion {
    prefix = "android-v"
    codeFormat = "MMNNPP"
}
EOF

cat >"$SCRIPT_BUILD/settings.gradle.kts" <<'EOF'
rootProject.name = "git-version-parity-script"
EOF
cat >"$SCRIPT_BUILD/build.gradle.kts" <<EOF
apply(from = "$VERSION_SCRIPT")
EOF

failed=0
check() {
    local state=$1
    local plugin_out script_out
    if ! plugin_out=$("$GRADLEW" --project-dir "$PLUGIN_BUILD" --console=plain -q androidGitVersion); then
        printf 'FAIL %s (plugin build failed)\n' "$state"
        failed=1
        return
    fi
    if ! script_out=$("$GRADLEW" --project-dir "$SCRIPT_BUILD" --console=plain -q \
        -PgitRepoDir="$REPO" androidGitVersion); then
        printf 'FAIL %s (script build failed)\n' "$state"
        failed=1
        return
    fi
    if diff -u <(printf '%s\n' "$plugin_out") <(printf '%s\n' "$script_out"); then
        printf 'PASS %s\n' "$state"
    else
        printf 'FAIL %s\n' "$state"
        failed=1
    fi
}

# on-tag: a version tag exactly on HEAD.
commit initial
git -C "$REPO" tag android-v1.2.3
tagged_commit=$(git -C "$REPO" rev-parse HEAD)
check on-tag

# after-tag: three commits on top of the tagged commit.
commit second
commit third
commit fourth
check after-tag

# dirty: an unstaged change to a tracked file.
printf 'dirty\n' >>"$REPO/file-1.txt"
check dirty
git -C "$REPO" checkout -q -- .

# branch: a branch name that JGit sanitizes.
git -C "$REPO" checkout -q -b feature/x
check branch
git -C "$REPO" checkout -q master

# detached: JGit reports the full commit id as the branch name.
git -C "$REPO" checkout -q --detach HEAD
check detached
git -C "$REPO" checkout -q master

# two-tags: two versions on the tagged commit; the higher one must win.
git -C "$REPO" tag android-v1.2.4 "$tagged_commit"
check two-tags
git -C "$REPO" tag -d android-v1.2.4 >/dev/null

# prerelease: accepted by the plugin's default `^<prefix>[0-9]+.*` pattern. The plain tag is
# removed first, because on one commit 0.4.14 prefers the shorter of two numerically equal names.
git -C "$REPO" tag -d android-v1.2.3 >/dev/null
git -C "$REPO" tag android-v1.2.3-rc1 "$tagged_commit"
check prerelease
git -C "$REPO" tag -d android-v1.2.3-rc1 >/dev/null

# no-tag: no `android-v*` tag anywhere.
check no-tag

exit "$failed"
