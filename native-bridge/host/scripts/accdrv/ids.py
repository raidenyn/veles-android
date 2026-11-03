"""Extension identity: the disposable acceptance extension's Chrome id/origin, derived from its manifest key.

The production host admits only the origins embedded at build time (host.rs: VELES_ALLOWED_ORIGINS,
compile-time only). The acceptance origin is therefore derived here from the extension's public key and
the controller rebuilds the SAME production source with that single origin recorded.
"""
import base64
import binascii
import hashlib
import json
import re
from pathlib import Path

from .errors import Blocked, Refused

ORIGIN_RE = re.compile(r"^chrome-extension://[a-p]{32}/$")
ID_RE = re.compile(r"^[a-p]{32}$")


def extension_id_from_key(key_b64):
    """Chrome extension id: first 16 bytes of SHA-256 over the DER SubjectPublicKeyInfo, nibbles mapped to a-p."""
    try:
        der = base64.b64decode(key_b64, validate=True)
    except (binascii.Error, ValueError, TypeError) as e:
        raise Refused(f"manifest key is not valid base64: {e}")
    if len(der) < 64 or der[0] != 0x30:
        raise Refused("manifest key is not a DER SubjectPublicKeyInfo")
    return "".join(chr(ord("a") + int(c, 16)) for c in hashlib.sha256(der).hexdigest()[:32])


def origin_for(ext_id):
    if not ID_RE.match(ext_id or ""):
        raise Refused(f"not a 32-character a-p extension id: {ext_id!r}")
    return f"chrome-extension://{ext_id}/"


def read_extension_identity(ext_dir):
    """(extension_id, origin) of the unpacked extension in ext_dir. The manifest must carry a key and the
    nativeMessaging permission and nothing broader (no host permissions, no externally_connectable)."""
    manifest = json.loads((Path(ext_dir) / "manifest.json").read_text())
    if not isinstance(manifest.get("key"), str):
        raise Refused("extension manifest has no key: the id would depend on the install path")
    perms = manifest.get("permissions")
    if perms != ["nativeMessaging"]:
        raise Refused(f"extension permissions must be exactly ['nativeMessaging'], got {perms!r}")
    for forbidden in ("host_permissions", "externally_connectable", "content_scripts", "web_accessible_resources",
                      "optional_permissions", "optional_host_permissions"):
        if forbidden in manifest:
            raise Refused(f"extension manifest must not declare {forbidden}")
    ext_id = extension_id_from_key(manifest["key"])
    return ext_id, origin_for(ext_id)


def binary_embeds_origin(exe, origin):
    """Best-effort preflight: the origin literal appears in the executable's bytes (build-time allowlist).

    NOT proof of the allowlist: the literal could appear without being the effective list. Chrome launching
    the host successfully (ready received) is the real evidence. Absence is a hard refusal, because the host
    would refuse the launch with exit 2 and the case could not mean anything.
    """
    data = Path(exe).read_bytes()
    needle = origin.encode()
    if not ORIGIN_RE.match(origin):
        raise Refused("origin is not one exact chrome-extension origin")
    if needle not in data:
        raise Blocked(f"{exe} does not embed {origin}; rebuild the production source with VELES_ALLOWED_ORIGINS={origin}")
    return True
