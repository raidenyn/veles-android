---
trigger: always_on
---

# Reviewer roster

The reviewers the superspec review gate can use and the presets over them. This is the only workflow
file that names a model: the other rules, the schemas, the config, the specs and the procedure refer to
the roster's presets and to the first and second reviewer of a preset.

| Label | Model | Effort |
| --- | --- | --- |
| Sol | GPT-6.2-Sol | medium |
| Opus | Opus-5.5 | medium |

Presets, each an ordered list of labels:

- `sol` = [Sol]
- `opus` = [Opus]
- `both` = [Sol, Opus] (first, then second)

A reviewer is a read-only subagent on the named model at the named effort, the model resolved to the
agent tool's own id.

- **A model cannot be started:** stop and tell the user; never substitute another model.
- **A two-reviewer preset with one reviewer unavailable:** stop and ask; run the other reviewer alone
  only if the user says so.

Change a model by editing this table only.
