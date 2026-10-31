# F-2 provenance comparison — after-run read-only Mac capture + live-repo/approved-fixture comparison

Captured 2026-10-27T17:18–17:26Z by the finalize session (ses_e81bc7103ffe3F21OMCsqIRppL,
glm-5.3-flash/ollama-cloud), READ-ONLY over ssh/adb; the Mac was awake; nothing was modified on the Mac or
in any repo evidence file. Companion raw artifacts: f2-run-tree-driver-strict_background.py,
f2-run-tree-evaluator-strict_background_core.py (byte-identical scp fetches of the files that ran),
f2-run-stdout-logs.txt, f2-macos-host-bundle-codesign.txt,
f2-register-manifest-launch-teardown-capture.jsonl (verbatim parent-transcript records with line numbers
and the transcript's full-file sha256).

## What actually ran (run tree ~/homepc/work/otp23-glm-dc5d127-cal/run/)

The launch record (parent transcript, 16:04:16Z and 16:12:01Z/16:28:54Z events, copied verbatim into
f2-register-manifest-launch-teardown-capture.jsonl) shows the parent copied
`strict_background.py` + `strict_background_core.py` from the cal-dir root into `run/scripts/`, ran
py_compile, and registered `me.nagaev.veles.otp23.plain` → the approved fixture executable — then launched
`/usr/bin/python3 scripts/strict_background.py --scenario cold|prewarm --out evidence/strict-mac-20261027-*.json`.

| run-tree file | sha256 (what ran) | live repo | equal? |
|---|---|---|---|
| scripts/macos_background_support.py | `e02062c3edfca461…` | `e02062c3edfca461…` | ✅ byte-identical |
| scripts/macos_cdp.py | `e962bcc94123faa6…` | `e962bcc94123faa6…` | ✅ byte-identical |
| scripts/macos_install.py | `fcc8604afc0c8662…` | `fcc8604afc0c8662…` | ✅ byte-identical |
| scripts/macos_poc.py | `c3217afb3d956df1…` | `c3217afb3d956df1…` | ✅ byte-identical |
| scripts/strict_phone_validation.py | `93057ec40c895294…` | `93057ec40c895294…` | ✅ byte-identical |
| scripts/strict_background.py (driver) | `6c45f3765d68459b…` | `478b836c444fa194…` | ❌ differs (see below) |
| scripts/strict_background_core.py (evaluator) | `1576c370f47b1aeb…` | `e44f616c04054707…` (F-1-fixed) | ❌ differs (see below) |
| extension/background.js | `2d8a048a914e7432…` | `2d8a048a914e7432…` | ✅ byte-identical |
| extension/connector.html | `d43f1b59c764753d…` | `d43f1b59c764753d…` | ✅ byte-identical |
| extension/connector.js | `c1c33331611a24ca…` | `c1c33331611a24ca…` | ✅ byte-identical |
| extension/manifest.json | `6cc440fe2a2b58f0…` | `6cc440fe2a2b58f0…` | ✅ byte-identical |
| src/identity.rs | `c6d5ee243c5285ef…` | `c6d5ee243c5285ef…` | ✅ byte-identical |
| **host executable** run/target/…/otp23-host | `e7c716c5d8c9c05a…` | approved fixture (post-grant-identity.json) | ✅ **equal to the approved fixture** |

The cal-dir-root files the parent copied at 16:04:16Z hash EQUAL to the run-tree files
(`strict_background.py` `6c45f376…`, `strict_background_core.py` `1576c370…`), so the launched bytes are
pinned end-to-end. Remote file mtimes: run-tree driver+core 16:10:55Z, run start 16:11:53Z (55 s later);
live repo driver mtime 16:13:31Z, core 16:14:36Z — i.e., the live files changed AFTER the runs' launch, so
the run tree snapshot predates them.

## The two differing files — enumerated by exact diff (run-tree → live)

* driver `strict_background.py`: (1) the docstring's untested login-session claim reworded to the
  calibrated SSH-context statement (preflight MINOR); (2) `CGSessionCopyCurrentDictionary` conversion now
  releases `data_ref`/`dict_ref` in a `finally` and uses `c_long` for `CFDataGetLength` (preflight-2 MINOR
  ctypes hygiene); (3) the verdict call changed from `core.evaluate(evidence)` to
  `core.evaluate(evidence, platform=PLATFORM)`. All three are post-run; (1)/(2) do not change recorded
  evidence semantics; (3) is exactly the trusted-platform wiring F-1 mandates for future runs.
* evaluator `strict_background_core.py`: the preflight-2 Q-1 fix — `evaluate(evidence, platform=…)` /
  `_foreground_check(fg, platform=…)` takes the TRUSTED top-level platform, new-schema platform-missing or
  unknown → BLOCKED, legacy (no platform key, no page_state_guard key) stays audit-only, and macOS
  additionally requires recorded session dictionaries (verified/on-console/unlocked) and page-state
  guards at both sample points; plus the calibration-raw pointer in the docstring.

Both differences are post-run ADJUDICATION-STRENGTHENING changes (fail-closed direction); neither rewrites
any recorded run evidence. The runs' OWN recorded ST-15 verdict was computed by the run-tree evaluator
(1576c370, pre-Q-1: page guard required via the self-recorded `platform: "macos"`); the post-hoc
adjudications v1/v2 re-evaluate with the current evaluator (v2 with the trusted top-level platform) —
that lineage is exactly what `evaluator_provenance` in each adjudication records.

## Host bundle (approved fixture) — after-run codesign capture

`f2-macos-host-bundle-codesign.txt` (raw capture): the run-dir bundle's `otp23-host` sha256
`e7c716c5d8c9c05ac8aaa838d5bdbdcc0918f352c270cb330d8edb5befeeb91c` = the approved fixture value recorded in
post-grant-identity.json (macos.host_sha256); `codesign -dvv`: Identifier `me.nagaev.veles.bridge`,
`TeamIdentifier=7T39M26Z99`, `Authority=Apple Development: yuriy@nagaev.me (ZWYYS8X98S)`, signed 26 Oct 2026
13:40:26; `codesign --verify --strict -v`: "valid on disk", "satisfies its Designated Requirement",
exit 0. The bundle Info.plist `7d4efdc7580079b9…` equals the value recorded in the parent's identity
capture; codesign's Executable= resolves to the `~/homepc/work/otp23-cd971f0/...` original path (the
run-target tree references/serves the same signed bundle; the executable bytes hash equal either way).

