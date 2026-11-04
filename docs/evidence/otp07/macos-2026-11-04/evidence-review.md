# Independent macOS physical evidence review — sanitized public version

## Verdict: approve — bounded foreground/CDP smoke only

**PASS for the recorded macOS foreground smoke; no blocking finding.** This is
not full OTP-07 acceptance, a Gate/BGC result, notarization or production
distribution approval, or a production identity migration. Findings:
**0 critical, 1 minor** (catalog self-entry, below).

The review used the original-byte private mirror, sibling phone telemetry and
runner witnesses, evaluator source, pinned source objects, and the locally
retained extension ZIP. It performed read-only hashing, archive-member
comparison, JSON cross-checks, and independent synthetic-byte calculations. No
agents, device operations, builds, test/self-test reruns, product edits, or
commits were performed. Paths, selectors, serials, raw handles, and other
private values are deliberately not reproduced here.

## Findings, ordered by severity

### Minor — the catalog's self-entry does not describe its final bytes

`remote/SHA256-CATALOG.txt:72` records its own hash as
`06c28889f6fbff36ef982de168866e78bc8769a23a0a14dfe4e2d7b4d3701d21`.
The actual preserved file hashes to
`4a9509ddeb2b4db529426d21fc19ab1ac28b347d5d8313c3edce8c00a59c882f`.
All **72 other listed entries match**. All **73 regular archive members**,
including this catalog, match the mirror byte-for-byte. This is a stale
self-entry, not evidence that the payload files differ from the supplied
archive. Do not describe this as a wholly successful catalog check. Preserve
the originals; a future derivative catalog should omit its self-entry and use
an external digest. The published checker does not add a self-hash loop. It
does not block the narrowly scoped result.

## Integrity and provenance

Independently calculated SHA-256:

| Artifact | SHA-256 |
| --- | --- |
| `mac-evidence.tar.gz` | `d19cad4f51d2a6247756425edead48249b131c241c5aa1f314569e5b8458e10f` |
| `remote/main.json` | `16aa5727b3a21dfbb50cebdbd59c05ec406176e536cd5296a3fc77773cc9be2d` |
| `remote/reopen.json` | `22ded06ff27c5788f680dd433271df2016be79863e566e3db00588a9f6f70924` |
| `remote/main.private.json` | `b7ab7ec126ffeebbf95efc6205ba08fb41f20f2aada1e0f87a7c80b6beb2d0f8` |
| `remote/reopen.private.json` | `6578308b61a62858be350866cba5091377cc3c329d8d7a54ff5251e485fb5337` |
| `macos-smoke.mjs` | `2afe92dc9ac8cad774f565e419c40557b5f72b55c544f224aec490d90936845f` |
| `fixture/A-transport.private.json` | `10064a55549690c62a0b8d9165a4dcfc15ead6cb97b9267ebe37d1fae94807d0` |
| `fixture/B-transport.private.json` | `490355e75c596f41fc17f58fde50676d6b3c0d33369141c72025b11c6a3e7e13` |

The archive is an integrity anchor, not cryptographic authentication of an
operator or hardware. Phone artifacts are separate controller captures, not
members silently added to the Mac archive. The archive contains no app bundle,
APK, or production binary. The extension ZIP exists locally and was
independently hashed and fully catalog-checked.

The evaluator's actual comparison with the reviewed Windows driver is **17
added / 15 removed lines**. The Windows baseline hashes to
`7e03aa8867b051be0ffe01416b155ad03c71deeceae2d90dbd001ddc2fe9676d`. This agrees
with the minimal-adaptation approval in the driver review; that earlier
approval alone was not treated as physical evidence.

## Claim-by-claim disposition

