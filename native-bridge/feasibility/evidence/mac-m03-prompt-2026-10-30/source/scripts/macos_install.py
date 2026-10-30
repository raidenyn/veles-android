"""Register / unregister the packaged macOS OTP-23 PoC Native Messaging host (current user only).

Writes a user-level manifest under
    ~/Library/Application Support/Google/Chrome/NativeMessagingHosts/<name>.json
pointing at the packaged executable inside the VelesOtp23PoC.app bundle. Never machine-wide,
never an installer, never a TCC reset. `register` refuses to overwrite a manifest it does not
own and saves a backup beside the manifest when it replaces an owned one; `unregister` removes
(or restores) only that owned manifest and its backup and touches nothing else.

Used by scripts/macos-register.sh and scripts/macos-unregister.sh, and unit-tested on any
platform by scripts/test_macos_poc.py (pure pathlib, no macOS-only calls).
"""

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
IDENTITY = ROOT / "src" / "identity.rs"
MANIFEST_DIR_DEFAULT = Path.home() / "Library" / "Application Support" / "Google" / "Chrome" / "NativeMessagingHosts"
APP_DEFAULT = ROOT / "target" / "macos-poc" / "VelesOtp23PoC.app"
PACKAGED_BINARY = "Contents/MacOS/otp23-host"
BACKUP_SUFFIX = ".otp23-poc-backup"


def fail(message, code):
    print(f"error: {message}", file=sys.stderr)
    raise SystemExit(code)


def read_identity():
    """Constants from src/identity.rs: the single source of truth shared with the Rust host."""
    text = IDENTITY.read_text(encoding="utf-8")

    def constant(name):
        found = re.search(rf'pub const {name}: &str = "([^"]+)";', text)
        if not found:
            fail(f"constant {name} not found in {IDENTITY}", 1)
        return found.group(1)

    extension_id = constant("EXTENSION_ID")
    origin = constant("EXPECTED_ORIGIN")
    host_name = constant("HOST_NAME_PLAIN")
    if origin != f"chrome-extension://{extension_id}/":
        fail("EXPECTED_ORIGIN does not match EXTENSION_ID in identity.rs", 1)
    if not re.fullmatch(r"[a-p]{32}", extension_id) or not re.fullmatch(r"[a-z0-9.]{1,64}", host_name):
        fail("identity constants have an unexpected shape", 1)
    return {"extension_id": extension_id, "origin": origin, "host_name": host_name}


def build_manifest(host_name, binary, origin):
    return {
        "name": host_name,
        "description": "Veles OTP-23 macOS PoC host; synthetic bytes only",
        "path": str(binary),
        "type": "stdio",
        "allowed_origins": [origin],
    }


def is_owned(document, host_name):
    """Owned iff the manifest names this harness and points inside the VelesOtp23PoC.app bundle."""
    if not isinstance(document, dict) or document.get("name") != host_name:
        return False
    path = document.get("path")
    if not isinstance(path, str) or not path:
        return False
    resolved = Path(path).resolve()
    return (
        resolved.name == "otp23-host"
        and resolved.parent.name == "MacOS"
        and resolved.parent.parent.name == "Contents"
        and resolved.parent.parent.parent.name == "VelesOtp23PoC.app"
    )


def register(app, manifest_dir):
    identity = read_identity()
    binary = Path(app).resolve() / PACKAGED_BINARY
    if not binary.is_file():
        fail(
            f"packaged host not found at {binary}; run\n"
            "  scripts/macos-package.sh\n"
            "first (it packages target/release/otp23-host and code-signs the bundle)", 1,
        )
    target = manifest_dir / f"{identity['host_name']}.json"
    document = build_manifest(identity["host_name"], binary, identity["origin"])
    payload = (json.dumps(document, indent=2) + "\n").encode("utf-8")
    manifest_dir.mkdir(parents=True, exist_ok=True)
    backup = target.with_name(target.name + BACKUP_SUFFIX)
    replaced = None
    if target.exists():
        previous = target.read_bytes()
        if is_owned(json.loads(previous.decode("utf-8")), identity["host_name"]):
            backup.write_bytes(previous)
            replaced = "backed up previous owned manifest"
        else:
            fail(
                f"{target} already exists and is not owned by this PoC; refusing to overwrite it\n"
                "(no file was modified). Remove or re-point that registration yourself.", 2,
            )
    target.write_bytes(payload)
    target.chmod(0o644)
    print(json.dumps({
        "registered": True,
        "host_name": identity["host_name"],
        "manifest": str(target),
        "executable": str(binary),
        "executable_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
        "origin": identity["origin"],
        "scope": f"user only ({manifest_dir})",
        "backup": str(backup) if backup.exists() else None,
        "note": replaced or "created new manifest",
    }, indent=2))


def unregister(manifest_dir):
    identity = read_identity()
    target = manifest_dir / f"{identity['host_name']}.json"
    backup = target.with_name(target.name + BACKUP_SUFFIX)
    if not target.exists():
        print(json.dumps({"unregistered": True, "manifest": str(target), "note": "absent; nothing to do"}))
        return
    if not is_owned(json.loads(target.read_bytes().decode("utf-8")), identity["host_name"]):
        fail(
            f"{target} is not owned by this PoC; refusing to remove an unrelated registration", 2,
        )
    if backup.exists():
        previous = backup.read_bytes()
        target.write_bytes(previous)
        backup.unlink()
        print(json.dumps({
            "unregistered": False, "restored_backup": str(target),
            "note": "restored the manifest backed up before this PoC replaced it; delete it "
                    "yourself if unwanted",
        }))
        return
    target.unlink()
    print(json.dumps({
        "unregistered": True, "manifest": str(target),
        "note": "removed only this owned manifest; the PoC app bundle under target/macos-poc "
                "and any Bluetooth (TCC) grants were not touched",
    }))


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("register", "unregister"):
        sub = commands.add_parser(name)
        sub.add_argument("--manifest-dir", type=Path, default=MANIFEST_DIR_DEFAULT,
                         help="Chrome user-level NativeMessagingHosts directory")
    commands.choices["register"].add_argument("--app", type=Path, default=APP_DEFAULT,
                                              help="path to VelesOtp23PoC.app")
    args = parser.parse_args()
    if args.command == "register":
        register(args.app, args.manifest_dir)
    else:
        unregister(args.manifest_dir)


if __name__ == "__main__":
    main()