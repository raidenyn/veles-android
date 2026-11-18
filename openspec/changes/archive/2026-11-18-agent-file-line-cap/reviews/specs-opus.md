# Review record: specs — reviewer Opus (Opus-5.5, medium)

Preset: opus (first Opus).
Upstream inputs:
- brainstorm.md sha256 3ae02aea3c496d32cb8a03d1fc6764f326dc9fe3e85a26970ff7ae115f3eab4f
- proposal.md sha256 79aa10cda5d78a94eff53ead15cfff5c267611e2846ef112a501367f309f9b48
- design.md sha256 fbdb53fff2a005ae241fc7e05604009ae735acd8e913d59092c7a4ab3ca11f00 — re-pointed for the non-triggering move of the traceability table from the regenerated `verify.md` to `traceability.md`; the reviewed design content is otherwise unchanged.

Cap: 5 rounds per reviewer per pass.

## Round 1 — verdict: changes-required (0 critical, 1 high, 4 medium, 5 low)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 1 | high | The "macOS narrative in docs" scenario said the identity rules are not duplicated in the docs pages, but `docs/desktop-ble-poc.md` already contains the same no-substitution/no-export/no-TCC-reset guidance, so the scenario fails on the planned tree and no design item trims that historical record. | **Fixed.** The scenario now requires the canonical rules in the node under the cited heading and the node's links to the two docs for history, and says this change adds no new copy. The historical PoC text is left intact. |
| 2 | medium | The node requirement's closed "only" list omits the start-at-root notice, the `(schema version v5)` text and the hook-delivered procedure pinned to `docs/AGENTS.md`, and "procedures live in docs" conflicts with the hook procedure. | **Fixed.** The list now includes the notice and any content another capability pins to the file (naming the v5 text and the hook section), and the sentence says it MUST NOT hold other narrative. |
| 3 | medium | The root requirement's "tool, checkout and OpenSpec detail MUST live in CONTRIBUTING.md ... not in the root file" conflicts with the required one-line plugin statement and the `/init` warning. | **Fixed.** The requirement now names what moves (tool versions, checkout setup, the OpenSpec CLI range) and what may stay (the one-sentence plugin requirement and the `/init` / parent-`CLAUDE.md` warning). |
| 4 | medium | The macOS scenario's "or a pointer" lets a pointer stand in for the canonical rules. | **Fixed.** The scenario now says the rules are found in the node, with the docs linked for history. |
| 5 | medium | The platform requirement's "MUST stay in the node" list omits the no-TCC-reset rule and "signing and notarization only in `publish-release.yml`". | **Fixed.** Both are now in the list. |
| 6 | low | The untrusted-checkout warning on a page named for OpenCode is a surprising home and the new wording misstates when the hooks run. | **Fixed.** The bullet now says the warning is tool-neutral (covering `.claude/settings.json` and `opencode.json`), states that the hooks run a script on every shell command, and requires `CONTRIBUTING.md` to cross-link it. |
| 7 | low | The roster requirement's no-model-names list adds two new docs but not `docs/opencode-rules.md`. | **Fixed.** It is added. |
| 8 | low | The check requirement's negative-test list does not include a repeated plugin range in the new pages. | **Fixed.** It now lists a repeated range in `docs/AGENTS.md`, `docs/agent-guidance.md` or `docs/opencode-rules.md`. |
| 9 | low | The cap says "tracked nested", while the check also scans untracked-but-not-ignored files. | **Deferred with reason.** The cap is on tracked files because CI checks out tracked content; an untracked over-long file fails as soon as it is committed, and the local check still reports every other invariant for it. Applying the cap to untracked files would fail a developer mid-edit for a file that is not part of the change yet. |
| 10 | low | The root-cap and one-pointer-line fact would have homes in `docs/AGENTS.md`, `docs/hook-delivered-rules.md` and `docs/agent-guidance.md`. | **Fixed.** `docs/AGENTS.md` is the single home (the `Fail open` requirement); `docs/hook-delivered-rules.md` and the adding-a-node note in `docs/agent-guidance.md` link that section. |

Notes:
- All 10 MODIFIED headers exactly match the main specs; each delta carries the full requirement text and scenarios, and `openspec validate agent-file-line-cap` is valid.
- No other requirement in `openspec/specs/` pins content to `docs/AGENTS.md` or states the 60/120 caps.

## Round 2 — verdict: approve (0 critical, 0 high, 0 medium, 2 low)

| # | Sev | Finding | Disposition |
|---|-----|---------|-------------|
| 11 | low | The node requirement's closed list does not name the CI-gate tokens (`native-host-windows`, `native-host-macos`, `native-host.cdx.json`, the host task names), so a trimmer could read "no other narrative" as permission to drop them. | **Fixed.** The list now includes "the tokens a CI gate requires", with the host names as the example. |
| 12 | low | The platform requirement says Android signing lives only in the node, then puts fixture build detail in `docs/android.md`, which reads like signing detail in two places. | **Fixed.** It now says the fixture build steps live in `docs/android.md` while every signing constraint and the keystore/init-script names stay in the node. |

Nine round-1 findings are fixed, #9 is deferred with a reason, and no new critical, high or medium finding was raised.

Approved content: specs/agent-guidance-layout/spec.md sha256 8500b20b606d68c0570dc89ef71823618339b831d22a94f06ab635b478e9c0db — includes the round-2 finding 11 and 12 fixes, a minor change after the approving round.
Approved content: specs/superspec-review-gate/spec.md sha256 7135daaba38f4065ba31ebaed08f6deb22f35900bf84b7b602deccc19f398ce1
Approved content: specs/openspec-skills-delivery/spec.md sha256 5f33452d3214973ed3e3256aacbd6238cbf38cbe40d089c0e55ab2d2af4b317b
Approved content: specs/hooked-rule-delivery/spec.md sha256 bc53cdb3f2e1422e30ee80130c4fd8b75c8477f845ef2338e83f2299d80ab595
