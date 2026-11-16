# Review record: design

- **Artifact**: `design.md`
- **Reviewer**: Opus (Opus-5.5, medium)
- **Session preset**: [Opus]
- **Cap**: 5 rounds
- **Upstream**: brainstorm.md sha256 `d95c221184f9475e95e94a369245fecc303d15fd7067234105fdfd7f859c1e09`; proposal.md sha256 `8c447272e7419f88407e4f868d382f8b1f8950d082bbb0b57d788016ff0205e8`

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | H1: `closePeer` finishing matching holds on a later pass collides with the responder's own `deleteFenced` → `closePeer` revocation finish and would immediately send the disconnect the hold exists to avoid. | fixed — `closePeer`/`closeAll` now snapshot matching holds synchronously at entry; the responder's hold is created later by its handler step, so the revocation finish cannot see it. A connector-app test with the production coordinator asserts no disconnect between the response's `sent` and the phone's `disconnected`. |
| medium | M1: the connector-app recovery assertion could pass even if no hold was created, because a premature disconnect also sets `disconnecting` and skips recovery. | fixed — the test now asserts the absent disconnect as well as the absent recovery. |
| low | L1: the `holdForDelivery` flag would also be set on an `app_error` termination from a `removal_request`. | fixed — the flag is set only with `!terminalError`; the owner also checks `STAGE_REMOVAL`. |
| low | L2: a hold-start mark could go stale if the handle already left `trackedConnections`. | fixed — the bottom cleanup loops of `handleChange` prune `removalHoldHandles` for handles not in `next`; the stale-mark sentence was replaced. |
| low | L3: hold creation when the port is already closed was unstated. | fixed — no hold is created when the controller is closed. |
| low | L4: D5 called the final `settle(...)` a transport action. | fixed — only the final `controller.disconnect` is deferred. |
| low | L5: three line references were slightly off. | fixed — `engine.ts:1101-1112`, the `#teardownInternal()` call sites at `:1086`/`:1106`, and the responder test at `removal.test.ts:476-498`. |

### Round 2 — approve

| Severity | Finding | Disposition |
|---|---|---|
| medium | M-a: `closeAll` leaves a hold created by an in-flight removal step alive to the bound; no responder path calls `closeAll` before its hold exists, so this only affects a reset admitted exactly during the responder step. | deferred — accepted divergence, documented in D6: the hold carries no keys, accepts no records and is bounded; changing `closeAll` to a post-settle second pass would add a distinguishing rule from `closePeer` for a rare race. |
| low | L-a: the snapshotted hold release must not be skipped when `closeSelected` rethrows after a link failure. | deferred to the plan — the plan's implementation task must require the snapshot release to run before the `closeSelected` await (or inside the same `allSettled`). |
| low | L-b: the existing `close-peer.test.ts` responder assertion and `removal.test.ts:276` exact-effect assertion need updating; `holdOpen` should create its map entry and timer before subscribing. | deferred to the plan — the plan must name both tests and the entry-before-subscribe order. |

Reviewed hash: `04b8de0aca9d48d41568abb80d8fc7424e0dd4ab21d87b9c9f9d7a773b6b6e6c`.

## Pass 2 — upstream consistency

The proposal was approved after the design's first pass. The design content is unchanged; this pass
checks it against the now-approved proposal.

| Severity | Finding | Disposition |
|---|---|---|
| low | L1: design D7 omits `web-extension/test/connector/close-peer.test.ts`, which the proposal's Impact names. | deferred to the plan — the plan must name the file and its disconnect-count assertions. |
| low | L2: design D8 states PASS criteria only for Android → Chrome and does not restate the no-reproduction fallback. | deferred to the plan — the proposal and brainstorm carry both; the plan's acceptance task must state the PASS criteria for both directions and the fallback. |
| low | L3: the brainstorm and design cite slightly different `engine.ts` line ranges. | informational — the design range was checked against the code; no action. |

Reviewed hashes: brainstorm `d95c2211…`, proposal `8c447272…`, design `04b8de0a…`.

Approved content: design sha256 04b8de0aca9d48d41568abb80d8fc7424e0dd4ab21d87b9c9f9d7a773b6b6e6c
