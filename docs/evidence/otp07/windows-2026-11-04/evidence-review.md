# Independent Windows physical evidence review — sanitized public version

## Verdict: approved — narrow bounded foreground Windows smoke only

The recorded attempt-b exchanges pass the bounded, CDP-attached, foreground
Windows synthetic-transport checks described below. The final independent
disposition dated 2026-11-04 approves this specifically narrowed operational
smoke, **with limits**. This is not full OTP-07 closure, macOS acceptance,
installer acceptance, a strict lifecycle result, an authenticated/background
transport result, a literal toolbar invocation, or a no-GATT-write proof. The
Android fixture signer/update chain is resolved. Windows/Chrome environment is
corroborated by an explicitly post-run supplement, not contemporaneous capture.

This document is a sanitized public rendering of the independent adjudication.
Earlier pending language below is retained as the historical record of the
first adjudication; the final disposition supersedes it for the bounded smoke
only. It contains no raw device labels, hints, serials, handles, user/machine
paths, extension ID, or private filenames. The original bytes remain in the
ignored private evidence tree. The reviewer did not use a top-level
`status: PASS` or stdout banners as proof; SHA-256 recomputation and offline
JSON/protocol reconciliation are analysis of existing records, not new test
execution.

## Findings

### 1. PASS / resolved — Android fixture signer/update chain; no Windows signing claim

The original signer document recomputes to 1,977 bytes, SHA-256
`c06806c8d32cdd4f617614a94e36079d5618672349c97d14ccb815860ceb2ef8`, exactly as
originally linked. It records six successful `apksigner` verification groups
for the candidate app/instrumentation APKs and the post-install pulls; all six
certificate digests agree with the candidate and with each other.

Retained pre-install records
(`5013c6b04f6d0ad041cb0b1d2e645c4ac81e9fc9ae08c1d6c1360f96835a6313`) add the
four pre-install app/test entries with the same certificate digest. Retained
`adb install -r` output
(`c8f87f03473de0c40321fa219386a51f737642ccc6a56c76c09775d0315a239e`) records
two successful app updates, matching post-install app digests, first-install
times before the run, and post-update Bluetooth grants. This supports same-key
in-place fixture updates; it is not a byte-for-byte app-data audit.

Semantic external Android signer acceptance for this fixture smoke is **PASS**.
It is distinct from Windows signing. The post-run environment supplement
records the host Authenticode status as **NotSigned**, so no Windows signing
claim is implied.

### 2. PASS with explicit post-run limitation — environment corroborated, not contemporaneous

The supplemental environment record recomputes to
`d318faeeca58725cc8293c8c429f4bc976cd4177d8213e247fb459b9b0959582`, 12,498
bytes. It was observed at `2026-11-04T13:50:30.2396875+07:00`, **after** the run
and cleanup, and explicitly states `supplemental: true` and
`contemporaneousWithRun: false`. Original OS and live Chrome-process captures
were not retained.

It reports Microsoft Windows 11 Pro version `10.0.26300`, build `26300`, 64-bit,
last boot `2026-11-02T21:35:14.5000000+07:00`, and the currently observed Google
Chrome product version **156.0.8078.80** with a `Valid` file signature. That
version matches both original run files' CDP browser version. The supplement
records the same host digest/size and Authenticode status **NotSigned**. It
also provides a post-run cleanup readback: registration absent, owned
host/Chrome activity absent, CDP listener absent, scheduled task absent.

This is sufficient operational Windows/Chrome provenance for the narrowed
smoke, but it is **not** contemporaneous OS or live Chrome
executable/profile/endpoint attestation. Original contemporaneous OS
attestation and live Chrome process/profile/endpoint attestation remain
**NOT ESTABLISHED**. Their absence does not require a rerun or a request to
recover files the supplement says never existed.

### 3. Retained limitation — process chronology is sampled; poller source is a transcript

A reopen process poll labels a sample `seen` at
`2026-11-04T13:33:30.2716554+07:00`, while the returned process creation time
is `2026-11-04T13:33:30.3013930+07:00`: creation is **29.7376 ms after** the
label. Taken literally as an instantaneous observation, the timestamps
contradict each other.

The supplied poller source is explicitly a **post-run transcription**, not a
retained original file (SHA-256
`434f38bb9841026e2e7c79108afafc93964bade244d6cd1971b6c1b1ae3ffeab`). It says
the sample time was taken before the `Win32_Process` query, which plausibly
explains the order but does not prove the exact original inline job semantics.
The poller has no retained gone line; exit evidence remains the separate
external-close snapshot.

