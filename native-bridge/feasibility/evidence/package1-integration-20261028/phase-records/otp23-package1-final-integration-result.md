# OTP-23 package1 FINAL INTEGRATION — RESULT (2026-10-28 evening; GLM-5.3-flash, 0 subagents, NO commits/push)

Authorization applied: the parent authorized the final 3-row integration after the focused Opus 5.5
review was DONE. The Opus report was read and its identity verified, the THREE remaining Windows rows
were promoted per the prepared candidate (integration-result §7) plus the already-integrated W-11/W-12 —
**FINAL CANONICAL COUNTS: 23 PASS / 6 BLOCKED / 3 waived (NOT RUN)**; open rows exactly **W-10, M-03,
M-04, M-08 (owner-deferred), M-11, A-01**; waived exactly W-01/M-01/M-02; **NO-GO; OTP-24 NOT
authorized**. No hardware/phone/Chrome/Mac action; no personal restart; no build/sign-fixture; no new
numeric or schema work (all pins frozen and verified).

## 1. Review applied

`/tmp/opencode/otp23-package1-p2-opus-v4-final.md` (`d85d4f5784ccda95a3ce85ed1430abee34519f54aa893c81cb3656b5dbe4ff1e`,
local Claude Code session `1b447229-ceaa-4cee-a5be-2181dfdd8838`, actual `claude-opus-5-5`, metadata
confirmed by report line + this session's safe transcript extraction): **verdict — the sole remaining
v3 defect (`_num(10**1000)` OverflowError) is CLOSED**; exact branch verified literally; the
`numeric_range` overflow boundary behaved as specified under ±10**1000 in every validator slot; v4
values re-derived identical; all 7 code pins, every input/output pin and the 8-entry supersedes chain
verified; **recommends W-02/W-07/W-08 PASS** through separate adjudication and the normal integration
step (which this phase performed). Non-blocking notes only (pre-existing schema-range parity, a direct
Python-call int-to-string limit unreachable from JSON) — NOT acted on, per "no new numeric/schema work".

## 2. Canonical changes (exact files + hashes after all edits)

| changed file | final sha256 | change |
|---|---|---|
| native-bridge/feasibility/acceptance/otp23-matrix.json | `bbe67839a9354818011b5040e896e7689551be459ddea43f4e5fa392afd1590f` | W-02/W-07/W-08 → PASS (account `admin`, blocker cleared, full history retained in `actual`); evidence now pins the v4 adjudication/derivations + the published Opus report; D-02 FINAL postscript (23/6/3; interim 20/9/3 kept as history; quota state explicitly not the final blocker); R-01 provenance appended (GLM implementation / GPT earlier reviews / user-authorized Opus final — no retroactive Opus authorship) |
| docs/otp-23-requirements-matrix.md | `2b6894aa2ccf0a11bc3ebe78384172cb2f7b1adc18a41870da0bc9c29e07dd65` | re-rendered from the JSON (renderer prose updated to final truth) |
| native-bridge/feasibility/scripts/matrix.py | `2ba2cabf80c0651e5ccc4c0f5cb710eef3c76ff06829f78d5b51def0926e571b` | the Background-criterion prose no longer says W-02/W-07/W-08 stay BLOCKED — records the 2026-10-28 W-11/W-12 promotion + the final 3-row integration (23/6/3, NO-GO) |
| native-bridge/feasibility/scripts/test_matrix.py | `245f696ca6f86fc5988f5a3b4c43fa12a4c45e531d34f53a025972555822fef8` | invariants assert the FINAL truth: W-02/W-07/W-08 PASS with the W-02 expected == the authoritative RFC amendment sentence, adjudication PASS + census 6/6, association v4 2322/2406, tab-close v4 2304/2310, published Opus report present, D-02 final counts + exact remaining rows; the W-11/W-12 test renamed to final-truth (`test_w11_w12_windows_strict_background_rows_pass_under_the_clarification`); blocked tuples now exactly (W-10, M-03, M-04, M-08, A-01, M-11); the non-adopted-identity test renamed to its historical-record role (e36328d2/6cf72ed0 history still asserted); NO expectation of any new owner waiver |
| docs/otp-23-contract-alignment.md | `9a40bb5ee98b41eedecc40659272db1e9a95aa4f796688e9107fc59d6496604a` | NEW section 19: the FINAL record (owner Opus-route instruction 9211bbf8…, report d85d4f57…, the three promoted rows with their candidate basis, final counts, gates) — sections 16-18 histories preserved |
| docs/otp-23-investigations.md | `40fad9e7ad2a831c6c28ea7bdab4ad93f71a67d847bb3ae0b2735573ba05b741` | NEW section 6: truthful current disposition pointing to the new evidence home; all histories preserved |

NOT changed (pins frozen, verified by sha): `scenario_matrix_core.py` `cb512418…`,
`test_scenario_matrix.py` `307a7eb1…`, `package1_b5_v2_derive.py` `9cad1a70…` (self-pinning), the four
other pinned producer/observer scripts, every v2/v3/v4 artifact and manifest, all raw evidence (P2 34,
connected 181, snapshot 238), all frozen baselines (196/257/49/37/26/11). `docs/rfcs/2026-08-21-otp-external-devices.md`
unchanged this phase (`d1de4177…` — the authoritative amendment already recorded; the matrix's W-02
`expected` was verified to contain the exact owner sentence).

## 3. Publication in the integration folder (all actual published files)

`native-bridge/feasibility/evidence/package1-integration-20261028/` — active `SHA256SUMS` refreshed and
FINAL = `46d2f8cb1d6def54d7095ac63c3c3319a22fdd87b0a476f33d6afa6437ae1924` → **21/21 verified** (0
FAILED), covering every actual published byte INCLUDING the frozen-v2's unlisted quota addition by
relative path:

- NEW `reports/otp23-p2-opus-v4-final.md` (`d85d4f57…`) — byte-identical (`cmp` verified).
- NEW `provenance/opus-review-model-metadata-final.json` (`a5762208…`) — FINAL safe metadata appended
  as a NEW file (the running-session partial capture is PRESERVED unchanged; counts 69 assistant
  entries = 68 `claude-opus-5-5` + 1 harness `<synthetic>`, differing only by capture moment; only
  assistant-envelope fields read).
- Updated `FINDING.md` + `PROVENANCE-MAP.md` — current FINAL 23/6/3, the review CAPTURED (not
  pending), role labels: earlier GPT reviews keep actual GPT authorship; the final review was
  user-authorized Opus; implementation stays GLM (`glm-5.3-flash`, 0 subagents); no Kimi anywhere.
- NEW `gates/final-gateF-*.log` ×3 (byte-identical copies of THIS phase's final gate runs) with the
  exact source commands and exit codes in `gates/SOURCES.md`.
- Prior files/manifests untouched (v2 15/15, v3 9/9, v4 9/9, P2 34/34, connected 181/181, snapshot
  238/238, frozen baselines all green).

## 4. Required FINAL gates (after the last source/doc edit; logs copied)

| gate | result | log |
|---|---|---|
| `JAVA_HOME=/home/dev/.jdk/zulu21 ./gradlew spotlessCheck --rerun-tasks` (forced) | exit 0 | `evidence/package1-integration-20261028/gates/final-gateF-spotless-rerun.log` |
| `JAVA_HOME=/home/dev/.jdk/zulu21 ./gradlew spotlessCheck detekt lintDebug testDebugUnitTest protocolCheck` | exit 0, BUILD SUCCESSFUL | `…/gates/final-gateF-gradle-full.log` |
| both verbatim ci.yml android.util.Log guard checks | logguards_exit=0 | `…/gates/final-gateF-logguards.log` |
| `py_compile` (matrix.py, test_matrix.py, core, test_scenario_matrix, derive) | OK | (run recorded; trivial) |
| full portable harness `python3 -m unittest discover -s . -p 'test*.py'` | **457 tests OK, 5 skipped** (456 + 1 new final-integration invariant test) | (run recorded) |
| `matrix.py render --write` + `check` + `decision` | `matrix ok: NO-GO`; NO-GO with the exact 6 open rows; waived exactly [W-01, M-01, M-02] | (run recorded) |
| `git diff --check` | 0 | — |
| all 13 evidence manifests | verified (see §3 tails: 16-entry v2 frozen set 15/15 + the relative-path addition in OUR manifest, etc.) | (run recorded) |
| affected Node/web-extension + shell checks | NOT affected — no web-extension/shell/native-bridge-node change (prior bindings reused) | — |
| Rust | NO source changes — prior source-bound records reused | — |

## 5. Worktree constraints & handoff

- NOTHING is committed/pushed/GitHub by these sessions. Tracked diff now 13 files / +3066 −102 (all
  previously authorized phase work uncommitted; untracked evidence trees intact). The parent obtains
  its final integrated committed-tree review and does the commit/push/publication itself.
- Preserve-work statement: every raw measurement, every retained failure, v2/v3/v4 (superseding
  chain), the frozen baselines and all pinned derivations are byte-exact preserved; the canonical
  W-02/W-07/W-08 history lives in the row `actual` texts (raw BLOCKED records unchanged on disk).
- No OTP-24 authorization, no release-readiness claim, NO-GO stands. M-08 remains owner-deferred
  ("Cannot restart Chrome now" — no Mac action; fixture/grant untouched).
- Remaining open ROWS (for later, each with its own owner decision/permission work): W-10, M-03,
  M-04, M-08 (deferred), M-11, A-01. Nothing else blocks the OTP-23 matrix record as committed.