<!-- PUBLIC REDACTED COPY: the name-sweep term list (names of unrelated applications' Native Messaging manifests on the owner's account) replaced by a placeholder; the private original is kept outside the repository (see ../final-integration-map.json). Otherwise byte-identical. -->
# OTP-23 Mac publication curation: result (2026-10-29, curation only)

## 0. Role envelope and scope

- **Curator:** Claude Code, `claude-opus-5-5`, session `752e1974-0617-470b-a425-dfbda97ac728`, no
  subagents. This is curation only, done while the independent A2 micro-review (Opus `e30e474d`
  route) was running.
- **Not done:**
  - no source, test or workflow edit
  - no hardware, Mac, phone, Chrome, TCC, signing, key or fixture action
  - no M-11 raw or code edit
  - no root Gradle gates (deferred to canonical integration)
  - no commit, push or GitHub
  - no row promotion: the matrix stays **23/6/3, NO-GO**
- **M-03 / M-04 remain BLOCKED.** No original prompt can be re-observed under the existing grant, the
  owner has not approved an M-04 off/on toggle, no waiver is inferred, and no production UX or backlog
  work was done.
- **Frozen and untouched**, verified after curation:

  | item | sha256 |
  |---|---|
  | A2 `mac_release_attribution.py` | `ce75a8f1…` |
  | A2 test | `219c78be…` |
  | frozen analyzer | `a49b3450…` |
  | workflow | `df1305d4…` |
  | `mac-retake-2026-10-29` (79 entries) | `f952c123…` |
  | `mac-release-attributed-2026-10-29` (A1, 6) | `dd05dac4…` |
  | `mac-release-attribution-source-history-2026-10-29` | `2a550c1c…` |
  | `mac-release-attributed-safe-2026-10-29` (A2 outputs, 6) | `c05174f4…` |
  | `mac-resume-2026-10-28` (35 active) | `9e8ed077…` |
  | `mac-m08-codephase-2026-10-28` | `339505b6…` |

  All pass `sha256sum -c`. `git diff --check` is clean, and `acceptance/` and `docs/` are unchanged.

## 1. NEW `evidence/mac-m11-dev-2026-10-29/`

`SHA256SUMS` is `071d14f114497f76b7f8c60ceaf2a7c5922dd5ff3e17cbefb5e1cfd69098c49a`. It has 25 entries,
the disk set equals the manifest, the folder has its own `.gitattributes` (`* -text -whitespace`), and
it contains no `__pycache__`.

| path | sha256 | origin |
|---|---|---|
| `attempts/m11-attempt-1/m11-capture.json` | `9f9e33d2…` | exact controller bytes |
| `attempts/m11-attempt-2/m11-capture.json` | `209fe71d…` | exact |
| `attempts/m11-attempt-2/…ac6b44f7….request.json` | `40c875b1…` | exact |
| `attempts/m11-attempt-2/…ac6b44f7….answer.json` | `0bda401f…` | exact; **post-run** (06:18:28Z, 36 min after the deadline), never accepted |
| `attempts/m11-attempt-3/m11-capture.json` | `f04c9045…` | exact |
| `attempts/m11-attempt-3/…8a055ccc….request.json` | `4e558a79…` | exact |
| `attempts/m11-attempt-3/.owner-answer-8a055ccc.tmp` | `d0037473…` | exact; **post-run** (about 3 h 17 min late), the rename was refused, never accepted |
| `controller/OPERATOR.md` | `31c3eefe…` | exact |
| `controller/observation-timeline.json` | `beaa7267…` | exact; the actual timings |
| `owner-records/account-class.txt` | `a2bdc40c…` | exact; 11:01:19Z, `raiden`/uid 501, `admin`, `dseditgroup` yes |
| `owner-records/original-grant-owner-check.json` | `9d7f1689…` | exact; VelesOtp23PoC "Still enabled, unchanged" (read-only) |
| `owner-records/otp23-mac-permission-owner-record.json` | `0711b3ee…` | exact; M-11 approval, M-04 toggles not authorized |
| `as-run-code/m11_dev_capture.py` | `976f61cf…` | local prep copy = attested as-run hash = Mac stage manifest `b1907348…` |
| `as-run-code/package-dev.sh` | `fa6ba4e8…` | local copy |
| `as-run-code/test_m11_prompt_watch.py` | `50aa32eb…` | local copy |
| `as-run-code/README-M11-PREP.md` | `93514f0b…` | local copy |
| `as-run-code/extension/{manifest,background,connector.js,connector.html}` | `6cc440fe`/`2d8a048a`/`c1c33331`/`d43f1b59` | equal to the capture-recorded `extension_files` and to repo HEAD |
| `provenance.json` | `bc66d07b…` | new |
| `curation-map.json` | `0f9e8706…` | new; 12 raw/record copies verified byte-exact against their sources |
| `FINDING.md` | `1027c44c…` | new |
| `adjudication-draft.md` | `b668aed3…` | new; **DRAFT**, conditional |