| Claim | Disposition | Independent basis / exact boundary |
| --- | --- | --- |
| Source identity | **PASS** | Git resolves `58b243d4a55ed18d6b3e3bd0dee05b2f505d7c24` to tree `aa5331e1b4f8a0874b5b877ca0838b1ac7d96c90`. Actual run readbacks and evaluator observations agree; tracked-clean source, not a claim that the review checkout itself is that commit. |
| Extension identity / serving | **PASS** | Independently hashed the local candidate ZIP: `4b317ec8a5aa3c54035f35bfc2cc8215f02e141102790ad9527a86d0d42f029f`. All 13 ZIP entries' names, sizes and hashes equal the Mac identity catalog and the Windows catalog. Main and reopen served connector JS/HTML/background digests and sizes match those actual ZIP members. The load response and observer source support unpacked loading; no rebuild/reproducibility claim. |
| A-alone → A+B binding and fixture continuity | **PASS, temporal/exclusive** | See binding reconstruction below. Identity is a controlled fixture association, not authenticated phone identity. |
| Main synthetic exchanges | **PASS: 9/9** | Independent request/reply reconstruction and every receipt-to-active-connection hash mapping agree. Eight boundary exchanges plus one B survivor exchange, not counts inferred from an aggregate PASS. |
| Reopen synthetic exchanges | **PASS: 4/4** | Independent reconstruction and mapping agree for two fresh A and two fresh B receipts. No old-handle receipt was accepted. |
| Oversize 4097 | **PASS, wire rejection only** | Actual outbound 4097 diagnostic followed by inbound `oversize_record` / `record`, with A still connected. No extra record in the retained audit. **Not** proof of zero GATT writes. |
| Deliberate disconnect / survivor / no automatic reconnect | **PASS, five-second scope** | UI disconnect, A absent, B retained, policy/port observation, no recovery scheduling/outbound recovery, then B's 4096 reply. This is deliberate-disconnect suppression, not recovery from accidental loss. |
| Main/reopen linkage and stale refusal | **PASS, local refusal** | Reopen hashes the actual main/private files, retains identical identity/bindings, starts with empty connections/records, and uses disjoint fresh handles. Both real old handles receive `local/unknown_handle` at `connection_handle`. No inbound stale event was injected. |
| Actual phone transport status and runner completion | **PASS with distinct witnesses** | Final phone events/counters, MTU and completion evidence reconstructed below. A's missing stdout summary is not replaced or inferred from B. |
| Signed Mac test vehicle / registration | **PASS at retained readback boundary** | Actual `codesign` output, identical old/new designated requirement, post-sign executable hash linkage, per-profile registration/readback and cleanup agree. **Not** production bundle/distribution/notarization validation. |

The main and reopen raw artifacts have empty failure/unmatched-record lists,
and all 13 retained record-audit entries are individually accounted for. These
are conclusions from their entries and calculations, not acceptance of their
top-level `status` fields.

## Binding and receipt reconstruction

1. A's single fixture interval starts at **10:56:14.910Z** and finishes at
   **11:03:49.804Z**. B's starts at **10:59:05.872Z** and finishes at
   **11:03:50.248Z** (phone wall-clock values). Both have the same requested run
   ID and one final telemetry document. A's TestRunner witness records one
   method start and finish, without an intervening restart. B's stdout reports
   one completed test. These corroborate the controller's continuous-session
   notes, not a complete OS process audit.
2. A-only capture has one device, one scan, and no connection at
   **10:58:05.614Z / 10:58:07.126Z**. A+B capture at
   **11:00:26.413Z / 11:00:27.921Z** retains the exact same scan handle,
   start/expiry, and A row, and adds exactly one B row. The retained diagnostic
   history has one start-scan and two device-found events, not a second scan.
   The captures use a real controller snapshot after a breakpoint-assisted real
   Search, followed by read-only polling.
3. A's row selector matches the A binding; the sole new row matches B. In each
   phase, device handle → selector → fixture reference and connection → device
   mappings agree. Both connected diagnostics precede that phase's receipts.
   The review independently SHA-256-hashed each private connection handle and
   compared it with **each** audit entry, rather than trusting `phoneRef` or row
   ordering. Main A receipts are sequences **3,5,6,8**; main B receipts
   **1,2,4,7,9**. Reopen A receipts are **3,4**; reopen B **1,2**. All reopen
   receipt hashes match fresh handles and none match either main handle.
4. Main private snapshots also retain all nine inbound-message diagnostics,
   whose handle/size/order agree with the audit. Two message-diagnostic times
   precede their record callback by 1 ms; this is not a content mismatch.
   Reopen does **not** retain a post-exchange raw diagnostic snapshot: its four
   observed receipt-handle hashes are in the original `recordAudit.records` and
   map to the private fresh connections. No additional reopen raw-wire
   transcript is claimed.

