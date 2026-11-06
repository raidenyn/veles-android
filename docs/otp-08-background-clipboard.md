# OTP-08: background lifecycle and synthetic clipboard

## Implementation

The connector still owns the Native Messaging port. A service-worker authority
issues a random session-only token bound to the exact connector URL, tab ID and
Chrome document ID. Inbound callbacks and outgoing requests pass through an
ordered generation check before reaching the controller/host. This extra queue
is capped at 128 entries and 262144 UTF-8 JSON bytes. Closing it erases pending
references, suppresses late authorization results and disconnects once.

The worker observes `tabs.onUpdated` frozen/discarded transitions,
`tabs.onRemoved`, and the removed tab ID in `tabs.onReplaced`. Revocation is synchronous in memory, persists in session
storage, and clears the tab diagnostic badge. The badge is diagnostic (`ON` for
an available raw connection), not an authenticated peer or OTP indicator.
Document freeze/pagehide also invalidates locally before closing transport.
Resume reloads the connector and negotiates a fresh host without replaying sends.

The Diagnostics disclosure offers **Copy synthetic code** and **Arm one
background copy**. The latter copies fixed synthetic code `012345` once on the
next raw native message; it never interprets or copies that message's contents.
There is no automatic-copy preference or real-OTP integration in OTP-08.

The worker validates the exact sender, token, message fields and six ASCII digits,
then serializes up to 16 pending copy operations. A clipboard-only offscreen
document validates worker provenance, writes a temporary textarea via
`execCommand('copy')`, erases/removes it in `finally`, and reports success or
failure. The worker closes the helper after each immediate request, including
failures. The helper has no native port, trust, history or session ownership.
Copy failure updates the diagnostic status and does not close transport.

The helper requests a one-use, operation-correlated worker execution admission
immediately before copying. Revoked requests still waiting in runtime messaging
are refused. The admission grant is the copy's commit point: a clipboard write
already admitted to execution cannot be retracted by a later freeze/removal.
The helper retains only its immediate request, not a connector token or session.
The native gate captures `lastError` synchronously but drains previously received
terminal events before EOF. Request completion/timeouts cancel any envelope not
yet posted through the asynchronous gate. Bootstrap lifecycle guards are installed
before ownership/activation awaits; failed activation releases ownership.

Required manifest changes: `offscreen` and `clipboardWrite` permissions in
addition to `storage` and `nativeMessaging`; Chrome minimum 132 for the
`frozen` transition API. CSP and host/tab permissions remain unchanged.

## Verification ledger (2026-11-04; physical acceptance pending)

This ledger does **not** close #90 or claim Gate B/C/D acceptance.

### Resumed physical work, 2026-11-05

The original stop after local checks was premature. Device preparation and
Windows physical investigation resumed; the following supplements the earlier
table without promoting its simulated or historical results.

- **Source-bound Android fixture:** built from HEAD `84ac068` using the existing
  approved fixture signing identity. Main APK SHA-256 is
  `25fca189d25251b24b03ca1c18ca64aaf6626b5b7dbdb4d1d656fea35ed1be68`;
  the test APK remains byte-identical to the retained test artifact. Both phones
  were updated in place after certificate comparison; data and signing keys were
  retained. Literal incremental build reconfirmation output and installed/pulled
  artifact witnesses are retained privately. This does not retroactively
  attribute the earlier main fixture APK to this source.
- **Physical observer:** uses the actual product controller, not a simulated
  native port. The disposable extension copy adds only a bootstrap export to the
  compiled connector plus a metadata observer; original bytes, derivative hashes
  and transformations are retained. It is an instrumented test vehicle, not a
  byte-identical production ZIP.
- **Evaluator correction:** Playwright enables focus emulation on attached
  pages. That affected the earlier freeze/background setup investigations. The
  physical evaluator now uses raw CDP, disables leftover focus emulation, and
  holds no debugger connection during the timed idle interval. The earlier
  unobserved freeze/discard transitions are therefore not proof that Chrome
  cannot perform those transitions; genuine recovery cases remain pending.
