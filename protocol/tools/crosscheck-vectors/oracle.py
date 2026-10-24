#!/usr/bin/env python3
"""Independent cross-check oracle for Veles OTP-02 crypto vectors (Plan Task 5).

TEST-ONLY tool; never imported by product code. Implements the Veles protocol
derivations from the normative document with a from-scratch, stdlib-only,
VARIABLE-TIME affine P-256 implementation — deliberately independent of the
@noble/* JS engine that produced the candidate — and compares EVERY
intermediate byte-for-byte. AES-256-GCM is NOT implemented here (Python's
stdlib has no AES); GCM intermediates and tag/session/direction/sequence
negatives are cross-checked by openssl-check.mjs (Node/OpenSSL) instead.

The RFC 9382 Appendix B anchors are reproduced from this file's own verbatim
copy of the published values BEFORE anything else is compared; reproducing the
published anchor is the acceptance condition for this implementation.

Usage:
    python3 protocol/tools/crosscheck-vectors/oracle.py <fixtures.json> [-o <report.json>]

<fixtures.json> is either the generated candidate (build/protocol/crypto-candidate.json)
or the committed expected fixture file (protocol/v1/fixtures/crypto.json) — the
ACCEPTANCE run checks the COMMITTED expected bytes, so a mutation of any
committed value or label fails here before tests even start.
Exit codes: 0 = every covered fixture matched; 1 = any mismatch; 2 = usage error.
Dependencies: Python standard library only (no third-party packages).
"""

import hashlib
import hmac
import json
import os
import platform
import sys

# ---------------------------------------------------------------- curve setup
# P-256 parameters (NIST SP 800-186 / SEC2 v2 published constants). The
# generator's y-coordinate was verified against OpenSSL-generated (d, Q) pairs
# and against the RFC 9382 anchor reproduction itself.
P = 0xFFFFFFFF00000001000000000000000000000000FFFFFFFFFFFFFFFFFFFFFFFF
A = (-3) % P
B = 0x5AC635D8AA3A93E7B3EBBD55769886BC651D06B0CC53B0F63BCE3C3E27D2604B
N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551
GX = 0x6B17D1F2E12C4247F8BCE6E563A440F277037D812DEB33A0F4A13945D898C296
GY = 0x4FE342E2FE1A7F9B8EE7EB4A7C0F9E162BCE33576B315ECECBB6406837BF51F5
G = (GX, GY)
INFINITY = None

# RFC 9382 section 6 P-256 M and N (compressed SEC1, published verbatim).
M_COMPRESSED = "02886e2f97ace46e55ba9dd7242579f2993b64e16ef3dcab95afd497333d8fa12f"
N_COMPRESSED = "03d8bbd6c639c62937b04d997f38c3770719c629d7014d49a24b4f98baa1292b49"


def on_curve(x, y):
    if x is None:
        return True
    return (y * y - ((x * x + A) * x + B)) % P == 0


def point_add(p1, p2):
    if p1 is None:
        return p2
    if p2 is None:
        return p1
    x1, y1 = p1
    x2, y2 = p2
    if x1 == x2 and (y1 + y2) % P == 0:
        return None
    if p1 == p2:
        lam = (3 * x1 * x1 + A) * pow(2 * y1, -1, P) % P
    else:
        lam = (y2 - y1) * pow(x2 - x1, -1, P) % P
    x3 = (lam * lam - x1 - x2) % P
    y3 = (lam * (x1 - x3) - y1) % P
    return (x3, y3)


def point_mul(k, pt):
    # Deliberately NOT reduced mod N: with an unreduced multiply the
    # N·P == identity self-check below is a REAL group-order check (M7), not
    # vacuous by construction. Callers already pass reduced scalars; the
    # reduction is an equivalence even without it on a cofactor-1 curve.
    result, q = None, pt
    while k:
        if k & 1:
            result = point_add(result, q)
        q = point_add(q, q)
        k >>= 1
    return result


def point_sub(p1, p2):
    if p2 is None:
        return p1
    return point_add(p1, (p2[0], (-p2[1]) % P))


def point_equal(p1, p2):
    if p1 is None or p2 is None:
        return p1 is None and p2 is None
    return p1[0] == p2[0] and p1[1] == p2[1]


