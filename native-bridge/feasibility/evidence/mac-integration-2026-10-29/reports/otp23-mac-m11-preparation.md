# OTP-23 M-11 preparation — exact fixture/host/manifest/provenance, commands, expected human step
(2026-10-28 code-phase prep, operator: ollama-cloud/glm-5.3-flash, 0 subagents — NOTHING launched, registered, packaged, or touched TCC; the Mac lease stays held for the parent and was not ended)

Read first: `/tmp/opencode/otp23-mac-permission-owner-record.json` — M-11 owner approval verbatim
"Approve separate dev test" (separate developer host, distinct identity + Native Messaging name,
disposable Chrome profile, actual automatic ad-hoc signature recorded, no substitution for the
approved signed fixture); M-04 permission toggles NOT authorized (`m04_permission_toggles_authorized:
false`); M-03 original prompt owner NOT observed (owner's read-only Settings label check = "VelesOtp23PoC").

## 1. Private NEW Mac stage (outside the approved fixture, unique M-11 root)

`/Users/raiden/homepc/work/otp23-m11-dev/` — created fresh (asserted non-existent), contents staged
and hashed in `stage-manifest.txt` (on the Mac):

| file | sha256 |
|---|---|
| `README-M11-PREP.md` (full plan + runbook) | `f7316eb90b96cc47633876ef4687708b1ea0165c6f663064c40b72ad541b6215` |
| `package-dev.sh` (dev bundle packager; NO signing command exists in it) | `fa6ba4e87febcae219dc46d868deb8e914a04cd0e4e9b7b94f6f93c8a203abb4` |
| `m11_dev_capture.py` (capture driver; never auto-clicks) | `833a0f52c9187a182a24b58c5837066aa696a9da6db4ef9a700e672cfb2f2ab8` |
| `extension/manifest.json` | `6cc440fe2a2b58f0e0de31192607636bd7d89ce653f0dd4c7e4435a3e8605c04` |
| `extension/background.js` | `2d8a048a914e74328869cd0727f29ed26488aa6d45ba0ae7d204b1ca129deb0b` |
| `extension/connector.js` | `c1c33331611a24ca95405b305ee80a69beb3904e228056d6c268e9d4f3b7e2ac` |
| `extension/connector.html` | `d43f1b59c764753d2c9ce4b5fbc47e1ea211420cd83a5d1501fd293e7a42d4aa` |
| empty dirs `source-raw/`, `capture/` | (reserved) |

Pre-state recorded (read-only, prep time): NO veles/otp23 manifest exists in the user-scope
NativeMessagingHosts dir (fresh; also no plain manifest — known post-M-08 state); no otp23
processes; approved fixture `codesign --verify --strict` OK.

## 2. Approved signed fixture — UNTOUCHED (re-verified read-only at prep time)

- Path `/Users/raiden/homepc/work/otp23-cd971f0/native-bridge/feasibility/target/macos-poc/VelesOtp23PoC.app`
- Binary sha256 `e7c716c5d8c9c05ac8aaa838d5bdbdcc0918f352c270cb330d8edb5befeeb91c`
- `codesign --verify --strict` OK; Identifier `me.nagaev.veles.bridge`; Apple Development authority
  chain (Apple Development: yuriy@nagaev.me (ZWYYS8X98S) → Apple Worldwide Developer Relations CA →
  Apple Root CA); CDHash `25bc7fc25e48c4470d4c3765b6a5f9b719ff381d` (published in M-03 evidence;
  identity re-read today); Settings label per owner read-only check: `VelesOtp23PoC`.
- The capture script REFUSES to run if this identity drifts at start, and re-verifies byte +
  strict-codesign identity after the run (script-enforced before/after readback).

## 3. Unsigned dev counterpart — EXISTING raw preferred, source-verified (no new build, no Apple key)

- Raw binary: `/Users/raiden/homepc/work/otp23-cd971f0/native-bridge/feasibility/target/release/otp23-host`
  sha256 `c36894dca9d47717410cf833ca4f181894d1144e48bc0f301232a2be307864ea` — this exact hash is the
  "unsigned raw host" published beside the signed `e7c716c5…` in the M-03 row (same packaging build).
- Source verification done TODAY: all 24 files under cd971f0 `src/**` plus `Cargo.toml`
  `c92e382f…`, `build.rs` `15b0b79f…`, `Cargo.lock` `e50ce3bd…` on the Mac are byte-identical to
  the repo's pinned `cd971f0` tree (`git show cd971f0:<path>` — full 24+3 comparison, zero
  mismatches). Same source ⇒ valid M-11 counterpart; no plain build was needed; NO Apple
  Development codesign and NO manual ad-hoc signing is applied to anything.
