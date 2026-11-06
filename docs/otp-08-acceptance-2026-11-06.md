# OTP-08 aggregate acceptance record (2026-11-06)

Status: **accepted 2026-11-06** after independent review and owner dispositions.
The first version of this record was rejected for overstating debugger-absence
evidence on macOS cold, for claiming per-case two-phone recovery, for an inaccurate
phone-B sentence, and for provenance and citation errors. Those claims are
narrowed here and the owner then accepted the remaining qualifications.

Scope: physical acceptance for OTP-08 background liveness, offscreen clipboard and
lifecycle invalidation, per the plan's Global Constraints and the RFC's Gate B
background/copy/lifecycle requirements. This record does not claim protected
transport, real OTP handling, or Gate B acceptance for OTP-09/OTP-12 work.

## Subject and evidence lineage

Source: base commit `84ac068` plus the feature changes on
`feat/otp08-background-clipboard`, committed as `562e603`.

Two extension worker builds appear in this evidence, and the record distinguishes
them:

- **`107d97a6…7f45`** — the build used by `prewarm-source-a`,
  `prewarm-source-b`, `cold-source-c` and the earlier Mac packaged checks.
- **`3329fb30…6c66`** — the build after the `tabs.onReplaced` fix. This is the
  build the verdict rests on: the Windows cold and prewarm runs, both macOS runs,
  the rebuilt-worker discard witness
  `replacement-fix-20261105-f/windows-raw-discard-fix-04.private.json`, the later
  removal runs, and the clipboard-denial run.

Runs in `copy-isolation-20261105-c`, `discard-recovery-20261105-d` and
`freeze-recovery-20261105-c`/`-e` belong to the **pre-fix** sequence. They are cited
below only as legacy witnesses for behaviour the fix did not touch — helper
interruption, freeze transitions, and freeze recovery — and are not assigned the
final worker hash. The successful rebuilt-worker discard witness is the
`replacement-fix-20261105-f` file named above, not the earlier discard run in which
the old generation remained stored.

Final build artifacts: `dist/connector.js` `2d712a41…0b37`,
`dist/background.js` `3329fb30…6c66`, `dist/offscreen.js` `4c70a600…46b4`,
`dist/manifest.json` `f2e966a4…edd1`.

Attestation is **inherited, not always in-run**. `win-prewarm-20261106-p` ran the
verified `3329fb` worker and the original host, but the retained in-run worker,
host, HKCU-manifest and installed-fixture witnesses sit in the earlier attempt
`win-prewarm-20261106-n`, whose snapshot precedes `-p`. Installed APK hashes and
signing identities come from earlier build and install witnesses; runs bind package
version, signer digest and readiness PIDs, not a fresh full APK digest.

## Acceptance criteria

From `docs/superpowers/plans/2026-11-04-otp08-background-clipboard.md`: separate
Windows and macOS cold and prewarm runs of at least 600 continuous seconds with the
connector backgrounded, an ordinary tab focused, no DevTools or artificial
throttling exemption during idle, a synthetic 4096-byte message from both
source-bound phones after minute seven with one armed offscreen copy, plus
separately forced freeze, discard and removal. Clipboard denial must be visible
without closing transport.

## Per-platform results

| Scenario             | Windows                      | macOS                                           |
| -------------------- | ---------------------------- | ----------------------------------------------- |
| Cold ≥600 s          | SUPPORTED WITH QUALIFICATION | SUPPORTED WITH QUALIFICATION (debugger absence) |
| Prewarm ≥600 s       | SUPPORTED WITH QUALIFICATION | SUPPORTED WITH QUALIFICATION                    |
| Freeze               | SUPPORTED WITH QUALIFICATION | SUPPORTED WITH QUALIFICATION                    |
| Discard              | SUPPORTED WITH QUALIFICATION | SUPPORTED WITH QUALIFICATION                    |
| Removal              | SUPPORTED                    | SUPPORTED WITH QUALIFICATION                    |
| Copy failure + retry | SUPPORTED                    | SUPPORTED                                       |
| Clipboard denial     | SUPPORTED WITH QUALIFICATION | Not separately run                              |

