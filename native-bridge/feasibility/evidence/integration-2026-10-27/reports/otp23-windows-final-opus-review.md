# OTP-23 Windows awaited-close fix and physical comparison — final independent Opus review

**Reviewer metadata (read from the transcript on disk):** Claude Code session `2fe20385-5ef1-4622-bf94-00b8c0c6477e`, every assistant message `claude-opus-5-5` (380 at capture), CLI `2.1.311`, 0 subagents.
- Read-only: no edits, hardware, or GitHub.
- I did not touch the Mac F-1..F-3 work.

**Fingerprints (2026-10-27T17:27:11Z):** root HEAD `dc5d127`, whole uncommitted `git diff` sha256 `a4a47935…8e14`. This includes the concurrent Mac edits, so the Windows files are pinned below.

| Item | sha256 |
|---|---|
| `src/winrt.rs` | `2ef50ec18fd96907…` |
| `src/closejoin.rs` | `25e44f6db140941c…` |
| `src/lib.rs` | `79bbf21fdd28b28b…` |
| `scripts/windows_closejoin_compare.py` | `86781a91b2714bcf…` (byte-identical to `scripts-used/`) |
| Evidence `windows-cleanup-2026-10-27/SHA256SUMS` | sha256 `8b30d332…3706`, 48/48 OK, list matches disk |
| Host binary | `8521de4d…ae33` (PC CertUtil in `gates/windows-binary-build.log`) |

**One binary, one source:** `compare-2/result.json` `source_sha256` records every `src/*.rs` of the PC build. **All of them equal the live Linux files** (I recomputed; 0 mismatches).

**Gates I ran:**
- `cargo fmt --check`: OK.
- Windows-target `clippy --all-targets -D warnings`: OK.
- Linux `cargo test --lib`: 8 OK.
- `py_compile` of the driver: OK.
- Feasibility Python suite: 348 OK.

## Earlier preflight findings: resolved
- **B-1:** the test compiles (`u64` tokens). The Windows log shows 13/13 OK.
- **I-1 (real-path regression):** `shutdown_joins_a_worker_retired_by_disconnect` drives `disconnect_link_mode` and then the factored `shutdown_join`, the same functions `Backend::shutdown` uses.
  - The red run against a HEAD-equivalent scratch (`gates/red-vs-head-scratch.log`, with the unused `closing` parameter) **FAILS** with "close is awaited when shutdown returns". The fixed tree passes.
  - `legacy_detached_close_is_unawaited…` shows that the legacy branch returns while the close is still running.
  - These tests pass the mode as a parameter, not through the environment, so they are parallel-safe.
  - The environment-flag run (`windows-cargo-test-env-flag-red.log`) turns the env-dependent queueing test red, which proves the switch is live in the shipped binary.
- **I-2 (bounded):** `reap_finished` runs on every retire. The churn tests (Windows and Linux) bound the sink to in-flight closes. A single 2 s budget covers live and retired handles together, and a wedged worker stays bounded.
- **I-3 (traces):** every close step is traced per connection with `ok` or `err hresult=0x…` and elapsed ms. `released` is worded "objects closed; link-down unconfirmed". The shutdown line reads `joined X/Y`.
- **I-4 (common anchor):** `conn N disconnect requested epoch_ms` appears in **both** arms. `connect worker started epoch_ms` appears in the second host.

## Physical comparison `compare-2`: verified from the raw traces
- All six first hosts linked **both** advertisers. The hints `…b39d` and `…5656` appear in every run, so no run is vacuous.
- The quiescence precondition is recorded per run (no host process, and ≥12 s since the previous exit for runs 2–6).
- Gaps measured from the common anchor: 1500, 1500, 1500, 1501, 1501, 1500 ms.

| Arm | First host | Second host |
|---|---|---|
| Awaited (runs 1, 4, 5) | All 6 close steps `ok` per connection within 0–2 ms; `released` before `exit`; `shutdown joined 2/2 within 10ms` | Both phones `subscribed` in about 725–760 ms (fast discovery, so the OS link was still up) |
| Legacy (runs 2, 3, 6) | `closing worker detached` for each connection; only the two `Remove*` steps ran; `shutdown joined 0/0`; `exit` with no `service_close`, `maintain_false`, `session_close`, `device_close` or `released` | Both phones `write characteristics GattCommunicationStatus=3` → `access_denied` |

So: **3 of 3 awaited clean, 3 of 3 legacy denied**, with matched anchor gaps and a close cost of only 0–2 ms. Delay cannot explain the difference.

