# Review record: brainstorm

- Artifact: `brainstorm.md`
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream: none (brainstorm is the first planning file)

## Pass 1

### Round 1 — changes-required

| Severity | Finding | Disposition |
|---|---|---|
| high | A capability without a wrapper fails the check, so every archive that adds a capability breaks `docs` CI and silently changes the closeout procedure; new specs go live with no MDX-safety check. | Fixed: a missing wrapper is a warning, never an error; the closeout is unchanged; the new *Site guard* scans every published page and imported file for MDX hazards. |
| high | No working validation gate if the Mintlify check stays red (previews paid-only). | Fixed: *Site guard* added as a step in the existing `docs` node (no new job), catching missing pages, broken imports and MDX hazards in CI. |
| medium | Hosted build assumed to match the local CLI (`.mintignore`, root `docs.json`, imports). | Fixed: open question added to verify on the deployed site that sampled unpublished paths return Not Found. |
| medium | Contradiction: "no existing file moves" vs moving `docs/sensitive-notifications.md`. | Fixed: summary names the one moved guide. |
| medium | Contradiction: `BUILD.md`/`CONTRIBUTING.md` "unchanged" yet links rewritten; decided vs fallback. | Fixed: the link rewrite is stated as the only edit, with line numbers; fallback for unresolved imports stated. |
| medium | Where the wrapper check runs, and whether it checks navigation listing. | Fixed: `docs/checks/check_site.py`, a step in `check-docs.yml` `agent-files`, contract allow-list updated; it checks wrappers are in navigation. |
| medium | Allowlist must re-include root `BUILD.md`/`CONTRIBUTING.md`; import from an ignored file unverified. | Fixed: both re-included explicitly; `CONTRIBUTING.md` default-ignore noted; fallback stated. |
| low | Owner-setup note had no file name or publication decision. | Fixed: `docs/mintlify-owner-setup.md`, unpublished. |
| low | Agent/CI documentation updates missing. | Fixed: `docs/AGENTS.md` section and `docs/ci.md` step. |
| low | Relative `.md` links in published pages (decision records). | Fixed: open question; `mint broken-links` decides and failures become site routes. |
| low | Reference count 69 not reproducible. | Fixed: scope stated (outside archived changes and `docs/superpowers/`), with the 74 figure. |
| low | CODEOWNERS header scope. | Fixed: header comment widened; the agent-file check's CODEOWNERS parsing to be tested in implementation. |
| low | `docs/reproducible-builds.md` is a `native-host` ci-path. | Fixed: noted under Plumbing. |
| low | Overview page is a second hand-maintained copy of the node map. | Deferred: the Overview serves a different reader (people, not agents) and is short; drift is accepted for this version. |
| low | Telemetry claim unchecked for hosting analytics and the AI assistant. | Fixed: *Privacy wording* limits claims about the site to the telemetry setting. |

### Round 2 — approve

All round-1 critical and high findings confirmed resolved; the deferred low finding (Overview drift) accepted.

| Severity | Finding | Disposition |
|---|---|---|
| low | A removed or renamed capability fails the guard until its wrapper follows, which the "closeout does not change" claim did not mention. | Fixed: the brainstorm states the wrapper must be removed or renamed in the same change, and `docs/AGENTS.md` says so. |
| low | The workflow contract file was not named. | Fixed: `verify/test/workflow-contracts.test.mjs` named. |
| low | The guard's hazard heuristics may flag syntax Mintlify accepts; calibrate on every published file. | Deferred to design: calibration against the current published set is a design and test concern. |
| low | The guard must derive the published set the way the hosted build does (`.mintignore` semantics vs navigation plus imports). | Deferred to design. |

Approved content: brainstorm sha256 c27ca9d75a2a5b085956825eb2aa827fd3b518d9f65ba6df139b52ff9670a0c7 (re-pointed after two non-triggering low fixes from round 2: the wrapper rename/removal rule and the named contract file)