**Windows cold** — `cold-fix-20261105-i`: 741.160218 s conservative window;
records at 528.755419 s and 564.286619 s; copy at 528.770219 s with zero helper
contexts; 741 contained foreground/active/unlocked OS samples and 741 agreeing
CIM/.NET zero-connection samples. Qualification: the OS clipboard already held the
fixed value at the start, so this run corroborates the copy but shows no
false→true transition.

**Windows prewarm** — `win-prewarm-20261106-p`: 720.084883 s conservative window;
records at 640.185839 s and 662.009939 s; copy at 640.201339 s with zero helpers;
721 contained OS samples all foreground/active/unlocked, maximum acquisition-start
gap 1.021085 s; 720 contained TCP samples of which 719 are fully valid with both
sources reporting zero. The single invalid sample is a transient CIM
"Shutting down" failure in which .NET still reported a listener and zero
established connections. Qualifications: sampled evidence, clipboard already
holding the fixed value, and provenance inherited from `-n` as described above.

**macOS cold** — `macos-cold-20261106-j`: 733.765388 s conservative window;
records at 531.440412 s and 567.989612 s; copy at 531.463812 s; OS clipboard
false→true; 724 conservatively contained foreground/on-console/unlocked samples.
Qualification and correction: this run has **no warning-free `netstat` source**.
Its only TCP sampler was `lsof`, every sample of which warned that enumeration
could be incomplete because of an unrelated AppTranslocation mount. Its footer
reports 835 samples while the file holds 834 sample records — a retained generator
defect. Strict debugger-absence completeness is therefore **not** established for
macOS cold.

**macOS prewarm** — `macos-prewarm-20261106-k`: 728.826004 s conservative window;
records at 550.395843 s and 584.959643 s; copy at 550.423443 s; OS clipboard
false→true; 720 warning-free `netstat` zero-connection samples plus 719 contained
OS samples. Qualification: `lsof` in the same sampler still carried its warning;
the `netstat` source carried the zero-connection claim.

macOS contained-sample counts above use a one-second guard at each wall-clock
boundary, because the OS sampler timestamps precede its clipboard read. Without
that guard the corresponding counts are 726 (cold) and 721 (prewarm).

**Lifecycle** — evidence supports per-transition revocation and a subsequent
recovery, not two-phone recovery after each individual transition:

- Windows freeze: the frozen/null/fresh-generation transition is recorded in
  `discard-recovery-20261105-d/windows-raw-freeze-02.private.json`, and the
  subsequent two-phone recovery in `freeze-recovery-20261105-e`. Both predate the
  `onReplaced` fix, which adds only a replacement-tab listener and does not touch
  freeze handling.
- Windows discard clearing: the fixed witness is
  `replacement-fix-20261105-f/windows-raw-discard-fix-04.private.json`, where the
  old generation became null under the rebuilt worker. The earlier
  `discard-recovery-20261105-d` follows the pre-fix discard in which the old
  generation remained stored.
- Windows removal: `removal-physical-20261105-g`/`removal-recovery-20261105-h`
  cleared the old generation, produced a new host, and then showed matching
  records from both phones.
- macOS: `macos-lifecycle-20261106-l` recorded zero records after freeze/resume
  and again after removal/recreation; two-phone matching replies appear later in
  `macos-lifecycle-recovery-20261106-m`, on a document created by a subsequent
  reset. That supports individual generation transitions and eventual recovery,
  not per-transition two-phone recovery.

**Clipboard denial** — `copy-permission-20261106-o`: the identical build with only
`clipboardWrite` removed from the manifest. The running browser reported
permissions `["nativeMessaging","offscreen","storage"]`. The copy reported "Copy
failed. Transport remains connected; retry explicitly.", both link handles stayed
connected, zero helpers remained, and both phones still answered diagnostic
requests. After restoring the manifest and restarting the browser, the same build
reported "Synthetic code copied." on one newly connected link. Qualification: the
denied result records the worker hash, but the restored-permission state and full
build attestation for the recovery step were observed in the session and are not
retained as files, so "identical full build" recovery rests on that unretained
observation.

## Recorded qualifications

1. OS and TCP evidence is sampled at roughly 1 Hz, not continuous instrumentation.
   Gaps of about 1 s cannot exclude brief activity.