## The Native Messaging manifest — what the runs used

* The GLOBAL manifest (`/Users/raiden/Library/Application Support/Google/Chrome/NativeMessagingHosts/me.nagaev.veles.otp23.plain.json`)
  was created at 16:04:16Z (parent transcript record: `"created new manifest"`) pointing at
  `~/homepc/work/otp23-cd971f0/.../VelesOtp23PoC.app/Contents/MacOS/otp23-host` with
  `executable_sha256 e7c716c5…b91c`, `allowed_origins chrome-extension://gmekhgkipjkikmaadaajljinkgfielei/`,
  `type stdio`; the FULL manifest content (name/description/path/type/allowed_origins) is in the captured
  record; a REGISTRATION_VALID re-read followed at 16:04:28Z.
* TODAY'S runs launched against THAT manifest (registered 13 min before the cold run; the teardown record
  at 16:41:50Z `"unregistered": true` + the current NM dir listing (no veles manifest; only unrelated
  1Password/Claude/Drive entries) close the loop: registered-before → used → removed-after, no
  veles host registered now).
* The per-profile manifests are NOT retained: the dedicated temp profile dirs
  (/var/folders/qy/.../veles-otp23-macos-profile-zpx00edd cold, -spsx7gkn prewarm) no longer exist on disk.
  The profile-scoped manifest path therefore could not be captured after the run; the run-time manifest of
  record is the user-scope global manifest above, captured at registration time in the parent transcript.
* Older identity captures preserved with their eras: otp23-mac-identity.log (26 Oct, `aa242c4deb…`
  executable for me.nagaev.veles.bridge in otp23-4e9c685 — a PRE-final build, NOT the one registered for
  these runs) and otp23-post-grant-mac.log (26 Oct 16:14, `e7c716c5…` cd971f0 registration). Both remain
  untouched in /tmp/opencode; neither was modified by the finalize session.

## Material-discrepancy verdict (F-2)

No blocker: the run tree's every runtime module except the two enumerated post-run files byte-matches the
reviewed live source; the two differing files changed only in the fail-closed/strengthening direction
after the runs; the host binary is byte-equal to the approved signed fixture with a strict codesign
verification; and the used manifest is on record from registration to teardown. The run-tree code state is
additionally hash-pinned here (the two fetched files). The provenance gap Opus F-2 identified is CLOSED
without any rerun.