The exclusive A-alone/new-B inference is supported by these observations and
the controller notes. CoreBluetooth hints are unverified selectors, not BLE
addresses or cryptographic identities. The phone and Mac clocks were **not
calibrated**. Reported wall-clock intervals support coarse orchestration only;
they cannot establish cross-device latency or exact cross-clock causality.

### Independently reconstructed content hashes

For length `n`, request bytes are `56 45 4c 01` followed by
`(index + seed) & 255` for indices `4..n-1`. Reply changes only byte 3 to `02`.
This calculation was implemented independently in Python, without executing
the evaluator, then compared with every computed reply hash and size in the
actual receipt entries. It matches the inspected fixture transformation
(`app/src/main/java/me/nagaev/veles/sharing/SyntheticTransportFixture.kt:40–46`,
worktree-relative; file bytes also match the pinned git object) and the
evaluator's expected-hash construction.

In the table, M-A/M-B and R-A/R-B are review aliases for main and reopen active
connections, **not raw handles**. Each row's computed reply hash equals both
the observed reply and the separately indexed record-audit digest; length and
receipt time also agree, and every receipt is within its request deadline.

| Phase / exchange | Active alias; receipt seq | Length / seed | Computed request SHA-256 | Computed and observed reply SHA-256 |
| --- | --- | --- | --- | --- |
| Main A:4 | M-A; 3 | 4 / 11 | `70848327e00df6dd9b45a88b6219523cd35c7379070d89bfa978407fa07229bd` | `3c261d9e6dc0bae7b24919c80c749c05f71bbf3455b8ecfe79d4595e277d01ff` |
| Main A:508 | M-A; 5 | 508 / 12 | `959b66d65288fd0a1b7611bea6677ca6c9b22f93c4652751bf26fea6da0fc338` | `e6db9ab8f6d978b117bef957043e07aa03fd4cc1dba9fecb5665fd2ce0f9642e` |
| Main A:509 | M-A; 6 | 509 / 13 | `6715e8fba97860570419007968d1509e5a94e1beba032cadfb29642375c5d8b5` | `9997934c64c7f86be8537dd5ee95b35a0e1781c85d1bbe5de3e61be43ad77ec7` |
| Main A:4096 | M-A; 8 | 4096 / 14 | `8afade9cf3187460039b8fb239bfe85a16bc00f4fb09f05a074b0fb604f9d709` | `1d950f4310d43563745b3dd679cb3518f4d804b7f033c6759686ff540a8ef42d` |
| Main B:4 | M-B; 1 | 4 / 31 | `70848327e00df6dd9b45a88b6219523cd35c7379070d89bfa978407fa07229bd` | `3c261d9e6dc0bae7b24919c80c749c05f71bbf3455b8ecfe79d4595e277d01ff` |
| Main B:508 | M-B; 2 | 508 / 32 | `bab31e09ea297b9cd526ce12cd53140541aa9f4f4ca545910e40a4d616107b83` | `aede72517606dc1a8a6ea8dfde06e39c841f0f4bce57f9298c4615e45df7fb49` |
| Main B:509 | M-B; 4 | 509 / 33 | `911903c80de67f2db927acec3135024afebd9f8653d0e872a01563d6f1392f0b` | `6b6812c719e9a6ac34582ec33978efc0478277d65d62a225a5eba7b377be2aeb` |
| Main B survivor | M-B; 9 | 4096 / 131 | `1b35de2f3607bfab002105abd52a495fa7392bffd193a5b75f1c3cc8d74d4e1f` | `9f6f48e68a848e2230161389d366186af25e9b2e213ef857d0ecba37b57cab8a` |
| Main B:4096 | M-B; 7 | 4096 / 34 | `6148421167608edbc3466f4442bf377021ad0cba3d8699e77f8e170732798158` | `80144c4497d35f36467c8b776e77a579f53e3241f43d834bcc6ec1403e30a7b6` |
| Reopen A:4 | R-A; 3 | 4 / 201 | `70848327e00df6dd9b45a88b6219523cd35c7379070d89bfa978407fa07229bd` | `3c261d9e6dc0bae7b24919c80c749c05f71bbf3455b8ecfe79d4595e277d01ff` |
| Reopen A:508 | R-A; 4 | 508 / 211 | `d018ba4ba75c852e168d84d2beb2a5a32de144debaf07d5990f2db640725b273` | `b2dc37b0b0551b340e3c82330dd3125109aebeddb13aa242c9dfa01dd34c4cf1` |
| Reopen B:4 | R-B; 1 | 4 / 202 | `70848327e00df6dd9b45a88b6219523cd35c7379070d89bfa978407fa07229bd` | `3c261d9e6dc0bae7b24919c80c749c05f71bbf3455b8ecfe79d4595e277d01ff` |
| Reopen B:508 | R-B; 2 | 508 / 212 | `a40cec322b1fc2644398839d807e141a960a89a8e49389a1e15566309cfd1b6f` | `37cd2b7fcc50612344b4ba702a2a699afa50fde87fae51f8deb0d03122e7bfec` |

