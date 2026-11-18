# Review record: brainstorm

- Artifact: brainstorm
- Reviewer: custom — GPT-6.2 Sol (`openai/gpt-6.2-sol` via OpenCode), effort medium (session-only custom model)
- Session preset: [custom]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: none (brainstorm is the first planning file)

## Pass 1

### Round 1 — changes-required

Reviewed content sha256 2b55a37cec0b1144a7df7e9ec0f79bba66e9555042d15b101944cebddd600fc8

| Severity | Finding | Disposition |
|---|---|---|
| high | OpenCode support is presented as settled, but the cited hook signatures come from the V1 `@opencode-ai/plugin` 1.18.15 typings; OpenCode 2.x uses the V2 `setup(context)` API, and loading, denial and re-arm behaviour are unverified. | Fixed. Agreed Approach makes OpenCode conditional on a runtime check of four named V2 capabilities, cites the V2 shape from the `opencode-rules` 2.0.0 source, and defines a per-capability fallback to the pointer. |
| high | "Delivered", caller identity, retry after denial and reset ordering are undefined; treating every call without an id as new could deny every retry. | Fixed. Key Decisions define "delivered", mark delivery at the denial so the re-run passes, treat a call without an agent id as the main context, and use a generation number so a late write cannot undo a reset. |
| high | Moving tracker intake and closeout is not a file move: tracker intake applies from the user naming a source; closeout monitoring resumes after compaction with no gated command; live references (the `schema.yaml` finalize hand-off and others) name the old paths. | Fixed. A prompt-pattern moment is added for tracker intake; closeout is marked sticky (re-delivered after compaction); all live references are migrated; a rule stays always-on if its coverage gap cannot be closed. |
| medium | The owner's type constraints, `gh pr edit` and the place of CD/release automation are not stated explicitly. | Fixed. Key Decisions list the owner's constraints separately from the builder's taxonomy calls, name `gh pr edit` as a gate, and place release workflows under `ci` and release packaging under `build`. |
| medium | What counts as a skill load per tool, and what happens when a moment and a gate share one shell call, are undefined. | Fixed. Moments are listed per kind (model-invoked skill, slash command, `SKILL.md` read, workflow command); in a shared call the gate runs first and blocks once. |
| medium | The pointer does not say what to do without working hooks, and hook failures are invisible. | Fixed. The pointer tells the agent to read the file at the named moment; a hook failure sends a short notice to read the file by hand. |
| medium | "Never lets a wrong title through" overstates a guidance-only design, and Alternative B is misdescribed as stateless. | Fixed. The summary says the mechanism reminds and does not judge; A's pros are reworded; B is redescribed as remembering only the denied attempt. |

### Round 2 — changes-required

Reviewed the content after the round-1 fixes; its hash was not captured before the round-2 fixes were applied.
Round-1 status per the reviewer: findings 2, 4, 5 and 7 resolved. Findings 1, 3 and 6 were not resolved and
continue as the round-2 findings below.

| Severity | Finding | Disposition |
|---|---|---|
| high | The pointer's only home is `.agents/rules/`, which OpenCode loads only through `opencode-rules`, so the fallback is not always on when the loader is missing. | Fixed. The pointers move to a generated block of the root `AGENTS.md`, which every tool reads natively. |
| high | The OpenCode acceptance criteria stop at primitives (re-arm and sticky delivery to the first resumed turn are not proven, and prompt-match intake is not checked), and the per-capability fallback conflicts with "a workflow rule stays always-on". | Fixed. Six end-to-end behaviours are required of both tools. The pilot may degrade per tool with the degradation documented; a workflow rule moves only if all its required behaviours work in both tools (closeout needs 1–5, tracker intake 1, 2 and 6), otherwise it stays always-on. |
| medium | The tracker prompt moment ("names a project or issue") is broader than the spec's source-of-work distinction. | Fixed. The prompt moment is stated to be an early reminder only. Tracking is still decided by the rule's own scope clause, so an over-broad match costs context, never a wrong claim. |
| medium | The delivery guarantee contradicts fail-open and conditional support. | Fixed. The claims are limited to installed, working hooks, and the degraded case is stated: a covered command can proceed with only the pointer. |

