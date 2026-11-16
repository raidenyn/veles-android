# Review record: design

- Artifact: design.md
- Reviewer: Sol (GPT-6.1-Sol, medium per the roster; executed at the environment's reviewer pin, high effort)
- Session preset: [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes: brainstorm.md sha256 c7826d38283fb95d1e5a1f2d8e6ac45c7e316a27c5582eb64eb54aa95e517903; proposal.md sha256 612b4e8b99c7f73d262c0cd1278f9f3a7a563c6380e1e966a93513c8012edded

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | Log selection and correlation could discard grant-write events: a path-qualified filter drops `TCCDEvent Create` records that carry the identifier but no executable path | fixed: the retained window is now a broad service-scoped superset (bundle id OR executable path OR `kTCCServiceBluetoothAlways`); the prompt and grant-write checks are separate searches over it, and the path is used only for attribution |
| medium | The identity-stability check had no baseline to compare against | fixed: the accepted designated requirement is recorded verbatim in `.agents/rules/macos-test-app-tcc.md` and quoted in the design; `codesign -d -r-` must equal it, and the first signed build records it once |
| medium | The experiment's log windows were not inspectable; authorization was not evidence of no prompt | fixed: the exact predicate and raw windows are preserved in `evidence/logs/` and referenced from the evidence record |

The artifact was revised and sent for round 2.

### Round 2 — approve

All three findings are resolved; the reviewer inspected the preserved NDJSON windows and confirmed
the positive-control and absence records. No new critical or high finding.

Approved content: design sha256 5a668ab796b9ea7e824b910412060b358349900b2678938293e18550b929ea53

## Pass 2 — rename amendment

### Round 1 — changes-required

| Severity | Finding | Disposition |
| --- | --- | --- |
| high | The migration plan omitted the acceptance driver, which rejects the new app name | fixed: the migration now covers `accdrv/macid.py`, its legacy/new-name acceptance and its test |
| medium | The Non-Goals still said no packaging-script change | fixed: it now allows the test-bundle naming change while excluding host implementation work |

### Round 2 — approve

No findings. The identity, baseline and launch contract remain coherent, and the consumer migration
is complete.

Approved content: design sha256 63d462b356813d730ab0517b6f0cfe5eef19dd57a88c4e53429a9ccc59935349
