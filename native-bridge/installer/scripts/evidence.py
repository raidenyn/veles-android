#!/usr/bin/env python3
"""OTP-25 acceptance evidence validator (Task 15). Python 3.12+ standard library only.

    evidence.py validate --run <dir> --cases <cases.json> --anchor <anchor.json> [--previous <dir>]
    evidence.py check-matrix --cases <cases.json> --matrix <matrix.json>

Exit status: 0 structurally valid, 1 invalid evidence, 2 missing environment or input.

WHAT SUCCESS MEANS. Exit 0 says the bundle is internally consistent, bound to an
externally supplied anchor, checksummed, independently adjudicated and append-only.
It does NOT say the installer works, and it can never mean acceptance: acceptance is
the owner's separate decision after independent adjudication of real physical
evidence. This tool cannot observe a machine; it only refuses bundles that could not
have come from an honest, bound, adjudicated run.

Bundle layout (``--run``)::

    run.json          operator-written record: binding, environment, clocks, accounts,
                      capture index (path, sha256, bytes), publication state
    ledger.jsonl      append-only hash-chained attempts (PASS, FAIL, BLOCKED, NOT_RUN)
    captures/         raw captures named in run.json (every file indexed)
    adjudication.json written by someone else; binds sha256(run.json) and the ledger head

Anchor (``--anchor``, supplied from OUTSIDE the run): per platform the exact
source_commit, config_sha256, descriptor_sha256, tools_sha256, unsigned_sha256,
package_sha256 and signer. The run may not carry its own copy.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

OUTCOMES = ("PASS", "FAIL", "BLOCKED", "NOT_RUN")
BINDING_FIELDS = ("source_commit", "config_sha256", "descriptor_sha256", "tools_sha256",
                  "unsigned_sha256", "package_sha256", "signer")
DIGEST_FIELDS = ("config_sha256", "descriptor_sha256", "tools_sha256", "unsigned_sha256", "package_sha256")
GENESIS = "0" * 64
MAX_CLOCK_OFFSET_SECONDS = 30.0
MAX_JSON_BYTES = 32 * 1024 * 1024
MAX_REPORT_BYTES = 64 * 1024
# The engine's InstallReport vocabulary (model.rs Operation / FailureCode / WarningCode).
REPORT_FIELDS = {"operation", "state", "prior_usable", "active_payload", "failure", "warnings"}
REPORT_OPERATIONS = ("inspect", "install", "repair", "rollback", "uninstall")
REPORT_FAILURES = ("invalid-input", "origin-rejected", "invalid-package", "trust-denied", "trust-unavailable",
                   "policy-denied", "unsupported-target", "busy", "host-active", "inspection-denied",
                   "foreign-state", "unsafe-path", "io", "recovery-required", "unsupported-state",
                   "no-rollback", "repair-package-required", "cleanup-pending")
REPORT_WARNINGS = ("compatibility-window", "leftover-files", "cleanup-pending")
SHA_RE = re.compile(r"^[0-9a-f]{64}$")
COMMIT_RE = re.compile(r"^[0-9a-f]{40}$")
TIME_RE = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
# An account is refused as "standard" when its identity capture shows any of these.
WINDOWS_ADMIN_MARKERS = ("S-1-5-32-544", "S-1-16-12288", "S-1-16-16384")
MAC_ADMIN_TOKENS = {"admin", "wheel", "root", "_appserveradm", "_appserverusr"}
WINDOWS_USER_ROW = re.compile(r"^(?P<name>\S.*?)\s+S-1-5-21(?:-\d+){4}[ \t]*$", re.MULTILINE)
MAC_ID_ROW = re.compile(r"^uid=(?P<uid>\d+)\((?P<name>[^()\s]+)\) gid=\d+\([^()\s]+\) groups=(?P<groups>\d+\([^()\s]+\)(?:,\d+\([^()\s]+\))*)$")
MAC_GROUP = re.compile(r"(\d+)\(([^()\s]+)\)")

NOT_ACCEPTANCE = ("structurally valid; this is NOT acceptance (no physical verdict is implied, "
                  "#89 and Gates B/C/D are unaffected)")


class MissingInput(Exception):
    """A required input or environment element is absent or unusable (exit status 2)."""


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _no_duplicates(pairs: list[tuple[str, Any]]) -> dict:
    out: dict = {}
    for key, value in pairs:
        if key in out:
            raise ValueError(f"duplicate key {key!r}")
        out[key] = value
    return out


def _parse(data: bytes) -> Any:
    return json.loads(data.decode("utf-8"), object_pairs_hook=_no_duplicates)


def _load_input(path: Path, what: str) -> Any:
    try:
        if path.stat().st_size > MAX_JSON_BYTES:
            raise MissingInput(f"{what} {path} is larger than {MAX_JSON_BYTES} bytes")
        return _parse(path.read_bytes())
    except FileNotFoundError:
        raise MissingInput(f"{what} not found: {path}") from None
    except (OSError, ValueError, UnicodeDecodeError) as error:
        raise MissingInput(f"{what} {path} is unreadable or not strict JSON: {error}") from None


def _time(value: Any) -> datetime | None:
    if not isinstance(value, str) or not TIME_RE.match(value):
        return None
    try:
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def _obj(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _str(value: Any) -> str:
    return value if isinstance(value, str) else ""


# --------------------------------------------------------------------------- cases


def load_cases(path: Path) -> dict[str, dict]:
    doc = _load_input(path, "cases file")
    cases = doc.get("cases") if isinstance(doc, dict) else None
    if not isinstance(cases, list) or not cases:
        raise MissingInput(f"cases file {path} has no cases")
    out: dict[str, dict] = {}
    for case in cases:
        if not isinstance(case, dict) or not isinstance(case.get("id"), str) or case["id"] in out:
            raise MissingInput(f"cases file {path} has a missing or duplicate case id")
        out[case["id"]] = case
    return out


def check_matrix(cases_path: Path, matrix_path: Path) -> list[str]:
    """Matrix rows must cover the cases exactly; no physical PASS may be recorded here."""
    cases = load_cases(cases_path)
    cases_doc = _load_input(cases_path, "cases file")
    blockers = _obj(cases_doc.get("blockers"))
    matrix = _load_input(matrix_path, "matrix file")
    errors: list[str] = []
    if _obj(matrix.get("acceptance")).get("decision") != "none":
        errors.append("matrix acceptance.decision must be 'none': acceptance is the owner's separate decision")
    if _obj(matrix.get("acceptance")).get("issue_89") != "open":
        errors.append("matrix acceptance.issue_89 must stay 'open'")
    rows = matrix.get("rows")
    if not isinstance(rows, list):
        return errors + ["matrix has no rows"]
    seen: set[str] = set()
    for row in rows:
        row = _obj(row)
        row_id = _str(row.get("id"))
        if row_id in seen:
            errors.append(f"matrix row {row_id} is duplicated")
        seen.add(row_id)
        if row_id not in cases:
            errors.append(f"matrix row {row_id!r} has no case")
            continue
        outcome = row.get("outcome")
        if outcome not in OUTCOMES:
            errors.append(f"matrix row {row_id} has outcome {outcome!r}")
        elif outcome == "PASS":
            errors.append(f"matrix row {row_id} records PASS: a physical PASS needs an adjudicated bundle "
                          "and the owner's decision, and none exists in this repository")
        elif outcome == "FAIL" and not row.get("attempts"):
            errors.append(f"matrix row {row_id} records FAIL with no attempt history")
        if outcome in ("BLOCKED", "NOT_RUN"):
            named = row.get("blockers")
            if not isinstance(named, list) or not named or any(b not in blockers for b in named):
                errors.append(f"matrix row {row_id} needs at least one known blocker")
            settle = _obj(cases[row_id].get("settle"))
            if not settle.get("commands") or not settle.get("manual"):
                errors.append(f"case {row_id} has no exact settling command and manual step")
        if row.get("attempts") and outcome in ("BLOCKED", "NOT_RUN"):
            errors.append(f"matrix row {row_id} lists attempts but is {outcome}")
    for case_id in cases:
        if case_id not in seen:
            errors.append(f"matrix is missing case {case_id}")
    return errors


# --------------------------------------------------------------------------- run


class _Run:
    def __init__(self, run: Path, cases: dict[str, dict], anchor_all: dict) -> None:
        self.run = run
        self.cases = cases
        self.anchor_all = anchor_all
        self.errors: list[str] = []
        self.capture_bytes: dict[str, bytes] = {}
        self.indexed: dict[str, dict] = {}

    def err(self, message: str) -> None:
        self.errors.append(message)

    # captures ----------------------------------------------------------------
    def index_captures(self, doc: dict) -> None:
        listed = doc.get("captures")
        if not isinstance(listed, list):
            self.err("run.json has no capture index (captures)")
            return
        root = (self.run / "captures").resolve()
        for item in listed:
            item = _obj(item)
            path, digest = _str(item.get("path")), _str(item.get("sha256"))
            if path in self.indexed:
                self.err(f"capture {path} is indexed twice")
                continue
            self.indexed[path] = item
            if not path.startswith("captures/") or ".." in Path(path).parts:
                self.err(f"capture path {path!r} escapes captures/")
                continue
            if (self.run / path).is_symlink():
                self.err(f"capture {path} is a symbolic link; raw captures must be regular files")
                continue
            file = (self.run / path).resolve()
            if root not in file.parents:
                self.err(f"capture path {path!r} escapes captures/")
                continue
            if not SHA_RE.match(digest):
                self.err(f"capture {path} has no valid sha256")
                continue
            try:
                data = file.read_bytes() if file.is_file() else None
            except OSError:
                data = None
            if data is None:
                self.err(f"capture {path} is missing from the run")
                continue
            if sha256_bytes(data) != digest or item.get("bytes") != len(data):
                self.err(f"capture {path} fails its checksum (index says {digest[:12]}...)")
                continue
            self.capture_bytes[path] = data
        captures_dir = self.run / "captures"
        if captures_dir.is_dir():
            for file in sorted(captures_dir.rglob("*")):
                if file.is_file() or file.is_symlink():
                    rel = file.relative_to(self.run).as_posix()
                    if rel not in self.indexed:
                        self.err(f"unlisted raw capture {rel}: every file must be in the checksummed index")

    def capture(self, path: Any, what: str) -> bytes | None:
        if not isinstance(path, str) or path not in self.capture_bytes:
            self.err(f"{what}: capture {path!r} is not a verified capture of this run")
            return None
        return self.capture_bytes[path]

    # binding -------------------------------------------------------------------
    def check_binding(self, doc: dict, platform: str) -> None:
        platforms = _obj(self.anchor_all.get("platforms"))
        anchor = platforms.get(platform)
        if not isinstance(anchor, dict):
            self.err(f"anchor has no entry for platform {platform}")
            return
        binding = _obj(doc.get("binding"))
        for field in BINDING_FIELDS:
            want, got = anchor.get(field), binding.get(field)
            if not isinstance(want, str) or not want:
                self.err(f"anchor has no {field} for {platform}")
                continue
            valid = COMMIT_RE.match(want) if field == "source_commit" else (SHA_RE.match(want) and want != GENESIS) if field in DIGEST_FIELDS else bool(want.strip())
            if not valid:
                self.err(f"anchor {field} is not a valid value")
            if got != want:
                self.err(f"binding {field} does not equal the externally supplied anchor")
        captures = _obj(binding.get("captures"))
        needed = {"package_digest": "package_sha256", "signature": "signer"}
        if platform == "macos":
            needed["gatekeeper"] = None  # type: ignore[assignment]
        for key, bound in needed.items():
            data = self.capture(captures.get(key), f"binding capture {key}")
            if data is None or bound is None:
                continue
            text = data.decode("utf-8", "replace")
            value = _str(anchor.get(bound))
            found = value in text.lower() if bound.endswith("sha256") else value in text
            if not found:
                self.err(f"binding {bound} is self-attested: the {key} raw capture does not contain the anchored value")

    # environment / clocks / accounts ---------------------------------------------
    def check_context(self, doc: dict) -> tuple[datetime | None, datetime | None]:
        env = _obj(doc.get("environment"))
        for field in ("os_version", "arch", "chrome_version"):
            if not _str(env.get(field)).strip():
                self.err(f"environment.{field} is required")
        clocks = _obj(doc.get("clocks"))
        start, end = _time(clocks.get("start_utc")), _time(clocks.get("end_utc"))
        if not _str(clocks.get("source")).strip():
            self.err("clock source is required")
        if start is None or end is None or start >= end:
            self.err("clock start_utc/end_utc must be ordered UTC timestamps")
        offset = clocks.get("host_offset_seconds")
        if not isinstance(offset, (int, float)) or isinstance(offset, bool) or abs(offset) > MAX_CLOCK_OFFSET_SECONDS:
            self.err(f"clock host_offset_seconds must be a number within +/-{MAX_CLOCK_OFFSET_SECONDS:g}s")
        self.capture(clocks.get("offset_capture"), "clock offset")
        return start, end

    def check_accounts(self, doc: dict) -> dict[str, dict]:
        accounts: dict[str, dict] = {}
        for account in doc.get("accounts") if isinstance(doc.get("accounts"), list) else []:
            account = _obj(account)
            ident = _str(account.get("id"))
            if not ident or ident in accounts:
                self.err("accounts need unique non-empty ids")
                continue
            accounts[ident] = account
        return accounts

    def prove_standard(self, account: dict, platform: str, label: str) -> None:
        """Standard-user proof is POSITIVE evidence: a successful, complete, parsable
        capture of the declared account, its groups and (Windows) integrity level.

        Absence of administrator markers proves nothing - an empty, truncated or
        unrelated capture has none - so every field below must be present and parse.
        """
        if account.get("kind") != "standard":
            self.err(f"{label}: account {account.get('id')!r} is {account.get('kind')!r}, not a standard user")
            return
        data = self.capture(account.get("identity_capture"), f"{label}: account identity")
        if data is None:
            self.err(f"{label}: account identity capture is required to prove a standard user")
            return
        declared = _str(account.get("user_name")).strip()
        if not declared:
            self.err(f"{label}: account {account.get('id')!r} needs user_name, the account name the identity capture must show")
            return
        text = data.decode("utf-8", "replace")
        if platform == "windows":
            self._prove_windows_identity(text, declared, label)
        else:
            self._prove_macos_identity(text, declared, label)

    def _prove_windows_identity(self, text: str, declared: str, label: str) -> None:
        user_at, group_at = text.find("USER INFORMATION"), text.find("GROUP INFORMATION")
        if user_at < 0 or group_at < 0:
            self.err(f"{label}: identity capture lacks the whoami /user and /groups sections "
                     "(empty, truncated or unrelated output proves nothing)")
            return
        user_section = text[user_at:group_at] if user_at < group_at else text[user_at:]
        group_section = text[group_at:user_at] if group_at < user_at else text[group_at:]
        users = [m for m in WINDOWS_USER_ROW.finditer(user_section)]
        if len(users) != 1:
            self.err(f"{label}: identity capture shows no single account row with a user SID")
        elif users[0].group("name").strip().lower() != declared.lower():
            self.err(f"{label}: identity capture shows {users[0].group('name').strip()!r}, "
                     f"not the declared account {declared!r}")
        sids = re.findall(r"\bS-1-\d+(?:-\d+)+\b", group_section)
        if "S-1-1-0" not in sids:
            self.err(f"{label}: identity capture has no complete group table (the Everyone group row is missing)")
        labels = re.findall(r"Mandatory Label\\(\S+) Mandatory Level\s+Label\s+(S-1-16-\d+)", group_section)
        if len(labels) != 1:
            self.err(f"{label}: identity capture shows no single mandatory integrity label")
        elif labels[0] != ("Medium", "S-1-16-8192"):
            self.err(f"{label}: integrity level {labels[0][0]} ({labels[0][1]}) is not the Medium level of a "
                     "standard user's token")
        if any(marker in text for marker in WINDOWS_ADMIN_MARKERS):
            self.err(f"{label}: identity capture shows administrator membership or a High integrity token")

    def _prove_macos_identity(self, text: str, declared: str, label: str) -> None:
        rows = [line for line in text.splitlines() if line.strip()]
        match = MAC_ID_ROW.match(rows[0].strip()) if len(rows) == 1 else None
        if match is None:
            self.err(f"{label}: identity capture is not exactly one complete `id` line "
                     "(uid, gid and groups); empty or truncated output proves nothing")
            return
        if int(match.group("uid")) == 0:
            self.err(f"{label}: identity capture shows uid 0, not a standard user")
        if match.group("name") != declared:
            self.err(f"{label}: identity capture shows {match.group('name')!r}, not the declared account {declared!r}")
        groups = MAC_GROUP.findall(match.group("groups"))
        if not groups or ",".join(f"{g}({n})" for g, n in groups) != match.group("groups"):
            self.err(f"{label}: identity capture has no parsable group list")
        tokens = {t.lower() for t in re.split(r"[^\w]+", text) if t}
        if tokens & MAC_ADMIN_TOKENS:
            self.err(f"{label}: identity capture shows administrator group membership")

    # ledger ------------------------------------------------------------------------
    def read_ledger(self) -> tuple[list[bytes], list[dict], str]:
        path = self.run / "ledger.jsonl"
        try:
            raw = path.read_bytes()
        except OSError:
            self.err("ledger.jsonl is missing")
            return [], [], GENESIS
        lines = raw.split(b"\n")
        if lines and lines[-1] == b"":
            lines.pop()
        entries: list[dict] = []
        prev = GENESIS
        for number, line in enumerate(lines, 1):
            try:
                entry = _parse(line)
            except (ValueError, UnicodeDecodeError):
                self.err(f"ledger line {number} is not strict JSON")
                return lines, entries, prev
            if not isinstance(entry, dict) or entry.get("seq") != number or entry.get("prev") != prev:
                self.err(f"ledger chain broken at line {number}: an entry was removed, reordered or altered")
                return lines, entries, prev
            entries.append(entry)
            prev = sha256_bytes(line)
        return lines, entries, prev

    def check_previous(self, previous: Path, lines: list[bytes]) -> None:
        try:
            old = (previous / "ledger.jsonl").read_bytes().split(b"\n")
        except OSError:
            raise MissingInput(f"previous run ledger not readable: {previous}") from None
        if old and old[-1] == b"":
            old.pop()
        if lines[: len(old)] != old:
            self.err("ledger is not append-only relative to the previous run: earlier entries changed or vanished")
        try:
            old_doc = _parse((previous / "run.json").read_bytes())
        except (OSError, ValueError, UnicodeDecodeError):
            raise MissingInput(f"previous run.json not readable: {previous}") from None
        for item in old_doc.get("captures", []) if isinstance(old_doc, dict) else []:
            item = _obj(item)
            if self.indexed.get(_str(item.get("path")), {}).get("sha256") != item.get("sha256"):
                self.err(f"ledger is not append-only: previous capture {item.get('path')} changed or vanished")

    def check_attempts(self, entries: list[dict], platform: str, accounts: dict[str, dict],
                       window: tuple[datetime | None, datetime | None]) -> None:
        start, end = window
        failures: dict[str, list[str]] = {}
        seen_ids: set[str] = set()
        last_started: datetime | None = None
        for entry in entries:
            attempt_id = _str(entry.get("attempt_id"))
            label = f"attempt {attempt_id or entry.get('seq')}"
            if not attempt_id or attempt_id in seen_ids:
                self.err(f"{label}: attempt_id must be unique and non-empty")
            seen_ids.add(attempt_id)
            case = self.cases.get(_str(entry.get("case")))
            if case is None:
                self.err(f"{label}: unknown case {entry.get('case')!r}")
                continue
            if case.get("platform") != platform:
                self.err(f"{label}: case {case['id']} platform differs from the run platform {platform}")
                continue
            outcome = entry.get("outcome")
            if outcome not in OUTCOMES:
                self.err(f"{label}: outcome {outcome!r} is not one of {', '.join(OUTCOMES)}")
                continue
            started, ended = _time(entry.get("started")), _time(entry.get("ended"))
            if started is None or ended is None or started > ended:
                self.err(f"{label}: started/ended must be ordered UTC timestamps")
                continue
            if last_started and started < last_started:
                self.err(f"{label}: ledger clock moves backwards")
            last_started = started
            if start and end and not (start <= started and ended <= end):
                self.err(f"{label}: outside the recorded clock window of the run")
            if outcome in ("BLOCKED", "NOT_RUN", "FAIL") and not _str(entry.get("reason")).strip():
                self.err(f"{label}: {outcome} needs a recorded reason")
            if outcome == "FAIL":
                failures.setdefault(case["id"], []).append(attempt_id)
            if outcome != "PASS":
                continue
            prior = failures.get(case["id"], [])
            prior = [f for f in prior if f != attempt_id]
            resolved = entry.get("resolves") if isinstance(entry.get("resolves"), list) else []
            missing = [f for f in prior if f not in resolved]
            if missing:
                self.err(f"{label}: PASS after prior failure(s) {', '.join(missing)} that it does not acknowledge in resolves")
            self.check_pass(entry, case, label, platform, accounts, started, ended)

    def check_pass(self, entry: dict, case: dict, label: str, platform: str, accounts: dict[str, dict],
                   started: datetime, ended: datetime) -> None:
        requires = _obj(case.get("requires"))
        account = accounts.get(_str(entry.get("account")))
        if account is None:
            self.err(f"{label}: account {entry.get('account')!r} is not declared in run.json")
        else:
            if requires.get("standard_account"):
                self.prove_standard(account, platform, label)
            if requires.get("fresh_account"):
                if account.get("fresh") is not True or self.capture(account.get("fresh_evidence"), f"{label}: fresh account evidence") is None:
                    self.err(f"{label}: this case needs a fresh account with captured freshness evidence")
        stamps: list[datetime] = []
        seen_actions: dict[str, dict] = {}
        for action in entry.get("actions") if isinstance(entry.get("actions"), list) else []:
            action = _obj(action)
            aid = _str(action.get("id"))
            when = _time(action.get("at"))
            if when is None or not (started <= when <= ended):
                self.err(f"{label}: action {aid} has a timestamp outside the attempt (clock)")
            else:
                stamps.append(when)
            if action.get("elevated") is not False:
                self.err(f"{label}: action {aid} was or may have been elevated; elevation disqualifies a standard-user PASS")
            if not _str(action.get("step")).strip() or not _str(action.get("returns")).strip():
                self.err(f"{label}: action {aid} needs the controlled step and its observed return")
            captured = self.capture(action.get("capture"), f"{label}: action {aid}")
            disposition = _obj(case.get("dispositions")).get(aid)
            if isinstance(disposition, dict):
                self.check_disposition(label, aid, action, captured, disposition)
            seen_actions[aid] = action
        for needed in case.get("actions", []):
            if needed not in seen_actions:
                self.err(f"{label}: required action {needed} is missing")
        seen_obs: dict[str, dict] = {}
        for obs in entry.get("observations") if isinstance(entry.get("observations"), list) else []:
            obs = _obj(obs)
            oid = _str(obs.get("id"))
            when = _time(obs.get("at"))
            if when is None or not (started <= when <= ended):
                self.err(f"{label}: observation {oid} has a timestamp outside the attempt (clock)")
            self.capture(obs.get("capture"), f"{label}: observation {oid}")
            seen_obs[oid] = obs
        for needed in case.get("observations", []):
            if needed not in seen_obs:
                suffix = " (a retention claim needs both grant-before and grant-after observations)" if needed.startswith("grant-") else ""
                self.err(f"{label}: required observation {needed} is missing{suffix}")
        if requires.get("retention_claim") and "grant-before" in seen_obs and "grant-after" in seen_obs:
            before, after = seen_obs["grant-before"], seen_obs["grant-after"]
            if before.get("capture") == after.get("capture"):
                self.err(f"{label}: grant-before and grant-after must be distinct captures (retention)")
            tb, ta = _time(before.get("at")), _time(after.get("at"))
            if tb is None or ta is None or not tb < ta:
                self.err(f"{label}: grant-before must be observed strictly before grant-after (retention)")
            maintenance = _time(_obj(seen_actions.get(_str(requires.get("retention_action")))).get("at"))
            if maintenance is None:
                self.err(f"{label}: the case's maintenance action {requires.get('retention_action')!r} has no "
                         "timestamp to bracket (retention)")
            elif tb is None or ta is None or not (tb < maintenance < ta):
                self.err(f"{label}: grant-before must be observed strictly before and grant-after strictly after "
                         f"the maintenance action {requires.get('retention_action')} - a one-sided bracket does not "
                         "show retention across it (retention)")

    def check_disposition(self, label: str, aid: str, action: dict, captured: bytes | None,
                          disposition: dict) -> None:
        """Hold an engine-invoking action to the case's expected result.

        The capture must be exactly the engine's one stdout document (strict JSON,
        the InstallReport fields, nothing else) and the observed return the exact
        exit status that report maps to. ``clean`` is a completed, settled operation
        with no failure and no pending cleanup; ``refused`` is an expected-negative
        action that must end in an allowed failure with exit status 1.
        """
        where = f"{label}: action {aid}"
        clean = disposition.get("result") == "clean"
        returns = action.get("returns")
        want = "0" if clean else "1"
        if returns != want:
            self.err(f"{where}: observed return {returns!r} is not the exit status {want} a "
                     f"{'completed' if clean else 'refused'} operation produces")
        if captured is None:
            return
        doc = self.parse_report(captured, where)
        if doc is None:
            return
        operations = [disposition["operation"]] if clean else list(disposition.get("operations") or [])
        if doc["operation"] not in operations:
            self.err(f"{where}: report operation {doc['operation']!r} is not the expected {'/'.join(operations)}")
        pending = "cleanup-pending" in doc["warnings"] or doc["failure"] == "cleanup-pending"
        if clean:
            if doc["failure"] is not None or pending:
                self.err(f"{where}: report records failure {doc['failure']!r}"
                         f"{' and a pending cleanup' if pending else ''}; that is not a clean maintenance result")
            if doc["state"] != disposition.get("state"):
                self.err(f"{where}: report state {doc['state']!r} is not the settled {disposition.get('state')!r}")
        else:
            allowed = list(disposition.get("failures") or [])
            if doc["failure"] not in allowed:
                self.err(f"{where}: report failure {doc['failure']!r} is not an expected refusal "
                         f"({', '.join(allowed)}); the action must be refused, not completed")

    def parse_report(self, data: bytes, where: str) -> dict | None:
        if len(data) > MAX_REPORT_BYTES:
            self.err(f"{where}: report capture exceeds the {MAX_REPORT_BYTES}-byte engine bound")
            return None
        try:
            doc = _parse(data[:-1]) if data.endswith(b"\n") else None
        except (ValueError, UnicodeDecodeError):
            doc = None
        if doc is None and not data.endswith(b"\n"):
            self.err(f"{where}: capture is not the engine's one newline-terminated report document")
            return None
        if doc is None:
            self.err(f"{where}: capture is not one strict-JSON installer report (empty, prose or several documents)")
            return None
        if not isinstance(doc, dict) or set(doc) != REPORT_FIELDS:
            self.err(f"{where}: capture is not exactly one installer report document with the engine's fields")
            return None
        warnings = doc.get("warnings")
        if (doc.get("operation") not in REPORT_OPERATIONS or not isinstance(doc.get("state"), str)
                or not doc["state"] or not isinstance(doc.get("prior_usable"), bool)
                or not (doc.get("failure") is None or doc.get("failure") in REPORT_FAILURES)
                or not isinstance(warnings, list) or len(warnings) > len(REPORT_WARNINGS)
                or len(set(map(str, warnings))) != len(warnings) or any(w not in REPORT_WARNINGS for w in warnings)):
            self.err(f"{where}: report fields are outside the engine's vocabulary")
            return None
        return doc

    # adjudication -------------------------------------------------------------------
    def check_adjudication(self, doc: dict, entries: list[dict], head: str) -> None:
        path = self.run / "adjudication.json"
        if not path.is_file():
            self.err("adjudication.json is missing: a separate independent adjudication is required")
            return
        try:
            adj = _parse(path.read_bytes())
            run_digest = sha256_bytes((self.run / "run.json").read_bytes())
        except (OSError, ValueError, UnicodeDecodeError):
            self.err("adjudication.json is unreadable or not strict JSON")
            return
        adj = _obj(adj)
        who = _str(_obj(adj.get("adjudicator")).get("id")).strip()
        operator = _str(_obj(doc.get("operator")).get("id")).strip()
        tested = {_str(a.get("id")) for a in doc.get("accounts", []) if isinstance(a, dict)}
        if not who or not operator:
            self.err("adjudicator and operator ids are required")
        elif who == operator or who in tested:
            self.err("adjudicator is not independent of the operator or the tested accounts")
        if operator and operator not in _obj(adj.get("adjudicator")).get("independent_of", []):
            self.err("adjudication must declare independence from the operator (independent_of)")
        if adj.get("run_json_sha256") != run_digest:
            self.err("adjudication does not bind this run.json (it changed after adjudication)")
        if adj.get("ledger_head_sha256") != head:
            self.err("adjudication does not bind this ledger head (entries were added, removed or rewritten)")
        if _time(adj.get("adjudicated_at")) is None:
            self.err("adjudicated_at must be a UTC timestamp")
        verdicts = {_str(_obj(v).get("attempt_id")): _obj(v) for v in adj.get("verdicts", []) if isinstance(v, dict)}
        for entry in entries:
            verdict = verdicts.get(_str(entry.get("attempt_id")))
            if verdict is None:
                self.err(f"adjudication has no verdict for attempt {entry.get('attempt_id')}")
            elif verdict.get("outcome") != entry.get("outcome"):
                self.err(f"adjudicated verdict {verdict.get('outcome')!r} differs from the claimed outcome "
                         f"{entry.get('outcome')!r} for attempt {entry.get('attempt_id')}")

    def check_publication(self, doc: dict) -> None:
        pub = _obj(doc.get("publication"))
        state = pub.get("raw_captures")
        if state not in ("withheld", "published"):
            self.err("publication.raw_captures must be 'withheld' or 'published'")
        elif state == "published":
            review = _obj(pub.get("privacy_review"))
            operator = _str(_obj(doc.get("operator")).get("id"))
            if review.get("status") != "approved" or not _str(review.get("reviewer")).strip() \
                    or _time(review.get("reviewed_utc")) is None:
                self.err("publishing raw captures needs an approved, dated privacy review")
            elif review.get("reviewer") == operator:
                self.err("the privacy reviewer must not be the operator")


def validate(run: Path, cases: Path, anchor: Path, previous: Path | None = None) -> list[str]:
    """Return the list of structural problems (empty = structurally valid, NOT acceptance)."""
    run = Path(run)
    if not run.is_dir():
        raise MissingInput(f"evidence run directory not found: {run}")
    case_map = load_cases(Path(cases))
    anchor_doc = _load_input(Path(anchor), "anchor")
    if not isinstance(anchor_doc, dict) or anchor_doc.get("schema") != 1:
        raise MissingInput(f"anchor {anchor} is not a schema-1 anchor document")
    if not (run / "run.json").is_file():
        raise MissingInput(f"run.json not found in {run}")
    state = _Run(run, case_map, anchor_doc)
    resolved_anchor, resolved_run = Path(anchor).resolve(), run.resolve()
    if resolved_run == resolved_anchor or resolved_run in resolved_anchor.parents:
        state.err("anchor must be external: it is inside the evidence run, so the run would vouch for itself")
    try:
        doc = _parse((run / "run.json").read_bytes())
    except (ValueError, UnicodeDecodeError, OSError):
        return state.errors + ["run.json is not strict JSON"]
    if not isinstance(doc, dict) or doc.get("schema") != 1:
        return state.errors + ["run.json is not a schema-1 run record"]
    platform = doc.get("platform")
    if platform not in ("windows", "macos"):
        return state.errors + [f"run platform {platform!r} must be windows or macos"]
    if "anchor" in doc:
        state.err("run.json must not carry its own anchor")
    state.index_captures(doc)
    state.check_binding(doc, platform)
    window = state.check_context(doc)
    accounts = state.check_accounts(doc)
    lines, entries, head = state.read_ledger()
    if previous is not None:
        state.check_previous(Path(previous), lines)
    state.check_attempts(entries, platform, accounts, window)
    state.check_adjudication(doc, entries, head)
    state.check_publication(doc)
    return state.errors


# --------------------------------------------------------------------------- CLI


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="evidence.py", description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)
    val = sub.add_parser("validate", help="structurally validate one evidence bundle")
    val.add_argument("--run", type=Path, required=True)
    val.add_argument("--cases", type=Path, required=True)
    val.add_argument("--anchor", type=Path, required=True)
    val.add_argument("--previous", type=Path)
    mat = sub.add_parser("check-matrix", help="check matrix.json against cases.json")
    mat.add_argument("--cases", type=Path, required=True)
    mat.add_argument("--matrix", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "validate":
            errors = validate(args.run, args.cases, args.anchor, args.previous)
            ok_text = NOT_ACCEPTANCE
        else:
            errors = check_matrix(args.cases, args.matrix)
            ok_text = "matrix is consistent with cases; it records no physical PASS and is NOT acceptance"
    except MissingInput as error:
        print(f"missing input: {error}", file=sys.stderr)
        return 2
    if errors:
        for error in errors:
            print(f"INVALID: {error}")
        return 1
    print(ok_text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
