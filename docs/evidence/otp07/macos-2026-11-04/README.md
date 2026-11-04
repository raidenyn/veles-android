# OTP-07 macOS physical smoke — public evidence summary (2026-11-04)

This directory publishes a sanitized, whitelist summary of a bounded macOS
foreground CDP smoke run for the OTP-07 Chrome connector. The original records,
private sidecars, raw handles, labels, hints, device serials, user/machine
paths, the extension ID, the evaluator driver, the APKs, the app bundle, and
binaries remain in an ignored private evidence tree. Nothing here is a copy of
those files.

- Machine-readable whitelist: [`summary.json`](summary.json)
- Sanitized adjudication: [`evidence-review.md`](evidence-review.md)
- Summary checker: [`check_summary.py`](check_summary.py) (stdlib only)

**Status:** the independent disposition approves the **narrow, bounded,
CDP-attached foreground macOS synthetic smoke only, with limits**. Findings:
**0 critical, 1 minor** (the preserved archive catalog's stale self-entry,
below). This is **not** full OTP-07 closure, installer/notarization/production
distribution acceptance, production identity migration, strict lifecycle
acceptance, authenticated/background transport, a literal toolbar invocation,
or a no-GATT-write proof. The signed host is the **existing approved OTP23
macOS test vehicle**, not a production distribution identity. macOS Bluetooth
permission denial/recovery was **not run**.

The source commit/tree remain the original immutable
`58b243d4a55ed18d6b3e3bd0dee05b2f505d7c24` /
`aa5331e1b4f8a0874b5b877ca0838b1ac7d96c90`. All follow-up changes for this
publication are evidence/docs only; no source was recomposed.

The earlier Windows record at
[`../windows-2026-11-04/`](../windows-2026-11-04/) is unchanged. Its
`status.macos: "NOT_RUN"` field and macOS text are the state at the Windows
adjudication; this later sibling record does not retroactively alter it.

## What was observed

- Extension candidate ZIP: SHA-256
  `4b317ec8a5aa3c54035f35bfc2cc8215f02e141102790ad9527a86d0d42f029f`.
  The staged extension catalog contains 13 entries and canonicalizes to
  `440b1c6dff6156f06c7993658726d440c093bba36b97acb9ad49c73fe084ec21`.
  It is identical to the catalog in the Windows record.
- Served files matched the staged catalog: `connector.js`
  `5596b03cf4c67868ec1e610664b566a967a82e510032b38a593b9fd32b784661`
  (128,070 bytes), `connector.html`
  `ec4478422db840af4038d73e8c217f88a86d553c660da30c7725864dd73df88b`
  (8,616 bytes), and `background.js`
  `8e3258a970d5726470625bd9bf8fa11f9d5139df92e0ca709cbcf1756a7b5b1c`
  (7,430 bytes). The identity and load-unpacked records support an unmodified
  unpacked load; no rebuild or reproducibility claim is made.
- macOS host: source executable
  `25f6ab2ef872bff8512a7c1bce496f5151a7ae0fb0f6d13d070c2223bdc82fa9`,
  post-sign bundle executable
  `05655ed8d4e962df808191c1e7bfb9906c5bdb6bb4b4355a146a4915baeca3e3`
  (1,881,264 bytes). The actual `codesign` readback reports valid on disk,
  satisfies the designated requirement, an Apple-generic anchor, identifier
  `me.nagaev.veles.otp23.poc`, and team `7T39M26Z99`. The old and new designated
  requirement lines are identical. This is an Apple Development-signed test
  vehicle, not ad-hoc and not production distribution/notarization.
- Evaluator: `macos-smoke.mjs` SHA-256
  `2afe92dc9ac8cad774f565e419c40557b5f72b55c544f224aec490d90936845f`.
  It is a 17-added/15-removed-line adaptation of the reviewed Windows driver
  `7e03aa8867b051be0ffe01416b155ad03c71deeceae2d90dbd001ddc2fe9676d`.
  The evaluator is retained privately and is not published as a public
  reproducer.