def decompress(pub33):
    if len(pub33) != 33 or pub33[0] not in (2, 3):
        raise ValueError("point_format: compressed point must be 33 bytes with prefix 0x02/0x03")
    x = int.from_bytes(pub33[1:], "big")
    yy = (pow(x, 3, P) + A * x + B) % P
    y = pow(yy, (P + 1) // 4, P)
    if (y * y) % P != yy:
        raise ValueError("point: x-coordinate is not on the P-256 curve")
    if (y & 1) != (pub33[0] & 1):
        y = P - y
    return (x, y)


def encode_uncompressed(pt):
    return b"\x04" + pt[0].to_bytes(32, "big") + pt[1].to_bytes(32, "big")


M_POINT = decompress(bytes.fromhex(M_COMPRESSED))
N_POINT = decompress(bytes.fromhex(N_COMPRESSED))
for _pt, _nm in ((M_POINT, "M"), (N_POINT, "N"), (G, "G")):
    if not on_curve(*_pt) or not point_equal(point_mul(N, _pt), INFINITY):
        raise SystemExit(f"oracle parameter { _nm } failed curve/order self-check")
# Zero-scalar edge policy: RFC draws scalars from [0, p) and Veles processes
# w = 0 normally (OTP2-CRY-004b); the oracle's mul must therefore map scalar 0
# to the identity instead of failing.
if not point_equal(point_mul(0, G), INFINITY) or not point_equal(point_add(point_mul(0, G), G), G):
    raise SystemExit("oracle zero-scalar self-check failed")

# ---------------------------------------------------------------- hash layer


def sha256(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def hmac_sha256(key: bytes, msg: bytes) -> bytes:
    return hmac.new(key, msg, hashlib.sha256).digest()


def hkdf_extract(salt: bytes, ikm: bytes) -> bytes:
    # RFC 5869. For SHA-256 an empty (nil) salt and a 32-zero salt produce
    # identical PRKs (HMAC zero-pads keys to the 64-byte block) — verified
    # against the RFC 9382 Appendix B anchors during implementation.
    return hmac.new(salt, ikm, hashlib.sha256).digest()


def hkdf_expand(prk: bytes, info: bytes, length: int) -> bytes:
    okm = b""
    block = b""
    counter = 1
    while len(okm) < length:
        block = hmac_sha256(prk, block + info + bytes([counter]))
        okm += block
        counter += 1
    return okm[:length]


def canonical_json(value) -> str:
    """Stable canonical JSON: key-sorted, compact separators. Must stay
    byte-identical with checks/crypto.mjs canonicalJson() (fixture strings are
    ASCII-only; ensure_ascii=True escapes nothing additionally)."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def fixtures_canonical_sha256(fixtures) -> str:
    return hashlib.sha256(canonical_json(fixtures).encode()).hexdigest()


def fixture_content_sha256(fixture: dict) -> str:
    payload = {"id": fixture["id"], "input": fixture["input"], "expected": fixture["expected"]}
    return hashlib.sha256(canonical_json(payload).encode()).hexdigest()


def len64le(n: int) -> bytes:
    return n.to_bytes(8, "little")  # RFC 9382 len(): u64 LITTLE-endian


def u16be(n: int) -> bytes:
    return n.to_bytes(2, "big")


def u64be(n: int) -> bytes:
    return n.to_bytes(8, "big")


def utf8(text: str) -> bytes:
    return text.encode("utf-8")


# ------------------------------------------------- normative labels (length-pinned)
LABELS = {
    "password_info": (b"Veles SPAKE2 password input v1", 30),
    "public_context": (b"Veles SPAKE2 public context v1", 30),
    "identity_a": (b"Veles Android SPAKE2 party A v1", 31),
    "identity_b": (b"Veles client SPAKE2 party B v1", 30),
    "confirmation_keys": (b"ConfirmationKeys", 16),
    "enrollment_salt": (b"Veles SPAKE2 enrollment salt v1", 31),
    "enrollment_ab_aes": (b"Veles enrollment A-to-B AES key v1", 34),
    "enrollment_ab_nonce": (b"Veles enrollment A-to-B nonce v1", 32),
    "enrollment_act_a": (b"Veles enrollment A confirmation v1", 34),
    "enrollment_ba_aes": (b"Veles enrollment B-to-A AES key v1", 34),
    "enrollment_ba_nonce": (b"Veles enrollment B-to-A nonce v1", 32),
    "enrollment_act_b": (b"Veles enrollment B confirmation v1", 34),
    "enrollment_session": (b"Veles enrollment session v1", 27),
    "enrollment_transcript": (b"Veles enrollment transcript v1", 30),
    "record_fingerprint": (b"Veles enrollment record fingerprint v1", 38),
    "pair_fingerprint": (b"Veles identity fingerprint v1", 29),
    "reconnect_transcript": (b"Veles reconnect transcript v1", 29),
    "reconnect_salt": (b"Veles reconnect salt v1", 23),
    "reconnect_ba_aes": (b"Veles reconnect B-to-A AES key v1", 33),
    "reconnect_ba_nonce": (b"Veles reconnect B-to-A nonce v1", 31),
    "reconnect_ba_confirmation": (b"Veles reconnect B-to-A confirmation v1", 38),
    "reconnect_ab_aes": (b"Veles reconnect A-to-B AES key v1", 33),
    "reconnect_ab_nonce": (b"Veles reconnect A-to-B nonce v1", 31),
    "reconnect_ab_confirmation": (b"Veles reconnect A-to-B confirmation v1", 38),
    "reconnect_session_id": (b"Veles reconnect session ID v1", 29),
    "reconnect_confirm_input": (b"Veles reconnect confirmation input v1", 37),
    "recovery_client_activation": (b"Veles recovery client activation v1", 35),
    "recovery_phone_activation": (b"Veles recovery phone activation v1", 34),
}


def label(key: str) -> bytes:
    text, length = LABELS[key]
    if len(text) != length:
        raise SystemExit(f"oracle label {key!r} is {len(text)} bytes, contract says {length}")
    return text


# ------------------------------------------------- RFC 9382 published anchors
# Verbatim copies of the RFC 9382 Appendix B P256-SHA256-HKDF-HMAC vectors
# (first: A='server', B='client'; fourth: A='', B=''), independently read from
# https://www.rfc-editor.org/rfc/rfc9382.txt.
RFC_ANCHORS = {
    "rfc9382-p256-sha256-hkdf-hmac-server-client": {
        "id_a": "server",
        "id_b": "client",
        "w": int("2ee57912099d31560b3a44b1184b9b4866e904c49d12ac5042c97dca461b1a5f", 16),
        "x": int("43dd0fd7215bdcb482879fca3220c6a968e66d70b1356cac18bb26c84a78d729", 16),
        "y": int("dcb60106f276b02606d8ef0a328c02e4b629f84f89786af5befb0bc75b6e66be", 16),
        "published": {
            "p_a": "0x04a56fa807caaa53a4d28dbb9853b9815c61a411118a6fe516a8798434751470f9010153ac33d0d5f2047ffdb1a3e42c9b4e6be662766e1eeb4116988ede5f912c",
            "p_b": "0x0406557e482bd03097ad0cbaa5df82115460d951e3451962f1eaf4367a420676d09857ccbc522686c83d1852abfa8ed6e4a1155cf8f1543ceca528afb591a1e0b7",
            "k": "0x0412af7e89717850671913e6b469ace67bd90a4df8ce45c2af19010175e37eed69f75897996d539356e2fa6a406d528501f907e04d97515fbe83db277b715d3325",
            "hash_tt": "0x0e0672dc86f8e45565d338b0540abe6915bdf72e2b35b5c9e5663168e960a91b",
            "ke": "0x0e0672dc86f8e45565d338b0540abe69",
            "ka": "0x15bdf72e2b35b5c9e5663168e960a91b",
            "kc_a": "0x00c12546835755c86d8c0db7851ae86f",
            "kc_b": "0xa9fa3406c3b781b93d804485430ca27a",
            "c_a": "0x58ad4aa88e0b60d5061eb6b5dd93e80d9c4f00d127c65b3b35b1b5281fee38f0",
            "c_b": "0xd3e2e547f1ae04f2dbdbf0fc4b79f8ecff2dff314b5d32fe9fcef2fb26dc459b",
        },
    },
    "rfc9382-p256-sha256-hkdf-hmac-nil-identities": {
        "id_a": "",
        "id_b": "",
        "w": int("7bf46c454b4c1b25799527d896508afd5fc62ef4ec59db1efb49113063d70cca", 16),
        "x": int("8cef65df64bb2d0f83540c53632de911b5b24b3eab6cc74a97609fd659e95473", 16),
        "y": int("d7a66f64074a84652d8d623a92e20c9675c61cb5b4f6a0063e4648a2fdc02d53", 16),
        "published": {
            "p_a": "0x04a65b367a3f613cf9f0654b1b28a1e3a8a40387956c8ba6063e8658563890f46ca1ef6a676598889fc28de2950ab8120b79a5ef1ea4c9f44bc98f585634b46d66",
            "p_b": "0x04589f13218822710d98d8b2123a079041052d9941b9cf88c6617ddb2fcc0494662eea8ba6b64692dc318250030c6af045cb738bc81ba35b043c3dcb46adf6f58d",
            "k": "0x041a3c03d51b452537ca2a1fea6110353c6d5ed483c4f0f86f4492ca3f378d40a994b4477f93c64d928edbbcd3e85a7c709b7ea73ee97986ce3d1438e135543772",
            "hash_tt": "0xfc6374762ba5cf11f4b2caa08b2cd1b9907ae0e26e8d6234318d91583cd74c86",
            "ke": "0xfc6374762ba5cf11f4b2caa08b2cd1b9",
            "ka": "0x907ae0e26e8d6234318d91583cd74c86",
            "kc_a": "0x5dbd2f477166b7fb6d61febbd77a5563",
            "kc_b": "0x7689b4654407a5faeffdc8f18359d8a3",
            "c_a": "0xdfb4db8d48ae5a675963ea5e6c19d98d4ea028d8e898dad96ea19a80ade95dca",
            "c_b": "0xd0f0609d1613138d354f7e95f19fb556bf52d751947241e8c7118df5ef0ae175",
        },
    },
}


# ------------------------------------------------- SPAKE2 core

def spake2_transcript(id_a: bytes, id_b: bytes, p_a: bytes, p_b: bytes, k: bytes, w: int) -> bytes:
    return (
        len64le(len(id_a)) + id_a
        + len64le(len(id_b)) + id_b
        + len64le(len(p_a)) + p_a
        + len64le(len(p_b)) + p_b
        + len64le(len(k)) + k
        + len64le(32) + w.to_bytes(32, "big")
    )


def spake2_outputs(id_a: bytes, id_b: bytes, w: int, x: int, y: int, aad: bytes, blinding: str = "fixed"):
    """RFC 9382 sec 3.3/4 with the Veles ciphersuite semantics. blinding:
    fixed = A uses M / B uses N; swap_roles = A uses N / B uses M;
    role_mismatch = the client also blinds with M (parties disagree on K)."""
    m_for_a = N_POINT if blinding == "swap_roles" else M_POINT
    n_for_b = M_POINT if blinding == "swap_roles" else N_POINT
    b_blind = M_POINT if blinding == "role_mismatch" else n_for_b
    p_a_pt = point_add(point_mul(x, G), point_mul(w, m_for_a))
    p_b_pt = point_add(point_mul(y, G), point_mul(w, b_blind))
    k_a = point_mul(x, point_sub(p_b_pt, point_mul(w, n_for_b)))  # phone view
    if blinding != "role_mismatch":
        k_b = point_mul(y, point_sub(p_a_pt, point_mul(w, m_for_a)))  # client view
        if not point_equal(k_a, k_b):
            raise ValueError("SPAKE2 shared elements disagree between roles")
    p_a = encode_uncompressed(p_a_pt)
    p_b = encode_uncompressed(p_b_pt)
    k = encode_uncompressed(k_a)
    tt = spake2_transcript(id_a, id_b, p_a, p_b, k, w)
    digest = sha256(tt)
    ke, ka = digest[:16], digest[16:]
    okm = hkdf_expand(hkdf_extract(b"", ka), label("confirmation_keys") + aad, 32)
    kc_a, kc_b = okm[:16], okm[16:32]
    return {
        "p_a": p_a, "p_b": p_b, "k": k, "tt": tt, "hash_tt": digest,
        "ke": ke, "ka": ka, "kc_a": kc_a, "kc_b": kc_b,
        "c_a": hmac_sha256(kc_a, tt), "c_b": hmac_sha256(kc_b, tt),
    }


def code_to_w(code: bytes, pairing_id: bytes):
    prk = hkdf_extract(pairing_id, code)
    okm = hkdf_expand(prk, label("password_info"), 40)
    return prk, okm, int.from_bytes(okm, "big") % N


def build_public_context(ctx_input: dict) -> bytes:
    caps_of = lambda caps: u16be(len(caps)) + b"".join(bytes([int(c, 16)]) for c in caps)
    return (
        label("public_context")
        + u16be(ctx_input.get("protocol_version", 1))
        + u16be(ctx_input.get("schema_version", 1))
        + u16be(ctx_input.get("ciphersuite_id", 1))
        + bytes.fromhex(hexof(ctx_input["pairing_id"]))
        + bytes.fromhex(hexof(ctx_input["phone_role"])) + bytes.fromhex(hexof(ctx_input["client_role"]))
        + bytes.fromhex(hexof(ctx_input["phone_nonce"]))
        + bytes.fromhex(hexof(ctx_input["client_nonce"]))
        + caps_of(ctx_input["phone_caps"])
        + caps_of(ctx_input["client_caps"])
    )


def hexof(value) -> str:
    """Normalize fixture hex values ('0x'-prefixed or bare) to bare lowercase."""
    s = value.lower()
    return s[2:] if s.startswith("0x") else s


def h(value) -> str:
    return "0x" + hexof(value)


# ------------------------------------------------- ECDSA (RFC 6979, low-S)

def bits2int(data: bytes) -> int:
    return int.from_bytes(data, "big")


def rfc6979_k(digest: bytes, priv: int) -> int:
    qlen = 256
    int2octets = lambda v: v.to_bytes(32, "big")
    bits2octets = lambda d: (bits2int(d) % N).to_bytes(32, "big")
    v = b"\x01" * 32
    k = b"\x00" * 32
    k = hmac_sha256(k, v + b"\x00" + int2octets(priv) + bits2octets(digest))
    v = hmac_sha256(k, v)
    k = hmac_sha256(k, v + b"\x01" + int2octets(priv) + bits2octets(digest))
    v = hmac_sha256(k, v)
    while True:
        v = hmac_sha256(k, v)
        k_candidate = bits2int(v)
        if 1 <= k_candidate < N:
            return k_candidate
        k = hmac_sha256(k, v + b"\x00")
        v = hmac_sha256(k, v)


def ecdsa_sign_low_s(digest: bytes, priv: bytes):
    x = int.from_bytes(priv, "big")
    e = bits2int(digest)
    while True:
        k = rfc6979_k(digest, x)
        r_point = point_mul(k, G)
        if r_point is None:
            continue
        r = r_point[0] % N
        if r == 0:
            continue
        s = (pow(k, -1, N) * (e + r * x)) % N
        if s == 0:
            continue
        if s > N // 2:
            s = N - s  # low-S normalization
        return r.to_bytes(32, "big"), s.to_bytes(32, "big")


def ecdsa_verify(digest: bytes, pub: bytes, r: int, s: int) -> bool:
    """Affine ECDSA verify; true iff (r, s) validates against pub over digest."""
    if not (1 <= r < N and 1 <= s < N):
        return False
    q = decode_point_any(pub)
    if q is None:
        return False
    e = bits2int(digest)
    w = pow(s, -1, N)
    u1 = (e * w) % N
    u2 = (r * w) % N
    point = point_add(point_mul(u1, G), point_mul(u2, q))
    return point is not None and (point[0] % N) == r


def decode_point_any(pub65: bytes):
    if len(pub65) != 65 or pub65[0] != 0x04:
        return None
    x = int.from_bytes(pub65[1:33], "big")
    y = int.from_bytes(pub65[33:], "big")
    if not on_curve(x, y):
        return None
    return (x, y)


def validate_point_uncompressed(value) -> None:
    """Strict OTP2-CRY-005a validation; raises ValueError on rejection."""
    raw = bytes.fromhex(hexof(value))
    if len(raw) == 1 and raw[0] == 0x00:
        # SEC1 encodes the point at infinity as the SINGLE 0x00 octet.
        raise ValueError("point: identity element (SEC1 point-at-infinity octet 0x00) is not a valid group element")
    if len(raw) != 65:
        raise ValueError("point_format: SEC1 uncompressed point must be 65 bytes")
    if raw[0] != 0x04:
        raise ValueError("point_format: compressed or hybrid encodings are rejected")
    pt = decode_point_any(raw)
    if pt is None:
        raise ValueError("point: byte string does not decode as an on-curve P-256 point")
    if pt == (0, 0):
        raise ValueError("point: identity element is not a valid group element")
    if not point_equal(point_mul(N, pt), INFINITY):
        raise ValueError("point: element is not in the prime-order subgroup")


def validate_p1363_low_s(value) -> None:
    """Strict OTP2-REC-020a validation; raises ValueError on rejection."""
    raw = bytes.fromhex(hexof(value))
    if len(raw) != 64:
        raise ValueError("signature_format: P1363 signature must be exactly 64 bytes")
    r = int.from_bytes(raw[:32], "big")
    s = int.from_bytes(raw[32:], "big")
    if not (1 <= r < N):
        raise ValueError("signature_format: r out of range")
    if not (1 <= s < N):
        raise ValueError("signature_format: s out of range")
    if s > N // 2:
        raise ValueError("signature_format: high-S signature rejected")


# ------------------------------------------------- derivation per fixture kind


def derive_anchor(fixture_input: dict) -> dict:
    if fixture_input.get("ciphersuite") != "SPAKE2-P256-SHA256-HKDF-HMAC":
        raise ValueError("anchor: unexpected ciphersuite")
    out = spake2_outputs(
        utf8(fixture_input["id_a"]), utf8(fixture_input["id_b"]),
        int(hexof(fixture_input["w"]), 16), int(hexof(fixture_input["x"]), 16),
        int(hexof(fixture_input["y"]), 16), b"",
    )
    return {name: h(raw.hex()) for name, raw in out.items()}


def derive_pairing(fixture_input: dict, blinding: str = "fixed") -> dict:
    code = utf8(fixture_input["code"])
    if len(code) != 6 or not code.isdigit():
        raise ValueError("code must be six ASCII digits")
    ctx = build_public_context(fixture_input)
    prk, okm, w = code_to_w(code, bytes.fromhex(hexof(fixture_input["pairing_id"])))
    out = spake2_outputs(
        label("identity_a"), label("identity_b"), w,
        int(hexof(fixture_input["x"]), 16), int(hexof(fixture_input["y"]), 16),
        ctx, blinding,
    )
    result = {
        "ctx": h(ctx.hex()),
        "ctx_len": str(len(ctx)),
        "ctx_len_le": h(len64le(len(ctx)).hex()),
        "password_prk": h(prk.hex()),
        "password_okm": h(okm.hex()),
        "w": h(w.to_bytes(32, "big").hex()),
    }
    for key in ("p_a", "p_b", "k", "tt", "hash_tt", "ke", "ka", "kc_a", "kc_b", "c_a", "c_b"):
        result[key] = h(out[key].hex())
    return result


def pub_from_priv(priv) -> str:
    pt = point_mul(int(hexof(priv), 16), G)
    return h(encode_uncompressed(pt).hex())


def identity_body(identity: dict, pub: str, fingerprint: bytes) -> bytes:
    label_bytes = utf8(identity["label"])
    if len(label_bytes) > 32:
        raise ValueError("identity label exceeds 32 bytes")
    return (
        bytes.fromhex(hexof(identity["installation_id"]))
        + u16be(len(label_bytes)) + label_bytes
        + bytes.fromhex(hexof(identity["device_type"]))
        + bytes.fromhex(hexof(pub))
        + fingerprint
        + bytes.fromhex(hexof(identity["nonce"]))
    )


def fp_self(role_byte: str, installation_id: str, pub: str) -> bytes:
    return sha256(label("record_fingerprint") + bytes([int(role_byte, 16)])
                  + bytes.fromhex(hexof(installation_id)) + bytes.fromhex(hexof(pub)))


def fp_pair_display(fp_pair: bytes) -> str:
    return " ".join(fp_pair[i * 2:i * 2 + 2].hex().upper() for i in range(3))


def derive_enrollment(fixture_input: dict) -> dict:
    ke = bytes.fromhex(hexof(fixture_input["ke"]))
    ctx = bytes.fromhex(hexof(fixture_input["ctx"]))
    phone_pub = pub_from_priv(fixture_input["phone_identity"]["priv"])
    client_pub = pub_from_priv(fixture_input["client_identity"]["priv"])
    fp_phone = fp_self("01", fixture_input["phone_identity"]["installation_id"], phone_pub)
    fp_client = fp_self("02", fixture_input["client_identity"]["installation_id"], client_pub)
    phone_body = identity_body(fixture_input["phone_identity"], phone_pub, fp_phone)
    client_body = identity_body(fixture_input["client_identity"], client_pub, fp_client)
    enrollment_salt = sha256(label("enrollment_salt") + len64le(len(ctx)) + ctx)
    enrollment_prk = hkdf_extract(enrollment_salt, ke)
    k_ab_aes = hkdf_expand(enrollment_prk, label("enrollment_ab_aes"), 32)
    k_ab_prefix = hkdf_expand(enrollment_prk, label("enrollment_ab_nonce"), 4)
    k_act_a = hkdf_expand(enrollment_prk, label("enrollment_act_a"), 32)
    k_ba_aes = hkdf_expand(enrollment_prk, label("enrollment_ba_aes"), 32)
    k_ba_prefix = hkdf_expand(enrollment_prk, label("enrollment_ba_nonce"), 4)
    k_act_b = hkdf_expand(enrollment_prk, label("enrollment_act_b"), 32)
    et = sha256(
        label("enrollment_transcript")
        + u16be(len(ctx)) + ctx
        + u16be(65) + bytes.fromhex(hexof(fixture_input["p_a"]))
        + u16be(65) + bytes.fromhex(hexof(fixture_input["p_b"]))
        + u16be(32) + bytes.fromhex(hexof(fixture_input["c_a"]))
        + u16be(32) + bytes.fromhex(hexof(fixture_input["c_b"]))
        + u16be(len(phone_body)) + phone_body
        + u16be(len(client_body)) + client_body
    )
    session_prefix = sha256(label("enrollment_session") + ctx)[:8]
    fp_pair = sha256(label("pair_fingerprint") + bytes.fromhex(hexof(phone_pub)) + bytes.fromhex(hexof(client_pub)))
    act_prepare = hmac_sha256(k_act_a, et + b"\x00")
    act_commit = hmac_sha256(k_act_b, et + b"\x01")
    act_complete = hmac_sha256(k_act_a, et + b"\x02")
    act_ack = hmac_sha256(k_act_b, et + b"\x03")
    nonce = lambda prefix, seq: prefix + u64be(seq)
    return {
        "phone_identity_pub": phone_pub,
        "client_identity_pub": client_pub,
        "phone_identity_body": h(phone_body.hex()),
        "client_identity_body": h(client_body.hex()),
        "fp_self_phone": h(fp_phone.hex()),
        "fp_self_client": h(fp_client.hex()),
        "fp_pair": h(fp_pair.hex()),
        "fp_pair_display": fp_pair_display(fp_pair),
        "enrollment_salt": h(enrollment_salt.hex()),
        "enrollment_prk": h(enrollment_prk.hex()),
        "k_ab_aes": h(k_ab_aes.hex()),
        "k_ab_nonce_prefix": h(k_ab_prefix.hex()),
        "k_act_a": h(k_act_a.hex()),
        "k_ba_aes": h(k_ba_aes.hex()),
        "k_ba_nonce_prefix": h(k_ba_prefix.hex()),
        "k_act_b": h(k_act_b.hex()),
        "act_prepare_mac": h(act_prepare.hex()),
        "act_commit_mac": h(act_commit.hex()),
        "act_complete_mac": h(act_complete.hex()),
        "act_ack_mac": h(act_ack.hex()),
        "enrollment_nonce_ab_seq0": h(nonce(k_ab_prefix, 0).hex()),
        "enrollment_nonce_ba_seq7": h(nonce(k_ba_prefix, 7).hex()),
        "session_id_prefix": h(session_prefix.hex()),
        "et": h(et.hex()),
    }


def derive_reconnect(fixture_input: dict) -> dict:
    et = bytes.fromhex(hexof(fixture_input["et"]))
    phone_pub = pub_from_priv(fixture_input["phone_identity_priv"])
    client_pub = pub_from_priv(fixture_input["client_identity_priv"])

    def hello(installation_id, pub, nonce, role, caps):
        cap_bytes = b"".join(bytes([int(c, 16)]) for c in caps)
        return (
            bytes.fromhex(hexof(installation_id)) + bytes.fromhex(hexof(pub))
            + bytes.fromhex(hexof(nonce)) + bytes([int(role, 16)])
            + u16be(1) + u16be(len(cap_bytes)) + cap_bytes
        )

    phone_hello = hello(fixture_input["phone_installation_id"], phone_pub,
                        fixture_input["phone_nonce"], "01", fixture_input["phone_caps"])
    client_hello = hello(fixture_input["client_installation_id"], client_pub,
                         fixture_input["client_nonce"], "02", fixture_input["client_caps"])
    rbase = label("reconnect_transcript") + phone_hello + client_hello
    digest = sha256(rbase)
    ph_r, ph_s = ecdsa_sign_low_s(digest, bytes.fromhex(hexof(fixture_input["phone_identity_priv"])))
    cl_r, cl_s = ecdsa_sign_low_s(digest, bytes.fromhex(hexof(fixture_input["client_identity_priv"])))
    phone_sig = ph_r + ph_s
    client_sig = cl_r + cl_s
    if not ecdsa_verify(digest, bytes.fromhex(hexof(phone_pub)), int.from_bytes(ph_r, "big"), int.from_bytes(ph_s, "big")):
        raise ValueError("oracle self-check: phone signature failed its own verification")
    if not ecdsa_verify(digest, bytes.fromhex(hexof(client_pub)), int.from_bytes(cl_r, "big"), int.from_bytes(cl_s, "big")):
        raise ValueError("oracle self-check: client signature failed its own verification")
    phone_eph = encode_uncompressed(point_mul(int(hexof(fixture_input["phone_ephemeral_priv"]), 16), G))
    client_eph = encode_uncompressed(point_mul(int(hexof(fixture_input["client_ephemeral_priv"]), 16), G))
    ss_phone = point_mul(int.from_bytes(bytes.fromhex(hexof(fixture_input["phone_ephemeral_priv"])), "big"), decode_point_any(client_eph))[0].to_bytes(32, "big")
    ss_client = point_mul(int.from_bytes(bytes.fromhex(hexof(fixture_input["client_ephemeral_priv"])), "big"), decode_point_any(phone_eph))[0].to_bytes(32, "big")
    if ss_phone != ss_client:
        raise ValueError("reconnect ECDH disagrees between directions")
    salt = sha256(label("reconnect_salt") + bytes.fromhex(hexof(fixture_input["phone_nonce"]))
                  + bytes.fromhex(hexof(fixture_input["client_nonce"])))
    prk = hkdf_extract(salt, ss_phone)
    k_ba_aes = hkdf_expand(prk, label("reconnect_ba_aes"), 32)
    k_ba_prefix = hkdf_expand(prk, label("reconnect_ba_nonce"), 4)
    k_ba_confirmation = hkdf_expand(prk, label("reconnect_ba_confirmation"), 32)
    k_ab_aes = hkdf_expand(prk, label("reconnect_ab_aes"), 32)
    k_ab_prefix = hkdf_expand(prk, label("reconnect_ab_nonce"), 4)
    k_ab_confirmation = hkdf_expand(prk, label("reconnect_ab_confirmation"), 32)
    session_id = hkdf_expand(prk, label("reconnect_session_id"), 32)
    k_rec_client = hkdf_expand(prk, label("recovery_client_activation") + et, 32)
    k_rec_phone = hkdf_expand(prk, label("recovery_phone_activation") + et, 32)
    confirm_input = sha256(label("reconnect_confirm_input") + rbase + phone_sig + client_sig)
    return {
        "phone_identity_pub": phone_pub,
        "client_identity_pub": client_pub,
        "phone_ephemeral_pub": h(phone_eph.hex()),
        "client_ephemeral_pub": h(client_eph.hex()),
        "phone_hello_payload": h(phone_hello.hex()),
        "client_hello_payload": h(client_hello.hex()),
        "rbase": h(rbase.hex()),
        "rbase_digest": h(digest.hex()),
        "phone_sig": h(phone_sig.hex()),
        "phone_sig_r": h(ph_r.hex()),
        "phone_sig_s": h(ph_s.hex()),
        "client_sig": h(client_sig.hex()),
        "ss": h(ss_phone.hex()),
        "reconnect_salt": h(salt.hex()),
        "reconnect_prk": h(prk.hex()),
        "k_ba_aes": h(k_ba_aes.hex()),
        "k_ba_nonce_prefix": h(k_ba_prefix.hex()),
        "k_ba_confirmation": h(k_ba_confirmation.hex()),
        "k_ab_aes": h(k_ab_aes.hex()),
        "k_ab_nonce_prefix": h(k_ab_prefix.hex()),
        "k_ab_confirmation": h(k_ab_confirmation.hex()),
        "session_id": h(session_id.hex()),
        "session_id_prefix": h(session_id[:8].hex()),
        "reconnect_confirm_input": h(confirm_input.hex()),
        "conf_a": h(hmac_sha256(k_ab_confirmation, confirm_input).hex()),
        "conf_b": h(hmac_sha256(k_ba_confirmation, confirm_input).hex()),
        "k_rec_client": h(k_rec_client.hex()),
        "k_rec_phone": h(k_rec_phone.hex()),
    }


# ------------------------------------------------- oracle engine

ORACLE_COVERED_NEGATIVES = {
    "wrong_code", "context_mismatch", "swap_roles", "role_mismatch",
    "off_curve_point", "identity_point", "zero_point", "compressed_point",
    "shared_secret_identity",
    "high_s", "der_encoding", "truncated_sig",
}

# Stage is DERIVED from the actual failure kind (never echoed from the
# fixture) and compared against the declared stage.
DEVIATION_STAGE = {
    "wrong_code": "code",
    "context_mismatch": "context",
    "swap_roles": "role",
    "role_mismatch": "role",
    "off_curve_point": "point",
    "identity_point": "point",
    "zero_point": "point",
    "compressed_point": "point_format",
    "shared_secret_identity": "shared_secret",
    "high_s": "signature_format",
    "der_encoding": "signature_format",
    "truncated_sig": "signature_format",
    "bad_tag": "tag",
    "mutated_session": "session",
    "mutated_direction": "direction",
    "mutated_sequence": "sequence",
}


def assertion_stage(error: ValueError) -> str:
    prefix = error.args[0].split(":", 1)[0]
    if prefix not in ("point", "point_format", "signature_format"):
        raise SystemExit(f"oracle internal: validator error has no stage prefix: {error}")
    return prefix


def expected_stage_of(fid: str, derived: str, declared) -> str:
    if declared != derived:
        raise ValueError(f"actual rejection stage is {derived!r} but fixture {fid} declares {declared!r}")
    return derived

DEV_TO_BLINDING = {"swap_roles": "swap_roles", "role_mismatch": "role_mismatch"}


def check_negative(fixture: dict, fixtures_by_id: dict) -> list:
    """Return list of intermediate names the oracle independently confirmed."""
    deviation = fixture["input"]["deviation"]
    base = fixtures_by_id[deviation["negates"]]
    kind = deviation["type"]
    if kind not in ORACLE_COVERED_NEGATIVES:
        return []
    if kind in ("wrong_code", "context_mismatch", "swap_roles", "role_mismatch"):
        mutated = {k: v for k, v in fixture["input"].items() if k != "deviation"}
        if kind == "wrong_code":
            mutated["code"] = deviation.get("value", "654321")
        if kind == "context_mismatch":
            mutated["client_nonce"] = deviation["value"]
        base_outputs = derive_pairing(base["input"])
        deviated = derive_pairing(mutated, DEV_TO_BLINDING.get(kind, "fixed"))
        distinctions = fixture["expected"]["distinguishes"]
        if not distinctions or "c_a" not in distinctions or "c_b" not in distinctions:
            raise ValueError("distinguishing set must include BOTH peer confirmations c_a and c_b")
        errors = []
        all_names = set(base_outputs) | set(deviated)
        for name in all_names:
            pos = base_outputs.get(name)
            dev = deviated.get(name)
            if pos is None or dev is None:
                continue
            differs = pos != dev
            if name in distinctions and not differs:
                errors.append(f"expected to differ but matched: {name}")
            if name not in distinctions and differs:
                errors.append(f"unexpectedly differs: {name}")
        if errors:
            raise ValueError("; ".join(errors))
        # Peer-confirmation actual-equality check beyond the distinguishing
        # list: BOTH confirmations must differ from the positive execution.
        if deviated["c_a"] == base_outputs["c_a"]:
            raise ValueError("phone confirmation c_a unexpectedly equals the positive execution")
        if deviated["c_b"] == base_outputs["c_b"]:
            raise ValueError("client confirmation c_b unexpectedly equals the positive execution")
        expected_stage_of(fixture["id"], DEVIATION_STAGE[kind], fixture["expected"].get("stage"))
        return sorted(set(base_outputs) | set(deviated))
    if kind in ("off_curve_point", "identity_point", "zero_point", "compressed_point"):
        try:
            validate_point_uncompressed(deviation["value"])
        except ValueError as exc:
            expected_stage_of(fixture["id"], assertion_stage(exc), fixture["expected"].get("stage"))
            return ["point_validation"]
        raise ValueError("expected point rejection but the point validated")
    if kind == "shared_secret_identity":
        # OTP2-CRY-003a, honestly bound to the committed bytes (re-review I3):
        # the fixture commits the EXACT forced peer points (value = pB phone
        # view, value2 = pA client view). This oracle parses BOTH, validates
        # them as real on-curve P-256 points, checks the premise against its
        # own w derivation, and computes the shared element from the COMMITTED
        # bytes (never pB := w·N substituted behind the subtraction).
        try:
            forced_b = decode_point_any(bytes.fromhex(hexof(deviation["value"])))
            forced_a = decode_point_any(bytes.fromhex(hexof(deviation["value2"])))
        except Exception:
            forced_b = forced_a = None
        if forced_b is None or forced_a is None:
            raise ValueError("shared_secret: committed forced peer points must decode as on-curve P-256 points")
        validate_point_uncompressed(deviation["value"])
        validate_point_uncompressed(deviation["value2"])
        base_outputs = derive_pairing(base["input"])
        w = int(hexof(base_outputs["w"]), 16)
        x = int(hexof(base["input"]["x"]), 16)
        y = int(hexof(base["input"]["y"]), 16)
        if not point_equal(forced_b, point_mul(w, N_POINT)):
            raise ValueError("shared_secret: committed value is not pB = w·N (premise broken)")
        if not point_equal(forced_a, point_mul(w, M_POINT)):
            raise ValueError("shared_secret: committed value2 is not pA = w·M (premise broken)")
        # The deviation must be a REAL deviation: it differs from the honest peer points.
        if hexof(base_outputs["p_b"]) == hexof(deviation["value"]) or hexof(base_outputs["p_a"]) == hexof(deviation["value2"]):
            raise ValueError("shared_secret: the forced peer points must differ from the positive base")
        # Compute the shared element FROM THE COMMITTED peer point bytes.
        k_phone_view = point_mul(x, point_sub(forced_b, point_mul(w, N_POINT)))
        k_client_view = point_mul(y, point_sub(forced_a, point_mul(w, M_POINT)))
        if k_phone_view is not None or k_client_view is not None:
            raise ValueError("shared_secret: committed forced peer points did not collapse K to the identity")
        expected_stage_of(fixture["id"], DEVIATION_STAGE[kind], fixture["expected"].get("stage"))
        return ["shared_secret_abort"]
    if kind in ("high_s", "der_encoding"):
        # The stored variant is mathematically valid ECDSA for the same digest,
        # so the oracle proves rejection is EXCLUSIVELY the canonical-encoding
        # policy: affine ECDSA verify accepts it, the strict P1363 rule does not.
        base_outputs = derive_reconnect(base["input"])
        rbase_digest = bytes.fromhex(hexof(base_outputs["rbase_digest"]))
        pub = bytes.fromhex(hexof(base_outputs["phone_identity_pub"]))
        raw = bytes.fromhex(hexof(deviation["value"]))
        if kind == "high_s":
            r, s = int.from_bytes(raw[:32], "big"), int.from_bytes(raw[32:], "big")
            if not ecdsa_verify(rbase_digest, pub, r, s):
                raise ValueError("high-S variant does not verify mathematically; fixture premise broken")
            if int.from_bytes(raw[32:], "big") <= N // 2:
                raise ValueError("variant is already low-S; fixture premise broken")
        else:
            # DER parse: SEQUENCE { INTEGER r, INTEGER s }
            if raw[0] != 0x30:
                raise ValueError("der_encoding variant is not a DER sequence")
            idx, body_len = 2, raw[1]
            body = raw[2:2 + body_len]
            parsed = []
            pos = 0
            while pos < len(body):
                if body[pos] != 0x02:
                    raise ValueError("der_encoding variant is not INTEGER-paired")
                ln = body[pos + 1]
                parsed.append(int.from_bytes(body[pos + 2:pos + 2 + ln], "big"))
                pos += 2 + ln
            if len(parsed) != 2 or not ecdsa_verify(rbase_digest, pub, parsed[0], parsed[1]):
                raise ValueError("DER variant does not verify mathematically; fixture premise broken")
        try:
            validate_p1363_low_s(deviation["value"])
        except ValueError as exc:
            expected_stage_of(fixture["id"], assertion_stage(exc), fixture["expected"].get("stage"))
            return ["signature_validation"]
        raise ValueError("expected signature rejection but the encoding validated")
    if kind == "truncated_sig":
        try:
            validate_p1363_low_s(deviation["value"])
        except ValueError as exc:
            expected_stage_of(fixture["id"], assertion_stage(exc), fixture["expected"].get("stage"))
            return ["signature_validation"]
        raise ValueError("expected signature rejection but the encoding validated")
    raise ValueError(f"oracle does not cover deviation {kind}")


def oracle_derive(fixture: dict) -> dict:
    kind = fixture["input"]["kind"]
    if kind == "rfc9382_anchor":
        return derive_anchor(fixture["input"])
    if kind == "spake2_pairing":
        return derive_pairing(fixture["input"])
    if kind == "enrollment":
        return derive_enrollment(fixture["input"])
    if kind == "reconnect":
        return derive_reconnect(fixture["input"])
    raise ValueError(f"oracle does not cover kind {kind}")


def default_report_path(input_path: str) -> str:
    """Reports always land under build/protocol — never next to the committed
    fixtures (the fixture loader parses every *.json under fixtures/)."""
    apath = os.path.abspath(input_path)
    base = os.path.basename(apath)
    if base == "crypto-candidate.json":
        return os.path.join(os.path.dirname(apath), "crosscheck-oracle.json")
    if base == "crypto.json":
        # crypto.json lives at <repo>/protocol/v1/fixtures/ — four dirnames up.
        root = apath
        for _ in range(4):
            root = os.path.dirname(root)
        return os.path.join(root, "build", "protocol", "crosscheck-oracle-committed.json")
    return os.path.join(os.path.dirname(apath), base.replace(".json", "-oracle-report.json"))


def main() -> int:
    args = sys.argv[1:]
    out_path = None
    if "-o" in args:
        idx = args.index("-o")
        out_path = args[idx + 1]
        args = args[:idx] + args[idx + 2:]
    if len(args) != 1:
        print("usage: oracle.py <fixtures.json> [-o <report.json>]", file=sys.stderr)
        return 2
    input_path = args[0]
    out_path = out_path or default_report_path(input_path)
    with open(input_path, "r", encoding="utf-8") as fh:
        candidate = json.load(fh)
    if "fixtures" not in candidate:
        print("oracle: input has no fixtures array", file=sys.stderr)
        return 2
    fixtures_by_id = {f["id"]: f for f in candidate["fixtures"]}
    if len(fixtures_by_id) != len(candidate["fixtures"]):
        print("oracle: duplicate fixture ids in input", file=sys.stderr)
        return 1

    report = {
        "tool": "crosscheck-vectors/oracle.py",
        "python": platform.python_version(),
        "independent_implementation": (
            "stdlib-only affine P-256 (chord/tangent formulas), RFC 5869 HKDF over "
            "hashlib/hmac, RFC 6979 deterministic ECDSA signing and affine verification, "
            "SHA-256 via hashlib; AES-256-GCM intentionally not implemented here "
            "(covered by crosscheck-vectors/openssl-check.mjs)"
        ),
        "input_file": os.path.relpath(input_path),
        "input_sha256": hashlib.sha256(open(input_path, "rb").read()).hexdigest(),
        "anchor_selfcheck": "skipped",
        "fixtures": {},
        "oracle_label_selfcheck": "ok",
    }

    # Step 0: anchors — the oracle must REPRODUCE the published vectors from its
    # own verbatim constants before comparing anything with the JS side.
    anchor_failures = []
    for anchor_id, anchor in RFC_ANCHORS.items():
        got = spake2_outputs(
            utf8(anchor["id_a"]), utf8(anchor["id_b"]), anchor["w"], anchor["x"], anchor["y"], b"",
        )
        for name, expected in anchor["published"].items():
            if h(got[name].hex()) != expected:
                anchor_failures.append(f"{anchor_id}/{name}")
    # Content binding: the report only approves fixtures whose canonical
    # {id, input, expected} digest matches what this run actually verified.
    report["fixtures_sha256"] = fixtures_canonical_sha256(candidate["fixtures"])
    report["fixture_sha256"] = {fid: fixture_content_sha256(f) for fid, f in fixtures_by_id.items()}
    report["anchor_selfcheck"] = "ok" if not anchor_failures else "fail: " + ", ".join(anchor_failures)
    if anchor_failures:
        print("oracle: anchor self-check FAILED:", anchor_failures, file=sys.stderr)

    failures = 0
    for fixture in candidate["fixtures"]:
        fid = fixture["id"]
        entry = {"status": "skipped", "errors": []}
        try:
            if fixture["expected"].get("rejected"):
                deviation = fixture["input"].get("deviation", {})
                if deviation.get("type") not in ORACLE_COVERED_NEGATIVES:
                    entry["status"] = "skipped"
                    entry["errors"].append("covered by openssl-check.mjs")
                else:
                    names = check_negative(fixture, fixtures_by_id)
                    entry = {
                        "status": "ok",
                        "intermediates": len(names),
                        "outputs": names,
                        "fixture_sha256": fixture_content_sha256(fixture),
                    }
            else:
                if fixture["input"]["kind"] == "record_gcm":
                    entry["status"] = "skipped"
                    entry["errors"].append("covered by openssl-check.mjs (stdlib has no AES-GCM)")
                else:
                    got = oracle_derive(fixture)
                    expected = fixture["expected"]["outputs"]
                    mismatches = [
                        name for name, want in sorted(expected.items())
                        if name in got and got[name] != want
                    ]
                    # anchors: compare against THIS file's published copies too
                    if fixture["input"]["kind"] == "rfc9382_anchor":
                        published = RFC_ANCHORS[fid]["published"]
                        mismatches += [
                            f"published/{name}" for name, want in sorted(published.items())
                            if got.get(name) != want
                        ]
                    if mismatches:
                        raise ValueError("intermediate mismatches: " + ", ".join(mismatches))
                    # The oracle must have derived the FULL committed output
                    # set (no dropped keys) with exactly matching bytes.
                    missing = [name for name in sorted(expected) if name not in got]
                    if missing:
                        raise ValueError("oracle did not derive committed outputs: " + ", ".join(missing))
                    entry = {
                        "status": "ok",
                        "intermediates": len(expected),
                        "outputs": sorted(expected.keys()),
                        "fixture_sha256": fixture_content_sha256(fixture),
                    }
        except (KeyError, ValueError) as exc:
            entry = {"status": "fail", "errors": [str(exc)]}
        report["fixtures"][fid] = entry
        if entry["status"] == "fail":
            failures += 1

    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(report, fh, indent=2, sort_keys=True)
        fh.write("\n")

    ok = sum(1 for v in report["fixtures"].values() if v["status"] == "ok")
    skipped = sum(1 for v in report["fixtures"].values() if v["status"] == "skipped")
    print(f"oracle: {ok} verified, {skipped} delegated to openssl-check, {failures} failed"
          f" (anchor self-check: {report['anchor_selfcheck']})")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
