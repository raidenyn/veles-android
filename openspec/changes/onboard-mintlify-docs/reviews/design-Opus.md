# Review record: design

- Artifact: `design.md`
- Reviewer: Opus (Opus-5.5, medium)
- Session preset: `opus` = [Opus]
- Cap: 5 rounds per reviewer per pass
- Upstream: `brainstorm.md` sha256 c27ca9d75a2a5b085956825eb2aa827fd3b518d9f65ba6df139b52ff9670a0c7; `proposal.md` sha256 0260f7c0185b8f9957eb6026c228fd827d306227fa373dd5646729d673b4ea65

## Pass 1

### Round 1 — approve (twelve minor fixes made afterwards, more than five, so review reopens)

| Severity | Finding | Disposition |
|---|---|---|
| medium | D6 default ignores unlisted; whether `!/CONTRIBUTING.md` overrides them; "wrapper" scope for Build/Contributing imports. | Fixed: defaults listed; explicit re-include assumed to override (fallback and spot check cover it); imports in every published `.mdx` page are resolved and scanned. |
| medium | Line-by-line code-span matching would flag multi-line spans in three curated pages. | Fixed: code spans matched per paragraph, CommonMark style. |
| medium | D7 named the wrong contract-test edit. | Fixed: count 2→3 at line 3700, new `agent-files` assertion, messages including the mutation test. |
| medium | `BUILD.md` line 16 relative link missing from D4. | Fixed: every relative link in both files becomes absolute; line 16 listed. |
| medium | GitHub does not render `.mdx`, so README links to a `.mdx` guide would show raw source. | Fixed: the guide stays plain `.md` with no components. |
| medium | Brace rule not shown to fail `mint validate`, contradicting the calibration rule. | Fixed: brace is a warning unless calibration shows the build rejects it. |
| low | Sub-group assignment, titles and Overview/Architecture file names unspecified. | Fixed: all 28 assigned; title rule and file names given. |
| low | "Latest release" ambiguous with three tag prefixes. | Fixed: link to the Releases page. |
| low | Local `mint` version was a rule, not a value. | Fixed: pinned `4.2.1199`. |
| low | Home page `/` assumption unverified. | Fixed: added to the deployed spot check. |
| low | Guard sees only tracked files. | Fixed: stated in D6 (and for `docs/AGENTS.md`). |
| low | git matching stands in for Mintlify's; the four pattern forms should be a rule. | Fixed: stated in D1 and enforced by the guard. |

### Round 2 — approve

All twelve round-1 fixes confirmed; the 28-capability assignment matches `openspec/specs/` exactly.

| Severity | Finding | Disposition |
|---|---|---|
| low | Contract-test count assertion is at line 3701, not 3700. | Fixed. |
| low | The dropped-tests mutation (line 3744) also asserts the message. | Fixed: both mutation tests named. |
| low | Brace warning overrides the brainstorm without saying so. | Fixed: D6 states the narrowing and why. |
| low | Absolute links override the brainstorm's "site routes" without saying so. | Fixed: Open Questions states the replacement and why. |

Approved content: design sha256 7820ef3cd781f22f5d64fa7df012d95e91ba6d3b0d9cfbde452690398b9db137 (re-pointed after four non-triggering low fixes from round 2: line reference, both mutation tests, brace narrowing note, link-rule note)
