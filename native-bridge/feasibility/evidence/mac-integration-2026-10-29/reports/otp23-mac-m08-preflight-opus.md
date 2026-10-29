# OTP-23 Mac M-08 resume — independent read-only preflight (Opus 5.5)

## Identity and scope

- **Role:** independent preflight reviewer, read-only. I changed no code, evidence, matrix or docs, and
  ran no subagents.
- **Files written:** only this report, plus scratch scripts in `/tmp/opus-mac-preflight/`.
- **Actual metadata,** read from envelope fields only of the Claude Code log
  `~/.claude/projects/-home-dev-repos-veles-android/1b447229-ceaa-4cee-a5be-2181dfdd8838.jsonl`:
  - Claude Code `2.1.312`.
  - Assistant records: `claude-opus-5-5` × 231 (all `effort: high`) and `<synthetic>` × 1.
  - Captured at `2026-10-28T16:00:05Z`.
  - This is the same session as my v4 and integrated reviews.
- **Repository state:** HEAD `9c3ddc54a3721eced84e49f02ed76ef156f0baed`. The only worktree change is
  the untracked `native-bridge/feasibility/evidence/mac-resume-2026-10-28/`. Its `SHA256SUMS`
  verifies 32/32.
- **Read-only Mac probes** over SSH (`raiden@192.168.11.116`, the given key):
  - `sw_vers`, `lsof -v`, `lsof -h`, and `man lsof`;
  - `lsof -nP -a -p $$ -i` with and without `-w`, run against the probing shell itself;
  - `mount | grep translocation` and `netstat -anv -p tcp | head -3`.

  No browser, TCC, fixture, build, signing, registration, sudo or mount action. The temporary files
  I created were removed.
- **Unchanged:** the matrix stays at 23/6/3 NO-GO, M-08 stays BLOCKED, and OTP-24 is not authorized.

## Bottom line (prioritized)

1. **P0 — a new deterministic blocker that GLM's result did not identify: a retake would still fail
   after fixing lsof.**
   - **What the driver requires:** HEAD `scenario_matrix.py` (9c3ddc5) requires `min_released=2` on
     every platform for shutdown, port-disconnect, tab-close and failure-isolation (`:594/598/602/616`),
     and for direct stdin-EOF (`:800`). Only `require_join` is limited to Windows.
   - **What the fixture does:** the approved Mac fixture `e7c716c5…` (source `cd971f0`) writes
     `conn N released` only *after* an awaited `release_device` (`cd971f0:src/macos.rs:557-559`).
     On EOF and shutdown the process exits before that, so the trace has the exit line and
     `command channel closed`, but no release line.
   - **Evidence:**
     - Every historical Mac lifecycle trace has 0 `released` lines (failure-isolation has 1, from
       its explicit disconnect).
     - Every `mac-resume-2026-10-28/lifecycle-1/*.host-trace.log` has 0.
   - **Consequence:** with the lsof issue fixed, every Mac teardown would still FAIL with
     `only 0 connection release line(s), expected >= 2`.
   - **This is a driver-criterion mismatch, not a host defect.** The phones see the links released
     at host exit; see Task 3.
2. **P1 — lsof endpoint channel.**
   - **Reject `-w`:** it suppresses every lsof warning, as the Mac's own man page documents. GLM's
     claim that it hides only harmless warnings is unproven, and this particular warning literally
     says "Output information may be incomplete".
   - **Recommended:** a precise, recorded classifier for exactly this foreign-nullfs mount-stat
     warning, confined to `macos_poc.py` (not frozen). Every other diagnostic stays `unavailable`,
     and both positive controls stay mandatory.
3. **Task 2 — the direct SSH stdin-EOF launch is not required.** M-08's stdin-EOF clause is
   satisfiable by the Chrome-provenance `port.disconnect()` and tab-close runs, by the same reading
   that the Windows W-08 PASS used and that was independently approved. No new grant or waiver is
   needed. The direct path stays BLOCKED (permission pending) as a retained diagnostic and is
   never called PASS.
