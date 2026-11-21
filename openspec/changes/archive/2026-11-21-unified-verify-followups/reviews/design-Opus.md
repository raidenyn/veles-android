# Review record: design — Opus

- Artifact: `design.md`
- Reviewer: Opus (Opus-5.5, medium)
- Preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes:
  - `brainstorm.md` sha256 4df8162115fc5e4d9607694fe07ac6ce69ba2d394c7769fb043b9bd1321b01b0
  - `proposal.md` sha256 3462e1cb71ae8c6b94d7211639ece96148f504012ac1930b0142f6519f8b9a55

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | `exempt` regexes use `.*` across shell operators, so `git push origin feat && git branch -d tmp` and a mixed `feat :old` push skip the proof. | fixed: segment-wise matching at `;`, `&`, `\|`, newline; only deletion segments exempt; anchored regexes; test table with chains and mixed refspecs. |
| high | Joining the verifier into the single-flight stop call suppresses the engine's stop gate for up to 30 minutes and delays its block. | fixed: separate `verifiesInFlight` slot; the engine stop keeps its per-turn behaviour and own resume. |
| medium | Kill semantics wrong: the engine child is killed by pid, and the launcher's dispatched child runs in its own session. | fixed: SIGTERM to the launcher, launcher SIGTERM handler runs `_stop_child`, SIGKILL after 5 s grace; residual documented. |
| medium | Predicate budget cannot guarantee "timeout is a deny" within the hosts' 5 s limit. | fixed: 3.5 s predicate deadline from event start; host-timeout fail-open documented. |
| medium | Engine/rule version skew between checkouts unaddressed. | fixed: `branch_pass` imported from the engine's own `scripts/`; versioned record; both skew directions documented as limits. |
| low | A contended run deleting the record could erase a concurrent pass. | fixed: delete only after taking the run lock. |
| low | Hook-state key format change. | fixed: `HOOK_STATE_VERSION` 2; the reader already resets on mismatch. |
| low | Duplicate check scope said "Build & Test or Commands section". | fixed: every shell fence (`_read_entries`). |
| low | OpenCode shell-timeout figure unverified. | fixed: softened and attributed to the tool description. |

### Round 2 — approve

| Severity | Finding | Disposition |
|---|---|---|
| low | `_stop_child` kills only the dispatched child; running step groups live on until orphan cleanup. | fixed after approval: stated as an accepted, documented limit in D5. |
| low | Splitting at `&` also splits redirections such as `2>&1`. | fixed after approval: `git push origin feat 2>&1` added to the test table. |

Approved content: design sha256 e1ec96f8fb13b7d9b9e2e79a788f41ec3384fa8acf291c0c548200d8cfe61e35 (re-pointed after four non-triggering low fixes since the round-2 approval: the step-group residual in D5, the redirection test case, and, from the specs review, the `exempt` validity wording in D3 and value-less Gradle options in D1)