Four-byte requests have no seeded body and are identical across peers/phases:
they prove a boundary, **not content-based peer isolation**. Larger peer
payloads have distinct hashes. Request hashes above are reconstructions, not
claims that the phone recorded request-content digests.

## Actual Android transport and runner witnesses

The review read the actual event arrays, grouped API/callback events by bound
operation, verified matching operation sets and status 0 throughout, and
independently summed notification byte counts. No phone-side content-digest
projection was used to produce these status conclusions.

| Phone | Complete/generated record sizes in order | Notify API / callbacks | Attempted fragment bytes | Final state |
| --- | --- | --- | --- | --- |
| A | 4, 508, 509, 4096; then 4, 508 | 15 / 15, all 0 | 5689 | final, after service stopped, clean; MTU 517 |
| B | 4, 508, 509, 4096, 4096; then 4, 508 | 24 / 24, all 0 | 9821 | final, after service stopped, clean; MTU 517 |

Both have two connects, two subscribes and two disconnects, no event/peer
truncation, no nonzero API/callback status, unbound callback, unresolved/dropped
pending operation, or callback after close. The 39 fragments/callbacks are not
39 application records: the complete/generated counts are **6 A + 7 B = 13**.
The final phone documents corroborate transport delivery and coarse session
ordering, but do not contain per-record content digests or the Mac connection
handles. Browser content digests and actual phone transport statuses remain
different witnesses.

A's instrumentation stdout stops at the test-class heading, with empty stderr.
Its final JUnit stdout summary is **not established**. However, the separate,
post-run actual-phone TestRunner log records one method started, that method
finished, and the run finished with **1 test, 0 failed, 0 ignored**. Do not
synthesize `OK (1 test)` into A's stdout, and do not diagnose an assumed
transport bug from the missing stdout. B's stdout actually contains
**`OK (1 test)`**; its stderr is also empty. Both final telemetry documents are
independently clean.

The original Android `apksigner` witness reports the approved fixture
certificate and APK identities and is byte-identical to the Windows attempt-B
signer evidence. Mac-run installed-byte continuity is a controller observation,
**not** a fresh `apksigner` execution or independent installed-APK pull
performed by this review.

## Mac identity, environment, permissions, and lifecycle

- **Signed test vehicle:** actual `codesign` output says valid on disk,
  satisfies its designated requirement, Apple-generic anchor, identifier
  `me.nagaev.veles.otp23.poc`, team `7T39M26Z99`, and an Apple Development
  signature, not ad-hoc. The actual old/new designated-requirement lines are
  identical, including the certificate condition, not merely the same team or
  bundle-name string. No signer email is copied here.
- **Unsigned vs signed:** source executable readback is
  `25f6ab2ef872bff8512a7c1bce496f5151a7ae0fb0f6d13d070c2223bdc82fa9`;
  post-sign executable readback is
  `05655ed8d4e962df808191c1e7bfb9906c5bdb6bb4b4355a146a4915baeca3e3`.
  Identity, both evaluator results, signer witness, registration, and cleanup
  consistently use the latter. CDHash is a different value, not file SHA-256.
  The Mac bundle/executable were intentionally excluded from the archive; this
  review verifies the preserved readbacks and hash linkage, **not a new local
  `codesign` verification or direct hash of absent executable bytes**.
