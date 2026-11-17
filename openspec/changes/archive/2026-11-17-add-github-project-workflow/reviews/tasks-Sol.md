# Review record: tasks

- Artifact: tasks
- Reviewer: Sol (GPT-6.1-Sol, medium)
- Session preset: [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes:
  - brainstorm.md sha256 cc91af6286ab0753e157b3cdeaad59feee8388d31ddcb689b22ebbdbdece7a53
  - proposal.md sha256 6a91d7ee3c7aaf368e6c6796593c19914a9f98da6a195fcdf9abb945ac3bcb52
  - design.md sha256 c350e1488ff4b27ebd570ae1bfcb6f3ed7d042e829cab7dbbf3582a72739bdd4
  - specs/github-project-automation/spec.md sha256 af61fd5016fbe326fd652565b8c0252c95a0bca37b178dc48e485c3553656c09
  - specs/superspec-tracker-intake/spec.md sha256 d58d36e42c00d40a523137131468f42f300e829fd68a0e207a75e8e28953db2d
  - specs/superspec-closeout/spec.md sha256 96d3d86652b73150904ca155db8f329589fd938a2ada660c7c308306be539fba

## Pass 1

### Round 1 — changes-required
| Severity | Finding | Disposition |
| --- | --- | --- |
| high | The intake safeguards were untasked | fixed (6.1) |
| high | The changed local-merge closeout was untasked | fixed (6.2, 6.3, 6.5, 6.6) |
| medium | Follow-up branches were missing (`n = p + 1`, missing-step output, incomplete descendant read) | fixed (4.3, 4.4) |
| medium | The fresh-read skip and the rate-limit cost were missing | fixed (2.2, 5.2) |
| medium | Dry-run acceptance tests were missing | fixed (2.5) |
| medium | Verification timing and offline prerequisites | fixed (7.1, 8.2, 8.4) |
| medium | Scratch access was requested too late | fixed (0.2) |
| medium | TDD sequencing | fixed (0.1; tests first; 5.1 before 5.2) |
| low | Identifier grep targets | fixed (7.3) |

### Round 2 — approve
| Severity | Finding | Disposition |
| --- | --- | --- |
| medium | Whether the finalize template's "set Done" wording is an edit target | fixed after approval (6.6 names it) |
| low | The parent-as-blocker safeguard had no task or test | fixed after approval (4.3) |

Approved content: tasks sha256 432e3205aab1f2743305dd44068299b4639e7e816fdf4b8b589e2c9e07282fc8 (re-pointed after 1 minor fix: the setup-check workflow facts; checkboxes only otherwise)

Upstream re-pointed after the pass-2 spec review of the specs artifact (consistency check by the same reviewer against the edited specs: no contradiction); the upstream hashes above are the current ones.
