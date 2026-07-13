# Release Tag Binding Fix Design

## Goal

Ensure tag-triggered GitHub Releases remain attached to the triggering semantic-version tag instead of publishing on a synthetic `untagged-*` tag. Make failed release runs safely repeatable without allowing duplicate published releases.

## Scope

This change modifies only `.github/workflows/release.yml` and adds focused validation for its release-management shell logic.

The following live repository operations are deferred until after the pull request is merged:

- Rebinding the existing `0.0.2` and `0.0.3` releases.
- Deleting the existing remote `untagged-*` tags.
- Running the tag-triggered workflow with the next real release tag.
- Re-running that workflow to validate retry behavior against GitHub.

## Workflow Design

### Existing-Release Guard

The build job will inspect releases for the triggering tag, including drafts, before building:

- If no matching release exists, continue normally.
- If a matching draft exists, treat it as debris from a failed or cancelled run. Delete the release by database ID without `--cleanup-tag`, preserving the real git tag, then continue.
- If a matching published release exists, emit an Actions error and abort to prevent duplicate publication.
- If the GitHub API query fails, fail the step. An API failure must not be interpreted as the absence of a release.

Release identity will use the database ID where draft handling requires an unambiguous reference.

### Draft Mutations

Every REST mutation of the draft release will include `tag_name="$GITHUB_REF_NAME"`:

- The PATCH that appends verification instructions to the release notes.
- The PATCH that changes `draft` to `false` and publishes the release.

The publish request will continue to send `draft=false` as a JSON boolean. Explicitly including `tag_name` prevents GitHub's draft-only synthetic ref from becoming the published release tag.

### Post-Publish Assertion

After publication, the workflow will fetch the release by database ID and compare its `.tag_name` with `$GITHUB_REF_NAME`. A mismatch will produce an Actions error and fail the job. This makes a future tag-association regression visible in the release run itself.

## Components And Data Flow

1. A semantic-version tag push starts `release.yml`; `$GITHUB_REF_NAME` is the required release tag throughout the workflow.
2. The guard checks for a release associated with that version and either continues, removes a stale draft, or rejects a published duplicate.
3. The build job creates a draft and passes its database ID to later operations.
4. Notes mutation preserves the intended `tag_name` explicitly.
5. The verification job validates reproducibility as before.
6. The publish job verifies the draft asset digest, publishes by release ID while explicitly setting `tag_name`, and reads the release back to assert the association.

No changes are required in `release-build.yml`; restoring correct tag associations makes its existing `gh release view "$VERSION"` protection effective again.

## Error Handling

- Published-release collisions fail before build and draft creation.
- Stale-draft deletion failures fail the workflow rather than continuing with ambiguous state.
- Release-query failures fail closed.
- Asset digest mismatches retain the existing failure behavior.
- Publish API failures fail the publish job.
- A successful PATCH followed by an unexpected tag association fails the post-publish assertion.

## Validation

Pull-request validation is local and static; it will not create, mutate, or delete live releases or tags.

- Parse or lint the workflow YAML.
- Syntax-check changed Bash blocks.
- Exercise extracted release-management logic with mocked `gh` responses for no existing release, stale draft, published release, successful tag binding, and mismatched post-publish tag binding.
- Run existing repository checks applicable to workflow-only changes.

End-to-end GitHub validation and one-time cleanup require a separate post-merge plan and explicit approval before destructive remote operations.

## Non-Goals

- Changing artifact attestation or reproducibility verification.
- Changing manual prerelease behavior in `release-build.yml`.
- Removing tags created intentionally for manual prereleases.
- Performing current release or remote-tag cleanup in this pull request.