2. macOS `lsof` consistently warned that enumeration could be incomplete because
   of an unrelated AppTranslocation mount. Only macOS prewarm has the independent
   warning-free `netstat` source.
3. Both Windows runs began with the OS clipboard already holding the synthetic
   value; they corroborate the copied result but show no false→true OS transition.
   The macOS runs did show that transition.
4. Provenance is partly inherited: worker/host/manifest/fixture witnesses for
   Windows prewarm come from the immediately preceding failed attempt, and APK
   hashes and signer identities come from earlier build and install witnesses.
5. The extension is an instrumented derivative of the production build (observer
   appended, connector exported for the observer), not a byte-identical packaged
   release.
6. Failed attempts are retained as failures: `win-prewarm-20261106-n` (a
   non-Chrome foreground PID held the desktop for 71 consecutive samples; the
   process name was queried live but no name mapping was retained) and
   `replacement-fix-physical-20261105-f` (full focus-window assertion failed;
   only a separately bounded narrower window was ever accepted, and that narrower
   result is not part of this aggregate verdict).

## Disposition of the earlier phone-B loss

`prewarm-source-a` recorded that phone B's unsolicited record never reached the
connector while its notification API calls and callbacks succeeded. From retained
evidence:

- During the failed idle interval the connector recorded no matching inbound record
  for B's handle and no link error. B's earlier warm-up inbound record does exist.
- A later distinct diagnostic write to the same handle failed at stage `write`
  with `send_failed`, after which the connector recorded a disconnected event for
  it (`windows-diagnostic-probe-both-…private.json`).
- The phone-side record of that disconnection appears in
  `prewarm-source-a/post-probe/phone-B-collect-otp23-transport-evidence.json`
  (`disconnects: 1`), i.e. before the final cleanup collection. No disconnect was
  recorded on the phone during the failed idle interval itself.

These observations are **consistent with** a silent link or delivery-path failure
in which the phone's local stack accepted the notification but the desktop never
received it. They do not uniquely establish that cause, and they do not exonerate
every product receive-path fault: the later write error demonstrates that error
reporting worked for that later operation, not that the original loss has been
localized.

Android notification callbacks acknowledge local acceptance, not peer receipt, so
they cannot be used as delivery proof. No retry, pacing or cap change was
introduced. Root cause remains unidentified; it did not recur in the
diagnostic-host run or in the later original-host runs. It remains an open
platform risk.

## Owner dispositions (2026-11-06)

The owner reviewed the qualifications above and disposed of them for OTP-08:

1. **Further macOS testing: skipped.** No additional Mac runs are required for
   OTP-08. The macOS results stand as recorded, including the macOS-cold
   `lsof` completeness qualification and the eventual-rather-than-per-transition
   recovery.
2. **Provenance: accepted as documented.** Inherited worker/host/manifest/fixture
   and APK attestations are accepted; no further provenance capture is required.
3. **Missing phone-B record: deferred, not resolved.** Investigation is moved to
   the OTP-15 fault-test checklist (with OTP-14 for reconnect/pull). The failed
   `prewarm-source-a` result stays failed and its cause unresolved. This is a
   non-blocking residual risk for OTP-08, not a reliability guarantee.
4. **Clipboard evidence: accepted.** Existing copy, helper-teardown, interruption
   and browser-permission-denial evidence is sufficient, including the
   unretained-observation qualification for the restoration step.

## Verdict

OTP-08's physical acceptance set is **accepted with the qualifications and owner
dispositions above**: both platforms show ≥600-second backgrounded delivery from
two source-bound phones, a synthetic copy with helper teardown, and real freeze,
discard and removal transitions with fresh generations and later recovery.
Clipboard denial is demonstrated without transport loss.

The qualifications are recorded, not erased: strict continuous debugger-absence
completeness is unestablished on macOS cold, per-transition two-phone recovery is
not demonstrated on macOS, Windows clipboard readback shows no transition,
provenance is partly inherited, and the earlier phone-B loss remains unexplained
and is deferred to the OTP-15 fault tests. This is a scoped verdict for OTP-08; it
does not close Gate B and does not validate protected transport.

Independently reviewed and approved by a reviewer other than the implementer.