The fast discovery in the awaited arm also refines the earlier association ("link still up implies denial"): the link stays up in both arms. What decides the outcome is whether the previous process released its GATT session and device objects before dying.

**Advertisers and source validity:** at capture time (17:11Z) each phone had exactly **one** active `me.nagaev.veles.otp23` advertiser (6T started 17:05:26Z, 10 Pro started 17:05:24Z). All earlier veles advertising sets had stopped. The two discovered devices are therefore the two phones.

**`compare-1` (zero links):** it is retained. Every run is marked `invalid` by the driver's validity rule (`conns=[]`), so it was not counted as vacuous passes.

## Claim scope (correct as written, with one sharpening)
- **Supported:** at a 1.5 s anchor-matched gap, under direct launch, on this PC, account and these phones, the unawaited close **caused** the AccessDenied, and the awaited close **removed** it (n = 3 per arm).
- **Not supported:**
  - a threshold;
  - the Chrome-launched path (same host code, untested);
  - other accounts;
  - all Windows denials in general.
- **A residual that a product must still handle:** a host that dies without running its close (crash, kill, renderer discard killing the port) leaves the same window. The distinct retryable classification and bounded reconnect policy stay in #87/#88. W-07/W-08 stay BLOCKED until their own acceptance rerun on the identity that is chosen. Do not relabel all permission denials as transient.

## IMPORTANT (minimum fixes before commit)
**I-A. The new comparison driver has no regression for its established bug, and its validity rule is weaker than the protocol.**
- `compare-1` was caused by a real driver bug (`all` token resolution). That bug is fixed but untested.
- `validity_for_arm` rejects zero connections, but it does **not** require:
  - exactly the two expected connections;
  - the second host to have attempted both phones;
  - the gap to be within a tolerance of the target (currently only recorded).
- **Fix:**
  - Add `scripts/test_windows_closejoin_compare.py` with pure tests covering:
    - `all`/`any1` token resolution (the `compare-1` failure);
    - rejection of empty or one-connection first phases;
    - awaited-arm invalidity when `released` follows `exit` or the shutdown is not N/N;
    - gap outside tolerance → invalid.
  - Add the two-connection and gap-tolerance conditions to the validity rule.
- `compare-2` already satisfies these conditions; I verified this, so no physical rerun is needed.

**I-B. The 6T fixture account mixes observation and inference. Correct the FINDING and result text.**
- **Observed (raw):**
  - the 6T round has no JUnit result;
  - `fixture-6T-forcestop-record.txt` shows a `me.nagaev.veles.otp23.test` process (uid `u0_a221`) still alive after the first force-stop, then none after both force-stops;
  - its "before force-stop" section is **empty**.
- **Not evidenced in the raws:**
  - "FOUR stale instrumentation sessions": inferred from **local** stale adb client processes, which do not prove active remote instrumentations;
  - the "app-data uid anomaly (`u0_a220` vs `u0_a221`)": no data-directory ownership output is preserved. An instrumentation APK has its own uid, so differing uids may well be normal.
- The same 6T produced `OK (1 test)` in the earlier macOS prewarm round. A standing data-directory defect is therefore contradicted, not established.
- **Fix:** state "no JUnit result; a test process outlived the stop marker and the first force-stop; cause unproven". Either delete the stale-sessions and uid claims, or label them operator-inferred with no raw.
- The comparison's validity does not depend on this: both advertisers were live and every first host linked both phones. The 5× duplicated connection-history lines appear on **both** phones (the 10 Pro had a clean JUnit run), so they are not evidence of 6T stale sessions either.

## MINOR
- `legacy_detached_close_is_unawaited…` asserts `shutdown` returned in under 150 ms against a 200 ms worker. That is fine, but it could flake on a heavily loaded CI. Widen the worker sleep (e.g. 1 s, with a 500 ms assert).
- The tail of the FINDING's §3 "Honest notes" is consistent. Keep H-2/H-4 as "not excluded generally".

## Verdict
- **The defect is fixed.** The fix is regression-backed: red against HEAD logic, green fixed, and the legacy switch is proven live. It is bounded and traced.
- **The causal claim for AccessDenied at the tested point is supported** by 3/3 versus 3/3 at matched 1.5 s anchor gaps, with all runs valid, non-vacuous and on a single binary and source.
- **Publishable as OTP-23 feasibility-host investigation work** after I-A (driver tests and stricter validity) and I-B (fixture-text correction). Neither requires hardware.
- **No matrix promotion:** W-07/W-08 stay BLOCKED, and OTP-24 is not authorised.
