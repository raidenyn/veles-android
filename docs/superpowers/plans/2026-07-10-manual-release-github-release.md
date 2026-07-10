# Manual-Trigger Release Build → GitHub Release Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make `workflow_dispatch` runs of `release-build.yml` publish a GitHub prerelease (APK + mapping.txt, auto-generated notes), matching the visible-release behavior of tag-triggered `release.yml`, without touching the other two triggers (`push: master`, labeled PRs) or `release.yml` itself.

**Architecture:** Extend the existing `release-build.yml` job with one new conditional step. Reuse the version string the job already computes (`androidGitVersion.name`) as the release's git tag. Guard against clobbering a real tag-triggered release by checking `isPrerelease` on any pre-existing release for that tag before deciding whether to overwrite or abort.

**Tech Stack:** GitHub Actions (YAML), Bash (`run:` steps), GitHub CLI (`gh`), `jq` (both preinstalled on `ubuntu-latest` runners).

## Global Constraints

- Only `github.event_name == 'workflow_dispatch'` creates a GitHub Release. `push` and `pull_request` triggers keep artifact-only behavior, unchanged.
- The release tag/version is exactly `androidGitVersion.name` (already computed in the existing "Rename APK" step for non-PR events) — no new version scheme.
- Every release created by this path is marked `--prerelease` and titled `Veles <version> (manual build)` (+ ` (unsigned)` suffix when `HAS_KEYSTORE != 'true'`).
- If a release already exists for that tag and `isPrerelease == true`, delete it and its tag (`gh release delete --cleanup-tag`) then recreate — re-running overwrites.
- If a release already exists for that tag and `isPrerelease == false` (a real tag-triggered release), abort the job with `::error::` and exit 1 — never touch it.
- `release.yml` is not modified. `permissions: contents: write` must be added to `release-build.yml` (it currently has no `permissions:` block, so it defaults to read-only, which is insufficient for `gh release create/delete`).

---

### Task 1: Add GitHub Release creation to `release-build.yml`

**Files:**
- Modify: `.github/workflows/release-build.yml`
- Modify: `CLAUDE.md:103-104`

**Interfaces:**
- Consumes: `steps.apk.outputs.name` (existing, the renamed APK filename), `env.HAS_KEYSTORE` (existing job env var, `"true"`/`"false"` string).
- Produces: new step output `steps.apk.outputs.version` (the derived `androidGitVersion.name`, only set for non-PR events) — no other task depends on this, but note it for future maintainers.

- [ ] **Step 1: Add `permissions: contents: write` to the workflow**

Edit `.github/workflows/release-build.yml`. Current top of file:

```yaml
name: release-build

on:
  workflow_dispatch:
  pull_request:
    types: [labeled, synchronize]
  push:
    branches: [master]

concurrency:
  group: release-build-${{ github.ref }}
  cancel-in-progress: true

jobs:
```

Change to:

```yaml
name: release-build

on:
  workflow_dispatch:
  pull_request:
    types: [labeled, synchronize]
  push:
    branches: [master]

concurrency:
  group: release-build-${{ github.ref }}
  cancel-in-progress: true

permissions:
  contents: write

jobs:
```

- [ ] **Step 2: Expose `$VERSION` as a step output in the existing "Rename APK" step**

Current step (id: `apk`):

```yaml
      - name: Rename APK
        id: apk
        run: |
          APK=$(ls app/build/outputs/apk/release/app-release*.apk 2>/dev/null | head -1)
          if [ "${{ github.event_name }}" = "pull_request" ]; then
            SHORT_SHA=$(echo "${{ github.event.pull_request.head.sha }}" | cut -c1-7)
            NAME="Veles-PR${{ github.event.pull_request.number }}-${SHORT_SHA}.apk"
          else
            VERSION=$(./gradlew -q :app:androidGitVersion | awk -F'\t' '/androidGitVersion.name/ {print $2}')
            NAME="Veles-${VERSION}-release.apk"
          fi
          mv "$APK" "app/build/outputs/apk/release/$NAME"
          echo "name=$NAME" >> "$GITHUB_OUTPUT"
          echo "Renamed to $NAME"
```

Change the `else` branch to also emit `version`:

