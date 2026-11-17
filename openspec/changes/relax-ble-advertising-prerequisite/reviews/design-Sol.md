# Review record: design — Sol

- Artifact: design (`design.md`)
- Reviewer: Sol (GPT-6.1-Sol, medium)
- Session preset: `sol` = [Sol]
- Cap: 5 rounds per reviewer per pass
- Upstream:
  - brainstorm sha256 727342681a009204f8dd7209d1e77024bb4616e82c26b33eb40f07c943cdd040
  - proposal sha256 f029fdb6639ca9d4eb3f03b0c1db725957d3c580806b3ae84f681caf46040a8b

## Pass 1

### Round 1 — approve

Reviewed content: design sha256 ab9d89f3effd157ec469510b3c3e3f1e984379281be51998b89328c7350bcf7f

| # | Severity | Finding | Disposition |
|---|---|---|---|
| 1 | low | D4 omits the brainstorm's null-advertiser regression test. | fixed: D4 asserts that a `null` advertiser still reports `Unsupported`. |
| 2 | medium | "Not `Unsupported`" does not prove the prerequisite passed; setup and positive evidence are unspecified. Robolectric 4.14.1 provides the needed shadow APIs. | fixed: D4 specifies the setup and requires `START_STICKY`, `Starting`, foreground promotion and no self-stop. |
| 3 | medium | Stop→Start checks a normal restart, not failure→teardown→fresh Start→success. | fixed: D4 adds a service-level failure/retry test through an extracted `onGattState` sink with unchanged behaviour; D5 states that the hardware run does not observe this sequence. |

Approved content: design sha256 3bf8a0bbeb8b2d544f18e9e17bd6456b7ee514e4c24f2fb9b40fe4884cc1184d — re-pointed from ab9d89f3… after 3 non-triggering minor fixes (findings 1–3 above).
