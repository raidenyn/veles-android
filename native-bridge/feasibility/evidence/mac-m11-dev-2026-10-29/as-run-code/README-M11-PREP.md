# OTP-23 M-11 preparation (private stage; 2026-10-28 code-phase prep, NOTHING executed)

Status: STAGED ONLY. No Chrome launch, no dev-host run, no CoreBluetooth init, no TCC prompt, no
registration write. The parent executes the two commands below ONLY after the independent review
approves, because the owner's M-11 approval ("Approve separate dev test",
/tmp/opencode/otp23-mac-permission-owner-record.json) covers the separate dev host with a distinct
identity/name — not an M-04 toggle and not any GitHub/canonical action.

## 1. What this measures (M-11 canonical criterion, existing admin account)

"The unsigned dev-registered host is launched by Chrome from a disposable profile; its TCC
authorization and scan result are recorded and compared with the signed fixture (M-03..M-06);
account class recorded truthfully." A DENIED or pending outcome is a valid classified observation
for the reviewer's actual adjudication — never rewritten into a fabricated unsigned scan PASS.

## 2. Distinct unsigned dev identity (no Apple Development codesign, no ad-hoc substitution)

- Source: the EXISTING raw unsigned counterpart of the SAME build as the approved signed fixture —
  `/Users/raiden/homepc/work/otp23-cd971f0/native-bridge/feasibility/target/release/otp23-host`,
  sha256 `c36894dca9d47717410cf833ca4f181894d1144e48bc0f301232a2be307864ea` (this exact hash is the
  "unsigned raw host" cited beside the signed `e7c716c5…` in the M-03 row; same packaging build).
  Source verified TODAY: all 21 files under cd971f0 `src/**` + the 3 manifest files (`Cargo.toml`,
  `build.rs`, `Cargo.lock`) in the cd971f0 work dir are byte-identical to the repo's pinned
  `cd971f0` tree (`git show cd971f0:`).
  No new build was needed; no Apple key was touched; nothing is re-signed.
- Dev bundle (produced by `./package-dev.sh` — NOT yet run): NEW
  `~/homepc/work/otp23-m11-dev/target-dev/VelesOtp23Dev.app` with
  `CFBundleIdentifier = me.nagaev.veles.bridge.dev` (distinct from `me.nagaev.veles.bridge`),
  `CFBundleName = VelesOtp23Dev`, executable `otp23-host`, verbatim copies of the approved
  fixture's NSBluetoothAlwaysUsageDescription / NSBluetoothPeripheralUsageDescription, LSUIElement —
  i.e. the approved fixture's plist shape with ONLY the identifier/name delta. NO signing command
  exists in the script; the binary keeps Apple Silicon's AUTOMATIC LINKER AD-HOC signature, which
  the script records truthfully (`codesign -dvv` read-only, `Signature=adhoc` expected).
- Approved fixture `/Users/raiden/homepc/work/otp23-cd971f0/.../VelesOtp23PoC.app` (sha256
  `e7c716c5d8c9c05ac8aaa838d5bdbdcc0918f352c270cb330d8edb5befeeb91c`, ID `me.nagaev.veles.bridge`,
  Team `7T39M26Z99`, codesign --verify --strict OK, CDHash `25bc7fc2…`): re-verified byte/state at
  prep time; the capture script refuses to proceed if its identity drifts and re-verifies after.

## 3. Registration model (CORRECTED 2026-10-29 per reviewer F-M11-1)

