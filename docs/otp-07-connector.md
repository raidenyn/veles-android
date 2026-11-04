# OTP-07 Chrome Native-Bridge Connector

Status: implementation and automated evidence complete for Tasks 1–5 and the
final fix wave. Separate independent dispositions approve the **narrow, bounded
foreground synthetic smoke only, with limits**, on both Windows and macOS,
recorded in `docs/evidence/otp07/windows-2026-11-04/` and
`docs/evidence/otp07/macos-2026-11-04/`. Windows environment evidence is
explicitly post-run, not contemporaneous; macOS environment observations are
contemporaneous before/phase records. The Android fixture signer/update chain is
resolved (Windows executed it; macOS references the same original witness
without re-execution). The macOS host is the existing approved OTP23 signed
test vehicle, not a production distribution identity. These observations do not
establish the full failure/recovery matrix.

## Owner-approved verification handoff (2026-11-04)

The owner authorized OTP-07 implementation to proceed without the full testing
matrix. The remaining six connector verification families and both non-blocking
review follow-ups are transferred to the standalone
[OTP-07 follow-up #127](https://github.com/raidenyn/veles-android/issues/127).
They no longer block implementation review, merge, or closeout of #88 after
PR #126 merges.

The follow-up is Backlog, Hardening, implementation order **22.5**: after
OTP-19 (#101) and before OTP-20 / Gate C (#102). It depends on #88, the installed
host work (#89), background/lifecycle integration (#90), and #101. Gate C must
resolve its per-item ledger or obtain an explicit owner disposition before
closing. Shared native-host cases remain coordinated with #125.

This transfers verification ownership; it does not turn NOT RUN or BLOCKED into
PASS, declare a full-matrix or Gate B/C/D pass, or waive other tasks' own
acceptance criteria. Historical platform evidence remains unchanged.

Plan: `docs/superpowers/plans/2026-11-04-otp07-chrome-connector.md`
Worktree during implementation: `.worktrees/otp07-chrome-connector`

## What is implemented

- Manifest permissions are exactly `storage` + `nativeMessaging`; the action has
  a title and no popup, `activeTab`, `tabs`, host permissions, or
  `web_accessible_resources`.
- The service-worker launcher discovers or creates one root `connector.html`
  page using a runtime ping and a `storage.session` tab-id fast path, never
  `tabs.query({ url })` or `tab.url`, and serializes concurrent clicks.
- Connector ownership is extension-wide and is decided before any Native
  Messaging port exists. The owner holds a real Web Lock (Chrome 102 supports
  it; the lock is released automatically on page death) and registers the owner
  tab/window with the service worker. Restored/duplicated connector documents
  show a visible secondary state, request focus of the existing owner, answer no launcher
  ping, and never call `connectNative`. Without Web Locks the service worker
  serializes claims and probes the recorded owner's document before replacing
  it, so a hidden/dead owner cannot block the extension forever.
- The connector page owns one Native Messaging port to
  `me.nagaev.veles.bridge`, negotiates envelope v1 / API v1, and uses strict
  inbound validation, bounded state, per-connection FIFO routing, count and byte
  limits, and idempotent close.
- Request ids come from a per-generation monotonic safe-integer counter, not an
  unbounded "already issued" set. There is no 4096-request session lifetime:
  successful work past that point stays healthy. Safe-integer overflow is
  refused explicitly without closing a healthy port; opening a new port
  generation resets the counter deliberately. Bounded tombstones preserve
  duplicate/unknown/timed-out terminal classification, and callers cannot
  inject/reuse ids.
- Scans, discovered devices, and connections are separate stores. One scan can
  discover multiple phones and each connection routes its own whole records.
- Hints come only from active peers in the existing `ChromeTrustRepository`.
  `connected` hints are provisional display data. The connector never calls
  `authenticated()`, fabricates proof, or persists a hint on connect.
- Reconnect is bounded at three attempts (1 s, 2 s, 4 s), is per peer/host, and
  never runs after user disconnect, intentional close, shutdown, tab close,
  incompatible API, or missing host. Loss recovery is gated on negotiated
  transport readiness, not the global UI error phase, so a retryable failure on
  one operation cannot suppress another phone's loss recovery.
- Validated unsolicited host errors (for example a mid-scan
  `scan_failed`/`access_denied` followed by `scan_state: stopped`) are surfaced
  as typed visible failures with macOS permission guidance. They are not
  presented as correlated request failures, do not close a healthy port, and do
  not disturb unrelated live connections.
- The UI provides startup/host status, search/stop, discovered devices, connect,
  connection rows, disconnect, error guidance, Reconnect/Retry, Shutdown, and a
  bounded metadata-only diagnostics disclosure. Dynamic text uses `textContent`.
- Missing host and incompatible API fail closed with visible install/update
  guidance and disabled BLE controls, while an explicit Retry remains enabled.

There is no production simulated-transport switch. Production bootstrap always
calls `chrome.runtime.connectNative`; the browser harness exists only in an
ephemeral test copy that is never part of `dist/` or the packaged zip.

## Automated evidence (2026-11-04)

All commands were run from `web-extension/`.

| Command | Result |
| --- | --- |
| `npm test` | 16 files, 212 tests passed, 0 failed |
| `npm run format:check` | exit 0, `All matched files use Prettier code style!` |
| `npm run lint` | exit 0 |
| `npm run typecheck` | exit 0 |
| `npm run build` | exit 0; Vite 6.4.5, 26 modules |
| `npm run test:bundle` | 1 file, 3 tests passed |
| `npm run package` | exit 0; deterministic zip and checksums emitted |
| `npm run test:connector-browser` | exit 0 in the harness environment described below |

Built `dist/` contains `connector.html`, `connector.js`, `manifest.json`, the
existing pages/scripts/chunks, and no test harness or fixture. The packaged
candidate for this run was
`build/web-extension/veles-extension-0.1.0.zip` with SHA-256
`4b317ec8a5aa3c54035f35bfc2cc8215f02e141102790ad9527a86d0d42f029f`.

### Node integration coverage

`test/connector/integration.test.ts` covers:

- hello offering exactly `api_versions: [1]`, and pre-ready BLE requests being
  refused locally;
- the host API overlap matrix: API v1 ready accepted, API v2 ready rejected as
  `incompatible_api`, and a pre-ready wire `incompatible_api` failing closed
  without a BLE request;
- missing-host and incompatible-host visible guidance/control states;
- one scan discovering two phones, two independent connections, two sends
  routed to the correct handles, and an inbound message delivered to the correct
  connection;
- a 4096-byte inbound record delivered whole, 4097 bytes rejected without
  delivery, a 4096-byte outbound record posted as one whole base64 record, and
  only a correlated `sent` terminalizing the send (transport-write semantics,
  not an application acknowledgement);
- exactly one `port_closed` failure per pending request on port death, no send
  replay after timeout, a late `sent` not resurrecting a closed connection,
  duplicate terminals failing closed, and events from an old port generation
  being ignored after replacement;
- intentional close not triggering automatic reconnect, including a real
  `ConnectorApp` close followed by a stale disconnect callback;
- request identity remaining healthy past 4096 successful sends with another
  live connection holding queued/pending work;
- recovery of a lost connected phone while an unrelated retryable operation
  failure is displayed, with no connect replay;
- unsolicited `scan_failed`/`access_denied` surfacing macOS permission guidance,
  preserving a healthy connection, and remaining idempotent after the host's
  `scan_state: stopped`;
- every captured outgoing envelope in one full happy-path flow checked by an
  independent test-only oracle derived from the frozen schema (not the
  production request validator); other flows and hint-connect variants are not
  exhaustively audited by that oracle;
- independent count-only and byte-only cap cases: four 4096-byte records on one
  link, eight such links globally, sixteen 1-byte records on one link, and 128
  one-byte records globally, each followed by a rejected extra operation. These
  close the Task 3 residual that previously hit count and byte ceilings at the
  same time.

### Packaged-browser evidence

`scripts/test-connector-browser.mjs` loads the built extension in Chromium.
It is not part of `npm test` and is not a physical-device test.

Environment used:

- `playwright-core` 1.58.2 (already a devDependency; no dependency change).
- Google Chrome for Testing 157.0.8103.38 via
  `VELES_TEST_CHROMIUM=/tmp/opencode/chrome-for-testing/chrome-linux64/chrome`.
- Playwright's own `playwright install chromium` reported
  `Playwright does not support chromium on ubuntu26.04-x64`; the Chrome for
  Testing binary was used instead, with required system runtime libraries
  installed on the host. This is environment setup, not an extension build
  dependency.

The script verifies:

- the unmodified packaged `connector.html` and `connector.js` load in a real
  extension context, and the real Chrome Native Messaging runtime produces the
  asynchronous missing-host guidance with Search disabled and Retry enabled;
- an ephemeral harness copy of the same built HTML runs the real production
  bundle/controller/UI with only `chrome.runtime.connectNative` replaced by a
  simulated `NativePortLike`;
- API v1 ready reaches ready state; API v2 ready shows the visible update
  guidance and posts no `start_scan`;
- one scan lists two phones and both connect with the correct outbound
  `device_handle`, `api_version: 1`, distinct A/B connection rows, and a real
  DOM disconnect routed to the selected handle;
- a 4096-byte record is accepted and shown in diagnostics, while 4097 bytes
  produces the visible fail-closed protocol error;
- two simultaneously loaded packaged connector documents create exactly one
  Native Port: the duplicate is visibly secondary and the owner keeps the only
  active port; closing the owner lets a replacement document become the sole
  owner;
- keyboard focus survives an unrelated device-list rebuild and, when the
  initiating control becomes unavailable after success, moves to an enabled
  Search or Reconnect control;
- the harness and fixtures are absent from the built `dist/` package.

This evidence proves packaged extension wiring, protocol/controller/state
behaviour, and DOM/UX under a simulated transport. It does **not** prove BLE,
native-host registration, OS permissions, or real phone interoperability.

## Remaining review follow-ups

The final scoped review approved the fixes with two minor residuals, now owned
by [#127](https://github.com/raidenyn/veles-android/issues/127):

- After a service-worker restart, a duplicate connector page still cannot open
  another native port, but its focus request may fail because the worker's
  owner record was memory-only. The extension action can rediscover and focus
  the existing connector.
- Independent outgoing-envelope auditing currently covers one happy-path flow;
  it does not yet audit all posts or independently track negotiation phase.

## Physical Windows/macOS smoke checklist — bounded foreground smoke on both platforms, with limits

Prerequisites for each platform: an actual Windows or macOS machine, the OTP-25
development native host registered as `me.nagaev.veles.bridge`, two physical
phones running the Veles app, and the extension package built from this commit
(or an equivalent reviewed build). Record the native-host build hash, extension
package hash, and phone labels. The Windows disposition accepts the narrow
bounded smoke with environment evidence explicitly post-run; original
contemporaneous OS/live-browser capture was not retained there. The macOS record
has contemporaneous before/phase environment observations. Both are bounded
foreground smoke, not full hardware passes.

| Step | Windows | macOS |
| --- | --- | --- |
| Register the OTP-25 dev host and confirm the manifest points at the built host | PARTIAL — temporary HKCU registration of the unsigned dev host; manifest readback, HKLM absence, and cleanup recorded; not installer acceptance | PARTIAL — per-profile manifest only; signed OTP23 test-vehicle executable readback and cleanup recorded; global NMH unchanged; not installer acceptance |
| Load the extension and open the connector from the action; confirm exactly one connector tab is focused | PARTIAL — controlled extension-page production-export invocation, not a literal action/toolbar click; one owner tab | PARTIAL — controlled extension-page production-export invocation, not a literal toolbar click; one owner tab |
| Search and confirm both phones are listed from one scan | OBSERVED — one scan found two phones in the same fixture session | OBSERVED — one scan found two phones in the same fixture session |
| Connect both phones; confirm independent connection rows | OBSERVED — two independent connection rows | OBSERVED — two independent connection rows |
| Send a synthetic whole record on each connection; observe the corresponding `sent` event | OBSERVED (bounded) — 9 main + 4 reopen synthetic replies; `sent` is transport/GATT write completion, not an application acknowledgement | OBSERVED (bounded) — 9 main + 4 reopen synthetic replies; `sent` is transport/GATT write completion, not an application acknowledgement |
| Disconnect explicitly; confirm no automatic reconnect and no send replay | OBSERVED (bounded) — no reconnect in the 5 s attached foreground window; not a strict lifecycle proof | OBSERVED (bounded) — no reconnect in the 5 s attached foreground window; not a strict lifecycle proof |
| Close the connector tab; reopen by hint or search and repeat a search | OBSERVED (bounded) — external close, then a new page with a repeated scan and fresh handles; not a hint-based reopen; process observations are sampled | OBSERVED (bounded) — external close, then a new page with a repeated scan and fresh handles; not a hint-based reopen; process observations are sampled |
| On macOS, exercise the Bluetooth permission denial/retry guidance and record the exact observed text | N/A | NOT RUN — no prompt/denial, TCC reset, grant, or revocation performed; denial recovery not exercised |
| Record pass/fail, OS, host build hash, extension package hash, phone labels, and any defects | REVIEWED SCOPED PASS — bounded foreground smoke approved with limits; host/extension hashes recorded; environment is post-run corroboration only; phone labels kept private | REVIEWED SCOPED PASS — bounded foreground/CDP smoke approved with limits; signed test-vehicle and extension hashes recorded; environment contemporaneous; 0 critical / 1 minor (stale catalog self-entry preserved); phone labels private |

The Windows entries summarize the bounded foreground smoke in
`docs/evidence/otp07/windows-2026-11-04/summary.json`; the detailed sanitized
review is in that directory's [`evidence-review.md`](evidence/otp07/windows-2026-11-04/evidence-review.md).
The macOS entries summarize the bounded foreground/CDP smoke in
`docs/evidence/otp07/macos-2026-11-04/summary.json`; its detailed sanitized
review is in [`evidence-review.md`](evidence/otp07/macos-2026-11-04/evidence-review.md).
No full hardware pass is claimed from the simulated-browser run. Neither
recorded smoke is full OTP-07 closure, installer acceptance,
notarization/production distribution, strict lifecycle acceptance, background
liveness, authenticated transport, a literal toolbar invocation, or a
no-GATT-write proof. The Windows host is recorded as Authenticode **NotSigned**
in the post-run environment supplement; the macOS host is recorded as the
existing approved OTP23 Apple Development-signed test vehicle with the same
designated requirement, not a production distribution identity. The Android
fixture signer/update chain is resolved (Windows executed it; macOS references
the same original witness without re-execution). The macOS Bluetooth
denial/retry guidance was not exercised. The Windows record is unchanged as of
its own adjudication; the macOS record is the sibling bounded foreground/CDP
result.
