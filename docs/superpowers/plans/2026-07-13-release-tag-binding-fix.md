# Release Tag Binding Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Keep tag-triggered GitHub Releases attached to their semantic-version tags and allow failed runs to replace stale drafts without permitting duplicate published releases.

**Architecture:** Keep all production behavior in `.github/workflows/release.yml`. The build guard distinguishes a stale draft from a published release, every draft PATCH explicitly preserves the triggering `tag_name`, and the publish job reads the release back to assert the final association.

**Tech Stack:** GitHub Actions YAML, Bash, GitHub CLI (`gh`), `jq`, `actionlint` or Python/PyYAML, and Ruby/YAML for local validation.

**Spec:** `docs/superpowers/specs/2026-07-13-release-tag-binding-fix-design.md`

## Global Constraints

- Modify only `.github/workflows/release.yml` in the implementation commit.
- Do not create, mutate, rebind, or delete live GitHub releases or remote tags while implementing this plan.
- Do not change `.github/workflows/release-build.yml`, attestation, artifact digest verification, or reproducibility verification.
- A published release for `$GITHUB_REF_NAME` must abort the workflow.
- A stale draft for `$GITHUB_REF_NAME` must be deleted by release ID without deleting the real git tag.
- A GitHub API failure must fail closed rather than being treated as “release not found.”
- Both release PATCH requests must send `tag_name="$GITHUB_REF_NAME"`.
- The publish PATCH must send `draft=false` as a JSON boolean.
- The publish job must fail if the release read-back `.tag_name` differs from `$GITHUB_REF_NAME`.

## Implementation Deviation

The implemented guard supersedes Task 1 Steps 1-3 and their original structural harness. Review found that `gh release view` combines published-release REST and draft-release GraphQL lookups, so matching its `release not found` stderr could treat a partial API failure as absence. The final workflow instead makes one GraphQL `repository.release(tagName:)` query, validates the complete response shape with `jq`, accepts only an explicit `release: null` as absence, and otherwise fails closed. Validation executes the guard extracted from the workflow with a mocked `gh` across absent, draft, published, API-error, GraphQL-error, and malformed-response cases.

---

### Task 1: Make Release Publication Tag-Safe And Retryable

**Files:**
- Modify: `.github/workflows/release.yml:29-36,111-132,161-192`

**Interfaces:**
- Consumes: `$GITHUB_REF_NAME`, `$GITHUB_REPOSITORY`, `${{ github.token }}`, and the existing `steps.create_draft.outputs.release_id` output.
- Produces: a guard that removes stale drafts but rejects published releases; PATCH requests pinned to `$GITHUB_REF_NAME`; a post-publish tag assertion.

- [ ] **Step 1: Create a temporary mocked `gh` harness that demonstrates the missing behaviors**

Create `/tmp/test-release-tag-binding.sh` without adding it to git:

```bash
#!/usr/bin/env bash
set -euo pipefail

WORKFLOW=.github/workflows/release.yml

assert_contains() {
  local expected=$1
  if ! grep -Fq -- "$expected" "$WORKFLOW"; then
    printf 'FAIL: workflow does not contain: %s\n' "$expected" >&2
    return 1
  fi
}

assert_contains '"databaseId,isDraft"'
assert_contains 'gh api -X DELETE "repos/$GITHUB_REPOSITORY/releases/$EXISTING_ID"'
assert_contains '-f tag_name="$GITHUB_REF_NAME"'
assert_contains '-f body="$NOTES"'
assert_contains '-F draft=false'
assert_contains 'PUBLISHED_TAG=$(gh api "repos/$GITHUB_REPOSITORY/releases/$RELEASE_ID" --jq .tag_name)'
assert_contains 'if [ "$PUBLISHED_TAG" != "$GITHUB_REF_NAME" ]; then'

printf 'PASS\n'
```

This is intentionally a structural regression harness: the workflow embeds shell directly, so it checks the required commands and arguments without introducing a second production script that could drift from the YAML.

- [ ] **Step 2: Run the harness to verify it fails against the current workflow**

