"""Checks that the fixed extension key, the derived Chrome id and the Rust constants agree.

Run:  python -I native-bridge/feasibility/scripts/verify-identity.py
Exit code 0 on success; prints the values it compared.
"""

import base64
import hashlib
import json
import pathlib
import re
import sys

root = pathlib.Path(__file__).resolve().parent.parent

manifest = json.loads((root / "extension" / "manifest.json").read_text(encoding="utf-8"))
der = base64.b64decode(manifest["key"], validate=True)
digest = hashlib.sha256(der).hexdigest()[:32]
derived = "".join(chr(ord("a") + int(c, 16)) for c in digest)

source = (root / "src" / "identity.rs").read_text(encoding="utf-8")
extension_id = re.search(r'pub const EXTENSION_ID: &str = "([a-p]{32})";', source).group(1)
origin = re.search(r'pub const EXPECTED_ORIGIN: &str = "([^"]+)";', source).group(1)

problems = []
if derived != extension_id:
    problems.append(f"derived id {derived} != identity.rs EXTENSION_ID {extension_id}")
if origin != f"chrome-extension://{extension_id}/":
    problems.append(f"EXPECTED_ORIGIN {origin} is not the exact origin of {extension_id}")
if "*" in origin:
    problems.append("wildcard in origin")
if manifest.get("permissions") != ["nativeMessaging"]:
    problems.append(f"unexpected permissions {manifest.get('permissions')}")
if "externally_connectable" in manifest or "host_permissions" in manifest:
    problems.append("manifest grants more than the harness needs")

print(f"key sha256-derived id : {derived}")
print(f"identity.rs id        : {extension_id}")
print(f"expected origin       : {origin}")
print(f"permissions           : {manifest.get('permissions')}")
if problems:
    print("FAIL:", *problems, sep="\n  ")
    sys.exit(1)
print("OK: key, derived id and Rust constants agree")
