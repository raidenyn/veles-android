"""Evidence recorder. It records what the driver measured; it can never record PASS.

declared_status is only ever BLOCKED (something required was not genuinely measured, or the run is complete and
awaits the separate adjudication/validator) or FAIL (a measured product-side expectation was violated).
"""
import hashlib
import json
import os
import re
from datetime import datetime, timezone
from pathlib import Path

from .errors import Blocked, Refused

SCHEMA = "veles-otp24-physical-evidence/1"
CATALOG_EXCLUDED = {"run.json", "SHA256SUMS", "adjudication.json"}
NAME_RE = re.compile(r"^[A-Za-z0-9._-]{1,80}$")


class ProductFailure(Exception):
    """A measured expectation about the production host was violated (not an infrastructure problem)."""


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%fZ")


class Recorder:
    def __init__(self, out_dir, case, clock=utc_now):
        self.out = Path(out_dir)
        self.out.mkdir(parents=True, exist_ok=False)
        (self.out / "captures").mkdir()
        self.case, self.clock = case, clock
        self.fields = {}
        self.actions, self.captures, self.blockers, self.failures = [], [], [], []
        self.failed_setups = []
        self.observations, self.controls, self.callbacks = [], [], []
        self.driver_errors = []

    # captures -----------------------------------------------------------------------------------------------------
    def capture(self, name, kind, data):
        if not NAME_RE.match(name) or name in {c["name"] for c in self.captures}:
            raise ValueError(f"bad or duplicate capture name {name!r}")
        if isinstance(data, (dict, list)):
            data = (json.dumps(data, indent=2, sort_keys=True) + "\n").encode()
        elif isinstance(data, str):
            data = data.encode()
        rel = f"captures/{name}"
        fd = os.open(self.out / rel, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            os.write(fd, data)
        finally:
            os.close(fd)
        self.captures.append({"name": name, "kind": kind, "path": rel, "sha256": hashlib.sha256(data).hexdigest(), "size": len(data)})
        return name

    def copy_in(self, name, kind, src):
        return self.capture(name, kind, Path(src).read_bytes())

    # actions ------------------------------------------------------------------------------------------------------
    def run_action(self, name, fn):
        """Time and record one action. Blocked/ProductFailure are recorded then re-raised; nothing is swallowed."""
        entry = {"seq": len(self.actions), "name": name, "started_at": self.clock(), "ended_at": None, "result": "pending"}
        self.actions.append(entry)
        try:
            value = fn()
        except (Blocked, Refused) as e:
            entry.update(ended_at=self.clock(), result="blocked", detail=str(e)[:300])
            self.blockers.append(f"{name}: {e}")
            raise
        except ProductFailure as e:
            entry.update(ended_at=self.clock(), result="failed", detail=str(e)[:300])
            self.failures.append(f"{name}: {e}")
            raise
        except BaseException as e:  # a driver defect is an infrastructure problem, not a product verdict
            entry.update(ended_at=self.clock(), result="error", detail=type(e).__name__)
            self.driver_error(name, e)
            raise
        entry.update(ended_at=self.clock(), result="ok")
        return value

    def driver_error(self, stage, exc):
        """An unexpected exception is a driver/infrastructure problem: BLOCKED (never FAIL), flagged for the CLI."""
        self.driver_errors.append({"stage": stage, "type": type(exc).__name__})
        self.blockers.append(f"{stage}: driver error {type(exc).__name__}")

    def block(self, reason):
        self.blockers.append(reason)

    def observation(self, name, result, evidence_ref):
        self.observations.append({"name": name, "result": result, "evidence_ref": evidence_ref})

    def control(self, name, result, evidence_ref):
        self.controls.append({"name": name, "result": result, "evidence_ref": evidence_ref})

    # finalize -----------------------------------------------------------------------------------------------------
    def finalize(self):
        required_obs = set(self.case.get("observations", []))
        missing = sorted(required_obs - {o["name"] for o in self.observations})
        missing_c = sorted(set(self.case.get("required_controls", [])) - {c["name"] for c in self.controls})
        done = [a["name"] for a in self.actions if a["result"] == "ok"]
        missing_a = [a for a in self.case["required_actions"] if a not in done]
        blockers = list(self.blockers)
        if missing_a:
            blockers.append(f"required actions not completed: {missing_a}")
        if missing:
            blockers.append(f"observations not measured: {missing}")
        if missing_c:
            blockers.append(f"controls not measured: {missing_c}")
        if self.failures:
            status, blocker = "FAIL", None
        else:
            status = "BLOCKED"
            blocker = " | ".join(blockers) if blockers else "driver run complete; awaiting separate adjudication and validate-evidence.py (this driver never declares PASS)"
        lines = [f"{c['sha256']}  {c['path']}" for c in sorted(self.captures, key=lambda c: c["path"])]
        extra = sorted(p for p in self._files() if p not in {c["path"] for c in self.captures} and p not in CATALOG_EXCLUDED)
        for rel in extra:
            lines.append(f"{hashlib.sha256((self.out / rel).read_bytes()).hexdigest()}  {rel}")
        catalog = ("\n".join(sorted(lines, key=lambda l: l.split("  ", 1)[1])) + "\n") if lines else ""
        (self.out / "SHA256SUMS").write_text(catalog)
        doc = {"schema": SCHEMA, "case_id": self.case["id"], "declared_status": status, "recorded_at": self.clock(),
               **self.fields, "actions": self.actions, "captures": self.captures, "observations": self.observations,
               "controls": self.controls, "callbacks": self.callbacks, "failed_setups": self.failed_setups,
               "checksum_catalog": {"path": "SHA256SUMS", "sha256": hashlib.sha256(catalog.encode()).hexdigest()}}
        if blocker:
            doc["blocker"] = blocker
        if self.failures:
            doc["failures"] = self.failures
        if self.driver_errors:
            doc["driver_errors"] = self.driver_errors
        (self.out / "run.json").write_text(json.dumps(doc, indent=2) + "\n")
        return doc

    def _files(self):
        for root, _, files in os.walk(self.out):
            for f in files:
                yield os.path.relpath(os.path.join(root, f), self.out).replace(os.sep, "/")