- **Registration and cleanup:** the retained stdio manifest targets that exact
  signed-bundle executable and only the actual extension origin. Independently
  verified manifest digest
  `1eed352fe25edad7c44b9775fa847ba1d065cc26d1b39efc4e351c5ea2c7abb3`,
  embedded-manifest equality, identity/external-file hashes, and byte-identical
  cleanup copy. Registration is in the isolated profile, not a user/system
  global location. Cleanup records exact readback and removal. Global manifest
  snapshot bodies are equal across cleanup. That is supporting evidence of no
  global cleanup changes, **not** a pre-run-to-post-run whole-machine audit; no
  global mutation is evidenced by the scoped artifacts. The owned Chrome was
  closed through the browser endpoint and no owned host process remained.
- **Contemporaneous environment:** actual preparation output reports macOS
  **26.5.2 / 25F84**, arm64 and Aqua. Pre-main readback again records OS 26.5.2,
  Chrome **156.0.8078.81**, GUI-session context, pinned source, manifest and
  evaluator digests. CDP version and both phase browser observations agree.
  Process state corroborates real Chrome/host process context and loopback-only
  CDP. These are actual before/phase observations, not metadata substituted
  from another run. Unlike the Windows record, environment evidence here is
  contemporaneous, not a post-run supplement.
- **Permissions:** successful real scan/device discovery demonstrates usable
  access in this already-authorized signed identity/context. The capture notes
  report no prompt/denial and no TCC reset, grant, or revocation. No
  permission-denial/recovery experiment or permission DB is present; none is
  claimed. Do not turn successful access into proof of denial recovery.
- **Process evidence, with begin/end context:** main begins **11:02:07.929Z**,
  ends around **11:02:23Z**, and its connector audit closes the tab. Actual v2
  samples show one exact-executable process from **11:02:09–22Z**; the
  after-main observation at **11:02:26Z** says none. Reopen begins
  **11:02:36.279Z**; samples show a different process/start from **11:02:38Z**,
  still present with the connector open at phase end **11:02:43Z**, through
  **11:02:55Z**, then none at **11:02:59Z** after closure. This supports
  observed disappearance and fresh launch, not exact exit latency, causal
  proof of stdin EOF, or a stdout-framing/lifecycle conformance test.
- **Observer provenance:** the retained observer source is the actual v2
  observer: exact executable-token equality, process/start context, and
  independent tick output. Its 101 ticks bracket both phases; polling is
  roughly one second with gaps up to two seconds, not continuous tracing. The
  superseded v1 output also matches observer/awk command lines, so it must not
  be counted as multiple host processes. Its original source was edited in
  place and is unavailable; **do not label the supplied v2 source as the
  original v1 source**.

## BLOCK / NOT ESTABLISHED and missing artifacts

**BLOCK for this bounded foreground smoke: none.** The minor self-entry issue
does not invalidate the external archive anchor or the other matching files.

**NOT ESTABLISHED (do not promote this result):**

- Full OTP-07/Gate/BGC, unattached/background delivery, real OTP/copy/auth flows,
  adversarial stale inbound-event filtering, accidental-loss recovery,
  installer behavior, production signing/distribution, and notarization were
  not exercised by these artifacts.
- GATT write absence for oversize, calibrated cross-device timing, authenticated
  fixture identity, and phone-side per-record content digests are not present.
  The Mac/browser content hashes must not be described as phone content hashes.
- Strict EOF/host-exit/stdout framing is not established by process sampling.
  No continuous process trace or EOF/stdout witness file is retained.
- The original pre-launch file is **missing from this mirror by deliberate
  privacy exclusion**, not an original that the review inspected. The retained
  `cleanup/03-pre-launch.redacted.txt` is a derivative; the redaction note gives
  original SHA-256
  `a030249e2f1c18a7e19cceeef65d5c4feeee8e16d9b4d167720cc3f5b7122432` and
  says one other-user process line was removed. The derivative bytes can be
  verified, **not** the absent original or the completeness of that redaction.
- Original sampler-v1 source and the early inline close-target/browser command
  sources were not retained. A's completed stdout summary is absent, but the
  separate actual TestRunner witness supplies the stated method/run completion.
  Bundle/binary and source-bundle bytes are excluded as documented; the
  extension ZIP does exist locally and was independently hashed and fully
  catalog-checked.

These are explicit evidence boundaries, not invented product failures or
requests to perform device work. Retain all private originals unchanged.
