# Apply Receipt

> Generated at the end of the apply phase to mark code-implementation
> complete and provide verify with the state it needs.
> Overwritten on each apply iteration; iteration counter grows.

**Change**: `relax-ble-advertising-prerequisite`
**Iteration**: `1`
**Applied at**: `2026-11-18 03:40` (UTC)
**Executor**: `executing-plans` (inline, at the user's choice), with one whole-branch review on Opus at the end

---

## Workspace

- **Worktree**: the session's T3 worktree (already isolated, so no extra worktree was created)
- **Branch**: `fix/relax-ble-advertising-prerequisite`, from `origin/master` `d41085c`

---

## Commits

- **Range**: `d41085c..d07b7b1`
- **Count**: `5`:
  - `e8a56ed` docs(openspec): scaffold
  - `11058f6` fix(android): prerequisite
  - `cbaac13` fix(android): failure mapping
  - `8c9eaa5` test(android): service start/retry
  - `d07b7b1` style(android): spotless import order

---

## Tasks

- **Completed**: `13 of 14` checkboxes in tasks.md flipped to `- [x]`
- **Remaining**:
  - `4.5`: this receipt and the PR note state what ran; it is ticked when the PR note exists.

---

## Checks run

These ran locally with a full JDK 21 (`/home/dev/.jdk/zulu21`), because the system Java is a runtime without `javac`.

- `./gradlew testDebugUnitTest`: PASS.
- `./gradlew :app:releaseHookGuard`: PASS.
- `./gradlew spotlessCheck detekt lintDebug`: PASS after one `spotlessApply` import-order fix (`d07b7b1`).
- The raw `android.util.Log` guard, run from the repository root: clean.
- `./gradlew protocolCheck`: PASS. The component map selects the protocol node because `android/` changed.
- The docs node: `check_agent_files.py` and 112 docs unit tests PASS; `openspec validate`: PASS.

## Real-device verification

**Equipment:**
- **Phones:**
  - OnePlus 6T (`ONEPLUS_A6013`, Android 15);
  - OnePlus 10 Pro (`NE2215`, Android 15).
- **Chrome:** dedicated profile on macOS 26.5.2, Chrome 157.0.8103.79.
- **Extension:** development channel, `version_name` `0.1.0-56-g13544ac`, id `mhpehgcgnfejmeaocnpaihfaklopeabm`.
  - `manifest.json` sha256 `5b5d6ff1…56ce0`;
  - directory hash `84fa9c8f…25f4ee`.
- **Native host:** `veles-native-host` 0.1.0 in the canonical `VelesOtpDebug.app`.
  - It carries the accepted designated requirement;
  - executable sha256 `d4e9ee38…6e10e9`.
- **Fixture APK:** `me.nagaev.veles.otp23`, built from `d07b7b1`.
  - APK sha256 `2027161e16e810ac199ff7d3f5305f9eb49307b5aab0e32ba288ae6d9b9d26b6`.
  - Signing certificate SHA-256 `9ef3104b…69df9`.
  - The installed app on both phones carried the same signer (signature hash `6462eee3`). It was installed with `adb install -r -d`, with no uninstall and no data clear.

**Setup notes:**
- **Chrome extension:** the Chrome update had dropped the unpacked extension. It was reloaded through the DevTools protocol (`Extensions.loadUnpacked`) and kept the same id.
- **Trust on the 6T:** Chrome's existing trust did not cover the 6T, so the first reconnect was refused (`re-pair required`). Trust was injected both ways with the debug-only `EXPORT_IDENTITY`/`INJECT_PEER` hooks and `velesDebug.injectPeer`. The 10 Pro needed the same.

### Finding: the issue's root cause was not reproduced

- **Both phones report multi-advertisement support.** An `app_process` probe (`ActivityThread.initializeMainlineModules()` plus `BluetoothAdapter`) reports `isMultipleAdvertisementSupported=true` and `isLeExtendedAdvertisingSupported=true` on both phones. The 6T's vendor capabilities list `max_adv_instances: 16`.
- **Master advertises on the 6T too.** The master-equivalent build (APK sha256 `1488e5e4…ea70ab`, same fixture key) was installed on the 6T:
  - right after the reinstall, the card read "Sharing is enabled, but the transport is not running … Disabled. This transport does not share OTPs yet.";
  - Enable transport then reached `Advertising`.
- **The issue misread the card.** The text it quotes as `Unsupported` is the card's `Disabled` string.
- **What OTP-12 saw** was the not-running state that a reinstall leaves behind, not a capability refusal. This change is a safe relaxation; it is not the fix for an observed 6T failure. The branch build was then reinstalled for the runs below.

### OnePlus 6T

- **Start:** Enable transport → card `Available over Bluetooth` (`Advertising`), foreground `SharingService`.
- **Restart (4.3):** Stop → `Disabled`, and the service is gone. Enable again → `Advertising`, foreground. A real `onStartFailure` → teardown → retry was **not** observed on hardware; the `SharingServiceTest` failure/retry test covers it.
- **Gate B scenario (4.2).** The minute values are offset-corrected controller time:
  - **Minute 0:**
    - the `scheduled` line `elapsed=147273326 scheduled delay=1200 deadline=148473322 pid=14722` was read at controller `1794971525.190`;
    - a `/proc/uptime` round trip of 1.65 s gives an offset of `1794824250.009`;
    - the phone's `scheduled` event therefore falls at minute −0.031.
  - **Session and seed:** an authenticated `full` session; a probe pull was answered (`pull answered=probe`). `GATE_B_SEED` without extras gave `lifetimes=600,600,600,600,600`.
  - **Screen off:** keyevent 223 at minute 0.267.
  - **Task removed:** `am stack remove` at minute 1.197, with the same pid 14722 before and after, and still foreground.
  - **During the wait:** 20 samples at about 60 s intervals, all `mWakefulness=Dozing isForeground=true pid=14722 tasks=0`.
  - **Arrival:** `elapsed=148473348 fired gen=8 … pid=14722`, 1200.022 s after `scheduled`, which is controller minute 19.969: **inside the 19..21 window**.
    - Chrome acked the pushed event 75 ms later (`ack event=…`).
    - The connector's diagnostics show the 120-byte inbound push message.
  - **Wake and pull:** wake at minute 20.992 (after the arrival). The reseed and `pullAll()` at minute 21.143 (after minute 15) returned 5 records: `Veles synthetic`, remaining 598 s.
  - **Deviations from the issue's gate:**
    - (a) This 6T has its lock screen disabled (`locksettings get-disabled` → `true`; `isKeyguardShowing=false`). The phone could not be *locked*, and its security settings were not changed.
    - (b) With the screen off, the phone reported `Dozing` (doze dream, display off), never `Asleep`.

    The timing, task-removal and screen-off conditions held. The "locked and asleep" conditions did not.
  - **Owner decision (2026-11-18):** the owner accepted this run with deviations (a) and (b) as the task's acceptance, so 4.2 is ticked. The deviations stay recorded here and in the PR note.
- **Cleanup:** after the run, `GATE_B_DISARM` returned `gate-b disarm ok`, and Chrome was disconnected.

### OnePlus 10 Pro (4.4)

- Enable transport → `Advertising`, foreground.
- After a debug trust injection, Chrome authenticated a `full` session and `pull()` was answered by the probe (`pull answered=probe`).

---

## Final review

Opus reviewed the whole branch (`d41085c..d07b7b1`):
- **No Critical findings.** All five Review Focus items hold.
- **Important (documentation):** the proposal and design still state the disproved 6T root cause. They must be reframed as a hardening, and the review gate re-run, before sync and archive.
- **Minors are deferred** in the ledger.

---

## Next step

1. Done (`e4b2e05`): the brainstorm, proposal and design are reframed as a hardening, and Sol re-approved them in review pass 2. Follow-up `raidenyn/veles-android#169` is filed.
2. Done: the owner accepted 4.2's deviations and asked for the follow-up issue for "sharing enabled but the transport not running after a reinstall or process death".
3. Run `/openspec-verify-change`.
