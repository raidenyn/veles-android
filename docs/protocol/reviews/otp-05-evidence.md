# OTP-05: Chrome installation identity and trust storage

Implements issue #84 and `veles-v1.md` §5.4's Chrome storage mapping.

## Storage and API

- `web-extension/src/trust/identity.ts`: random 128-bit installation ID and
  non-extractable Web Crypto P-256 ECDSA signing key stored in one IndexedDB
  record (`veles-installation-v1`). Loads verify algorithm/usages, point validity
  and private/public correspondence using a fresh sign/verify challenge.
- `storage.ts`: a single versioned `chrome.storage.local` snapshot under
  `veles.trust.v1`, restricted with `TRUSTED_CONTEXTS` before all operations.
  Neither sync storage nor a content-script message bridge is used.
- `repository.ts`: identity-bound, whole-snapshot validation/replacement under
  an origin-wide Web Lock. Unknown fields, malformed points/IDs, invalid
  fingerprints, duplicate peers, invalid states and partial storage fail closed
  with a generic re-pair error. No automatic identity regeneration on failure.
  Infrastructure failures instead return `TrustUnavailableError` (retry later),
  never a suggestion to erase valid trust.
- `continuity.ts`: pending records belong to a connector document, whose liveness
  is represented by a document-lifetime Web Lock. Only the owner evaluates its
  monotonic deadline or activates it. Observers preserve another live owner's
  pending metadata; after owner loss it is discarded. Active activation mirrors
  and fully active records have no client-side expiry or continuity dependence.
- `trust.js` is a production ES-module entry exporting `ChromeTrustRepository`.
  The worker restricts local storage on every load. Future connector code creates
  the repository in its document and supplies a protocol authentication verifier.
  The default verifier rejects every proof. The verifier must bind each stage to
  the current session, peer identity, ET and stage-specific MAC/signature; these
  cryptographic flows belong to OTP-09/OTP-10.

`addPending` accepts only a phone (device type 0), canonical uncompressed P-256
key, 128-bit ID, 32-byte ET and a label of at most 32 UTF-8 bytes. It never replaces
an existing peer. `confirm(complete)` persists `active_activation_pending` after
the protocol verifier accepts activation completion; `confirm(phone_active)`
upgrades it after authenticated phone-active evidence. Idempotent duplicates
require fresh verification too. No full-session authorization is inferred from
transport hints or local record state alone.

`authenticated` records last-seen time and optionally a bounded (1..256 UTF-8
bytes, no ASCII controls) connection hint only after verification. Hint lookup is
not a trust-selection API. `forgetHint` and `revoke` affect only the selected
peer. `reset` is explicit all-trust recovery, first writing an invalid durable
marker so interruption cannot expose stale trust. There is no recovery UI in
this task. Future UI must explain phone-side revocation when an expired pending
installation still exists on the phone before re-pairing (§5.4).

Initialization spans two browser stores: an interrupted first initialization is
conservatively unavailable until explicit reset rather than silently repaired.
Each store's record replacement is atomic; this does not claim a distributed
transaction across IndexedDB and Chrome storage. A local ceiling of 256 peers
bounds validation work, independently of transport connection capacity.

## Verification

```sh
cd web-extension
npm ci --ignore-scripts
npm run format:check
npm run lint
npm run typecheck
npm test
npm run build
npm run test:bundle
npx playwright-core install --with-deps chromium
npm run test:browser
```

The Node suite includes real Web Crypto with fake IndexedDB for deterministic
corruption, failure, clock and concurrency cases. Browser tests use Playwright's
pinned Chromium in an ephemeral profile with an unpacked MV3 extension. They
import the **production `trust.js`**, with a separately bundled, test-only
fixture that is never packaged. Only the test copy's content-script URL match is
changed to a loopback HTTP fixture; the production permission list and worker
are used. Checks cover:

- stable identity and non-exportability before/after a real browser restart;
- pending deletion on document loss while activation mirrors and active records
  survive document/browser restart;
- a second live document cannot erase another document's pending record;
- a real content script receives a Chrome local-storage access denial and cannot
  see the extension-origin IndexedDB, including immediately after browser restart
  before any extension page reapplies access restrictions;
- corruption and missing keys fail closed;
- profile removal produces an empty store and new installation identity;
- captured page logs contain neither the identity ID nor public key.

These tests exercise storage, not SPAKE2, native transport or enrollment MACs.
The fixture's proof verifier is intentionally test-only. Its corruption helpers
bundle source identity/model/storage adapters separately; the repository under
test is imported from production `trust.js`. Browser profile removal
is tested directly; extension removal uses Chromium's extension-owned storage
lifecycle rather than an external application-managed file.

Local environment: Chromium 145.0.7632.6 (Playwright 1.58.2). On this Ubuntu 26.04
host, Playwright's downloader did not complete extraction; the same pinned Chrome
for Testing archive was downloaded/extracted using curl/unzip and passed via
`VELES_TEST_CHROMIUM`. That optional override affects only local test launch.
PR/master CI installs the pinned browser normally on its supported Ubuntu runner;
the release artifact producer does not run downloaded browser code.

Required repository gate: `JAVA_HOME="$JAVA_ROOT/zulu21" ./gradlew spotlessCheck
detekt lintDebug` succeeds. The CI raw-`android.util.Log` guard is also run before
PR creation. Android lint reports existing warnings, with no errors.

Final review: see [Opus 5.5 findings and disposition](otp-05-opus-disposition.md).
The exact-pin npm install-script, license and remote-code policy checks pass.
The aggregate supply-chain verifier was run but stops on an unchanged Rust SBOM
coverage mismatch (`pkg:cargo/itoa@1.0.18` missing from `rust.cdx.json`); it is not
claimed passing.