Run:

```bash
bash /tmp/test-release-tag-binding.sh
```

Expected: non-zero exit with the first missing requirement, currently `FAIL: workflow does not contain: "databaseId,isDraft"`.

- [ ] **Step 3: Replace the existing-release guard with draft cleanup and fail-closed lookup**

Replace `.github/workflows/release.yml` lines 29-36 with:

```yaml
      - name: Guard against pre-existing release
        env:
          GH_TOKEN: ${{ github.token }}
        run: |
          ERROR_FILE="$RUNNER_TEMP/release-view-error"
          if EXISTING_JSON=$(gh release view "$GITHUB_REF_NAME" \
            --repo "$GITHUB_REPOSITORY" \
            --json databaseId,isDraft 2>"$ERROR_FILE"); then
            EXISTING_ID=$(printf '%s' "$EXISTING_JSON" | jq -r .databaseId)
            IS_DRAFT=$(printf '%s' "$EXISTING_JSON" | jq -r .isDraft)
            if [ "$IS_DRAFT" = "true" ]; then
              echo "Deleting stale draft release $EXISTING_ID for $GITHUB_REF_NAME"
              gh api -X DELETE "repos/$GITHUB_REPOSITORY/releases/$EXISTING_ID"
            else
              echo "::error::A published release for tag $GITHUB_REF_NAME already exists. Aborting to avoid a duplicate release."
              exit 1
            fi
          elif grep -Fq "release not found" "$ERROR_FILE"; then
            echo "No pre-existing release for $GITHUB_REF_NAME"
          else
            echo "::error::Could not check for a pre-existing release for $GITHUB_REF_NAME"
            cat "$ERROR_FILE" >&2
            exit 1
          fi
```

`gh release view` is draft-aware, unlike the published-release tag endpoint. The missing-release diagnostic is the only non-fatal lookup result; authentication, network, and API failures take the final branch and fail closed. Deleting through `DELETE /releases/{id}` removes only the stale release and does not perform `--cleanup-tag`.

- [ ] **Step 4: Pin the notes mutation to the triggering tag**

Replace the notes PATCH at the end of `Append verification instructions to release notes`:

```yaml
          gh api -X PATCH "repos/$GITHUB_REPOSITORY/releases/$RELEASE_ID" -f body="$NOTES"
```

with:

```yaml
          gh api -X PATCH "repos/$GITHUB_REPOSITORY/releases/$RELEASE_ID" \
            -f tag_name="$GITHUB_REF_NAME" \
            -f body="$NOTES"
```

- [ ] **Step 5: Pin publication to the triggering tag and assert the result**

Replace the final publish commands:

```yaml
          # Publish by release ID (unambiguous), not by tag.
          # -F (not -f) coerces draft to a JSON boolean.
          gh api -X PATCH "repos/$GITHUB_REPOSITORY/releases/$RELEASE_ID" -F draft=false
          echo "Published release $GITHUB_REF_NAME (ID: $RELEASE_ID)"
```

with:

```yaml
          # Publish by release ID (unambiguous), while explicitly replacing
          # GitHub's synthetic draft tag with the triggering version tag.
          # -F (not -f) coerces draft to a JSON boolean.
          gh api -X PATCH "repos/$GITHUB_REPOSITORY/releases/$RELEASE_ID" \
            -f tag_name="$GITHUB_REF_NAME" \
            -F draft=false
          PUBLISHED_TAG=$(gh api "repos/$GITHUB_REPOSITORY/releases/$RELEASE_ID" --jq .tag_name)
          if [ "$PUBLISHED_TAG" != "$GITHUB_REF_NAME" ]; then
            echo "::error::Release published on '$PUBLISHED_TAG' instead of '$GITHUB_REF_NAME'"
            exit 1
          fi
          echo "Published release $GITHUB_REF_NAME (ID: $RELEASE_ID)"
```

- [ ] **Step 6: Run the structural harness to verify the required behavior is present**

Run:

```bash
bash /tmp/test-release-tag-binding.sh
```

Expected: `PASS`.

