# crosscheck-vectors — test-only independent verifiers

Two mutually independent implementations verify the committed crypto fixtures
(`protocol/v1/fixtures/crypto.json`) and the generated candidate
(`build/protocol/crypto-candidate.json`). Provenance, pins and acceptance
evidence live in `protocol/v1/fixtures/PROVENANCE.md`.

| Tool | Language/dependencies | Verifies |
|---|---|---|
| `oracle.py` | Python 3 (≥3.8), **stdlib only** — `hashlib`, `hmac`, `json` | Every non-AES intermediate: RFC 9382 anchors (reproduced from its own verbatim copy of the published constants BEFORE any comparison), Veles code→w transform, public context, TT, Ke/Ka, KcA/KcB, cA/cB, enrollment schedule/ET/fingerprints, reconnect rbase/ECDH-X/HKDF/recovery keys, deterministic RFC 6979 ECDSA signatures (independent signing + affine verification), and the code/context/role/point/signature negatives (including the premise that high-S and DER variants are mathematically valid but non-canonical). AES-256-GCM is intentionally NOT implemented (stdlib has no AES) — the record positives and tag/session/direction/sequence negatives are delegated to `openssl-check.mjs`. |
| `openssl-check.mjs` | Node ≥22 built-in `crypto` (OpenSSL) | ECDH shared-secret X (SEC1 DER import; public point derived by OpenSSL, not noble), RFC 5869 HKDF (expand as an HMAC loop because Node has no expand-only API), SHA-256/HMAC confirmation chains (including the published RFC anchors' hash chain), ECDSA P1363 verification with exact single-prehash semantics (a double-hash digest signature must NOT verify), AES-256-GCM ciphertext/tag + decryption round trips, and the record/signature negatives. |

Not independent of each other is the pair {this dir's two tools} in one
respect only: both run under Node/Python on one machine — but they share no
code, no constants beyond the RFC-published points, and no intermediate
representations. The noble engine itself is cross-checked by both.

## Exact commands (from the repository root)

```sh
# candidate (pre-commit provenance)
python3 protocol/tools/crosscheck-vectors/oracle.py build/protocol/crypto-candidate.json
node    protocol/tools/crosscheck-vectors/openssl-check.mjs

# committed acceptance run (mutating any committed byte fails here)
python3 protocol/tools/crosscheck-vectors/oracle.py protocol/v1/fixtures/crypto.json
node    protocol/tools/crosscheck-vectors/openssl-check.mjs protocol/v1/fixtures/crypto.json
```

Reports: `build/protocol/crosscheck-{oracle,openssl}.json` (candidate) and
`build/protocol/crosscheck-{oracle,openssl}-committed.json` (acceptance).
Exit codes: 0 all covered fixtures verified; 1 any mismatch; 2 usage.

## Manifest / dependency pins

- `oracle.py`: no third-party packages (stdlib only). Acceptance environment:
  Python 3.14.4. Never imported by product code; variable time by design.
- `openssl-check.mjs`: no npm dependencies (Node built-ins). Acceptance
  environment: Node v26.12.1 (bundled OpenSSL 3.5.x). Never imported by
  product code.
- The noble JS engine they cross-check is pinned in
  `protocol/package.json` / `protocol/package-lock.json`:
  `@noble/curves@2.4.0`, `@noble/hashes@2.4.0`, `@noble/ciphers@2.4.0` (MIT).
- No code in this directory may be ported into a runtime path; test-only
  status is normative (OTP2-FIX-006).