# OTP-24 inbound diagnostics

## Intent

Locate the loss of synthetic replies on Windows BLE links using correlated evidence rather than speculative retries or pacing. The existing physical fixture records completed inbound record hashes and outgoing notification results; it does not capture fragment receipt, reassembly outcomes, or reply-queue decisions.

## Approach

Extend the existing opt-in debug-only bounded transport telemetry at the actual GATT receive, reassembly and reply-queue boundaries. Compared with inferring receipt from host writes, direct boundary observations can distinguish missing fragments from rejection and queue loss. Reimplementing reassembly in the test fixture would risk observing behavior different from the real transport.

Record per-peer timestamps, fragment counts and metadata, reassembly outcomes, completed record hashes, reply generation and queue results, never payload bytes or raw addresses. Surface truncation and dropped evidence explicitly. Preserve transport behavior and ensure diagnostics are inactive outside opt-in debug runs.

Build the APKs on Linux with the already-authorized replacement fixture key. Verify both installed and built certificates before updating in place. Run controlled one-link, two-link idle and two-link failure comparisons, first slower and then at 200 ms, with globally unique records and matching phone/count/timing. Correlate host writes, phone receipts, replies and host messages; do not interpret a local write completion as phone receipt.

Only after evidence isolates a component, add a reproducing regression test before fixing production behavior. Validate on native Windows/MSVC and physical hardware and arrange independent evidence review. No retries or pacing workaround without evidence.