Both original timestamps and the precision are preserved. Do not round,
reorder, or allege falsification. The line supports a returned
reopened-process record, **not** existence at the earlier timestamp or exact
launch latency. The other snapshots establish zero host processes at sampled
instants, not continuous timing. No live main-phase PID record is supplied, so
a direct assertion that the reopen PID differs from a main PID is
**NOT ESTABLISHED**. Strict exit/EOF timing is not claimed.

### 4. NOT ESTABLISHED — phone-side content digests and synchronized latency

The phone telemetry contains actual record lengths, fragment events,
notification API returns, and bound callbacks, but its JSON projection omits
the event SHA-256 field. Neither phone file contains per-record content
hashes. The distinct per-peer hashes in this record are **Chrome record**
digests independently checked against the fixture algorithm; they are not
independently observed phone-side digests.

A/B association is supported by sequential-start scans and continuous fixture
sessions, not by cryptographic endpoint authentication or a phone-side hash
audit excluding every possible symmetric misbinding. Phone and Windows wall
clocks are also not calibrated; no causal violation or subsecond end-to-end
latency should be inferred from different clocks. Browser-local deadline
checks and phone-local monotonic ordering do reconcile.

## Independently verified items

### PASS — source, catalog, served assets, and recorded host chain

- HEAD is `58b243d4a55ed18d6b3e3bd0dee05b2f505d7c24`, tree
  `aa5331e1b4f8a0874b5b877ca0838b1ac7d96c90`; both raw phases record the same
  clean source identity.
- The local evaluator recomputes to
  `7e03aa8867b051be0ffe01416b155ad03c71deeceae2d90dbd001ddc2fe9676d`.
  It is retained privately, not published as a public reproducer.
- All 13 catalog entries match the staged `web-extension/dist` set.
  Canonicalizing `{path, sha256}` gives
  `440b1c6dff6156f06c7993658726d440c093bba36b97acb9ad49c73fe084ec21`.
- In both phases the served `connector.js`, `connector.html`, and
  `background.js` digests agree with the verified catalog:
  `5596b03cf4c67868ec1e610664b566a967a82e510032b38a593b9fd32b784661`,
  `ec4478422db840af4038d73e8c217f88a86d553c660da30c7725864dd73df88b`,
  and `8e3258a970d5726470625bd9bf8fa11f9d5139df92e0ca709cbcf1756a7b5b1c`.
- The host digest
  `36a9fcda38d63292b720b7f3c84fa29e5dc6bcc0ef45b1e0cc13a5a55a5d91af` is
  consistently recorded in identity, both phases, the registration journal,
  and readback. The Windows executable is not copied here, so this verifies
  the recorded hash/path chain, not a new hash of those bytes or an
  independent build reproduction.

### PASS — registration semantics and recorded cleanup

The registration journal records a create with HKCU and HKLM originally
absent. Readback records HKCU pointing at the intended manifest, HKLM absence,
`stdio`, the exact one extension origin, the intended host path, and a matching
host digest. The preserved manifest recomputes to
`e1db322de6b3d07b5778842e4d9d5c160a4c394b2407467aa97a6add23a9dd91` (443
bytes); the cleanup record compares it with the journal and removes the
temporary key and manifest. This establishes consistent external
journal/readback/cleanup evidence, not a fresh registry query or installer
acceptance.

### PASS — A-alone to A+B binding in the same fixture sessions

A starts alone and is observed ready; the solo scan contains exactly one row.
B starts later while A runs; the next scan contains A unchanged plus exactly
one new B row. The raw hints/labels in the private scans match the binding
document and the selected devices, and the public selector hashes recompute.
This is operational fixture association, not authentication, and does not rely
on row order, opaque handle persistence, or the word `bound`.

Both final fixture files carry the same run ID. Their single instrumentation
lifetimes encompass binding, the main exchanges, and the reopen exchanges.
There is no recorded fixture restart between binding and the exchanges. The
recorded stop command is after both phases, and both instrumentation outputs
end normally with empty stderr.

### PASS — reconstructed request/reply hashes and full record reconciliation

