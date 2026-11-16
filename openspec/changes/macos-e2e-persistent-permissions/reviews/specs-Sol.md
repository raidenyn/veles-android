# Review record: specs

- Artifact: specs
- Reviewer: Sol (GPT-6.1-Sol, medium per the roster; executed at the environment's reviewer pin, high effort)
- Session preset: [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: brainstorm.md sha256 01670f07cf77f34f048604305d5e26ec96b4867d92a67cdb9f017ef472da6c7d; proposal.md sha256 9e5baea97f5cfed8ce9dfee7d96a3be679c440a109e6b69df6884d0157d52910; design.md sha256 8ef5414d2f8e7699bf1a4288917acbc6307ed6ffb452011c18829b3e369ef145

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | A changed certificate was unconditionally classified as a new TCC identity, contradicting the conditional DR rule | fixed: the ad-hoc scenario now covers only ad-hoc/linker signing and a changed bundle identifier; a replaced certificate with an unchanged requirement has its own reuse scenario, and the "New identity" scenario is conditioned on a changed DR |
| medium | The undetermined-state rule conflicted with a legitimate planned first-use grant | fixed: a planned, attended first-grant scenario was added, and the stop/retry rule is scoped to unexpected states during a reuse run |
| low | The launch-attribution explanation turned a conservative policy into a universal platform claim | fixed: the measured SSH case is stated as measured, and other terminal contexts are described as forbidden by policy but unmeasured |

The artifact was revised and sent for round 2.

### Round 2 — approve

All prior findings are resolved: certificate replacement is judged by the designated requirement,
planned first-use authorization is distinct from an unexpected reuse-run state, and the attribution
wording separates measured SSH behavior from unmeasured terminal contexts. No new critical or high
finding.

Approved content: specs/agent-guidance-layout/spec.md sha256 c68d9626a8f206b93c059e2ef72eecbc258b66b135bac05956d9c4e7b99c4eaf
Approved content: specs/real-device-testing/spec.md sha256 9b693d2db617e1ceec77b4388d1d90ea8d9d4b15f74568a21776256740d773c3

## Consistency revalidation — rename amendment

### Round 1 — approve

The app-name rename is cosmetic for these requirements: they pin the identity and the grant-reuse
behavior, not the display or path name. The upstream design change (acceptance-driver compatibility
and the app name) does not alter any requirement or scenario. No findings.

Approved content: specs/agent-guidance-layout/spec.md sha256 c68d9626a8f206b93c059e2ef72eecbc258b66b135bac05956d9c4e7b99c4eaf
Approved content: specs/real-device-testing/spec.md sha256 9b693d2db617e1ceec77b4388d1d90ea8d9d4b15f74568a21776256740d773c3

## Consistency revalidation — identifier migration

### Round 1 — approve

The requirements use the approved identity generically and do not name the identifier; the move from
`me.nagaev.veles.otp23.poc` to `me.nagaev.veles.debug` and the fresh grant do not change any
requirement or scenario. No findings.

Approved content: specs/agent-guidance-layout/spec.md sha256 c68d9626a8f206b93c059e2ef72eecbc258b66b135bac05956d9c4e7b99c4eaf
Approved content: specs/real-device-testing/spec.md sha256 9b693d2db617e1ceec77b4388d1d90ea8d9d4b15f74568a21776256740d773c3