**Attestation vs measurement.** The captures don't self-record the script sha, so `976f61cf` is
recorded as **controller attestation** (`OPERATOR.md`), not a capture-level pin. The `Info.plist`
bytes aren't published (they were generated on the Mac). The captures record its four fields and the
`codesign -dvv` output: `adhoc,linker-signed`, TeamIdentifier not set, Info.plist not bound. The dev
binary isn't published; its capture-recorded `c36894dc…` equals the pinned raw counterpart. No Mac
access was used.

**Kept private** (`/tmp/approved-otp23-privacy/mac-m11-dev-2026-10-29/`, `700`/`600`; sha256 in
`provenance.json`):

| file | sha256 | why |
|---|---|---|
| `m11-driver.log` | `16550c0f…` | Chrome keychain/password/allocator/GPU-adapter noise interleaved with the stdout summary |
| `m11-driver-attempt2.log` | `0cec52b5…` | same |
| `m11-driver-attempt3.log` | `058493a5…` | same |
| `capture-with-listing.invalid-mixed-stream` | `3dc1f88d…` | invalid mixed stream |

**Transport duplicates, not republished:**
- `all-attempts.tgz` `3245a664…`: all 7 members byte-equal to the published files.
- `capture.tgz` `ab61ad30…`: its member equals the published attempt-1 capture.
- `owner-answer.json`, `owner-answer-attempt3.json` and `prompt-request.json` equal published files.

All tar members are name-clean.

**M-11 facts, read from the captures and unchanged:**

| attempt | result | caveats |
|---|---|---|
| 1 | setup FAIL: the relative manifest `path` (from `--stage .`) gave a synthesized `port_closed` | not an OS rejection |
| 2 | `hello ready`; `scan_failed/permission_pending` (NotDetermined semantics) | deadline at 720.9 s, no answer accepted |
| 3 | `hello ready`; `scan_state started` → `authorized_started` (inferred; no enum or trace) | deadline at 3600.3 s, no answer accepted |

- **Packager:** it exited 126 when run directly; the same bytes then ran via `bash`.
- **Signed fixture:** `e7c716c5…`, strict verify OK before and after every attempt.
- **Registration:** only the owned dev manifest; unregistered after every attempt, and profiles
  removed.
- **Dev grant:** persists.
- **Not claimed:** no advertising and no discovery claim. No TCC reset, no M-04 toggle, and no
  inference about the original prompt.

The future-run notes (absolute `--stage`/`--out-dir`, `bash` packager, answer before the window
closes, self-recorded sha) are **public notes only**. The as-run bytes are immutable.

## 2. NEW `evidence/mac-integration-2026-10-29/`

`SHA256SUMS` is `7bb6af166ab30fae870b408de858f2255f6e5eee9ad2ec1ebde8b884101ead3c`. It has 26 entries,
the disk set equals the manifest, and the folder has its own `.gitattributes`.

**Reports (16 exact copies plus 3 redacted public copies):**

| report | sha256 |
|---|---|
| preflight-opus | `56f7d6a8` |
| parent-preflight-decision | `32623fdc` |
| code-result (GLM) | `96908492` |
| fix-result | `84d5c108` |
| fix implementer metadata | `edb62df1` |
| fix-verification-opus | `34935220` |
| retake-result | `9715522d` |
| physical-review-opus | `0a4275a2` |
| A1 attribution-result | `5ff12cf6` |
| final-data-review-opus | `a7f6f3ca` |
| safe-analysis-parent-decision | `6e68fc0e` |
| A2 safe-attribution-result | `b92dba90` |
| m11-preparation | `93b26cbc` |
| m11-loop-fix-result | `3d8f5f21` |
| m11-loop-verification-opus | `ee5cf425` |
| **REDACTED** `otp23-mac-resume-result.public.md` | `3aad04ca`; private original `a9ec52c7`; another local user's and a foreign app's names |
| **REDACTED** `otp23-mac-resume-human-action.public.md` | `a7fce0e7`; private original `c41d0d2a` |
| **REDACTED** `otp23-mac-m08-code-verification-kimi.public.md` | `229a9a6f`; private original `288dcd7f`; unrelated applications' NM manifest names |

Each redacted copy carries a leading comment and is otherwise byte-identical. The originals are in
`/tmp/approved-otp23-privacy/mac-integration-2026-10-29/` (`700`/`600`).

**`README.md`** (`b78638a8`) indexes every report with its author/session, role, as-of UTC and
scope. It marks the superseded content explicitly:
- the preflight and physical-review 16-byte premise
- A1, superseded and pointing to the source history
- the runbook's `--stage .` defect
- A2, review pending

