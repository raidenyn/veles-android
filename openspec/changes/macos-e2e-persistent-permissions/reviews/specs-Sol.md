# Review record: specs

- Artifact: specs
- Reviewer: Sol (GPT-6.1-Sol, medium per the roster; executed at the environment's reviewer pin, high effort)
- Session preset: [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: brainstorm.md sha256 c23498c533380384c936caba135d106fc980570aec4717aa6f0a0bb3b6034d25; proposal.md sha256 699bd8c725342240c701b64678db3ebea98c03696eadefe958024d484104229c; design.md sha256 5a668ab796b9ea7e824b910412060b358349900b2678938293e18550b929ea53

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