- [ ] **Step 7: Exercise guard decisions and post-publish assertion with mocked command results**

Create `/tmp/test-release-logic.sh`:

```bash
#!/usr/bin/env bash
set -euo pipefail

guard_decision() {
  local status=$1
  local payload=$2
  local error=$3

  if [ "$status" = 0 ]; then
    local is_draft
    is_draft=$(printf '%s' "$payload" | jq -r .isDraft)
    if [ "$is_draft" = true ]; then
      printf 'delete-draft\n'
    else
      printf 'abort-published\n'
    fi
  elif grep -Fq 'release not found' <<<"$error"; then
    printf 'continue\n'
  else
    printf 'abort-lookup\n'
  fi
}

publish_decision() {
  local expected=$1
  local actual=$2
  if [ "$actual" = "$expected" ]; then
    printf 'published\n'
  else
    printf 'abort-mismatch\n'
  fi
}

[ "$(guard_decision 1 '' 'release not found')" = continue ]
[ "$(guard_decision 0 '{"databaseId":123,"isDraft":true}' '')" = delete-draft ]
[ "$(guard_decision 0 '{"databaseId":123,"isDraft":false}' '')" = abort-published ]
[ "$(guard_decision 1 '' 'HTTP 503: service unavailable')" = abort-lookup ]
[ "$(publish_decision 0.0.4 0.0.4)" = published ]
[ "$(publish_decision 0.0.4 untagged-deadbeef)" = abort-mismatch ]

printf 'PASS\n'
```

Run:

```bash
bash /tmp/test-release-logic.sh
```

Expected: `PASS`. This verifies all required decision branches without contacting GitHub.

- [ ] **Step 8: Validate YAML and embedded Bash syntax**

Run:

```bash
if command -v actionlint >/dev/null; then
  actionlint .github/workflows/release.yml
else
  python3 -c "import yaml; yaml.safe_load(open('.github/workflows/release.yml')); print('YAML OK')"
fi
```

Expected: `actionlint` exits with status 0 and no diagnostics, or the fallback prints `YAML OK`.

Use Ruby's standard YAML parser to extract every workflow `run` block, replace GitHub expression syntax with inert text, and syntax-check the resulting Bash as one file:

```bash
ruby -ryaml -e '
  workflow = YAML.load_file(".github/workflows/release.yml", aliases: true)
  scripts = workflow.fetch("jobs").values.flat_map { |job| job.fetch("steps", []).filter_map { |step| step["run"] } }
  puts scripts.join("\n\n").gsub(/\$\{\{.*?\}\}/m, "github_expression")
' > /tmp/release-run-blocks.sh
bash -n /tmp/release-run-blocks.sh
```

Expected: `bash -n` exits 0 with no output.

- [ ] **Step 9: Review the workflow-only diff and commit**

Run:

```bash
git diff --check
git diff -- .github/workflows/release.yml
```

Expected: `git diff --check` exits 0; the diff contains only the guard, notes PATCH, publish PATCH, and post-publish assertion described above.

Commit:

```bash
git add .github/workflows/release.yml
git commit -m "fix(release): preserve version tag when publishing (#49)"
```

- [ ] **Step 10: Remove temporary test files and verify branch state**

Run:

```bash
rm -f /tmp/test-release-tag-binding.sh /tmp/test-release-logic.sh /tmp/release-run-blocks.sh
git status --short --branch
```

Expected: the branch is ahead of `origin/master`; there are no uncommitted implementation changes. The already committed design and plan documents remain separate commits.

## Post-Merge Follow-Up

Do not execute these operations as part of this plan. After the pull request is merged, create a separate plan that requires explicit approval before each remote mutation and covers:

1. Rebind release IDs `352735238` and `352797616` to tags `0.0.2` and `0.0.3`.
2. Verify both releases resolve by semantic-version tag.
3. Delete the two orphaned `untagged-*` remote tags.
4. Verify only intended tags remain.
5. Run the next real tag-triggered release and confirm the post-publish assertion passes.
6. Validate stale-draft retry behavior without overwriting a published release.