### Round 3 — changes-required

Reviewed content sha256 b64b9c2041c484fdd7854d0d1e507fc344c3bbe921799b0992fdc8daa2ab2d24

Round-2 status per the reviewer: findings 1, 3 and 4 resolved. Finding 2 was not resolved and continues as the
round-3 tracker-intake finding below.

| Severity | Finding | Disposition |
|---|---|---|
| high | Tracker intake may migrate on behaviours 1, 2 and 6 although it declares skill and command moments and a gate that must re-arm (behaviours 3 and 4). | Fixed. Tracker intake now requires behaviours 1–4 and 6. |
| high | The pilot may ship pointer-only in OpenCode, contradicting the owner's requirement of both tools, and no owner exception is recorded. | Fixed. The pilot ships only when behaviours 1–4 work in both tools. If one cannot be made to work, implementation stops and the owner decides; the pointer is a runtime fallback, not a planned release state. |
| low | The summary says both workflow rules move; the agreed approach makes it conditional. | Fixed. The summary is now conditional on verification. |

### Round 4 — changes-required

Reviewed content sha256 604a1d0bdb179add43ee6ffe34f0634dcb59fb64bb47f70894655f0f2cb9e4f6

Round-3 status per the reviewer: all three findings resolved.

| Severity | Finding | Disposition |
|---|---|---|
| medium | Behaviour 3 can be satisfied by a single skill or workflow-command moment, though the rule promises every declared moment kind. | Fixed. Behaviour 3 now requires each declared moment kind to be verified separately. |

### Round 5 — approve

Reviewed content sha256 625274879084f803c341cd50170bebffa5a6b8a8c184f5027ccac4d3ffb4f118

Round-4 status per the reviewer: resolved. No critical or high finding remains.

| Severity | Finding | Disposition |
|---|---|---|
| low | The "each moment kind" wording could say explicitly that tracker-intake prompt matching is verified under behaviour 6, not as part of the pilot's 1–4 release gate. | Deferred to design.md, where the verification behaviours are specified in full; the brainstorm already lists prompt matching as behaviour 6. |

Approved content: brainstorm sha256 625274879084f803c341cd50170bebffa5a6b8a8c184f5027ccac4d3ffb4f118

## Pass 2 — scope narrowed (triggering normative edit)

After the design review's second round, the owner narrowed the change to the mechanism plus the commit-type
pilot. The closeout and tracker-intake moves, sticky re-delivery, prompt-pattern moments and their behaviours
(old 5 and 6) were removed; a pointer-fallback behaviour was added as behaviour 5. This is a normative edit, so a
new pass opens.

### Round 1 — changes-required

Reviewed content sha256 6e47f4f95c179827285b86342881050a6333d42d19314596ae242c2eda88c911

The reviewer confirmed that the owner's narrowed scope is recorded and that no removed capability remains in
scope.

| Severity | Finding | Disposition |
|---|---|---|
| medium | Behaviour 1 names only the OpenCode plugin, although the gate requires all five behaviours of both tools. | Fixed. Each tool loads its own integration (Claude Code hooks, the OpenCode plugin). |
| medium | Behaviour 4 says the gate always re-blocks after a compaction, contradicting "only if not delivered since". | Fixed. "With no early moment in between" was added. |

### Round 2 — approve

Reviewed content sha256 a33e21200c90a0e2643cc748c6009889bab800c7ce751bd3fb7a8fd7005b20d2

Both round-1 findings resolved; no critical or high finding.

| Severity | Finding | Disposition |
|---|---|---|
| low | The Agreed Approach still says commits after a compaction are caught by the gate, without the "no early delivery in between" qualifier. | Fixed (non-triggering, one minor fix). |

Approved content: brainstorm sha256 3d530fff7e7efe707e722eea4a33a11b7cb899b9078a23b2c870d736c2da680d — re-pointed from a33e21200c90a0e2643cc748c6009889bab800c7ce751bd3fb7a8fd7005b20d2 after one non-triggering minor fix (the round-2 low finding: the post-compaction gate sentence qualified)