- Android fixture: app APK
  `3e2a6c41bb0eb6d3ef9b07f478d29b0fe417dcf7df1915c0b6482a754c059f08`,
  instrumentation APK
  `afadd554b475013f62102c72b62c0badc46706d6f9f3e26dfe52ede4687fac84`,
  signing certificate
  `9ef3104b934a37b111726ff91c7cb5ec5affc0e8b6a90742ed0bc55501969df9`.
  The original Windows `apksigner` witness is byte-identical and transferred;
  it was **not re-executed on macOS**, and no app install, clear, or signing
  change was made on the Mac. Installed-byte continuity is a controller
  observation, not a fresh independent pull or signer execution.

## Android signer/update chain

The retained signerreference is the original Windows witness, 1,977 bytes,
SHA-256 `c06806c8d32cdd4f617614a94e36079d5618672349c97d14ccb815860ceb2ef8`.
It records six successful `apksigner` verification groups for the candidate and
post-install app/instrumentation APKs, all with the same certificate digest.
The Mac run only references these bytes; it does not add a new signing result.
This supports the same approved physical fixture and same-key in-place update
chain. It is not a macOS signing result and not a byte-for-byte app-data audit.

## Bounded transfers

There are 13 synthetic request/reply exchanges: 9 in the main phase and 4 in
the reopen phase. Request hashes were reconstructed from the approved fixture
algorithm (binary prefix `56 45 4c 01`, remaining byte `(i + seed) & 255`;
reply changes only byte index 3 to `0x02`). Reply hashes match the actual
recorded receipts. The full list, including seeds and per-exchange receipts, is
in `summary.json`.

| Phase | Count | Receipt sequence mapping |
| --- | --- | --- |
| Main | 9 | B4, B508, A4, B509, A508, A509, B4096, A4096, B-survivor-4096 |
| Reopen | 4 | B4, B508, A4, A508 |

The phone side reports 15 notification API successes and 15 completion
callbacks on A, and 24 and 24 on B; every API and callback status was `0`, with
no unbound callbacks, pending drops, callbacks after close, truncation, or
dropped peers. The complete-record/reply totals are 6/6 (A) and 7/7 (B) at MTU
517. The 39 fragments/callbacks are not 39 application records; the
complete/generated totals are 6 A + 7 B = 13. The 4-byte cases exercise the
fixed-prefix boundary only and do not prove content-based peer isolation; the
longer seeded payloads have distinct hashes per peer. A `sent` event means
transport/GATT write completion, **not** an application reply or
acknowledgement.

The phone telemetry does **not** contain per-record content digests, and the
phone and Mac wall clocks are not calibrated. Endpoint association is
operational, not cryptographic, and no subsecond end-to-end latency is
inferred.

## Fixture runner witnesses

A's final JUnit stdout summary is **not established**: its instrumentation
stdout stops at the test-class heading and its stderr is empty. A separate,
actual post-run Android TestRunner log records one test with 0 failed and 0
ignored, including one method start and finish with no intervening restart.
That log is a distinct witness and is **not** substituted into A's stdout.
B's instrumentation stdout actually contains `OK (1 test)`; its stderr is also
empty. Both final telemetry documents are independently clean.

## Environment, registration, and permissions

- **Contemporaneous macOS environment:** preparation output reports macOS
  **26.5.2 / 25F84**, arm64 and Aqua. Pre-main readback again records OS
  26.5.2, Chrome **156.0.8078.81**, a clean pinned source, the manifest and
  evaluator digests. CDP version and both phase observations agree. These are
  actual before/phase observations, unlike the Windows post-run supplement.
- **Registration:** the manifest targets the exact signed-bundle executable
  and only the actual extension origin. It is installed in the isolated
  Chrome profile (`NativeMessagingHosts` only), not a user/system global
  location. The manifest digest is
  `1eed352fe25edad7c44b9775fa847ba1d065cc26d1b39efc4e351c5ea2c7abb3`
  (344 bytes), and the cleanup copy is byte-identical. Cleanup records removal,
  the global NMH snapshot bodies are equal before/after, the owned Chrome was
  closed through the browser endpoint, and no owned host process remained. This
  is scoped cleanup evidence, not a whole-machine audit.
- **Bluetooth:** a real scan discovered both fixtures without a prompt or
  denial in the already-authorized signed identity/context. No TCC reset,
  grant, or revocation was performed and no permission database is retained.
  No denial/recovery experiment exists, so denial recovery is **NOT RUN**.

