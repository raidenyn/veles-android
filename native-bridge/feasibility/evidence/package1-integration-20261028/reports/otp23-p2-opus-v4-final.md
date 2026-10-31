# OTP-23 package 1 P2/B5 v4 — independent focused final verification (Opus 5.5)

## Scope and identity

- Focused check of the **sole remaining v3 defect** in `/tmp/opencode/otp23-package1-p2-gpt-v3-final.md`
  (sha256 `217e6f2f2bcd896a7cda0bb8b6cf484ae3da7151f98f29ca6fb399bf2d40d4d2`): `_num(10**1000)` raised
  `OverflowError`. Also checks the v4 derivation as described in `otp23-package1-windows-finish-result.md` §8.
  It does not reopen the broad schema/fuzz backlog.
- Role: independent reviewer, read-only for source, evidence and canonical files. The only file written is
  this report. Scratch probes and the re-derivation went to `/tmp/opus-v4-probe/`. No hardware, phone,
  Chrome or Mac actions; no subagents, commits, pushes or GitHub.
- Actual model metadata, read from fields only (no transcript text) in this session's Claude Code log
  `~/.claude/projects/-home-dev-repos-veles-android/1b447229-ceaa-4cee-a5be-2181dfdd8838.jsonl`:
  - Claude Code `2.1.312`.
  - Assistant records: 62 × `claude-opus-5-5` and 1 × `<synthetic>`. The synthetic record is the harness
    placeholder for the interrupted turn, not a model response.
  - Captured at `2026-10-28T13:55:01Z`.
- Repository HEAD is `8f365fce0d31f975610c235085a95b9136ac25e2`. The worktree status hash was identical
  before and after my work.

## Verdict

**The sole remaining defect is CLOSED. Recommend W-02, W-07 and W-08 PASS**, through separate
adjudication and the normal integration step. This report changes no canonical row by itself.

| row | recommendation |
|---|---|
| W-02 | **PASS.** The owner-scoped narrow clarification is verbatim in `w02-adjudication.json`. The census has 6/6 usable samples, no host windows or endpoints, one never-visible `ConsoleWindowClass` wrapper per sample, no wrapper endpoints, and equal sessions. This matches `/tmp/opencode/otp23-package1-owner-approval.json` exactly: no host window of any kind, no visible wrapper window, no network endpoint. |
| W-07 | **PASS.** The B5-2 exact-branch condition is now met. The association values are unchanged. |
| W-08 | **PASS.** The same condition is met. The tab-close values are unchanged. |

M-08 stays owner-DEFERRED (`"Cannot restart Chrome now"`). Overall status stays **NO-GO**, and **OTP-24
remains unauthorized**. The prior Windows strict W-11/W-12 PASS and the GLM interim integration are
unaffected.

## B5-2 exact branch (`scenario_matrix_core.py:761-771`, sha `cb512418…c3b3`)

```python
if isinstance(value, bool): return False
if isinstance(value, int):  return True
return isinstance(value, float) and math.isfinite(value)
```

This is the literal contract: booleans are rejected, non-boolean ints are accepted with no float
conversion, and floats are accepted only if `math.isfinite`. Probe results:

- `_num(10**1000)` and `_num(-(10**1000))` both return True and do not raise.
- 1001-digit ints parsed by `json.loads` are accepted.
- `0`, `-1793170763088`, `1793170763088.5` and `1e308` are accepted.
- `True`, `False`, `NaN`, `+Inf`, `-Inf`, `None`, `"1"`, `[1]` and `1j` are all rejected. JSON `NaN`,
  `Infinity`, `-Infinity`, `true` and `false` are rejected too.

## Validator overflow boundary (`phone_burst_association` → `_phone_burst_association_impl`, l.774-795)

Only `OverflowError` is caught. It becomes `status: FAIL` with a `numeric_range: …` problem and empty
`phones`, so no association is claimed and no acceptance gate is added. I ran ±10**1000 through every
numeric slot of the real validator, using the test fixtures:

- **No uncaught exception anywhere.**
- Inputs that overflowed later arithmetic produced a controlled FAIL with `numeric_range`:
  - `stop.epoch_pc_ms = -H`, both directly and after a `json.dumps`/`json.loads` round trip.
  - `clock.uncertainty_ms = +H`.
  - Event epoch ±H with a float clock offset.
- Every other huge-value case went through the existing named checks. Host exit ±H, anchor −H, clock
  offset ±H, phone stop −H and `started_at` +H all FAIL that way.
- Existing rejections are unchanged: `True`, `False`, `NaN`, `+Inf` and `-Inf` in the stop marker or the
  host exit still FAIL through the named `fixture stop marker missing/unusable` and
  `host exit/anchor epochs missing` checks.

Non-blocking notes, outside the scope of this condition:

- A few huge values (anchor +H, stop +H, `started_at` −H) give PASS under the unchanged criteria. The same
  happens with float-representable `1e300`, so this is a schema-range question that predates v4; v4 did
  not introduce it.
- A direct Python call with an int over 4300 digits (e.g. `10**5000`) raises `ValueError` from Python's
  int-to-string limit inside a problem f-string. JSON input cannot reach this: `json.loads` itself
  rejects ints over 4300 digits.

Regression tests at `test_scenario_matrix.py:1965-2004`:

- `test_num_accepts_huge_ints_without_overflow`
- `test_giant_stop_epochs_never_escape_the_validator`

Both pass, and so do the four existing bool/non-finite tests.

## Ordinary evidence values (v4 vs v3, re-derived)

- **v4 vs v3:** ignoring only the `what`/`supersedes`/`provenance` fields, all four outputs have **0
  differences**.
