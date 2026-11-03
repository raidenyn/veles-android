"""macOS approved-identity check for the signed TEST VEHICLE bundle (read-only; this module never signs).

Constants and rules are copied from native-bridge/host/scripts/macos-test-bundle.sh (which copied them from real
recorded `codesign -dvv` output). The certificate hash is read from the actual certificate via
`codesign --extract-certificates`; nothing is inferred. A production identity is explicitly NOT claimed.
"""
import hashlib
import re
from pathlib import Path

from .errors import Blocked, Refused

APPROVED_BUNDLE_ID = "me.nagaev.veles.otp23.poc"
APPROVED_TEAM_ID = "7T39M26Z99"
APPROVED_AUTHORITY = "Apple Development: yuriy@nagaev.me (ZWYYS8X98S)"
APP_NAME = "VelesOtp23PoC.app"
EXEC_NAME = "veles-native-host"


def check_signature_authority(text):
    """Problems (empty = the exact approved identity). Mirrors check_signature_authority in the shell script."""
    lines = text.splitlines()
    problems = []
    if any("Signature=adhoc" in l for l in lines):
        problems.append("signature is ad-hoc")
    if f"Identifier={APPROVED_BUNDLE_ID}" not in lines:
        problems.append(f"signed identifier is not the approved {APPROVED_BUNDLE_ID}")
    if f"TeamIdentifier={APPROVED_TEAM_ID}" not in lines:
        problems.append("TeamIdentifier is not the approved team")
    authorities = [l[len("Authority="):] for l in lines if l.startswith("Authority=")]
    if not authorities or authorities[0] != APPROVED_AUTHORITY:
        problems.append("leaf authority is not the approved certificate")
    if not any(a.startswith("Apple Root CA") for a in authorities):
        problems.append("no Apple root authority in the certificate chain")
    return problems


def bundle_executable_ok(exe):
    """The executable must be <...>/VelesOtp23PoC.app/Contents/MacOS/veles-native-host (the approved bundle)."""
    p = Path(exe)
    parts = p.parts
    if len(parts) < 4 or parts[-1] != EXEC_NAME or parts[-2] != "MacOS" or parts[-3] != "Contents" or parts[-4] != APP_NAME:
        raise Refused(f"{exe} is not inside the approved test-vehicle bundle {APP_NAME}/Contents/MacOS/{EXEC_NAME}")


def read_signing(exe, run, cert_dir):
    """Signing block for run.json, read from real codesign output via `run(argv) -> (rc, stdout, stderr)`.

    BLOCKED (never a fallback) when codesign cannot read the identity; Refused when it reads a different one.
    """
    bundle_executable_ok(exe)
    rc, out, err = run(["codesign", "-dvv", str(exe)])
    text = (out or "") + (err or "")  # codesign prints its description on stderr
    if rc != 0:
        raise Blocked(f"codesign -dvv failed (rc {rc}); the signed identity cannot be read")
    problems = check_signature_authority(text)
    if problems:
        raise Refused("; ".join(problems))
    rc, out, err = run(["codesign", "--verify", "--strict", "-R=anchor apple generic", str(exe)])
    if rc != 0:
        raise Refused("signature does not satisfy 'anchor apple generic'")
    cert_dir = Path(cert_dir)
    cert_dir.mkdir(parents=True, exist_ok=False)
    rc, out, err = run(["codesign", "-d", f"--extract-certificates={cert_dir / 'cert'}", str(exe)])
    leaf = cert_dir / "cert0"
    if rc != 0 or not leaf.is_file():
        raise Blocked("the leaf certificate could not be extracted; its hash cannot be recorded")
    return {"identity": APPROVED_AUTHORITY, "team_id": APPROVED_TEAM_ID, "bundle_id": APPROVED_BUNDLE_ID,
            "cert_sha256": hashlib.sha256(leaf.read_bytes()).hexdigest(), "required": True,
            "bundle_kind": "test_vehicle", "production_identity_claimed": False}


def windows_authenticode(exe, run):
    """Authenticode status of the exact executable via PowerShell; the statement is the tool's own output."""
    if "'" in str(exe) or "\n" in str(exe):
        raise Refused("executable path contains a character that cannot be passed safely to PowerShell")
    rc, out, err = run(["powershell", "-NoProfile", "-NonInteractive", "-Command",
                        f"(Get-AuthenticodeSignature -LiteralPath '{exe}').Status.ToString()"])
    status = (out or "").strip()
    if rc != 0 or not re.match(r"^[A-Za-z]+$", status):
        raise Blocked("Get-AuthenticodeSignature did not produce a status")
    return f"Get-AuthenticodeSignature status: {status}"