The approved fixture algorithm is the `VEL1` prefix (`56 45 4c 01`) followed by
`byte[i] = (i + seed) & 255`; the reply changes only byte index 3 to `02`.
These are binary version bytes, not ASCII digits. The following request hashes
are reconstructed from that generator, not falsely described as archived
phone-side request digests; reply hashes match both the exchange observation
and the actual receipt ledger.

| Case | Length / seed | Request SHA-256 | Reply SHA-256 |
| --- | --- | --- | --- |
| Four-byte boundary | 4 / any | `70848327e00df6dd9b45a88b6219523cd35c7379070d89bfa978407fa07229bd` | `3c261d9e6dc0bae7b24919c80c749c05f71bbf3455b8ecfe79d4595e277d01ff` |
| Main A | 508 / 12 | `959b66d65288fd0a1b7611bea6677ca6c9b22f93c4652751bf26fea6da0fc338` | `e6db9ab8f6d978b117bef957043e07aa03fd4cc1dba9fecb5665fd2ce0f9642e` |
| Main A | 509 / 13 | `6715e8fba97860570419007968d1509e5a94e1beba032cadfb29642375c5d8b5` | `9997934c64c7f86be8537dd5ee95b35a0e1781c85d1bbe5de3e61be43ad77ec7` |
| Main A | 4096 / 14 | `8afade9cf3187460039b8fb239bfe85a16bc00f4fb09f05a074b0fb604f9d709` | `1d950f4310d43563745b3dd679cb3518f4d804b7f033c6759686ff540a8ef42d` |
| Main B | 508 / 32 | `bab31e09ea297b9cd526ce12cd53140541aa9f4f4ca545910e40a4d616107b83` | `aede72517606dc1a8a6ea8dfde06e39c841f0f4bce57f9298c4615e45df7fb49` |
| Main B | 509 / 33 | `911903c80de67f2db927acec3135024afebd9f8653d0e872a01563d6f1392f0b` | `6b6812c719e9a6ac34582ec33978efc0478277d65d62a225a5eba7b377be2aeb` |
| Main B | 4096 / 34 | `6148421167608edbc3466f4442bf377021ad0cba3d8699e77f8e170732798158` | `80144c4497d35f36467c8b776e77a579f53e3241f43d834bcc6ec1403e30a7b6` |
| Main B survivor | 4096 / 131 | `1b35de2f3607bfab002105abd52a495fa7392bffd193a5b75f1c3cc8d74d4e1f` | `9f6f48e68a848e2230161389d366186af25e9b2e213ef857d0ecba37b57cab8a` |
| Reopen A | 508 / 211 | `d018ba4ba75c852e168d84d2beb2a5a32de144debaf07d5990f2db640725b273` | `b2dc37b0b0551b340e3c82330dd3125109aebeddb13aa242c9dfa01dd34c4cf1` |
| Reopen B | 508 / 212 | `a40cec322b1fc2644398839d807e141a960a89a8e49389a1e15566309cfd1b6f` | `37cd2b7fcc50612344b4ba702a2a699afa50fde87fae51f8deb0d03122e7bfec` |

- Main has 8 initial replies plus B's 4096-byte survivor reply = **9**, all
  distinct receipt sequence numbers. The sequence-to-exchange mapping is B4,
  B508, B509, A4, A508, A509, B4096, A4096, B-survivor-4096. Reopen has **4**
  replies: B4, B508, A4, A508, each on a fresh connection.
- For all 13 exchanges the ledger connection hash matches its selected
  connection, length and SHA agree, the receipt lies inside the browser-local
  send/deadline interval, send settlement is `ok`/`wire`, and no digest is
  pending or failed. All records are accounted for exactly once. There are
  zero unknown, unexpected, optional, unmatched, or unsettled records and zero
  unexpected errors/timeouts.
- The 4-byte cases are fixed-prefix boundary handling only; their seeds do not
  alter bytes, so they do **not** establish peer isolation. Longer seeded
  payloads have distinct request/reply hashes per peer and per reopen seed.
- Main final drain has 9 records with no pending digests and no post-audit
  records before close. Reopen final drain has 4 with no pending digests; the
  reopen tab is deliberately left open for external observation, so the driver
  ledger is not claimed to cover the later interval through external close.

### PASS — actual phone API/callback evidence and clean final telemetry

The fixture documents are final and explicitly after service stop. Cleanliness
was recomputed from counters and events, not from a `clean: true` flag.

