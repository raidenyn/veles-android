# Review record: tasks — Sol

- Artifact: `tasks.md`
- Reviewer: Sol (GPT-6.2-Sol, medium)
- Preset: `sol` = [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream hashes:
  - `brainstorm.md` sha256 3391633d78486d00995eff372b327d598d0720ba5d84754bbaa764eb4c66c0f9
  - `proposal.md` sha256 826874093f598bd0fa9c43928783802a74ca309c704bd48babc8854123cb3b0c
  - `design.md` sha256 2cb7cd4b1c30634eab4340716439ee439fbe12c7a6ee2b3c8bde343d8276afdb
  - `specs/affected-node-verification/spec.md` sha256 af47c61afbf8dc8cca9f87f8fe26e53d094eba2e9f9a1eab1cb55ad07ec9267f
  - `specs/verification-stop-hook/spec.md` sha256 944e3d646b541f49583c80f485046dd3f41d14efb5f74beadefcadf3c48662f1
  - `specs/hooked-rule-delivery/spec.md` sha256 7b27f2cec1b9ee5dc12b6a904667f86829f14c5d18eb841cb0cc9854bf33ab99
  - `specs/agent-guidance-layout/spec.md` sha256 f2af203a88b279ba05718e0289352685abdfa8a411c9c5cdb5d8a8ded06e1538
  - `specs/ci-pipeline/spec.md` sha256 c12f7c82fa2b655fcec5e38edcffd614fd20eb044f18911a090ba24e85600e35

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | Rollout sequence (CLI-only landing, measurement, then hook), rollback pairing. | fixed: phases A/B/C; sections 10, 11, 14.1; budget change goes to the owner. |
| high | No task owns the CLI end to end; producer exits 0 without output. | fixed: 8.1. |
| high | D12 table vs spec on time charges. | fixed: section 13 states the spec governs; 13.3 tests no-work and internal-error runs. |
| high | End-to-end timeout, contention and watchdog-deadline tests. | fixed: 8.1, 13.1 (earlier admitted deadline), 13.6. |
| high | Positive remembered-pass path untested. | fixed: 12.4. |
| medium | 3.8 source check; test file location. | fixed: preamble and 3.1. |
| medium | Table before docs; real-table regressions. | fixed: section 2 before 3; 5.4. |
| medium | Guard script behaviour untested. | fixed: 2.2. |
| medium | CI table-only selection; absent and wrong override tests. | fixed: 10.1, 9.4. |
| medium | Pre-push triggers and table-rule scoping unverified. | fixed: 14.2, 14.3. |
| medium | Oversized tasks. | fixed: table split 3.3–3.5; hook state split 13.2–13.4; drift check split 9.2–9.4. |
| low | Closeout handoff for site wrappers. | fixed: 15.5. |

### Round 2 — changes-required

Round-1 findings: all 12 resolved.

| Severity | Finding | Disposition |
|---|---|---|
| high | Every Linux CI test must map to a branch-gate entry; "CI only" too open (workflow-contract suite). | fixed: 1.2 requires the mapping and limits "CI only". |
| high | Repeat key omits outcome kind and error kind. | fixed: 13.4. |
| medium | Notice path for `PASS (remembered)`. | fixed: 12.4 with one formatter for block and notice. |

### Round 3 — changes-required

Round-2 findings: all 3 resolved.

| Severity | Finding | Disposition |
|---|---|---|
| high | The Rust build–clean–assert sequence cannot run in the active checkout without deleting the verifier's state and lock. | fixed: 1.2 classifies destructive `build/`-cleaning sequences as "CI only" with the reason. |
| low | The table rule must point to `docs/verify.md`. | fixed: 14.3. |

### Round 4 — approve

Round-3 findings: both resolved.

| Severity | Finding | Disposition |
|---|---|---|
| medium | Resolver killed at its cutoff must be a `timeout`, tested in a real run. | fixed (non-triggering): 5.2. |
| low | CI setup steps represented as requirements belong in "CI only". | fixed (non-triggering): 1.2. |

Approved content: tasks sha256 e4afa9c2586119159df6f96a49ada278ddb091631b8f617058b91447b555d2e5 — re-pointed from b79734ef72040ce6b66bb7b00a26570221d3416f199ec2789486992486c05627 after two minor fixes (resolver timeout in 5.2; CI setup steps in 1.2); non-triggering.