- **Windows cold:** the full 720-second automatic attempt failed after a late
  ordinary-tab focus loss. A separate independent adjudication establishes an
  explicit continuous **650-second** qualifying window containing two matching
  4096-byte records after minute seven, a correlated OS clipboard transition,
  and a timestamped hidden-page helper query returning zero helpers. The longer
  attempt's failure is retained; the narrower result is not aggregate task
  acceptance.
- **Windows prewarm:** **FAILED**. Both initial byte-verified exchanges worked,
  but only phone A's later record reached the connector. Phone B reports its
  scheduled push and successful notification API/callback operations; those are
  not desktop delivery acknowledgements. A subsequent distinct diagnostic write
  still worked on A but failed on B at the native write stage. The loss boundary
  and its cause remain unresolved. Later fixture-lifetime timeouts during
  investigation are recorded separately and do not explain the earlier missing
  record.
- **Native diagnosis:** a separate, explicitly labelled Windows diagnostic host
  adds bounded cold-failure HRESULT/GATT-status metadata only. It preserves the
  product's error mapping, wire contract, caps and retry behavior. Its results
  are diagnostic, not acceptance of the unchanged production host. No pacing,
  speculative retransmission or cap adjustment has been introduced.
- **Subsequent Windows evidence:** original-host `prewarm-source-b` received
  both source-bound 4096-byte records and passed its browser assertions after a
  720-second detached interval. Independent review found 719 contained
  foreground/active/unlocked OS samples and 719 agreeing successful TCP-zero
  samples, plus a sampled clipboard transition and helper teardown. This is not
  aggregate acceptance. Its intended prompt
  follow-up failed, and the later cleanup attempts also failed; final phone
  service telemetry does not repair that gap. The earlier prewarm failure remains
  unexplained.
- **Windows lifecycle and copy isolation:** a fresh physical two-phone run
  observed a real helper close, visible copy failure and explicit retry; both
  phones returned matching diagnostic replies afterward. This was injected
  helper interruption, not OS permission denial. Raw CDP also produced genuine
  freeze and discard transitions; fresh connector generations subsequently
  reconnected to both phones and exchanged byte-matching records. On discard,
  Chrome emitted `tabs.onReplaced` with a new tab ID: the old ID's generation
  remained in session storage because the worker does not handle that event.
  The missing tab lookup also rejects stale validation, but complete invalidation
  and cleanup are not established. These results do not close the lifecycle gate.
- **Replacement-event fix:** a subsequent worker change invalidates the removed
  tab ID on `tabs.onReplaced`. The regression failed before the fix and passed
  afterward; 250 source tests, typecheck, lint, formatting, build and three bundle
  tests passed. In a new real-Chrome discard, the executing worker was bound to
  the rebuilt script hash and the old generation became null; activation created
  a fresh replacement generation. A subsequent prewarm attempt failed its full
  focus assertion. Independent review established a narrower conditional
  739.775-second post-focus window containing both records and copy after minute
  seven, but identified provenance gaps; it is not aggregate acceptance. A later
  independently reviewed changed-worker cold run established scoped physical
  two-phone delivery and helper-copy evidence over a conservative 741-second
  interval, with prompt host shutdown and phone stop witnesses. Its clipboard
  already held the fixed value, so it did not show an OS clipboard transition.
- **Windows prewarm (current build):** after the replacement-event fix and a
  focus-interrupted attempt, a clean original-host run passed its full browser
  assertions over a conservative 720.085-second interval: both source-bound
  4096-byte records arrived at 640.2 s and 662.0 s, the copy completed at 640.2 s
  with zero helper contexts, and samplers started before backgrounding recorded
  721 foreground/active/unlocked OS samples and 719 of 720 fully valid
  zero-connection TCP samples (one transient CIM failure where .NET still
  reported zero). Sampled evidence, and the OS clipboard already held the fixed
  value, so no false→true transition is claimed.
- **Clipboard denial (new):** an identical build with only `clipboardWrite`
  removed reported permissions `["nativeMessaging","offscreen","storage"]`; the
  copy failed visibly, both links stayed connected, no helper remained, and both
  phones still answered diagnostic requests. Restoring the manifest returned the
  same build to a successful copy.
