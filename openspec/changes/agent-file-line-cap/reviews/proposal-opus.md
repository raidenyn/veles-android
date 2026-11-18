# Review record: proposal — reviewer Opus (Opus-5.5, medium)

Preset: opus (first Opus).
Upstream inputs: brainstorm.md sha256 3ae02aea3c496d32cb8a03d1fc6764f326dc9fe3e85a26970ff7ae115f3eab4f — re-pointed for the non-triggering fork-divergence correction; the design content is unchanged.
Cap: 5 rounds per reviewer per pass.

## Round 1 — verdict: approve (0 critical, 0 high, 2 medium, 4 low)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | medium | Impact → Checks omits the CI nodes the doc edits select: `docs/reproducible-builds.md` is a `native-host` ci-path, `docs/otp-25-installers.md` an `installer` ci-path, `docs/checks/` a `supply-chain` path; the "no workflow change" line implies a docs-only footprint. | **Fixed.** The Checks bullet now runs `components.py changed --base ...` and names the checks of every selected node (`installer`, `native-host`, `supply-chain`, with `.github/` as a global path). |
| 2 | medium | Modified Capabilities → `agent-guidance-layout` does not declare the delta to "Existing content is preserved or explicitly dropped", whose scenario names the fixture keystore and `core.hooksPath`. | **Fixed.** The capability bullet now names that requirement and both facts: the fixture build line moves to `docs/android.md` and the `core.hooksPath` paragraph to `docs/ci.md`. |
| 3 | low | The "Impact: non-breaking" line omits the new constraint on future changes: a new node, `repo-*` rule or hooked rule needs compression or a move. | **Fixed.** The length-policy block now records the consequence: the root sits at 39, and each added node, rule or hooked pointer line must be offset by compression or a docs move. |
| 4 | low | The Checker/ownership From line says "three instruction pages unprotected", but that content is in `docs/AGENTS.md`, which `**/AGENTS.md` already protects. | **Fixed.** The From line now says the instructions live in the protected `docs/AGENTS.md`; the risk is that moving them to plain docs pages drops that protection. |
| 5 | low | The platform-canonical-folder delta says "the node's linked docs page" singular and does not say which facts must stay in the node file; macOS detail goes to two pages. | **Fixed.** The bullet names `docs/otp-25-installers.md` and `docs/desktop-ble-poc.md`, and the facts that stay: the macOS identity statement, the two cited signing headings and the Android keystore/init-script names. |
| 6 | low | "One home per fact" omits the brainstorm's verification obligation (linked or dropped with a reason, plus a section-by-section check). | **Fixed.** The content-migration reason now records the destination for every moved section and a reason for anything dropped in the verification record. |

Notes:
- The four spec deltas match the requirements that pin content to `docs/AGENTS.md`; the `commit-conventions` no-delta is correct because the guidance-only sentence stays in `scripts/AGENTS.md`.
- Outside archives and specs, `docs/AGENTS.md` is referenced only from root `AGENTS.md`, `CONTRIBUTING.md`, `docs/ci.md`, the checker and its tests, and a path-ownership fixture in `scripts/tests/test_components.py`; the sweep covers them.

## Round 2 — verdict: approve (0 critical, 0 high, 0 medium, 1 low)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | low | Round-1 #2 said the capability bullet names the two destinations, but it named only the facts; the builder would have to guess the pages. | **Fixed.** The bullet now names `docs/android.md` for the fixture build line and `docs/ci.md` for the `core.hooksPath` paragraph. |

All six round-1 findings are fixed in substance; no new critical, high or medium finding was raised.

Approved content: proposal.md sha256 79aa10cda5d78a94eff53ead15cfff5c267611e2846ef112a501367f309f9b48 — includes the round-2 finding 1 fix (naming the two destination pages), a minor change after the approving round.
