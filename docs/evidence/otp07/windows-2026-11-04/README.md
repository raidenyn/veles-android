# OTP-07 Windows physical smoke — public evidence summary (2026-11-04)

This directory publishes a sanitized, whitelist summary of a bounded Windows
foreground smoke run for the OTP-07 Chrome connector. The original records,
private sidecars, raw handles, labels, hints, device serials, user/machine
paths, the extension ID, the evaluator driver, the APKs, and binaries remain
in an ignored private evidence tree. Nothing here is a copy of those files.

- Machine-readable whitelist: [`summary.json`](summary.json)
- Sanitized adjudication: [`evidence-review.md`](evidence-review.md)
- Summary checker: [`check_summary.py`](check_summary.py) (stdlib only)

**Status:** the final independent disposition approves the **narrow, bounded,
CDP-attached foreground Windows synthetic smoke only, with limits**. This is
**not** full OTP-07 closure, installer or Windows signing acceptance, strict
lifecycle acceptance, authenticated/background transport, a literal toolbar
invocation, or a no-GATT-write proof. macOS physical testing remains
**NOT RUN** pending a foreground test-account desktop session.

> **Historical scope (clarified after the Windows adjudication).** This Windows
> record and its `summary.json` are unchanged. The `status.macos: "NOT_RUN"`
> field and the macOS sentence above describe the state **as of the Windows
> adjudication**, not a later claim about macOS. A separate, later macOS
> bounded foreground/CDP record is published at
> [`../macos-2026-11-04/`](../macos-2026-11-04/) with its own scoped review. It
> does not retroactively change this Windows result.

The source commit/tree remain the original immutable
`58b243d4a55ed18d6b3e3bd0dee05b2f505d7c24` /
`aa5331e1b4f8a0874b5b877ca0838b1ac7d96c90`. All follow-up changes for this
publication are evidence/docs only; no source was recomposed.

## What was observed

- Extension candidate ZIP: SHA-256
  `4b317ec8a5aa3c54035f35bfc2cc8215f02e141102790ad9527a86d0d42f029f`.
  The staged extension catalog contains 13 entries and canonicalizes to
  `440b1c6dff6156f06c7993658726d440c093bba36b97acb9ad49c73fe084ec21`.
- Served files matched the staged catalog: `connector.js`
  `5596b03cf4c67868ec1e610664b566a967a82e510032b38a593b9fd32b784661`
  (128,070 bytes), `connector.html`
  `ec4478422db840af4038d73e8c217f88a86d553c660da30c7725864dd73df88b`
  (8,616 bytes), and `background.js`
  `8e3258a970d5726470625bd9bf8fa11f9d5139df92e0ca709cbcf1756a7b5b1c`
  (7,430 bytes).
- Windows host: 767,488 bytes, SHA-256
  `36a9fcda38d63292b720b7f3c84fa29e5dc6bcc0ef45b1e0cc13a5a55a5d91af`.
  The post-run supplement records Authenticode status **NotSigned**. This is
  an unsigned local development binary, not an installer or signed release.
  The build is documented as a command without test-driver features; that
  build metadata is operator-reported and is not independent feature
  attestation.
- Evaluator: `windows-smoke.mjs` SHA-256
  `7e03aa8867b051be0ffe01416b155ad03c71deeceae2d90dbd001ddc2fe9676d`.
  The evaluator is retained privately and is not published as a public
  reproducer.
- Android fixture: app APK
  `3e2a6c41bb0eb6d3ef9b07f478d29b0fe417dcf7df1915c0b6482a754c059f08`,
  instrumentation APK
  `afadd554b475013f62102c72b62c0badc46706d6f9f3e26dfe52ede4687fac84`,
  signing certificate
  `9ef3104b934a37b111726ff91c7cb5ec5affc0e8b6a90742ed0bc55501969df9`.
- Temporary per-user registration pointed at the intended host manifest; the
  manifest, HKCU readback, HKLM absence, and cleanup were recorded. This is not
  installer acceptance.

## Android signer/update chain

The external Android fixture signer chain is resolved. The supplied
`evidence/signer-evidence.txt` is 1,977 bytes, SHA-256
`c06806c8d32cdd4f617614a94e36079d5618672349c97d14ccb815860ceb2ef8`, and
records six successful `apksigner` verification groups for the candidate and
post-install app/instrumentation APKs, all with the same certificate digest.

Retained pre-install records add the four pre-install app/test entries
(SHA-256 `5013c6b04f6d0ad041cb0b1d2e645c4ac81e9fc9ae08c1d6c1360f96835a6313`),
and the retained `adb install -r` output adds two successful app updates
(SHA-256 `c8f87f03473de0c40321fa219386a51f737642ccc6a56c76c09775d0315a239e`).
This supports same-key in-place fixture updates. It is not a Windows signing
result and not a byte-for-byte app-data audit.

