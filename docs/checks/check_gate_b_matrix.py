"""Gate B evidence matrix validator (OTP-12), design D10.

`validate(matrix, journey_ids)` checks `docs/gate-b/matrix.json` against the required row set,
the status vocabulary, the initial-state pin, the acceptance invariants, the evidence fields
every verdict needs and the case-specific evidence. It is a pure reader: it never writes, adds
or upgrades a status, and it never records the Gate B decision. A clean result is necessary,
not sufficient; the decision is a separate adjudication outside this tooling.

Stdlib only; Python 3.8 compatible; run from the repository root with
``python3 docs/checks/check_gate_b_matrix.py`` and discovered by
``python3 -m unittest discover -s docs/checks``.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DEFAULT_MATRIX = os.path.join(ROOT, "docs", "gate-b", "matrix.json")
DEFAULT_JOURNEYS = os.path.join(ROOT, "protocol", "conformance", "gate-b-journeys.json")

DECISIONS = ("OPEN", "ACCEPTED")
STATUSES = ("NOT RUN", "FAIL", "PASS")
PLATFORMS = ("windows", "macos", "android")
PHONE_COUNTS = ("none", "A", "AB")

NONE_CASES = ("host-missing-recovery", "host-incompatible-recovery")
A_CASES = (
    "extension-search",
    "spake2-pairing",
    "hint-reconnect-fresh-keys",
    "protected-pull",
    "push",
    "background-delivery",
    "offscreen-copy",
    "forced-freeze",
    "forced-discard",
    "connector-closure",
    "connector-restart",
    "host-closure",
    "host-restart",
    "revocation",
    "bluetooth-interruption",
)
AB_CASES = ("two-phone-lifecycle",)
ANDROID_A_CASES = ("locked-phone-20min", "fixture-retention-expiry")

TOP_LEVEL_FIELDS = ("decision", "gate_b_accepted", "initial_state", "decision_reference", "rows")
REQUIRED_TOP_LEVEL_FIELDS = ("decision", "gate_b_accepted", "initial_state", "rows")
ROW_FIELDS = (
    "id",
    "platform",
    "phones",
    "case",
    "journeys",
    "status",
    "blocker",
    "no_bond_handle_or_hint_reliance",
)
EVIDENCE_FIELDS = (
    "source_revision",
    "evaluator_revision",
    "hashes",
    "identities",
    "models",
    "os_builds",
    "chrome_versions",
    "adapters",
    "focused_tab",
    "exemption_absent",
    "actions",
    "bindings",
    "return_codes",
    "expected",
    "actual",
    "classification",
    "cleanup",
    "raw_captures",
    "checksum_catalog",
    "repetitions",
    "reproducibility",
    "no_plaintext_fallback",
    "host_process_queue",
    "redacted_logs",
    "provenance_anchor",
    "adjudication",
)
# A reference or a revision is one non-empty string, never a boolean stand-in.
TEXT_EVIDENCE_FIELDS = (
    "source_revision",
    "evaluator_revision",
    "focused_tab",
    "provenance_anchor",
    "adjudication",
)
# Identities, models, builds, hashes, adapters, bindings, return codes, classifications,
# cleanup, expected and actual results: one non-empty string or a non-empty list.
TEXT_OR_LIST_EVIDENCE_FIELDS = (
    "hashes",
    "identities",
    "models",
    "os_builds",
    "chrome_versions",
    "adapters",
    "bindings",
    "return_codes",
    "classification",
    "cleanup",
    "expected",
    "actual",
    "reproducibility",
)
# Captures, catalogues, repetitions, logs, actions and queue observations carry content.
CONTENT_EVIDENCE_FIELDS = (
    "raw_captures",
    "checksum_catalog",
    "repetitions",
    "redacted_logs",
    "actions",
    "host_process_queue",
)
# The affirmative flags are exactly true; `false` is not evidence of the safe outcome.
AFFIRMATIVE_EVIDENCE_FIELDS = ("exemption_absent", "no_plaintext_fallback")
FREEZE_CASE_EVIDENCE = (
    "forced_trace",
    "tabs_observations",
    "badge_clear",
    "port_generation_invalidated",
    "old_port_closed_no_host",
    "repull_retained_expired",
)
CASE_EVIDENCE = {
    "forced-freeze": FREEZE_CASE_EVIDENCE,
    "forced-discard": FREEZE_CASE_EVIDENCE,
    "connector-restart": ("fresh_handles", "fresh_keys"),
    "host-restart": ("fresh_handles", "fresh_keys"),
    "hint-reconnect-fresh-keys": ("fresh_keys",),
    "bluetooth-interruption": ("fresh_keys",),
    "offscreen-copy": ("clipboard_transition",),
    "connector-closure": ("no_host_process",),
    "host-closure": ("no_host_process",),
    "background-delivery": ("other_tab_focused",),
    "locked-phone-20min": ("foreground_present",),
}
# Case extras split by kind: a safe outcome is exactly true, everything else needs content.
AFFIRMATIVE_CASE_EVIDENCE = ("foreground_present", "other_tab_focused")
CONTENT_CASE_EVIDENCE = (
    "forced_trace",
    "tabs_observations",
    "badge_clear",
    "port_generation_invalidated",
    "old_port_closed_no_host",
    "repull_retained_expired",
    "fresh_keys",
    "fresh_handles",
    "no_host_process",
    "clipboard_transition",
)

NOT_ACCEPTANCE_STATEMENT = (
    "a validator pass is not Gate B acceptance: this tool never writes or upgrades a status "
    "and records no Gate B decision; the decision is a separate adjudication outside this tooling"
)


class Findings(list):
    """The validator's result: a list of findings that always carries the statement.

    An empty `Findings` is still a successful validation only in the structural sense: the
    statement travels with the result so a direct `validate()` call cannot be presented as a
    Gate B acceptance.
    """

    def __init__(self, findings=(), statement=NOT_ACCEPTANCE_STATEMENT):
        super().__init__(findings)
        self.statement = statement

    def __str__(self):
        return "\n".join(list(self) + [self.statement])


def required_row_ids():
    """The exact required row id set, `<platform>.<phones>.<case>`."""
    rows = set()
    for platform in ("windows", "macos"):
        for case in NONE_CASES:
            rows.add("%s.none.%s" % (platform, case))
        for case in A_CASES:
            rows.add("%s.A.%s" % (platform, case))
        for case in AB_CASES:
            rows.add("%s.AB.%s" % (platform, case))
    for case in ANDROID_A_CASES:
        rows.add("android.A.%s" % case)
    return rows


def _cases_for(platform, phones):
    if platform == "android":
        return ANDROID_A_CASES if phones == "A" else ()
    if phones == "none":
        return NONE_CASES
    if phones == "A":
        return A_CASES
    if phones == "AB":
        return AB_CASES
    return ()


def _is_text(value):
    """A non-empty string: a reference, revision, model or identity written out."""
    return isinstance(value, str) and value.strip() != ""


def _is_meaningful(value):
    """A meaningful evidence value, recursively.

    A non-empty string, a finite number, or a non-empty list or object whose every element or
    value is itself meaningful. Booleans, nulls, empty strings, empty lists, empty objects and
    containers holding only placeholders are not evidence.
    """
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, bool):
        return False
    if isinstance(value, int):
        return True
    if isinstance(value, float):
        return math.isfinite(value)
    if isinstance(value, (list, tuple)):
        return len(value) > 0 and all(_is_meaningful(item) for item in value)
    if isinstance(value, dict):
        return len(value) > 0 and all(_is_meaningful(item) for item in value.values())
    return False


def _is_text_or_list(value):
    """A non-empty string, or a non-empty list of meaningful values."""
    if _is_text(value):
        return True
    if not isinstance(value, list) or not value:
        return False
    return all(_is_meaningful(item) for item in value)


def _has_content(value):
    """Non-empty content, never a bare boolean: captures, catalogues, logs, actions, ..."""
    return _is_meaningful(value)


def validate(matrix, journey_ids):
    """Return the findings for `matrix` as a `Findings` list.

    The list is empty only when every structural rule passes, and it always carries the
    not-acceptance statement (`result.statement`, also printed by `str(result)`), because a
    validation pass is necessary and not sufficient. `journey_ids` is the set of journey ids
    defined by `protocol/conformance/gate-b-journeys.json`. The matrix is never modified.
    """
    findings = Findings()
    if not isinstance(matrix, dict):
        return Findings(["matrix: must be a JSON object"])
    known_journeys = set(journey_ids)

    for field in REQUIRED_TOP_LEVEL_FIELDS:
        if field not in matrix:
            findings.append("matrix: missing top-level field %s" % field)
    for field in sorted(set(matrix) - set(TOP_LEVEL_FIELDS)):
        findings.append("matrix: unknown top-level field %s" % field)

    initial_state = matrix.get("initial_state")
    if not isinstance(initial_state, bool):
        findings.append("matrix: initial_state must be a boolean")

    decision = matrix.get("decision")
    if decision not in DECISIONS:
        findings.append("matrix: decision %r is outside %s" % (decision, list(DECISIONS)))
    gate_b_accepted = matrix.get("gate_b_accepted")
    if not isinstance(gate_b_accepted, bool):
        findings.append("matrix: gate_b_accepted must be a boolean")

    if initial_state is True:
        if decision != "OPEN":
            findings.append("matrix: initial_state requires decision OPEN, found %r" % (decision,))
        if gate_b_accepted is not False:
            findings.append("matrix: initial_state requires gate_b_accepted false")

    if decision == "ACCEPTED" and gate_b_accepted is not True:
        findings.append("matrix: decision ACCEPTED requires gate_b_accepted true")

    rows = matrix.get("rows")
    if not isinstance(rows, list):
        findings.append("matrix: rows must be a list")
        rows = []

    present_ids = []
    for index, row in enumerate(rows):
        _validate_row(index, row, known_journeys, findings)
        if isinstance(row, dict) and _is_text(row.get("id")):
            present_ids.append(row["id"])

    if initial_state is True:
        for row in rows:
            if isinstance(row, dict) and row.get("status") != "NOT RUN":
                where = row["id"] if _is_text(row.get("id")) else "rows"
                findings.append("%s: initial_state requires every row NOT RUN" % where)

    seen = set()
    for row_id in present_ids:
        if row_id in seen:
            findings.append("%s: duplicate row id" % row_id)
        seen.add(row_id)

    required = required_row_ids()
    for row_id in sorted(required - seen):
        findings.append("missing required row %s" % row_id)
    for row_id in sorted(seen - required):
        findings.append("unknown row %s" % row_id)

    if gate_b_accepted is True:
        if decision != "ACCEPTED":
            findings.append("matrix: gate_b_accepted requires decision ACCEPTED")
        if not _is_text(matrix.get("decision_reference")):
            findings.append("matrix: gate_b_accepted requires a non-empty decision_reference")
        for row in rows:
            if not isinstance(row, dict):
                continue
            row_id = row.get("id") if _is_text(row.get("id")) else "rows"
            if row.get("status") != "PASS":
                findings.append(
                    "%s: gate_b_accepted requires every row PASS, found %r"
                    % (row_id, row.get("status"))
                )

    return findings


def _validate_row(index, row, journey_ids, findings):
    if not isinstance(row, dict):
        findings.append("rows[%d]: must be an object" % index)
        return
    row_id = row.get("id")
    where = row_id if _is_text(row_id) else "rows[%d]" % index

    for field in sorted(set(row) - set(ROW_FIELDS) - {"evidence"}):
        findings.append("%s: unknown row field %s" % (where, field))
    for field in ROW_FIELDS:
        if field not in row:
            findings.append("%s: missing row field %s" % (where, field))
    for field in ("platform", "phones", "case", "status", "blocker"):
        if field in row and not isinstance(row[field], str):
            findings.append("%s: %s must be a string" % (where, field))

    platform = row.get("platform")
    phones = row.get("phones")
    case = row.get("case")
    if isinstance(platform, str) and isinstance(phones, str) and isinstance(case, str):
        expected_id = "%s.%s.%s" % (platform, phones, case)
        if row_id != expected_id:
            findings.append("%s: id must be %s" % (where, expected_id))
        if platform not in PLATFORMS:
            findings.append("%s: unknown platform %r" % (where, platform))
        elif phones not in PHONE_COUNTS:
            findings.append("%s: unknown phones value %r" % (where, phones))
        elif case not in _cases_for(platform, phones):
            findings.append(
                "%s: case %r is not a required %s.%s row" % (where, case, platform, phones)
            )

    journeys = row.get("journeys")
    if "journeys" in row:
        if not isinstance(journeys, list) or any(not isinstance(entry, str) for entry in journeys):
            findings.append("%s: journeys must be a list of journey id strings" % where)
        elif len(set(journeys)) != len(journeys):
            findings.append("%s: journeys cites a journey id twice" % where)
        else:
            for journey in journeys:
                if journey not in journey_ids:
                    findings.append("%s: cites unknown journey id %r" % (where, journey))

    status = row.get("status")
    if "status" in row and status not in STATUSES:
        findings.append("%s: unknown status %r outside %s" % (where, status, list(STATUSES)))

    blocker = row.get("blocker")
    if isinstance(blocker, str) and status == "NOT RUN" and not blocker.strip():
        findings.append("%s: a NOT RUN row must carry a non-empty blocker" % where)

    no_bond = row.get("no_bond_handle_or_hint_reliance")
    if status == "PASS":
        if no_bond is not True:
            findings.append(
                "%s: a PASS row requires no_bond_handle_or_hint_reliance true" % where
            )
    elif status in ("NOT RUN", "FAIL"):
        if no_bond is not None:
            findings.append(
                "%s: no_bond_handle_or_hint_reliance must be null unless the status is PASS"
                % where
            )

    evidence = row.get("evidence")
    if status == "NOT RUN":
        if "evidence" in row:
            findings.append("%s: a NOT RUN row must not carry an evidence object" % where)
    elif status in ("PASS", "FAIL"):
        if "evidence" not in row:
            findings.append("%s: a %s row requires an evidence object" % (where, status))
        elif not isinstance(evidence, dict):
            findings.append("%s: evidence must be an object" % where)
        else:
            # A malformed case value is already a finding; no case-specific evidence is
            # evaluated against it.
            _validate_evidence(where, case if isinstance(case, str) else None, evidence, findings)


def _validate_evidence(where, case, evidence, findings):
    for field in TEXT_EVIDENCE_FIELDS:
        if not _is_text(evidence.get(field)):
            findings.append(
                "%s: evidence field %s must be a non-empty string reference" % (where, field)
            )
    for field in TEXT_OR_LIST_EVIDENCE_FIELDS:
        if not _is_text_or_list(evidence.get(field)):
            findings.append(
                "%s: evidence field %s must be a non-empty string or list" % (where, field)
            )
    for field in CONTENT_EVIDENCE_FIELDS:
        if not _has_content(evidence.get(field)):
            findings.append(
                "%s: evidence field %s must carry non-empty content, not a boolean"
                % (where, field)
            )
    for field in AFFIRMATIVE_EVIDENCE_FIELDS:
        if evidence.get(field) is not True:
            findings.append("%s: evidence %s must be true" % (where, field))
    for field in CASE_EVIDENCE.get(case, ()):
        if field in AFFIRMATIVE_CASE_EVIDENCE:
            if evidence.get(field) is not True:
                findings.append(
                    "%s: case %s requires the evidence flag %s to be true"
                    % (where, case, field)
                )
        elif not _has_content(evidence.get(field)):
            findings.append(
                "%s: case %s requires non-empty evidence %s, not a boolean"
                % (where, case, field)
            )


def report(matrix, journey_ids):
    """The validator's full output: the findings followed by the not-acceptance statement."""
    findings = validate(matrix, journey_ids)
    return list(findings) + [findings.statement]


def _load(path):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description=(
            "Validate the Gate B evidence matrix. It never writes a status and never records "
            "the Gate B decision; a pass is necessary, not sufficient."
        )
    )
    parser.add_argument("--matrix", default=DEFAULT_MATRIX, help="matrix JSON path")
    parser.add_argument(
        "--journeys", default=DEFAULT_JOURNEYS, help="journey file whose ids a row may cite"
    )
    arguments = parser.parse_args(argv)
    matrix = _load(arguments.matrix)
    journeys = _load(arguments.journeys)
    findings = validate(matrix, set(journeys["journeys"]))
    for finding in findings:
        print(finding)
    print(findings.statement)
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
