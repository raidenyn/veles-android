# SHA256SUMS naming in this folder (status after the 2026-10-27 follow-up integration)

There are three checksum manifests in this folder. Only the nameless `SHA256SUMS` is active.

- **`SHA256SUMS` (active).** Regenerated on 2026-10-27 by the GLM-5.3-flash follow-up
  integration session after the F-1/F-2/F-3 review fixes; covers EVERY current file in this
  folder except itself, including both historical manifests and the v1/v2 adjudications. Must
  verify clean (`sha256sum -c SHA256SUMS` from inside this folder).

- **`SHA256SUMS-before-review.original` (HISTORICAL, non-active).** Byte-exact copy of the
  pre-append v1 manifest (sha256 `3487728ce4feaddf681be371837a299aa544ab354d2a844ea3a13f971575a584`
  — the exact file fingerprinted by the mac-final Opus review and the fix verification). Its
  `./README.md` entry pins the PRE-append (reviewed) README bytes; `sha256sum -c` on it fails
  exactly on `./README.md` BY DESIGN after the F-3 README appendix. Kept to preserve that older
  review fingerprint; not used as the current coverage.

- **`SHA256SUMS-v2` (HISTORICAL, non-active).** The review-time manifest covering the 34 evidence
  files as they stood when the mac-final Opus fix verification re-verified them
  (34/34 OK, sha256 `66ba89565501f5b8bd3119dfb3f67f8adbe0e13ed263723a959669815df1cc1e`). Because
  later integration work only ADDED files (this notes file and the two historical manifests), a
  `sha256sum -c SHA256SUMS-v2` still verifies its 34 listed files unchanged; semantically it is
  the 2026-10-27 review-time snapshot, not the current-state coverage.

No current file is left uncovered by the active manifest, and no active checksum fails.
Original raw evidence files are byte-unchanged (they were never rewritten; git attributes and
the manifests guarantee byte stability).