Chrome discovers native-messaging manifests on macOS ONLY in the SYSTEM
(`/Library/Google/Chrome/NativeMessagingHosts/`) and USER
(`~/Library/Application Support/Google/Chrome/NativeMessagingHosts/`) locations; there is NO
`--user-data-dir` (profile) manifest location. The earlier claim that a profile-local manifest is
"the default" and "the same verified mechanism the frozen M-08 driver used" is WITHDRAWN: every
working M-08 macOS run had the USER-SCOPE `me.nagaev.veles.otp23.plain.json` present (the frozen
driver's `check_registration` requires it); profile-local discovery was never measured.

- THE ONLY RELIED-UPON LAYER: a USER-SCOPE manifest
  `~/Library/Application Support/Google/Chrome/NativeMessagingHosts/me.nagaev.veles.otp23.dev.json`,
  written ONLY with the now-REQUIRED `--user-scope-manifest` flag (the script rejects a run without
  it before creating anything or launching anything). Ephemeral and per-user: the DISTINCT filename
  can never collide with the approved harness's `me.nagaev.veles.otp23.plain.json`; the script
  refuses to start if a dev manifest already exists; at the end ONLY this harness's own dev file is
  removed (byte-verified against what it wrote before unlink; anything else is left untouched and
  reported).
- The disposable Chrome profile ALSO receives a copy under `<profile>/NativeMessagingHosts/`; it is
  recorded for completeness only, is NOT relied upon, and dies with the profile.
- Precisely why this is safe: the dev manifest's `name` (`me.nagaev.veles.otp23.dev`) and `path`
  (inside VelesOtp23Dev.app) differ from the approved manifest's name and path; `allowed_origins`
  is the EXACT single origin `chrome-extension://gmekhgkipjkikmaadaajljinkgfielei/` (the unpacked
  extension id comes from the unchanged manifest `key`, so the origin is unchanged). Registration
  state is read and recorded BEFORE (no dev manifest exists anywhere now; no plain manifest exists
  either — known post-M-08 state) and AFTER.

## 4. Extension: exact source, delta = NONE

`extension/` in this stage is the byte-identical copy of the repo HEAD extension files (sha256:
manifest.json `6cc440fe…`, background.js `2d8a048a…`, connector.js `c1c33331…`, connector.html
`d43f1b59…`). The host name is a pure CONNECT PARAMETER (`connector.js:121-127`:
`connect(hostName)` → `chrome.runtime.connectNative(hostName)`), passed by the capture script as
`otp23.connect("me.nagaev.veles.otp23.dev")` — so the "host-name parameter delta" against the
approved source is ZERO bytes; the only delta anywhere is the dev registration manifest
(name + path). No shared repo extension/driver/M-08 file is edited during the M-08 review freeze.

## 5. Commands (parent, after review approval — in order)

```bash
cd /Users/raiden/homepc/work/otp23-m11-dev
cp -p /Users/raiden/homepc/work/otp23-cd971f0/native-bridge/feasibility/target/release/otp23-host source-raw/otp23-host   # or pass the path directly
./package-dev.sh source-raw/otp23-host                                 # builds VelesOtp23Dev.app (no signing), records provenance
python3 m11_dev_capture.py --stage . --out-dir capture/m11-attempt-1 --user-scope-manifest   # REQUIRED; unique dir; never --overwrite
```

The capture script: verifies the approved fixture identity → records registration state → checks
the dev bundle's Info.plist (`CFBundleIdentifier = me.nagaev.veles.bridge.dev`, `CFBundleName =
VelesOtp23Dev`, no differing `CFBundleDisplayName`; refuses otherwise, with the refusal recorded in
`problems`) → builds the disposable profile → writes the user-scope dev manifest → launches Chrome ONLY with the same
disposable-profile CDP arguments the frozen M-08 driver uses → loads the unpacked private
extension (expected id `gmekhgkipjkikmaadaajljinkgfielei`) → `otp23.connect("me.nagaev.veles.otp23.dev")`
→ `otp23.hello()` (host launches; NO Bluetooth touch yet) → sends `startScan` NON-BLOCKING (the
first Bluetooth-touching event; the TCC dialog appears here if tccd needs one) → prints
`PROMPT-WATCH-REQUESTED nonce=<nonce> answer_file=<path>` and writes
`capture/<attempt>/m11-prompt-<nonce>.request.json` → keeps the host and port ALIVE and polls
(default 12 min). Host-trace authorization lines are recorded as observations only; the first one
("before adapter init") is not terminal. A scan error from the host's 10 s adapter budget
(`permission_pending`) while the dialog is open is recorded as pending, NOT as a denial. The loop
ends only when the owner's nonce-bound answer file exists AND an actual scan reply was observed
after it (or an authorized started scan exists), or at the deadline. After the answer it issues at
most 2 new scans to observe the actual post-answer authorization/scan result. Classification uses
the final scan reply plus the post-init `adapter init: … authorization=` line, never the pre-init
line → stopScan for every started scan → `otp23.shutdown()` or browser close → teardown: disposable profile
removed, user-scope dev manifest removed (only if byte-identical to what this harness wrote),
approved fixture re-verified, `m11-capture.json` + `m11-dev.host-trace.log` saved.
No click is ever automated: the dialog is closed by the OWNER.

## 6. Expected human step (fresh nonce; required before Allow/Deny)

When `PROMPT-WATCH-REQUESTED nonce=<nonce>` appears (or via
`capture/<attempt>/m11-prompt-<nonce>.request.json`), the parent asks the owner with THAT nonce:
"Read the EXACT name this Bluetooth permission dialog shows — which app is asking — and report it
verbatim FIRST; do not click yet." The owner's verbatim prompt name AND the owner's own Allow/Deny
choice are recorded by the parent into `answer_file` as
`{"nonce": "<nonce>", "answered_by": "owner", "dialog_observed": true, "prompt_name_shown": "...",
"choice": "allow|deny"}`. If the owner saw NO dialog, the answer is
`{"nonce": "<nonce>", "answered_by": "owner", "dialog_observed": false}`. That is the owner's own
truthful observation and is never inferred. An answer with a wrong nonce or without
`answered_by=owner` is ignored and recorded as a problem. The script binds the valid answer to the
nonce in `m11-capture.json`. The M-11 observation is exactly WHICH app the dialog names (Chrome vs
the dev binary): not inferred from Settings, not assumed.

## 7. Sequencing conditions (reviewer-required) and comparison baseline (read-only)

Sequencing: M-11 runs ONLY AFTER the three fresh signed M-08 retake scenarios have PASSED — their
live authorized scans with the approved fixture are the real baseline; a codesign identity check
alone is NOT grant proof. After the dev run: the owner's read-only Settings re-check (planned; see
the owner record for the exact question) must show `VelesOtp23PoC` intact. A further signed
authorization/scan probe under NORMAL operation (no permission toggle) is OPTIONAL and only if the
parent separately schedules it; it is not part of this plan. No M-04 toggles are authorized, and
codesign verification is never claimed as grant evidence.

- Comparison baseline: the three fresh signed M-08 retake scenarios' live authorized scans
  (above), supplemented by the historical approved-fixture scan evidence
  `evidence/macos-poc-authorized-scan.json` (cd971f0 tree) and the recorded post-grant
  authorization states. No M-04 toggle or approved-fixture permission change is authorized.
- Read-only identity/grant readbacks: approved bundle sha + `codesign --verify --strict` before AND
  after the dev run (script-enforced); the owner's read-only Settings observation "VelesOtp23PoC"
  (per the owner record) is re-requested AFTER the dev run — a NEW `VelesOtp23Dev` entry appearing
  would be a recorded fact (never removed); the approved entry must remain unchanged.
- Everything the dev run created dies with the attempt (disposable profile, including its inert
  manifest copy) or is removed (own user-scope dev file, byte-verified); the approved fixture and
  its grant are expected byte/state identical.

## 8. What stays frozen

The M-08 code phase (all four changed files + new helpers/tests + code-phase evidence dirs) is
untouched while Opus reads; this M-11 stage lives OUTSIDE the repo (all private files here), touches
no repo path, launches nothing, registers nothing.