**`route/`** (exact bytes):

| file | sha256 | notes |
|---|---|---|
| `otp23-mac-resume-owner-record.json` | `6f0c4e83` | |
| `otp23-mac-kimi-reviewer-approval.json` | `de3d8fd6` | 2026-10-28, "Use kimi k3" |
| `otp23-mac-opus-continuation-owner-record.json` | `7b223d05` | date **2026-10-29**; the parent corrected it from a mistyped 2026-10-28 before publication |

**`model-metadata.json`** (`07e8930a`) holds whitelisted structured fields only. It has no messages,
result texts, usage bodies, headers or error bodies.

From the opencode DB, read-only, by `modelID`/`providerID`:

| session | model | messages | role |
|---|---|---|---|
| `ses_e7cde50b…` | glm-5.3-flash/ollama-cloud | 257 assistant msgs, 2026-10-28 15:29–17:59Z | implementer |
| `ses_e7c65ccb…` | kimi-k3/ollama-cloud | 45 msgs, 17:41–17:49Z | reviewer |
| `ses_e8393db6…` | gpt-6.1-sol/openai | 49 msgs in the M-11 window | parent and M-11 controller |

From the Claude Code envelopes (`session_id`, model keys, `is_error`, `terminal_reason`,
`num_turns`, `subagents_spawned`):

| session | role(s) |
|---|---|
| `591fbc41` | availability READY |
| `1b447229` | preflight; then a code-verification **429**, so it approved nothing |
| `752e1974` | implementer: fix, retake + loop fix, A1, A2 |
| `7b4ee6ca` | fix micro-verification, physical review, loop verification; one causal-premise attempt **429** |
| `e30e474d` | final data review |
| `c401a87c` | M-11 operator attempt, **429 before any tool**, no model usage |

Every session spawned 0 subagents. The route GLM → Kimi → Opus is stated with its records.

**`path-map.json`** (`aca217f2`) resolves all **41** `/tmp` and `/home` references cited inside both
new folders. **None is unresolved**, and every "published" target exists.

| status | count |
|---|---|
| published | 17 |
| published-redacted | 3 |
| published-as-run-code | 1 |
| published-identical-bytes | 1 (the deferral record = committed `package1-b5-v4-20261028/mac-deferral-2026-10-28.json`, `ace5e31e…`) |
| not-published, with a reason (task prompt, 0-byte stderr, session event log, APK, reviewer and implementer scratch, transient temp dirs, implementer transcript) | 13 |
| private archive | 4 |
| private API envelope (whitelisted) | 1 |
| workspace root | 1 |

**`curation-map.json`** (`9f16ad26`) lists the exact copies with sha256 and the redactions as public
sha ↔ private-original sha.

## 3. Checks run

- `sha256sum -c` on both new folders and all six frozen/history folders: all OK. The disk set equals
  the manifest for both new folders.
- 12 M-11 raw/record copies are byte-identical to their sources, and all 8 tar members are
  byte-equal to the published copies.
- **Name sweep:** the other user's account, the foreign app, and unrelated app/manifest identifiers
  (<other applications' manifest identifiers>)
  have **0 hits** in both new folders and the tar members. This is not a repository-wide
  privacy-absence claim: the earlier finding about committed `76a70e4` content still stands, and
  prior frozen history is not rewritten.
- **Existing resume curation, re-verified:**
  - 35 active entries (`9e8ed077…`)
  - the archived original is 32 lines over 31 distinct paths (`.gitattributes` listed twice)
  - 30 non-narrative paths are byte-identical in the active manifest; only `FINDING.md` differs
    (private original `2518c163…`)
  - private archive tgz `6564c38e…`
- `git diff --check` is clean, and no tracked file was changed by this curation.

## 4. Limits and what remains

- **Infrastructure detail for you to decide on:**
  - Two published reports (the preflight, and the Kimi public copy) mention the SSH key **filename**
    and the Mac's LAN address.
  - Both strings are already in committed HEAD evidence (`post-grant-2026-10-26/strict-mac-20261027/…`)
    and in the reviewed `mac_clock_corroborate.py`.
  - Neither is a secret (no key material), so I left them unredacted and am flagging them rather than
    editing silently.
- **A2 review:** A2 and its outputs await the independent micro-review that is running now. The
  integration README marks A2 "review pending".
- **M-11 adjudication:** the M-11 adjudication is a **draft**. It needs an independent review of the
  account record and the draft before any row use.
- **At canonical integration:** run the root Gradle gates (`spotlessCheck detekt lintDebug`, the
  Log guard), the full Python suite, and the commit and PR flow.
- **Private storage:** move the private archives under `/tmp/approved-otp23-privacy/` to durable
  private storage.

STOPPED. No commit, push, GitHub action or row promotion.