| Phone | Complete records / generated replies | Notify API successes | Bound callbacks / raw callbacks seen | Attempted bytes | Connect / subscribe / disconnect |
| --- | --- | --- | --- | --- | --- |
| A | 6 / 6 | 15 | 15 / 15 | 5,689 | 2 / 2 / 2 |
| B | 7 / 7 | 24 | 24 / 24 | 9,821 | 2 / 2 / 2 |

Every recorded notify API and callback code is `0`; bound-operation sets match
accepted API operations without duplicates, and API byte sums match the
counters. There are zero nonzero statuses, unbound callbacks, unresolved or
dropped pending notifications, callbacks after close, dropped peers, or
truncation. Events are ordered on each phone's monotonic clock. At MTU 517,
the 4-byte framing overhead and 508-byte fragment capacity independently
explain the totals. These are real notification API returns **and** completion
callbacks, not application acknowledgements or authenticated OTP traffic.

### PASS — disconnect, survivor progress, stale local refusal, phase linkage

- The disconnect observation subscribes to the actual controller/policy/port
  before deliberately disconnecting A. In the 5-second window it records
  cancellation and disconnection, no schedule/connect/scan recovery, no wire
  error/close, and no record. A is absent, B remains connected, and B then
  completes the new 4096-byte seeded request. This is bounded attached
  foreground observation, not a forever/no-reconnect or background claim.
- Reopen links the exact original main file and private sidecar by hashes, with
  the same run ID, identity, binding document, source, catalog, host digest,
  and phone selectors. Initial connections and records are zero; both new
  connection and device handles differ.
- Both actual old main handles were exercised through `controller.send` and
  refused **locally** with `unknown_handle`/`connection_handle`, consistent
  with the controller source. This proves exercised local old-handle refusal,
  **not** injected inbound stale-event rejection. Fresh-record handle checks
  are correctly labelled observation-only.

### PASS, limited — 4097 rejection, launcher, and sampled lifecycle

- The 4097-byte case records a settled **wire** `oversize_record` at stage
  `record`, with no resulting record, and the connection remains usable. The
  host's narrower 4096 fragment limit is the effective boundary. **No
  GATT-write-absence proof is established.** Phone counters consistent with
  valid traffic do not turn this into a write-absence measurement.
- Both phases attach over CDP, capture the real bundled app through a debugger
  `handleAction` breakpoint, discover two bound peers in one scan, and connect
  both. The saved source/catalog and physical callbacks support the real
  extension/native-host/phone path; no substituted transport is evidenced.
- The service-worker import fails as disclosed. All three launcher probes
  instead invoke the production export from a controlled extension page, keep
  one active/focused connector owner, and record zero native-port calls in that
  probe counter. This is **not a literal toolbar click, not the worker's module
  instance, and not a browser-wide native-port audit**.
- Main page count goes from one to zero after tab close. External process
  snapshots record zero before and after main at the recorded sampled times.
  A process record is returned during reopen subject to finding 3. External
  reopen close and host-gone are recorded. These are sampled lifecycle
  observations, **not** strict EOF/exit timing, absence of transient extra
  processes, or direct main-vs-reopen PID comparison (no main PID exists).

## Prior attempts

One attempt is explicitly `BLOCKED` on identity JSON BOM parsing before
identity/transport work (original SHA-256
`1cd7d03d80ca224a83747575d67e41a002668d08445a730928cd26093fb82ce7`). Another
is explicitly `BLOCKED` on non-unique/missing bound-fixture discovery (original
SHA-256 `f701586fbccb204b82898f4b6b715c3043903f8011ac1e3c70f62be46e52de7a`);
the run was restarted before attempt-b. Neither is counted as a successful
exchange phase, promoted to PASS, or mixed into attempt-b's fixture binding.

## Original-byte integrity anchors

All hashes below were recomputed from existing bytes without normalizing JSON
whitespace, BOMs, or line endings. The complete whitelist is in `summary.json`.

