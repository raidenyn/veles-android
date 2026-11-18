# Review record: brainstorm — Sol

- Artifact: brainstorm (`brainstorm.md`)
- Reviewer: Sol (GPT-6.1-Sol, medium)
- Session preset: `sol` = [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream: none (brainstorm is the first planning file)

## Pass 1

### Round 1 — approve

Reviewed content: brainstorm sha256 22954a6946d3cbffc62a1227a71abb89e764c16ba209d9c53db5cb4e8897543f

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | medium | The acceptance summary omits default fixture lifetimes, the `scheduled` line with the process id, and controller-clock timing. | fixed: the device-run bullet now lists every acceptance step from the issue. |
| 2 | medium | Service-level verification of failure → teardown → fresh start → advertising is unspecified; callback mapping tests alone don't establish it. | fixed: added a Robolectric service-level test that the relaxed prerequisite no longer stops the service, plus a Stop/Start retry check on the OnePlus 6T for the controller-dependent sequence. |
| 3 | medium | "Changes nothing on multi-advertisement devices" contradicts the new `FEATURE_UNSUPPORTED` → `Unsupported` mapping. | fixed: the claim is qualified in the Design Summary and Agreed Approach. |
| 4 | low | The retry rationale is overstated: `DATA_TOO_LARGE` is not cleared by retrying the same payload. | fixed: the codes are described as non-capability errors, with the transient ones and the local faults named separately. |

Approved content: brainstorm sha256 727342681a009204f8dd7209d1e77024bb4616e82c26b33eb40f07c943cdd040 — re-pointed from 22954a69… after 4 non-triggering minor fixes (findings 1–4 above).

## Pass 2 — reframe after apply

Trigger: the real-device run disproved the tracked issue's premise (`apply.md`, "Finding: the issue's root cause was not reproduced"). With the owner's agreement (2026-11-18) the brainstorm, proposal and design were reframed as a hardening that removes a stale Android-5-era proxy; the code is unchanged. One Sol round reviewed the brainstorm, proposal, design and the tasks label together, checked the AOSP and repository claims, and checked consistency with the spec, tasks and plan.

### Round 1 — approve

Reviewed content: brainstorm sha256 0dd5b5ff7c4e5e73ccfa73a45add4b80226a891bdefebe694e8e88e6e53d9b70

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | — | No finding: the correction matches `apply.md`, and the disproved premise is kept only as history. | no action |

Approved content: brainstorm sha256 0dd5b5ff7c4e5e73ccfa73a45add4b80226a891bdefebe694e8e88e6e53d9b70