- **Aggregate acceptance:** recorded separately in
  `docs/otp-08-acceptance-2026-11-06.md`, which was corrected twice under
  independent review before sign-off. It is a scoped OTP-08 verdict, not Gate B.
- **Mac:** the user independently verified the host's ED25519 fingerprint;
  it now matches the stored key and current SSH presentation. The earlier
  different presented key was not accepted and its cause remains unknown.
  The intended user's console is now on-console and unlocked. Independent review
  supports a scoped physical cold delivery/copy result over a conservative
  733.765-second interval, with prompt fixture cleanup. The TCP sampler warned
  that enumeration could be incomplete, so strict debugger-absence proof remains
  qualified. A separately reviewed physical prewarm run established a scoped
  success: both records and the synthetic copy occurred after minute seven in a
  conservative 728.8-second window, with warning-free `netstat` zero-connection
  samples and prompt cleanup. Real Chrome freeze, discard and removal transitions
  then revoked old generations, restored fresh connector generations, and both
  physical phones reconnected with matching records. Actual helper closure
  produced a visible copy failure and a successful explicit retry. No OS clipboard
  permission-denial test was performed, and these results are not aggregate
  Gate B acceptance.

All physical raw records, failed attempts, source snapshots and independent
adjudications remain in private, git-ignored storage pending privacy-reviewed
publication. The broader native-host verification and remaining lifecycle cases
retain their existing gate ownership.

The table below retains the original 2026-11-04 checkpoint. For current physical
results, use the resumed-work record above.

| Check | Result / scope |
|---|---|
| Baseline | 212 source tests passed before implementation |
| Final local checks | 249 source tests / 22 files; typecheck, ESLint, Prettier, Vite build, 3 bundle tests and `git diff --check` passed |
| Worker authority | Exact sender/document binding, revocation, restart, in-flight races and stale badge refusal covered by source tests |
| Clipboard | Invalid input/authorization, serialized helpers, <=16 pending requests, stale queued copies, create/write/close failures and DOM erasure covered by source tests |
| Native generation boundary | Ordered callbacks, synchronous close, obsolete authorization, outbound revocation, entry/byte overflow covered by source tests |
| Actual Mac Chrome offscreen helper | Synthetic copy returned success; actual runtime context enumeration showed no helper left afterward. No OS clipboard readback claimed |
| Packaged browser | Real production missing-host UX and offscreen helper; native records/phones simulated. Existing routing, bounds, ownership and focus tests retained |
| Forced freeze | **BLOCKED** in CDP-attached Chrome 156.0.8078.81: `Page.setWebLifecycleState(frozen)` returned success but `tabs.get().frozen` remained false. Explicit DOM freeze/resume injection verified local closure/reload and a changed worker token; it is not actual freeze evidence |
| Forced discard | `tabs.discard` returned `discarded:true` and destroyed the attached target; original tab was then absent/unrestorable under this automation. Reopening obtained a fresh generation. **BLOCKED** for same-tab discard restoration acceptance; no OS/native link-cleanup claim |
| Removal | Attached packaged-browser test covers old-tab absence/revocation and new-document ownership |
| Windows physical acceptance | **NOT RUN**: no OTP-08 production-shaped host/fixture run prepared in this session |
| Windows/macOS cold/prewarm >=600 s | **NOT RUN**: requires two source/signature-bound phone fixtures, registered compatible production-shaped hosts and detached idle/foreground observation. Browser tests remained CDP-attached and use simulated transport |
| Protected delivery | Excluded here; OTP-09/OTP-12 retain ownership |

### Review and remaining implementation findings

A fresh read-only reviewer found six blocking async integration defects. The
fix pass added regressions for delayed copy admission, terminal-before-EOF
ordering, replacement token fencing/conditional end, unsent-request cancellation,
freeze during bootstrap, and rejected activation cleanup. The full 249-test
source suite passed after the fixes. This records review findings plus verified
fixes, not an independent re-review approval or physical acceptance adjudication.