4. **Task 3 — the GLM Mac clock probe can't carry a 30 s bound** (±15.8/16.8 s). An **in-band causal
   anchor** from data already captured binds phone clock to host-trace time to within about ±57 ms,
   with no absolute Mac anchor and no host change. I validated it on the retained lifecycle-1 data.
5. **Task 4:**
   - **M-04:** can be met by a minimal connector-only guidance change plus an owner-performed toggle
     OFF and back ON. No waiver needed, apart from the prompt-owner clause.
   - **M-03 and M-11:** both need explicit owner decisions. M-11 must use a **distinct identity**, so
     that it cannot disturb the approved fixture's Bluetooth grant.

## Task 1 — lsof endpoint channel

**Verified facts**

- **Platform:** macOS 26.5.2 (25F84), lsof 4.91 (libproc-based). `lsof -h` shows `+|-w` with
  warnings enabled ("Warnings (+)"), `/dev` warnings disabled.
- **Actual stderr,** captured live on the probing shell, rc=1 and 0 stdout rows:
  ```
  lsof: WARNING: can't stat() nullfs file system /private/var/folders/yc/d7qkpc795mz8nprkhm8xlpr80000gp/T/AppTranslocation/7BA7CCAC-123C-4593-9838-F34CA30F033C
        Output information may be incomplete.
        assuming "dev=2e000004" from mount table
  ```
- **With `-w`:** rc=1, 0 stdout rows, and 0 stderr bytes. `-w` only hides the text; nothing else
  changes.
- **Mount table:** exactly one translocation mount, `(nullfs, local, nodev, nosuid, read-only, nobrowse,
  mounted by <another local user>)`.
- **The captured raws never recorded this stderr.** `macos_poc.lsof_endpoints`
  (`macos_poc.py:424-444`) returns `None` and drops it. The exact text exists only in GLM's
  stderr/event logs and prose. This is an instrumentation gap in its own right.

**Documented semantics (Mac `man lsof`)**

