# Review record: proposal

- Artifact: `proposal.md`
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream: `brainstorm.md` sha256 c27ca9d75a2a5b085956825eb2aa827fd3b518d9f65ba6df139b52ff9670a0c7

## Pass 1

### Round 1 — approve

No critical or high finding. Modified-capability claims confirmed against `component-versioning` and
`ci-pipeline`; no other existing spec's requirements change.

| Severity | Finding | Disposition |
|---|---|---|
| medium | The wrapper for this change's own capability cannot exist before the archive; when it is added was unstated. | Fixed: added in the closeout's documentation review, after the archive. |
| low | The `documentation-site` bullet did not claim the `.mintignore` CODEOWNERS entry and the `docs/AGENTS.md` guidance. | Fixed: both named in the capability. |
| low | Branding values, the privacy-wording limit and the import fallback were dropped from the brainstorm. | Fixed: one line each in What Changes. |
| low | The deployed-site open questions were missing from Impact. | Fixed: stated as reported, not assumed. |
| low | The CI bullet was loose about which nodes run. | Fixed: names `docs`, `tooling`, `installer`, `supply-chain`. |
| low | The MDX-hazard rule becomes an authoring constraint on every future spec; heuristics must be calibrated on all 28 specs. | Deferred to design and `docs/AGENTS.md`. |

Approved content: proposal sha256 0260f7c0185b8f9957eb6026c228fd827d306227fa373dd5646729d673b4ea65 (re-pointed after five non-triggering minor fixes from round 1: own-capability wrapper timing, capability scope, brainstorm details, deployed-site checks, CI node list)