## Bounded transfers

There are 13 synthetic request/reply exchanges: 9 in the main phase and 4 in
the reopen phase. Request hashes were reconstructed from the approved fixture
algorithm (binary prefix `56 45 4c 01`, remaining byte `(i + seed) & 255`; reply changes only
byte index 3 to `0x02`). Reply hashes match the actual recorded receipts. The
full list, including seeds and per-exchange receipts, is in `summary.json`.

| Phase | Count | Receipt sequence mapping |
| --- | --- | --- |
| Main | 9 | B4, B508, B509, A4, A508, A509, B4096, A4096, B-survivor-4096 |
| Reopen | 4 | B4, B508, A4, A508 |

The phone side reports 15 notification API successes and 15 completion
callbacks on A, and 24 and 24 on B; every API and callback status was `0`, with
no unbound callbacks, pending drops, callbacks after close, truncation, or
dropped peers. The complete-record/reply totals are 6/6 (A) and 7/7 (B) at MTU
517. The 4-byte cases exercise the fixed-prefix boundary only and do not prove
content-based peer isolation; the longer seeded payloads have distinct hashes
per peer. A `sent` event means transport/GATT write completion, **not** an
application reply or acknowledgement.

The phone telemetry does **not** contain per-record content digests, and the
phone and Windows wall clocks are not calibrated. Endpoint association is
operational, not cryptographic, and no subsecond end-to-end latency is
inferred.

## Environment corroboration — explicit post-run only

A supplemental environment record was observed at
`2026-11-04T13:50:30.2396875+07:00`, **after** the run and cleanup. Its SHA-256
is `d318faeeca58725cc8293c8c429f4bc976cd4177d8213e247fb459b9b0959582`
(12,498 bytes). It is explicitly `supplemental: true` and
`contemporaneousWithRun: false`; original OS and live Chrome-process captures
were not retained.

It reports Microsoft Windows 11 Pro version `10.0.26300`, build `26300`, 64-bit,
last boot `2026-11-02T21:35:14.5000000+07:00`, and the currently observed
Google Chrome product version `156.0.8078.80` with a `Valid` file signature.
That version matches both original run files' CDP browser version. The
supplement also records the same host digest/size and Authenticode status
**NotSigned**. This corroborates the report across the run; it is **not**
contemporaneous OS or live-process attestation, and it does not prove every
current-file property was measured during the run.

## Lifecycle and launcher limits

Process observations are sampled: zero host processes were sampled before and
after the main phase, and the reopen close recorded tab-close and host-gone
timestamps. The reopen process poll preserves a timestamp discrepancy: the
returned creation time is **29.7376 ms after** the timestamp labelled `seen`.

The supplied poller source is explicitly a **post-run transcription**, not a
retained original file (SHA-256
`434f38bb9841026e2e7c79108afafc93964bade244d6cd1971b6c1b1ae3ffeab`). It says
the sample time was taken before the `Win32_Process` query, which plausibly
explains the timestamp order but does not prove the exact original inline job
semantics. The poller has no retained gone line. No main PID, exact launch
latency, continuous absence, or strict exit/EOF timing is claimed.

The launcher probes invoke the production export from a controlled extension
page; this is not a literal toolbar click, not the worker module instance, and
not a browser-wide native-port audit.

## Prior attempts

Two earlier attempts were `BLOCKED` and are not promoted:

- identity manifest parsing failed on a UTF-8 BOM before transport work
  (original SHA-256
  `1cd7d03d80ca224a83747575d67e41a002668d08445a730928cd26093fb82ce7`);
- bound fixtures were not uniquely discovered, and the run was restarted
  before attempt-b (original SHA-256
  `f701586fbccb204b82898f4b6b715c3043903f8011ac1e3c70f62be46e52de7a`).

## Review

An independent GPT-based reviewer separately reconciled the narrow transfers
and issued a final scoped disposition: **approved — narrow bounded foreground
Windows smoke only, with limits**. The Android signer/update chain is resolved;
the environment is post-run corroborated, not contemporaneous. The review
explicitly retained the process-chronology and phone-content/clock limitations
and did not approve full OTP-07, macOS, packaging/signing, background/strict
lifecycle, or gate acceptance.

## Verify

```bash
python3 docs/evidence/otp07/windows-2026-11-04/check_summary.py
```

The checker recomputes the catalog hash and all 13 synthetic request/reply
hashes, checks receipt mapping, telemetry counters, signer/environment status
and limitations, and rejects private-path/identifier shapes in the published
JSON. The ignored generator additionally scans for known raw private values
before writing.
