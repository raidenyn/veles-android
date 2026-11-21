# Review record: proposal — Opus

- Artifact: `proposal.md`
- Reviewer: Opus (Opus-5.5, medium)
- Preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes:
  - `brainstorm.md` sha256 4df8162115fc5e4d9607694fe07ac6ce69ba2d394c7769fb043b9bd1321b01b0

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| medium | "Same output contract" cannot hold: the OpenCode stop path has no notice channel, so notices would be dropped. | fixed: OpenCode keeps the block contract; notices become non-resuming messages (`synthetic({resume:false})`, already used on the prompt path). |
| medium | The busy-session resume is stated as settled; no OpenCode real-session acceptance. | fixed: marked not yet observed with a spike; one real OpenCode turn joins the acceptance. |
| low | The "From" line says only `check`/`test` entries carry `doc`. | fixed: every command, producer and `excluded` line. |
| low | Accepted limits missing (plain-terminal push, no override, unprovisioned machine). | fixed: listed in Impact. |
| low | "Complete run" undefined. | fixed: defined inline. |
| low | `docs/verify.md` "CI only" depends on `excluded` reasons. | fixed: the reasons that matter move into `docs/verify.md` (design D6). |

### Round 2 — approve

| Severity | Finding | Disposition |
|---|---|---|
| low | The notice examples omit the deadline-incomplete notice. | fixed after approval: "an incomplete run" added. |
| low | "The reasons that matter" left to the design. | deferred: design D6 names them. |
| low | The contention cost of a background branch run is missing from the accepted limits. | fixed after approval: added to Impact. |

Approved content: proposal sha256 3462e1cb71ae8c6b94d7211639ece96148f504012ac1930b0142f6519f8b9a55 (re-pointed after two non-triggering low fixes made after the round-2 approval: the incomplete-run notice example and the contention limit)
