---
pointer: "Every planning file and the closeout docs go through the review gate (ask for a preset first; stop after the plan); delivered at the openspec planning skills and commands; read this file then if it was not."
skills:
  - "openspec-ff-change"
  - "opsx:ff"
  - "openspec-propose"
  - "opsx:propose"
  - "openspec-new-change"
  - "opsx:new"
  - "openspec-continue-change"
  - "opsx:continue"
  - "openspec-apply-change"
  - "opsx:apply"
  - "openspec-verify-change"
  - "opsx:verify"
  - "openspec-archive-change"
  - "opsx:archive"
commands:
  - "\\bopenspec\\s+new\\s+change\\b"
  - "\\bopenspec\\s+instructions\\s+(brainstorm|proposal|design|specs|tasks|plan)\\b"
---

Before reviewing, verifying or applying in a superspec change, read the sections Reviewer brief, Review passes and caps, Revision binding and Escalation of `docs/superspec-workflow.md`.

- **Scope:** the planning files (brainstorm, proposal, design, specs, tasks, plan) and the closeout documentation; never implementation, which keeps the standard single review subagent.
- **Ask first:** before the first review of a session, ask the user for a roster preset or a custom model; a custom model is session-only and nothing is remembered across sessions.
- **Gate on files:** review every planning file before it is used or presented as final, including a `design.md` pre-filled by brainstorming, files written through ff or propose, and files found without a record.
- **The reviewer** is a read-only subagent.
- **Findings:** fix or rebut every critical and high finding; record a disposition for medium and low.
- **Caps:** at most 5 rounds per reviewer per pass; at a cap, stop and explain in plain language (the *Escalation* section), then wait.
- **Records:** live in the change's `reviews/`; an approval is bound to the file's hash.
- **Re-review:** only a critical or high fix or rebuttal, more than 5 minor findings (medium, low, cosmetic) fixed since the last completed review, or a normative edit that is not a finding fix, reopens review. Up to 5 minor fixes and cosmetic edits are recorded and the approval hash re-pointed with a note.
- **The audit** runs whenever verification runs, however it is invoked, using the *Revision binding* audit checklist; any failure is FAIL.
- **Escalation** from a single-reviewer choice to a two-reviewer preset is allowed, never a downgrade.
- **The apply stop:** after the plan is approved, stop and ask the user to switch to a coding model; ff and propose end at the plan.