- **Values:**
  - Association: PASS with no problems, same central. Gaps are 2322 and 2406 ms, intervals [629,4015]
    and [1482,3330].
  - Tab-close: gaps 2304 and 2310 ms, intervals [819,3789] and [1388,3232]. Both intervals lie wholly
    after the host's own exit and below the bound.
  - Sample positions: 9371, 9473, 9584, 9686, 9944, 9951. Trace origin `1793170753120`, spread 0.
- **Independent re-derivation:** `package1_b5_v2_derive.py --out /tmp/opus-v4-probe/rederive` exited 0
  and reproduced every value. The only differences were:
  - the `derived_utc`/`pinned_at` timestamps and therefore the output hashes;
  - `supersedes[].sha256 = None`. `superseded_chain` resolves predecessors through `out.parent`, which
    does not exist for an out-of-tree `--out`. This is an artifact of my temp re-run, not a defect in the
    committed v4. In the tree, all 8 pointers match the v3/v2 manifests (below). As a hardening note,
    the script could fail rather than emit `None`, but that is not a blocker.
- **Owner records:** both re-copied owner records are byte-identical to the originals (`cmp`).

## v4 package, pins and immutability

- **v4 `SHA256SUMS`:** sha `dc214fde8c1519072b0e99b50a94fdc3758fc073516e84465ccc9c1f5f0c4303`, 9/9 OK,
  no unmanifested files.
- **Code pins (7):** `derivation-record.json` lists them with the derivation script first:
  `package1_b5_v2_derive.py` `9cad1a70…06f0`, pinned from its runtime-measured sha. `derivation_script`
  equals `code[0]`. All 7 match the live files:
  - `scenario_matrix_core.py` `cb512418…`
  - `package1_phone_burst_telemetry.py` `c6fcfd6e…`
  - `package1_phone_bound.py` `40d8f824…`
  - `scenario_matrix.py` `f8284c98…`
  - `pc_phone_clock_probe.py` `06f0de1b…`
  - `chrome_validate.py` `78d53ca9…`
- **Code list per output:** each of the 4 outputs carries `provenance.code` equal to the record's list.
- **Output pins:** all 4 in the record match.
- **Input pins:** all 33 distinct `{path, sha256}` pins across the record and outputs match the live
  bytes, including raw inputs. None are missing.
- **Citation:** the v3-final citation sha `217e6f2f…` matches.
- **Supersedes chain:** all 8 entries match the v3 and v2 artifact hashes (`2f74f34d`, `0a987c6f`,
  `81e3fe5e`, `946c74bc`, `b0840066`, `3deac5e2`, `cea77aa2`, `4bbdf512`).
- **Immutable predecessors:**
  - v3: `3bf5a493…` 9/9.
  - v2: `7b2e924f…` 15/15. One unlisted later addition, `provenance/v4-review-attempt-quota-state.json`
    (mtime 12:01Z), is disclosed in `otp23-package1-integration-result.md` §4. The manifested v2 bytes
    are unchanged.
  - P2 raw: `addf32b2…` 34/34.
  - Connected: `218da0f6…` 181/181.

## Exact commands run (from `native-bridge/feasibility/scripts` unless noted)

```text
sha256sum scenario_matrix_core.py test_scenario_matrix.py package1_b5_v2_derive.py package1_phone_burst_telemetry.py package1_phone_bound.py scenario_matrix.py pc_phone_clock_probe.py chrome_validate.py
python3 -I /tmp/opus-v4-probe/probe.py "$PWD"        # ±10**1000 in every validator slot + bool/NaN/Inf
python3 -I /tmp/opus-v4-probe/probe2.py "$PWD"       # 1e300 parity, json 4301-digit limit, 10**5000
python3 -m unittest test_scenario_matrix.PhoneBurstAssociation                       -> Ran 29, OK
python3 -m unittest test_scenario_matrix.PhoneBurstAssociation.{test_num_accepts_huge_ints_without_overflow,test_giant_stop_epochs_never_escape_the_validator,test_num_helper_rejects_nonfinite_floats,test_rejects_nonfinite_stop_epochs_fail_closed,test_rejects_nonfinite_clock_values,test_rejects_nonfinite_event_epoch_and_host_anchors} -v -> Ran 6, OK
python3 -m unittest test_scenario_matrix                                             -> Ran 136, OK (skipped=5), exit 0
python3 -m unittest discover -s . -p 'test_*.py'                                     -> Ran 456, OK (skipped=5), exit 0
(evidence/<dir>) sha256sum SHA256SUMS && sha256sum -c --quiet SHA256SUMS             # v4 9, v3 9, v2 15, p2 34, connected 181
python3 -I /tmp/opus-v4-probe/audit.py /home/dev/repos/veles-android                # code/output/input pins
python3 -I /tmp/opus-v4-probe/cmp.py /home/dev/repos/veles-android                  # v4-vs-v3 values, supersedes, W-02
(feasibility) python3 -I scripts/package1_b5_v2_derive.py --out /tmp/opus-v4-probe/rederive -> exit 0; structural diff vs v4
(repo) git rev-parse HEAD; git diff --check (exit 0); python3 -I -m py_compile <core, derive, tests> (OK)
```

The first combined run of the module and discovery suites in one pipeline was killed externally (exit
137) before printing a result. Re-run separately, both exited 0 with the counts above.

## Boundary

These are recommendations only. Canonical state is not edited here: it stays at the interim
20 PASS / 9 BLOCKED / 3 waived, and the three-row integration (expected 23/6/3) belongs to the parent or
GLM integration step. M-08 stays owner-deferred. Overall status is NO-GO, and OTP-24 is not authorized.