- Its signature status recorded truthfully (Apple Silicon linker automatic ad-hoc;
  `codesign -dv`: `Identifier=otp23_host-b86638eeb61c91b4, Mach-O thin (arm64)`) — recorded, never
  created or replaced; the approved fixture is never ad-hoc re-signed or touched.
- `target/debug/otp23-host` (`75a1e178…`) exists as a second raw candidate; NOT used (release raw
  is the counterpart of the packaged fixture build).

## 4. Dev bundle (distinct identity) — produced later by `package-dev.sh` (NOT run yet)

- Output: `~/homepc/work/otp23-m11-dev/target-dev/VelesOtp23Dev.app` (new target-dev dir; refuses
  to overwrite an existing bundle; refuses any raw binary whose sha ≠ `c36894dc…`).
- `CFBundleIdentifier = me.nagaev.veles.bridge.dev` (distinct from `me.nagaev.veles.bridge`),
  `CFBundleName = VelesOtp23Dev`, executable `otp23-host`, `LSUIElement`, versions 0.1.0/1,
  minimum macOS 13.0 — SAME plist shape as the approved fixture with ONLY identifier/name delta;
  `NSBluetoothAlwaysUsageDescription` / `NSBluetoothPeripheralUsageDescription` are verbatim copies
  of the approved fixture's strings ("usageDescriptions normal").
- No codesign step; the script only RECORDS (`codesign -dvv`, read-only) the automatic linker
  ad-hoc signature + prints a JSON provenance record (raw sha, bundle sha, plist fields,
  `codesign_commands_run: []`).

## 5. Native Messaging host manifest + extension delta

- Dev host name: `me.nagaev.veles.otp23.dev` (distinct from `…plain` and `…tauri`; never collides
  with any approved identity). Host binary validation is UNAFFECTED (the host binary checks only
  the passed origin; `HOST_NAME_PLAIN` is a driver-side constant, invisible to the binary).
- Dev manifest document (written into the disposable profile by the capture script, default layer):
  `{"name": "me.nagaev.veles.otp23.dev", "description": "Veles OTP-23 M-11 dev host; synthetic
  bytes only", "path": "<stage>/target-dev/VelesOtp23Dev.app/Contents/MacOS/otp23-host",
  "type": "stdio", "allowed_origins": ["chrome-extension://gmekhgkipjkikmaadaajljinkgfielei/"]}` —
  EXACT single origin, unchanged extension id `gmekhgkipjkikmaadaajljinkgfielei` (manifest `key`
  unchanged).
- **Extension delta vs approved source: ZERO bytes** — the stage `extension/` is byte-identical to
  repo HEAD (hashes above match the M-08 pins exactly). The host name is a pure connect parameter
  (`connector.js:121-127` → `chrome.runtime.connectNative(hostName)`); the capture script passes
  `otp23.connect("me.nagaev.veles.otp23.dev")`. The ONLY delta anywhere is the dev registration
  manifest (name + path). No shared repo extension/driver/M-08 file was edited (repo is untouched
  this phase: working tree = exactly the frozen M-08 delta).
- Registration layers (precise; "per-user" is NOT truly profile-local): LAYER A (default)
  PROFILE-LOCAL `<profile>/NativeMessagingHosts/me.nagaev.veles.otp23.dev.json` — dies with the
  disposable profile, zero user-scope writes; LAYER B (opt-in `--user-scope-manifest`) writes the
  DISTINCT user-scope filename `me.nagaev.veles.otp23.dev.json`, ownership recorded, and at
  teardown ONLY this harness's own file is removed (byte-verified before unlink; anything else
  left alone). Later restore scope = own registration only, nothing else.

## 6. Commands the parent runs AFTER review approval (still not executed)

```bash
cd /Users/raiden/homepc/work/otp23-m11-dev
cp -p /Users/raiden/homepc/work/otp23-cd971f0/native-bridge/feasibility/target/release/otp23-host source-raw/otp23-host
./package-dev.sh source-raw/otp23-host
python3 m11_dev_capture.py --stage . --out-dir capture/m11-attempt-1        # unique dir, never --overwrite
# optional user-scope layer: add --user-scope-manifest
```

## 7. Capture design + the exact expected human step (fresh nonce)

