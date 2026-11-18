# Review record: tasks

- Artifact: `tasks.md`
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream: `brainstorm.md` sha256 c27ca9d75a2a5b085956825eb2aa827fd3b518d9f65ba6df139b52ff9670a0c7; `proposal.md` sha256 0260f7c0185b8f9957eb6026c228fd827d306227fa373dd5646729d673b4ea65; `design.md` sha256 5b11d7a9ce98550112951d0a763fa17caf5a4711639efa1128f00738a83f3080; `specs/documentation-site/spec.md` sha256 9fc6f000d8307f98a3984320370e5f527ea05cc232bc68314051bcf07ec35357; `specs/component-versioning/spec.md` sha256 714bad1f17bd08338e7a80400b21329ed17b0f97bf51025f3755be0eb8044d0e; `specs/ci-pipeline/spec.md` sha256 a975d5fae45639010597c16b0bfc5d54bac0e28c2631e3b4e3d26066e926b616
- Upstream re-pointed after the implementation-time re-review (see the last pass below): the spec, design, tasks and plan were edited to match the built behaviour.

## Pass 1

### Round 1 — approve (more than five minor fixes made afterwards, so review reopens)

| Severity | Finding | Disposition |
|---|---|---|
| medium | 1.6 would have the builder narrow repository-policy rules Mintlify accepts. | Fixed: calibration limited to hazard and build-breaking rules; policy rules named as not calibrated. |
| medium | 1.1 fixture commits, failing without git identity and exposed to user config. | Fixed: `git add` only, global and system config isolated. |
| medium | No task stages new files although the guard sees only tracked files. | Fixed: staging in 2.2 and 5.6; 7.1 checks for untracked files first. |
| medium | 6.4 lacks mutation tests for the ci-pipeline scenario. | Fixed: two mutation tests (step removed, arguments changed). |
| medium | 3.4 ran `broken-links` before the new pages existed and missed links in new pages. | Fixed: moved to 5.6, covering old and new pages. |
| medium | 4.4 "on-device processing" ignores BLE sharing. | Fixed: wording covers Bluetooth sharing to a paired computer when enabled, without a pairing guide, checked against the sharing code. |
| low | 2.3 full navigation or skeleton unclear. | Fixed: skeleton. |
| low | Python 3.8 not installed. | Fixed: `uv run --python 3.8`. |
| low | Re-include matching for default ignores imprecise. | Fixed: exact `!/<path>` with two fixtures. |
| low | 4.3 sources too thin. | Fixed: string resources and permissions screen named. |
| low | Stale header comment and test title/messages. | Fixed: in 6.3 and 6.4. |
| low | No verification of branding and telemetry scenarios. | Fixed: in 7.3 and 7.4. |
| low | Exit-code and warnings-only tests not named. | Fixed: in 1.5. |
| low | `BUILD.md` line 84 edited twice. | Fixed: note in 3.1. |

### Round 2 — approve

All round-1 fixes confirmed; cited line numbers (3701, 3710, 3733, 3744) verified.

| Severity | Finding | Disposition |
|---|---|---|
| low | 4.4 Bluetooth statement goes beyond design D5's summary. | Fixed: the task states why (accuracy under *Privacy wording*). |
| low | 4.4 Bluetooth sentence could grow into pairing steps. | Fixed: one sentence, no steps. |
| low | 2.2 comparison runs before the guide moves. | Fixed: moved files excepted. |
| low | 6.1 "pinned list" vague. | Fixed: test name and lines 240-249. |
| low | 5.6 could edit `openspec/specs/` files, which the spec forbids. | Fixed: specs never edited; a reported spec link is stopped and reported (none has a relative link today). |
| low | Python 3.8 if `uv` cannot fetch it. | Deferred: reported at apply if it happens. |

Approved content: tasks sha256 e97cf94b486c2a71fb9f61059edc61059746642619f10f181731315071e69ebc (re-pointed after four non-triggering low fixes from round 2: privacy sentence scope, 2.2 exception, test name, specs never edited)

## Pass 2 — implementation-time re-review

Implementation-time re-review (after the code was built): calibration against the Mintlify CLI and the whole-branch review showed approved text stale in places (CONTRIBUTING.md default ignore, privacy wording, braces, pinned RFC links, hand-written descriptions). The controller edited the spec, design, tasks and plan; Opus re-reviewed them.

### Round 1 — approve (one note fix)

| Severity | Finding | Disposition |
|---|---|---|
| low | "As built" note cited task "5.6, 3.4"; 3.4 does not exist. | Fixed: "5.6". |
| medium | Task 5.3 cited the spec requirement by its old name after the rename. | Fixed: cites "Build page import and Contributing summary". |

Approved content: tasks sha256 2835786f20f5943f3cbd7f15c3576d65fcecb496e97b83fdadf3a3468306e534 (re-pointed after two non-triggering minor fixes from the re-review, plus the appended "As built" section reviewed in this pass)