```yaml
      - name: Rename APK
        id: apk
        run: |
          APK=$(ls app/build/outputs/apk/release/app-release*.apk 2>/dev/null | head -1)
          if [ "${{ github.event_name }}" = "pull_request" ]; then
            SHORT_SHA=$(echo "${{ github.event.pull_request.head.sha }}" | cut -c1-7)
            NAME="Veles-PR${{ github.event.pull_request.number }}-${SHORT_SHA}.apk"
          else
            VERSION=$(./gradlew -q :app:androidGitVersion | awk -F'\t' '/androidGitVersion.name/ {print $2}')
            NAME="Veles-${VERSION}-release.apk"
            echo "version=$VERSION" >> "$GITHUB_OUTPUT"
          fi
          mv "$APK" "app/build/outputs/apk/release/$NAME"
          echo "name=$NAME" >> "$GITHUB_OUTPUT"
          echo "Renamed to $NAME"
```

- [ ] **Step 3: Add the new "Create GitHub Release" step**

After the existing `actions/upload-artifact@v7` step (the last step in the file):

```yaml
      - uses: actions/upload-artifact@v7
        with:
          name: ${{ steps.apk.outputs.name }}
          path: |
            app/build/outputs/apk/release/Veles-*.apk
            app/build/outputs/mapping/release/mapping.txt
          if-no-files-found: error
```

Add this new step right after it:

```yaml
      - name: Create GitHub Release
        if: github.event_name == 'workflow_dispatch'
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          VERSION="${{ steps.apk.outputs.version }}"
          if [ "$HAS_KEYSTORE" = "true" ]; then
            SUFFIX=""
          else
            SUFFIX=" (unsigned)"
          fi

          if EXISTING_JSON=$(gh release view "$VERSION" --json isPrerelease 2>/dev/null); then
            IS_PRERELEASE=$(echo "$EXISTING_JSON" | jq -r '.isPrerelease')
            if [ "$IS_PRERELEASE" = "true" ]; then
              echo "Replacing previous manual release for $VERSION"
              gh release delete "$VERSION" --cleanup-tag --yes
            else
              echo "::error::A real (non-prerelease) release already exists for version $VERSION. Refusing to overwrite it with a manual build. Push new commits before re-dispatching."
              exit 1
            fi
          fi

          gh release create "$VERSION" \
            "app/build/outputs/apk/release/${{ steps.apk.outputs.name }}" \
            "app/build/outputs/mapping/release/mapping.txt" \
            --title "Veles $VERSION (manual build)$SUFFIX" \
            --prerelease \
            --generate-notes \
            --target "$GITHUB_SHA"
```

Note: `steps.apk.outputs.name` is reused here instead of reconstructing `Veles-${VERSION}-release.apk`, so the attached file always matches whatever the Rename APK step actually produced.

- [ ] **Step 4: Sanity-check the embedded shell script syntax locally**

The new step's `run:` block is plain bash. Extract it to a scratch file and check it parses, since there's no CI dry-run available locally:

```bash
cat > /tmp/release-step-check.sh <<'EOF'
#!/usr/bin/env bash
set -e
VERSION="0.0.1-3-gabc123"
HAS_KEYSTORE="false"
if [ "$HAS_KEYSTORE" = "true" ]; then
  SUFFIX=""
else
  SUFFIX=" (unsigned)"
fi

if EXISTING_JSON=$(gh release view "$VERSION" --json isPrerelease 2>/dev/null); then
  IS_PRERELEASE=$(echo "$EXISTING_JSON" | jq -r '.isPrerelease')
  if [ "$IS_PRERELEASE" = "true" ]; then
    echo "Replacing previous manual release for $VERSION"
    gh release delete "$VERSION" --cleanup-tag --yes
  else
    echo "::error::A real (non-prerelease) release already exists for version $VERSION. Refusing to overwrite it with a manual build. Push new commits before re-dispatching."
    exit 1
  fi
fi

gh release create "$VERSION" \
  "app/build/outputs/apk/release/Veles-${VERSION}-release.apk" \
  "app/build/outputs/mapping/release/mapping.txt" \
  --title "Veles $VERSION (manual build)$SUFFIX" \
  --prerelease \
  --generate-notes \
  --target "$GITHUB_SHA"
EOF
bash -n /tmp/release-step-check.sh
```