The script: fixture identity gate → registration-state read → disposable profile + profile-local
dev manifest → Chrome launched with the SAME disposable-profile CDP arguments the frozen M-08
driver uses (`--user-data-dir=<mkdtemp> --remote-debugging-port=0 --enable-unsafe-extension-debugging
--no-first-run --no-default-browser-check --disable-sync --window-size=1000,700 about:blank`) →
`Extensions.loadUnpacked` (expected id `gmekhgkipjkikmaadaajljinkgfielei`) → connector page →
`otp23.connect("me.nagaev.veles.otp23.dev")` → `otp23.hello()` (host launches; NO Bluetooth touch)
→ `startScan` sent NON-BLOCKING (first Bluetooth-touching event; the OS prompt appears HERE if
tccd needs one) → prints `PROMPT-WATCH-REQUESTED nonce=<nonce> answer_file=…` and writes
`capture/<attempt>/m11-prompt-<nonce>.request.json` → polls (default 12 min, bounded) for the scan
reply / host-trace `authorization`/`adapter init` lines / the answer file → classified outcome →
stopScan-if-started → `otp23.shutdown()` or clean close → teardown (profile removed; optional
user-scope own manifest removed byte-verified) → `m11-capture.json` + `m11-dev.host-trace.log` +
all readbacks saved.

HUMAN STEP (never automated): when `PROMPT-WATCH-REQUESTED nonce=<nonce>` appears, the parent asks
the owner, with that nonce: **"Read the EXACT name the Bluetooth permission dialog shows — which
app is asking — and report it verbatim FIRST; do not click yet."** Then the OWNER chooses Allow or
Deny themselves; the parent records both verbatim into `answer_file`
`{"prompt_name_shown": "<verbatim>", "choice": "allow|deny", "answered_by": "owner"}`; the script
binds it to the nonce in `m11-capture.json`. The M-11 observation is exactly which app the dialog
names (Chrome vs the dev binary) — read by the owner, never inferred from Settings, never assumed.

A DENIED/pending outcome is a valid M-11 observation: recorded
(`scan_error:access_denied:…` class or `authorizing_via_dialog_pending_reply`), never rewritten
into an unsigned scan PASS; the reviewer adjudicates the row.

## 8. Comparison with the approved signed fixture (before/after, read-only)

Baseline (no approved-fixture re-run — no toggles authorized): the approved fixture's historical
scan/authorization evidence (`…/feasibility/evidence/macos-poc-authorized-scan.json` in the cd971f0
tree) plus the recorded post-grant states; identity/grant readback = approved bundle sha + strict
codesign before AND after (script-enforced) + owner's read-only Settings re-check after the dev
run (`VelesOtp23PoC` must be unchanged; a NEW `VelesOtp23Dev` entry would be a recorded fact,
never removed).

## 9. Constraints honored at prep time and for the planned run

M-08 code/tests/evidence left FROZEN (repo working tree unchanged this phase — 4 modified + 4 new
files exactly as in `/tmp/opencode/otp23-mac-m08-code-result.md`); no Chrome launch, no dev-host
run, no CB init, no TCC prompt, no registration write, no build, no fixture overwrite, no phone
work, no personal-Chrome control, no commit/push/GitHub/canonical PASS. Mac lease remains held by
the parent and was NOT ended; no device work was performed by this phase beyond read-only SSH
checks. Everything above is reproducible from the staged hashes; the parent may run the exact
commands in §6 once the M-08 review approves.
---

## 10. APPENDED 2026-10-29 — reviewer F-M11-1 correction + pre-launch fixes (Opus 5.5 implementer continuation; §1–§9 above kept verbatim as history)

§5's "LAYER A (default) PROFILE-LOCAL" model and §6's optional `--user-scope-manifest` are
**superseded**. Chrome on macOS discovers native-messaging manifests ONLY in
`/Library/Google/Chrome/NativeMessagingHosts/` and
`~/Library/Application Support/Google/Chrome/NativeMessagingHosts/`. There is no `--user-data-dir`
location, and profile-local discovery was never measured. The only relied-upon layer is the
distinct ephemeral per-user `me.nagaev.veles.otp23.dev.json` (exact single origin
`chrome-extension://gmekhgkipjkikmaadaajljinkgfielei/`, disposable profile, byte-verified own-file
cleanup). `--user-scope-manifest` is now REQUIRED; a run without it is rejected by argparse before
anything is created or launched.

Superseding §6 command (still NOT executed; only after the three fresh signed M-08 retake scenarios):

```bash
cd /Users/raiden/homepc/work/otp23-m11-dev
cp -p /Users/raiden/homepc/work/otp23-cd971f0/native-bridge/feasibility/target/release/otp23-host source-raw/otp23-host
./package-dev.sh source-raw/otp23-host
python3 m11_dev_capture.py --stage . --out-dir capture/m11-attempt-1 --user-scope-manifest
```