## Lifecycle and launcher limits

Process observations are sampled, not continuous. Main begins at
`11:02:07.929Z`; an exact-executable process is sampled from `11:02:09` through
`11:02:22Z`, and the after-main observation at `11:02:26Z` reports none. Reopen
begins at `11:02:36.279Z`; a different process/start is sampled from `11:02:38Z`,
still present with the connector open at phase end `11:02:43Z` through
`11:02:55Z`, and none remains at `11:02:59Z` after closure. The 101 observer
ticks bracket both phases at roughly one-second polling with gaps up to two
seconds. Raw PIDs are not published. This supports observed disappearance and
fresh launch, not exact exit latency, causal proof of stdin EOF, or strict
stdout framing. The superseded observer-v1 output matches the observer/awk
command lines; its original source was edited in place and is not retained, so
it must not be counted as multiple host processes.

The launcher probes invoke the production export from a controlled extension
page; this is not a literal toolbar click, not the worker module instance, and
not a browser-wide native-port audit.

## Catalog, archive, and preserved originals

The archived evidence file is `mac-evidence.tar.gz`, 61,003 bytes, SHA-256
`d19cad4f51d2a6247756425edead48249b131c241c5aa1f314569e5b8458e10f`.
It contains 73 regular files. The preserved catalog also lists 73 entries.
**72 entries match** the mirror, and all **73 archive members** match the
mirror byte-for-byte.

The catalog's own line is **stale**: it records
`06c28889f6fbff36ef982de168866e78bc8769a23a0a14dfe4e2d7b4d3701d21`, while the
actual preserved catalog hashes to
`4a9509ddeb2b4db529426d21fc19ab1ac28b347d5d8313c3edce8c00a59c882f`. This is a
minor self-entry finding, not evidence that the payload files differ from the
archive. The original is preserved unchanged; a future derivative catalog
should omit its self-entry and use an external digest. The published checker
does **not** add a self-hash loop.

Two original-source boundaries are retained rather than repaired:

- the original pre-launch file was deliberately kept private and is **not
  archived**; the archived copy is a one-line-redacted derivative. The original
  SHA-256 is `a030249e2f1c18a7e19cceeef65d5c4feeee8e16d9b4d167720cc3f5b7122432`.
  The completeness of that redaction cannot be verified from the derivative;
- the superseded observer-v1 source was edited in place and is unavailable. Its
  output is preserved with SHA-256
  `6f42d85c6939baf280df9a2c45e80226ddd5382670998c64c1202b8d9a6125f8`.

## NOT ESTABLISHED

- Full OTP-07/Gate/BGC, unattached/background delivery, real OTP/copy/auth
  flows, adversarial stale inbound-event filtering, accidental-loss recovery,
  installer behavior, production signing/distribution, and notarization were
  not exercised.
- GATT write absence for the 4097-byte oversize rejection, calibrated
  cross-device timing, authenticated fixture identity, and phone-side
  per-record content digests are not present. Browser content hashes must not
  be described as phone content hashes.
- Strict EOF/host-exit/stdout framing is not established by process sampling.
- Bluetooth permission denial/recovery was not exercised.
- The original pre-launch bytes and the observer-v1 source are unavailable
  within the published/archived evidence set, as described above.

These are explicit evidence boundaries, not invented product failures or
requests to perform device work. Retain all private originals unchanged.

## Review

A separate GPT-based reviewer independently reconciled the narrow transfers and
issued a final scoped disposition: **approved — bounded foreground/CDP macOS
smoke only, with limits**. Findings were **0 critical, 1 minor** (stale catalog
self-entry). The review retained the phone-content/clock, lifecycle, launcher,
permission-denial, and signing-scope limitations and did not approve full
OTP-07, packaging/notarization, background/strict lifecycle, or gate acceptance.

## Verify

```bash
python3 docs/evidence/otp07/macos-2026-11-04/check_summary.py
```

The checker recomputes the catalog hash and all 13 synthetic request/reply
hashes, checks receipt mapping, telemetry counters, signed test-vehicle and
developer-scope flags, environment provenance, registration/cleanup scope,
bounded observations, and catalog/archive accounting. It also rejects
private-path/identifier shapes in the published JSON. The ignored generator
additionally compares known raw private values before writing.