Run: `bash -n /tmp/release-step-check.sh`
Expected: no output, exit code 0 (syntax is valid). This only checks parsing, not runtime behavior (`gh`/`jq` aren't invoked) — real behavior is verified in Task 2.

Delete the scratch file afterward: `rm /tmp/release-step-check.sh`

- [ ] **Step 5: Update `CLAUDE.md`'s release documentation**

Current text at `CLAUDE.md:103-104`:

```markdown
`release-build.yml` runs `assembleRelease` automatically on master pushes and as an opt-in
on PRs (add the `release-build` label); it can also be triggered manually from master.
```

Change to:

```markdown
`release-build.yml` runs `assembleRelease` automatically on master pushes and as an opt-in
on PRs (add the `release-build` label); it can also be triggered manually via
`workflow_dispatch`, which additionally publishes a prerelease GitHub Release (tagged with
the derived version, titled "(manual build)") — re-dispatching at the same commit replaces
the previous manual release, but the job aborts rather than overwriting a real tag-triggered
release.
```

- [ ] **Step 6: Commit**

```bash
git add .github/workflows/release-build.yml CLAUDE.md
git commit -m "feat: publish a prerelease GitHub Release on manual release-build dispatch"
```

---

### Task 2: Live verification on GitHub Actions

**Files:** none (no code changes — this task exercises the workflow from Task 1 against the real `raidenyn/veles-android` GitHub repo)

**Interfaces:**
- Consumes: the `release-build.yml` from Task 1, already pushed to a branch or master.

> **STOP — this task performs real, hard-to-reverse actions on the shared GitHub repo**: it triggers a live GitHub Actions run, creates a real git tag and GitHub Release, and (in one scenario) deletes a release. **Do not run any of the commands below without the user explicitly confirming first**, and confirm again before the delete/overwrite scenario specifically. If working from an agent/subagent, surface this stop and wait for the human owner (not another agent) to approve.

- [ ] **Step 1: Push Task 1's commit and confirm with the user before proceeding**

Push the branch containing Task 1's commit (or merge to master, per the user's preference — ask). Do not dispatch the workflow until the user has confirmed it's fine to create a real prerelease/tag in `raidenyn/veles-android`.

- [ ] **Step 2: Dispatch the workflow and confirm a prerelease is created**

Ask the user to run (or run it yourself only after explicit confirmation):

```bash
gh workflow run release-build.yml --repo raidenyn/veles-android --ref master
```

Then poll for completion and inspect the result:

```bash
gh run list --repo raidenyn/veles-android --workflow=release-build.yml --limit 1
gh release view "$(gh release list --repo raidenyn/veles-android --limit 1 --json tagName -q '.[0].tagName')" --repo raidenyn/veles-android
```

Expected: the run succeeds; `gh release view` shows `isPrerelease: true`, a title of the form `Veles <version> (manual build)` (with ` (unsigned)` appended if no keystore secrets are configured), and both the APK and `mapping.txt` attached.

- [ ] **Step 3: Re-dispatch at the same commit and confirm overwrite behavior**

With no new commits on the dispatched ref, run the workflow again (same command as Step 2). Expected: the run succeeds, the previous manual release/tag for that version is deleted and recreated (check the release's `createdAt`/`publishedAt` timestamp advances, and there's still exactly one release for that tag — not two).

- [ ] **Step 4: Confirm the collision guard protects a real tag-triggered release**

This requires a real semver tag to exist at the dispatched commit. If master's current commit doesn't yet have one, coordinate with the user on whether to push a test tag (e.g. the next real version) via the normal `release.yml` flow first — do not fabricate a throwaway tag on `master` without asking, since `release.yml` triggers on any `[0-9]+.[0-9]+.[0-9]+` push.

Once a real tag-triggered release exists at the dispatched commit, dispatch `release-build.yml` again at that same commit. Expected: the run **fails** at the "Create GitHub Release" step with the `::error::A real (non-prerelease) release already exists...` message, and the real release is untouched (`gh release view <tag> --json isPrerelease` still shows `false`).

- [ ] **Step 5: Confirm `release.yml` is unaffected**

Check the most recent tag-triggered run of `release.yml` (from Step 4, or the existing history): confirm its release is `isPrerelease: false` and its title/notes match the pre-existing convention (no "(manual build)" text). This confirms no regression to the tag-triggered path.