| Artifact | SHA-256 |
| --- | --- |
| evaluator `windows-smoke.mjs` | `7e03aa8867b051be0ffe01416b155ad03c71deeceae2d90dbd001ddc2fe9676d` |
| identity | `101ce790f4ae38459d8eeb7ce2a724f8ae264931b5d6a450ed3dc201e5c24e5a` |
| phone bindings | `a6f2a8fd055788f2ab78841233f1e184054f30ab6c9534d168a22da1143ac829` |
| main phase | `9a2c9b39be1ad6e0f81c5dac8194abac40ffcd1f7711d2b15e69f60e438712da` |
| main private sidecar | `68740afca7367fb28c9d5694a69eb25a83611eec285187a8d65f78a4c2293e9f` |
| reopen phase | `45c4d2c219ae965182c52fd8d5acc8cada12cde0f017e20d9eb1e20a4a96dd4a` |
| reopen private sidecar | `3dd4e47ebe1df3250beabaad33350f587a3359a0fa4d605bf16e05e6187b31ca` |
| binding scan A solo | `89c8304a1b74084286f5a4740bd788e363ca903818bbce3855f4e86770a4a361` |
| binding scan A+B | `6f68f8150f9fd1cef945a3b36534095dcb734cbd5679f155c08f3f269d195245` |
| fixture telemetry A | `c4597798c89d022dd72801dd07b642b510a3375563f107c59ae1ce37444036db` |
| fixture telemetry B | `f9f93fd2582635008a91472fb0d39adb6258d3cad733e3151ed539723608f0e3` |
| registration readback | `7ddc54f6a6023708599abd7148476f84e00b95a95d584e9af18a5ec96ce7bbfc` |
| registration journal | `06ce1b7641841c4e798ac63788b91ae8f3856bb6cb2f38dd0817f606e117f014` |
| manifest-at-cleanup copy | `e1db322de6b3d07b5778842e4d9d5c160a4c394b2407467aa97a6add23a9dd91` |
| cleanup record | `b1caef457579c8a5c21819626afb7bfb900873f0ef141c045aaddf1b5df0a377` |
| signer evidence | `c06806c8d32cdd4f617614a94e36079d5618672349c97d14ccb815860ceb2ef8` |
| pre-install signer copies | `5013c6b04f6d0ad041cb0b1d2e645c4ac81e9fc9ae08c1d6c1360f96835a6313` |
| adb install results | `c8f87f03473de0c40321fa219386a51f737642ccc6a56c76c09775d0315a239e` |
| launch batch artifact | `3b9761b30fa17a4b35c651eec24a18fbf28d11cfe08942895483af01109bece8` |
| load-unpacked result | `b80538050b9d58c2dae7cd06d066437b960ca2ac74e2a056d60818bd472acb4d` |
| environment post-run supplement | `d318faeeca58725cc8293c8c429f4bc976cd4177d8213e247fb459b9b0959582` |
| poller source (transcription) | `434f38bb9841026e2e7c79108afafc93964bade244d6cd1971b6c1b1ae3ffeab` |
| prep provenance | `d80fb4d4bce6abe76548f9b8502f1c4cfd56f05e7a428edc71ac2c33513b2b03` |

The binding text anchors, selector/connection hashes, phase private-sidecar
links, and main-to-reopen prerequisite hashes agree with their recorded
references. The source commit/tree remain the original immutable
`58b243d4a55ed18d6b3e3bd0dee05b2f505d7c24` /
`aa5331e1b4f8a0874b5b877ca0838b1ac7d96c90`; follow-up publication changes are
evidence/docs only.

## Disposition

The prior review conditions are evidenced for catalog/served identity,
operational same-session phone binding, full receipt reconciliation, linked
main/reopen old handles, observed recovery suppression, and the discriminating
reopen round. The final scoped disposition **approves the narrow, bounded,
CDP-attached foreground Windows development-host synthetic smoke, with
limits**. The Android fixture signer/update chain is resolved. Windows/Chrome
environment is corroborated by an explicitly post-run supplement, not
contemporaneous attestation; host Authenticode status is **NotSigned**.

Precise process chronology, independent phone-side content hashes,
contemporaneous environment capture, and stronger acceptance claims remain
**NOT ESTABLISHED**. No full OTP-07, macOS, packaging/signing,
background/strict lifecycle, or gate acceptance is implied. No new product
defect was established.

**Permitted summary:** “Approved as a narrow, CDP-attached foreground Windows
development-host synthetic smoke: 9 main and 4 reopen replies independently
reconciled, with real two-phone completion callbacks and clean final telemetry.
Original Android fixture signer/update records are verified. Windows/Chrome
environment is corroborated by an explicitly post-run supplement, not
contemporaneous attestation. The 29.7376 ms sampled-process discrepancy is
preserved and only plausibly explained by a labelled post-run transcription. No
full OTP-07, macOS, packaging/signing, background/strict lifecycle or gate
acceptance is implied.” Keep all originals private and preserve the narrow
scope.