Two non-blocking findings remain deferred:

- Worker bookkeeping currently retains null generation records and in-memory
  epoch/revocation entries for unrelated removed/frozen/discarded tabs. Scope
  and reclaim this state in a follow-up before sustained lifecycle hardening.
- Helper execution-admission requests authenticate the offscreen sender and
  replies are operation-correlated. The `runtime.sendMessage` success response
  itself does not expose responder provenance; an authenticated dedicated helper
  channel would strengthen that boundary. No external-content exploit was
  established with the current trusted listener set.

### Historical local build binding (before replacement-event fix)

Base commit: `84ac0684384f09a689617706217c96d56c7fd396`; feature changes are
uncommitted on `feat/otp08-background-clipboard`. These historical hashes were
read from the earlier Vite output and match the product tested by the earlier
Mac packaged run; they do not describe the subsequent replacement-event fix:

| Artifact | SHA-256 |
|---|---|
| `dist/connector.js` | `2d712a4114e6f1ebcbb941e145352459afa133b187cde9abbf8b39c89c3e0b37` |
| `dist/background.js` | `107d97a6b805f5c6321c6da8578e0066bf6bdbaffc963ff91b74582f28f87f45` |
| `dist/offscreen.js` | `4c70a6008ec34ecfe10858f4e9204001b1734d4e3095f54947cdbcd2076d46b4` |
| `dist/manifest.json` | `f2e966a48aff56abead35343dbcc5fbae9f125ed9c707275f8caa3b878a6edd1` |

The changed/untracked `web-extension/` source-file checksum catalog has SHA-256
`03a925266860a6fd4a1b157e56161e301acc7a08bd382d9d0eee3086fceeb78e`,
computed from `git ls-files -mo --exclude-standard -z -- web-extension | sort -z |
xargs -0 sha256sum | sha256sum` at this exact uncommitted state. This is a local
binding, not signed release provenance.

Current build after the replacement-event fix: `dist/background.js` is
`3329fb3075819931d92380f3fde270e994e21040d6afa36e36038370576c6c66`;
`dist/connector.js`, `dist/offscreen.js` and `dist/manifest.json` are unchanged
from the table above. The Windows and Mac physical runs in this resumed session
used that rebuilt worker after its executing hash was verified through CDP.

### Reproduce the packaged-browser check

Run the ordinary extension checks with `npm test`, `npm run typecheck`,
`npm run lint`, `npm run format:check`, `npm run build`, then
`npm run test:bundle` and `npm run test:connector-browser`.

The browser script normally launches local Chromium. In this Linux environment,
Playwright 1.58.2 did not support Ubuntu 26.04; fallback Chromium extraction did
not finish within the two attempted tool time bounds. The test instead used the
home Mac's dedicated test Chrome through its loopback-only CDP tunnel:

```sh
VELES_BROWSER_CDP=http://127.0.0.1:9222 npm run test:connector-browser
```

That optional mode syncs only an ephemeral extension test copy to the Mac work
directory and loads it into the dedicated profile. It uninstalls that test copy
afterward. The original packaged connector is exercised first; the test then
adds the simulated-port harness to `connector.html` in the ephemeral copy so
the exact-page worker authorization remains unchanged. Explicit `BLOCKED`
output is part of the result, not a passing physical verdict.

### Physical acceptance still required

For each Windows/macOS cold and prewarm run, bind exact source/build/signing
identities, Chrome/OS/account/adapter and phone fixture identities. Keep the
connector hidden/unfocused for >=600 continuous seconds with an ordinary tab
focused and actual OS foreground evidence, no DevTools clients, clean flags,
no audio/WebRTC/artificial keepalive. After minute seven, deliver unsolicited
byte-exact 4096-byte synthetic messages from both phones and observe one armed
helper copy and cleanup. Exercise clipboard denial without transport loss.
Separately force genuine browser freeze, discard and removal, observe worker
transitions and immediate invalidation, and prove new raw-message recovery,
fresh host generations, bounded queues and actual old-host/link cleanup.
Preserve failed attempts and obtain independent raw-evidence adjudication.