Additional pre-launch fixes in `m11_dev_capture.py`:
- `APPROVED_APP` (a `str`) was used as `APPROVED_APP / "Contents/MacOS/otp23-host"`, which raised
  `TypeError` at the first precondition and again in `finally`. The script could never have run and
  wouldn't have written its record (fail-closed: nothing was launched or registered). This was
  pre-existing in the reviewed `833a0f52`. Fixed with `pathlib.Path(APPROVED_APP) / …` at both sites.
- Before any manifest write or launch, the dev `Info.plist` must read
  `CFBundleIdentifier=me.nagaev.veles.bridge.dev`, `CFBundleName=VelesOtp23Dev`, and have no
  differing `CFBundleDisplayName`, otherwise the script refuses. The approved fixture has no
  `CFBundleDisplayName`, and its label `VelesOtp23PoC` comes from `CFBundleName` (read-only Mac
  check), so this is the same key with only the name changed. `package-dev.sh` is unchanged.

Superseding §1 stage table (on the Mac; `stage-manifest.txt` sha256
`d3c2523255253afde1bfcbe1bcff068a12283b0fe7d2759cb940248909d34b1d`):

| file | sha256 |
|---|---|
| `README-M11-PREP.md` | `3942a5963ab25f454026626949b2f7b45f2d1204533d4c67b3fe17babf1aa2c7` |
| `package-dev.sh` (unchanged) | `fa6ba4e87febcae219dc46d868deb8e914a04cd0e4e9b7b94f6f93c8a203abb4` |
| `m11_dev_capture.py` | `3e7be057a2b87351b30f9f4f79c8d1339bdecb24623651054e2e7766760b6b3b` |
| extension ×4 (unchanged) | `6cc440fe…` / `2d8a048a…` / `c1c33331…` / `d43f1b59…` |

History preserved on the Mac in `_pre-fix-2026-10-28/` (its own `SHA256SUMS`): pre-fix
`m11_dev_capture.py 833a0f52…`, `README-M11-PREP.md f7316eb9…`, the old `stage-manifest.txt`
`111b3947f2c121d9290b7da742b2ac7ea78c62f35df01f20ef512193b869ed01`, and the interrupted GLM
session's stray partial upload (it had landed in `~/homepc/work/` root) as
`glm-interrupted-partial-upload-2026-10-28-m11_dev_capture.py 8b804fd9…`. `stage-manifest-history.txt`
records both manifest generations. Nothing was launched, registered, built, signed, or packaged.

## 11. APPENDED 2026-10-29 — M11-1 prompt-watch loop fix (+N1/N2/N3); still NOT executed

The independent micro-verifier (`otp23-mac-m08-fix-verification-opus.md`, finding M11-1) found that
the loop ended on the host's pre-init trace line. That shut the host down within about 2 s and never
bound the owner's answer. Fixed in `m11_dev_capture.py`:
- trace authorization lines are observations, not terminal events;
- the host and port stay alive until the owner's nonce-bound answer AND a post-answer scan reply
  (or an authorized started scan) exist, or the deadline passes;
- the host's 10 s `permission_pending` reply means "pending at host budget", never a denial;
- after the answer, at most 2 rescans observe the actual post-answer result;
- classification uses the final reply plus the post-init `adapter init: … authorization=` line;
- "no dialog" is allowed only as the owner's own `dialog_observed: false`;
- N1: refusals are recorded in `problems`;
- N3: a missing approved app no longer drops the capture;
- N2: the README order is fixed.

Offline regression: `test_m11_prompt_watch.py` (8 tests: fake-clock loop + stub Chrome/CDP `main()`)
passes locally (Python 3.14) and on the Mac (3.9.6).

Mac stage `stage-manifest.txt` is now `b19073483d68d74f4c953f0814f1cd7be7fdcdbc515e971ef69e8e2437aaf53c`.
The prior generation (`d3c25232…`, with `m11_dev_capture.py 3e7be057…` and `README 3942a596…`) is
archived in `_pre-m11-1-2026-10-29/` with its own `SHA256SUMS`:

| file | sha256 |
|---|---|
| `m11_dev_capture.py` | `976f61cf038a3d46543bf03a80e8ae3b55a83b9516519d631e31e50f7a40e02d` |
| `README-M11-PREP.md` | `93514f0b5ebb2eb8dc4f9beb3c22e8b800a4906632243ac5b6fbe2213606722c` |
| `test_m11_prompt_watch.py` (new) | `50aa32eba131962738627c198572721c3daa70a4702954df6f5ef0aa38776478` |
| `package-dev.sh` (unchanged) | `fa6ba4e8…` |
| extension ×4 (unchanged) | `6cc440fe…` / `2d8a048a…` / `c1c33331…` / `d43f1b59…` |

Run command unchanged (`--user-scope-manifest` required). M-11 needs another independent
micro-verification before execution, and the owner's live prompt choices must be coordinated
with them.