- **`+|-w`:** "Enables (+) or disables (-) the suppression of warning messages." This is global. The
  man page also says `-w` suppresses the alternate-device report ("You can suppress the report by
  specifying the -w option"). Nothing restricts it to non-errors.
- **`+|-e s`** (per-filesystem exemption) is "currently implemented only for Linux", so there is no
  precise lsof-native exclude on macOS.
- **`-b`** avoids stat/lstat/readlink but *adds* warnings, so it doesn't help.
- **ALTERNATE DEVICE NUMBERS:** when stat of a mounted filesystem is blocked, Darwin obtains the device
  number from the mount table. That is the third line above, so lsof recovered the device number.

**Assessment**

- **Why the warning doesn't touch the socket census:** it comes from mount-table setup. The census we
  need (`-a -p PID -i`) enumerates the process's socket descriptors through libproc, not files on that
  nullfs filesystem. The mandatory same-sample control (Chrome's CDP listener found by the same lsof
  call) proves in-run that socket enumeration works despite the warning.
- **But `-w` would hide *any* future warning** (another mount, a different incompleteness), which is
  exactly the "blind masking" that I-1 forbids. **Do not adopt `-w`.**
- **Environment options are out of scope:** `sudo umount` of another user's mount, or quitting or
  ejecting another user's app, is excluded by the constraints.
- **netstat is not a drop-in replacement:** `netstat -anv` has a `process:pid` column on this macOS.
  But that column is the socket's last-user pid, not file-descriptor ownership, and switching would be
  a new instrument with a new parser and a new review. I don't recommend it as the primary channel;
  at most it is an optional cross-check.

**Recommended minimal fix: change `macos_poc.py` only, keeping `scenario_matrix.py` frozen**

1. Add a pure classifier, `classify_lsof_stderr(stderr, foreign_nullfs_mounts) -> (benign: bool, records)`.
   - It accepts stderr only if it consists **entirely** of one or more exact 3-line blocks:
     - `^lsof: WARNING: can't stat\(\) nullfs file system (/\S+)$`
     - `^\s+Output information may be incomplete\.$`
     - `^\s+assuming "dev=[0-9a-f]+" from mount table$`
   - Each block's path must be a member of `foreign_nullfs_mounts`.
   - Anything else (an extra line, a missing `assuming dev` line, another filesystem type, a path
     not in the set, any other text) → not benign → `None`.
2. Build `foreign_nullfs_mounts` from `/sbin/mount` output, once per process (cached).
   - Include only lines of the form `… on <path> (nullfs, …, mounted by <name>)` where `<name>` is
     not the current user (`pwd.getpwuid(os.getuid()).pw_name`).
   - If `mount` fails or can't be parsed, use an empty set, so the warning stays `unavailable`.
3. In `lsof_endpoints`, keep the argv unchanged (no `-w`) and keep every existing rule:
   - rc must be 0 or 1;
   - parse failure → `None`;
   - `[]` only for a successful query.
   - Replace "any stderr → None" with "stderr is empty **or** classified benign **and recorded** → continue".
4. **Recording is mandatory for acceptance, with no frozen-file edit:**
   - When env `VELES_OTP23_LSOF_DIAG=<run-dir>/lsof-diagnostics.jsonl` is set, append one JSON line
     per invocation: `{wall_ms, pid, argv, rc, stdout_rows, stderr_verbatim, classification, mount_snapshot_sha256}`.
   - Write the mount-table snapshot once to `<run-dir>/mount-nullfs.txt`, with the owner name
     replaced by `uid≠self`.
   - A benign warning is accepted **only if** the record was written. If the env var is unset or the
     write fails → `None`. This keeps the default fail-closed and makes every accepted warning
     auditable.
5. **Unit tests** in `test_macos_poc.py` (portable, mocked `subprocess`):
   - exact block plus rows → rows;
   - exact block, rc=1, no rows → `[]`;
   - two exact blocks for two foreign mounts → rows;
   - block without the `assuming dev` line → `None`;
   - path not in the foreign set → `None`;
   - mount owned by self → `None`;
   - any extra stderr line → `None`;
   - a non-nullfs filesystem type → `None`;
   - rc 2 with the block → `None`;
   - env var unset with the block → `None`;
   - diagnostics write failure → `None`;
   - the existing I-1 tests (rc=0 with any other warning → `None`) unchanged and green.

   Add a test that the diagnostics line holds the verbatim stderr.
6. **No change** to `hygiene_probe` or `summarise_probe`: the `tcp4` field and both positive controls
   (`chrome_visible_top_level`, `chrome_cdp_listener_present`) stay mandatory per sample.

**Residual, recorded but not fixed:** the ready-time `MacAdapter.hygiene()` (`scenario_matrix.py:182-195`)
counts stdout lines and ignores rc and stderr. That is pre-existing, the file is frozen, and it is not
the criterion-bearing measurement.

## P0 — release lines on Mac (needs a parent decision before any retake)

Options, from cleanest down. No default amendment:

- **(A) Parent explicitly unfreezes `scenario_matrix.py` for a Mac-only gate.** Pass
  `min_released=2 if adapter.platform == "windows" else None`; `require_join` is already gated this
  way. The driver keeps recording `trace_released_count` (0 on Mac).
  - In exchange, every Mac scenario **must** pass the phone-side release association in Task 3: both
    phones' disconnect events, after the last proven-alive exchange and within 30 s of the host's own
    exit line.
  - This replaces a host-trace proof that this fixture cannot emit with the external observation W-07
    already uses. It doesn't loosen "disconnect links".
  - Consequence: the v4 pin `scenario_matrix.py f8284c98…` stays valid historically, but re-deriving
    v4 must then run at the v4 commit, because the live hash changes. Record that in the commit
    message and provenance.
- **(B) Keep the driver frozen and run it as-is.** Teardown would FAIL with exactly one problem:
  `only 0 connection release line(s)`. The full teardown detail is still serialized in `actual`. A
  separate adjudication then supplies the phone-side release proof, the same pattern as W-02's
  separate adjudication.
  - This is honest, but it records a raw FAIL. It needs explicit parent acceptance and independent
    review.
- **Not recommended:** rebuilding or re-signing the Mac fixture so it awaits releases (new signing,
  owner-gated); weakening `check_teardown`.

## Task 2 — M-08 stdin-EOF scope

**Canonical text**

- **M-08 `expected`:** "As W-02, W-07, W-08, W-09 on macOS".
- **W-08 `expected`:** "Same cleanup after the **extension stdin pipe** closes without a shutdown request."
- **RFC** (`docs/rfcs/2026-08-21-otp-external-devices.md:1561-1579`): the process "communicates only
  through Chrome-owned stdin and stdout" and "Chrome port closure, stdin EOF, and `shutdown` each stop
  scans, disconnect links, close stdout, and leave no process".
- **Windows precedent:** W-08 PASSed (canonical row `actual`, "FINAL EVIDENCE") because the
  Chrome-provenance `port.disconnect()` made the host see stdin EOF itself. The trace mechanism was
  `stdin_closed`, with no shutdown/`shutdown_complete`, exit 0, links released and the process gone.
  That counted as "one observed trigger carrying both the W-07 trigger and the W-08 resulting-EOF
  semantics", corroborated by tab-close.
- **Independent approval:** this was approved through GPT P2/B5 (`15a28c80…`) and my v4 verification
  (`d85d4f57…`). The direct launch was explicitly recorded there as "NOT Chrome provenance".

**Conclusion**

- **Not required:** the standalone DIRECT stdin-EOF launch over SSH. It is not the "extension stdin
  pipe", so it can't satisfy W-08 by itself anyway.
- **What satisfies the M-08 EOF clause:** Chrome `port.disconnect()` plus tab-close runs, each with all
  of the following:
  - `trace_mechanism == "stdin_closed"`;
  - no shutdown or `shutdown_complete` frames;
  - exit code 0 and `stdout_drained=true`;
  - host and wrapper gone, no surviving owned host;
  - phone-side link release within the bound (Task 3).
- **Remaining M-08 coverage:** the shutdown run covers W-09, and W-02 is covered by the connected-phase
  probe.
- **No new grant or waiver is needed.**
- **Bookkeeping:**
  - Keep the direct `LC-stdin-eof` record as BLOCKED `permission_denied` / `permission_pending`
    (retained TCC diagnostic). It is never PASS and never waived.
  - Note that this path would *also* hit the P0 `min_released=2` at `:800`.
  - The M-08 adjudication must state the W-08-precedent reading explicitly, for independent review.
    It is a reading of existing text, not an amendment.

## Task 3 — Mac clock helper critique and the minimum for a release bound

**Critique of `helpers/mac_phone_clock_probe.py` (sha256 `041b8781…`, identical to the `/tmp` copy) and
`phones/clock-offset-mac.json`**

- **Correct:**
  - The sign: phone−local minus Mac−local = phone − Mac, so `mac = phone − offset`.
  - No stale Windows offset.
  - Raw midpoints, latencies and stamps preserved.
  - The conservative sum.
- **Defect 1 (centering):** macOS `date +%s` *truncates*, so the true time lies in `[s, s+1000)`. The
  helper uses `stamp = s*1000` with a symmetric ±500 term, which leaves a 500 ms bias uncovered.
  Use `s*1000+500 ± 500`, or better, a millisecond Mac clock. `python3` exists on the Mac; GLM's
  stop markers already use `python3` millisecond epochs.
- **Defect 2 (uselessly wide):** each sample spawns a new `ssh`/`adb` process (latencies 1.8–14.7 s),
  and the uncertainty uses spread/2 + max latency/2. The result, ±15.8–16.8 s, cannot support any 30 s
  "whole interval" check.
  - The rigorous assumption-free bound is the **intersection** of the per-sample intervals
    `[stamp − finished, stamp − started]`. An empty intersection → unavailable.
  - Use persistent sessions: one `ssh … python3 -u` loop echoing `time.time_ns()` per stdin line, and
    one persistent `adb shell` loop.
- **Defect 3 (provenance):**
  - Store `started`/`finished` raw, not only the rounded midpoint and latency.
  - Embed the helper's runtime self-sha256 in its output.
- **Cross-check against this run's data:** the helper's medians are consistent with the in-band anchor
  below (implied phone−Mac ≈ −58…+17 ms and −780…−859 ms, inside −735±16824 and −1209±15762). So the
  helper isn't wrong, just far too loose.

**Minimum for the release bound without an absolute Mac anchor (preferred): an in-band causal anchor**

The host trace and the phone telemetry already bind each other.

- For each 16-byte proof exchange: the host logs `in … type=send … record_base64.len=24` at `t_in`.
  The phone receives the write, then logs `notify_api bytes=20` at phone epoch `e`. The host then logs
  `out … type=message … len=24` at `t_msg`.
- So by causality `e ∈ (origin + t_in, origin + t_msg)`.
- Taking the hull over both handles' 16-byte exchanges means no handle-to-phone mapping is needed.

Validated on the retained lifecycle-1 data (`/tmp/opus-mac-preflight/anchor.py`):

| scenario | trace window (ms) | anchor width | phone-minus-trace offset, cross-checked |
|---|---|---|---|
| shutdown | [2199, 2318] | 119 ms | consistent across scenarios per phone |
| port-disconnect | [3171, 3286] | 115 ms | consistent |
| tab-close | [3499, 3613] | 114 ms | consistent |
| failure-isolation | [3547, 3661] | 114 ms | consistent |

With this anchor, the phone disconnect events fall **−68…+197 ms around the host's own `exit` line**
in all four scenarios.
- That measurably upgrades GLM's "inferred, low confidence" correlation.
- But these are aborted-run Chrome-session-close exits. **They must not be credited** to any M-08
  scenario.

**Specification (new helper `mac_release_bound.py`; frozen v4 files untouched; reuses
`core.phone_burst_association` unchanged)**

1. **Pair each scenario with its telemetry burst** (connected → disconnected of the Mac central's
   `peer_sha256_prefix`) by order and by GLM's clock probe (bursts about 5+ s apart). Use exactly one
   burst per scenario; ambiguity → FAIL.
2. **Anchor interval per phone:**
   - intersect the 16-byte-proof bracket with the 4096-byte-exchange bracket (first 512-byte
     `notify_api` of that burst against the 4096 `send`/`message` hull);
   - empty → FAIL;
   - record `offset_phone_minus_trace = midpoint` and `uncertainty = half-width + 1 ms`.
3. **Release interval:** `DIS_trace = DIS_phone − offset ± u`. It must satisfy both:
   - `DIS_trace_lo ≥ t_last_exchange_message`: released after the last proven-alive exchange in this
     process;
   - `DIS_trace_hi ≤ t_exit + 30 000`: within the pre-declared bound of the host's own exit line.

   Record the whole interval.
4. **Fixture-stop guard,** entirely on the phone clock: the phone stop marker (already recorded as a
   phone-side `date` bracket in `stop-markers.csv`) must be later than `DIS_phone` and
   `≥ (t_exit + 30 000 + u) + offset`.
5. **Process gone:** use the driver's existing `owned_gone_after_s` (≤ 10 s), `pid_alive=false`
   and no survivors. An absolute "gone" epoch is **not needed** for the 30 s bound, which is measured
   against the host's own exit line, as on Windows.
   - If the parent unfreezes `scenario_matrix.py` (P0-A), it may also record `teardown_trigger_wall_ms`
     and `gone_observed_wall_ms` (two `now_ms()` calls) for an absolute Mac-clock bracket. This is
     optional.
6. **Input pins:** traces, lifecycle JSONs, both `transport-*.json`, stop markers and the clock JSON,
   plus the helper's self-sha. Keep `phones/clock-offset-mac.json` as a corroborating raw only.

## Task 4 — remaining Mac rows (no TCC reset, no new signing by default)

**M-04** ("Deny produces access_denied with **exact** System Settings guidance; prompt owner recorded;
no TCC reset")

The RFC says "the **connector** presents exact System Settings and retry guidance". That is satisfiable
by the OTP-23 feasibility connector (unpacked extension, no signing; the extension ID comes from the
manifest key and is unchanged).

- **Today there is no such text in the connector.** The only text is the Python driver's
  `TCC_MESSAGE` (`macos_poc.py:52-56`). It names "'Google Chrome' (and/or the VelesOtp23PoC app)",
  so it is not exact.
- **Minimal path:**
  1. Add to `extension/connector.js` a pure `guidanceFor(error, platform)`. For macOS
     `access_denied`/`permission_denied` with `recoverability: retryable`, it returns one exact
     string naming the pane (`System Settings › Privacy & Security › Bluetooth`), **the exact list
     entry the owner observed**, and "then press Retry". Expose it via `otp23.lastGuidance`, render it
     in `connector.html`, and unit-test it in `scripts/connector-api.test.mjs`.
  2. The owner switches that entry OFF (a user toggle, as previously used, not a reset).
  3. Run a scan, and record `access_denied` plus the verbatim guidance.
  4. The owner follows the guidance (switch ON) and retries in the same extension session. Recovery
     must happen without reinstall or loss of extension state.
- The entry name must be read by the owner in System Settings. That is a read-only observation.
- **Remaining:** the "prompt owner" clause stays tied to M-03.

**M-03** (prompt owner observed on the existing account, signed fixture)

- The signed fixture's grant already exists. Toggling it OFF doesn't re-prompt, and removing the
  entry is a per-app TCC reset, which is excluded.
- **Precise blocker:** no non-reset way exists to re-observe *this fixture's* prompt.
- **Owner decision needed:**
  - **(a)** Keep M-03 human-gated and deferred; or
  - **(b)** record an owner-performed read-only observation of which entry holds the Bluetooth grant
    (Chrome vs the bridge app). Whether (b) satisfies "prompt owner observed" is an
    adjudication/wording decision, **not** a default.
- The M-11 run (below) will observe a real prompt, but for a *different* identity. It can't be
  credited to M-03.

**M-11** (unsigned dev-registered host on the existing account)

**Owner decision required.** Proposed minimum:
1. The owner approves a plain build from the same source tree (no Developer ID; on Apple Silicon the
   linker's ad-hoc signature is automatic, so record it truthfully).
2. **Use a distinct identity:**
   - a different bundle identifier (e.g. `me.nagaev.veles.bridge.dev`), or an unbundled executable;
   - a distinct Native Messaging host name;
   - registration scoped to a disposable profile.

   **Safety rationale (risk, not verified here):** an ad-hoc binary claiming
   `me.nagaev.veles.bridge` could make tccd re-prompt and, if allowed, replace the stored code
   requirement for that bundle ID. That could invalidate the approved fixture's existing grant.
3. Chrome launch, then the owner observes **who the prompt names** (Chrome or the binary) and answers
   it. That is a permission action, so the owner decides allow or deny.
4. Record the authorization result and the scan, compared with M-03…M-06, and the account class.
5. Unregister afterwards. The approved fixture and its grant must be byte- and state-unchanged.
   Verify with `codesign --verify --strict` and a read-only grant check before and after.

## Concrete acceptance checks (to verify GLM's implementation before the physical retake)

1. **lsof fix:**
   - Only `macos_poc.py` and `test_macos_poc.py` change for Task 1. `git diff` shows no change to the
     seven frozen v4 pins unless the parent chose P0-A, which then touches only `scenario_matrix.py`'s
     `min_released` call sites.
   - No `-w` in any lsof argv: `grep -n '"-w"' macos_poc.py scenario_matrix.py` is empty.
   - All the Task 1 unit cases pass; `test_macos_poc` and `test_scenario_matrix` stay green;
     `matrix.py check` passes.
   - A Linux dry probe of the classifier against the **verbatim 3-line block above** returns benign
     only with a matching foreign-mount set and a written diagnostics line.
2. **Read-only Mac pre-run self-test (no Chrome):**
   - With `VELES_OTP23_LSOF_DIAG` set, run `macos_poc.lsof_endpoints` on a Python child that holds a
     `127.0.0.1:0` listener. It must return that listener's port.
   - The diagnostics JSONL holds the verbatim warning, classified benign.
   - With the env var unset, it returns `None`.
3. **P0 decision recorded (A or B) before the retake.** With A: one unit test that the Mac adapter
   path calls `verify_gone` with `min_released=None` and the Windows path with 2.
4. **Retake (new unique dir, never `--overwrite`)**, every Chrome scenario:
   - 6/6 probe samples `usable=true`;
   - host `all_layers=0`, `tcp4=[]`;
   - `chrome_visible_top_level ≥ 1` and `chrome_cdp_listener_present=true` in **every** sample;
   - context audit usable;
   - diagnostics lines count = invocations, all benign or none.
5. **Teardown, per scenario:**
   - **shutdown:** `shutdown_complete` is the last output, exit 0.
   - **port-disconnect and tab-close:** mechanism `stdin_closed`, no shutdown frames, exit 0,
     `stdout_drained=true`.
   - **All scenarios:** owned host and wrapper gone ≤ 10 s, no survivors.
   - **failure-isolation:** `unknown_handle`, and the survivor gets 4096 B exactly.
6. **Release bound:** `mac_release_bound.py` gives both phones a non-empty anchor intersection (width
   ≤ 250 ms), a single fitting burst per scenario, `DIS_lo ≥ t_last_exchange`, `DIS_hi ≤ t_exit + 30 s`,
   and the stop guard holds. All inputs are pinned, and both phones' telemetry is
   `final/clean/!truncated/peers_dropped=0` with one shared run id.
7. **Direct stdin-EOF:** retained as BLOCKED (permission pending) and not counted. The M-08
   adjudication states the W-08-precedent reading and gets independent review before any row change.
8. **Publication privacy:** in the later curation, redact the other local user's account name and app
   name from `mac-resume-2026-10-28/FINDING.md` (currently present). Keep the mount path or a hash of
   it, plus the `uid≠self` fact.

## Owner and parent decisions needed (concise)

- **Parent:**
  - P0 option **A** (unfreeze `scenario_matrix.py` for the Mac-only `min_released` gate) or **B**
    (frozen driver plus separate adjudication).
  - Approve the Task 1 classifier design, **not `-w`**.
- **Owner:**
  - M-04: the entry-name observation, and the OFF→ON toggles.
  - M-03: (a) defer, or (b) the read-only grant-owner observation, subject to adjudication.
  - M-11: approve an unsigned build with a distinct identity, and answer its prompt.

No TCC reset, scope amendment or waiver is recommended.

## Commands run (read-only)

```text
cat /tmp/opencode/otp23-mac-resume-result.md /tmp/opencode/otp23-mac-resume-human-action.md
git rev-parse HEAD; git log --oneline -4; git status --porcelain
sed -n 380,445p scripts/macos_poc.py; sed -n 158,260p scripts/scenario_matrix.py; sed -n 540,640p scripts/scenario_matrix.py
sed -n 560,640p scripts/scenario_matrix_core.py; grep -n min_released scripts/scenario_matrix.py; grep -n released scripts/scenario_matrix_core.py
git grep 'conn {} released' cd971f0 -- native-bridge/feasibility/src; git show cd971f0:native-bridge/feasibility/src/macos.rs | sed -n 530,565p
grep -c released evidence/post-grant-2026-10-26/macos-initial/macos-lifecycle/*.host-trace.log evidence/mac-resume-2026-10-28/lifecycle-1/*.host-trace.log
(mac-resume) sha256sum -c SHA256SUMS (32/32); python3 -I -c <probe sample / link_proof / telemetry inspection>
python3 -I /tmp/opus-mac-preflight/anchor.py <mac-resume dir>                       # in-band anchor on retained data
grep 'lsof: WARNING' /tmp/opencode/otp23-mac-resume-stderr.log                         # exact line only
ssh (read-only): sw_vers; lsof -v; lsof -h | grep -i warn; man lsof (-w, -e, -b, ALTERNATE DEVICE NUMBERS);
                 lsof -nP -a -p $$ -i  (with/without -w; rc/stdout/stderr); mount | grep -i translocation; netstat -anv -p tcp | head -3
python3 -I -c <matrix rows M-08/W-07/W-08/W-09/M-03/M-04/M-11 criteria>; sed RFC :1560-1600
python3 -I -c <envelope-only model/effort/version metadata extraction>
```
