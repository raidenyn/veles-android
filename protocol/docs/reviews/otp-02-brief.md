# OTP-02 independent review brief

Status: final reviewed-content inventory; user acceptance pending. Actual
independent technical verdicts and their scoped history are recorded in
`otp-02-subagent.md` and `otp-02-opus.md`; final evidence and pending decisions
are in `otp-02-disposition.md`. This brief is not itself a verdict.

Review the scope below against the RFC
[`docs/rfcs/2026-08-21-otp-external-devices.md`](../../rfcs/2026-08-21-otp-external-devices.md),
the approved design
[`docs/superpowers/specs/2026-10-23-otp-02-protocol-contract-design.md`](../../superpowers/specs/2026-10-23-otp-02-protocol-contract-design.md),
and the implementation plan
[`docs/superpowers/plans/2026-10-23-otp-02-protocol-contract.md`](../../superpowers/plans/2026-10-23-otp-02-protocol-contract.md).
Use the exact reviewer prompt at
`/home/dev/.cache/opencode/packages/superpowers@git+https:/github.com/obra/superpowers.git/node_modules/superpowers/skills/requesting-code-review/code-reviewer.md`.
Assess security, interoperation, canonical encoding, state authorization,
recovery, downgrade behavior, and evidence truth. Report ID, severity, exact
file/anchor, and recommended disposition.

Limitations to review rather than infer away:

- The evidence inventory's document proof establishes traceability, not that a
  generic loader test proves every security property.
- State fixtures use simulated cryptographic outcomes and flag-level policy
  checks; they do not prove endpoint integration behavior.
- Heartbeat receive-side timing/spacing is not modeled by the state interpreter.
- Independent technical approvals exist for the current hash inventory;
  requirement statuses remain evidence/traceability statuses, and user acceptance
  must not be inferred from them.

The review scope excludes this directory and review metadata. SHA-256:

```text
5c578eb7469aaae547328dd5ba1478c91058adbf0a9b124a6c85050e17811aad  docs/protocol/veles-v1.md
2eaf4d706485b1a41f6fd1acb8367df2048691823a88dc71d294f2be5f704008  protocol/README.md
1264a78619c7e1c9115464a7c4bb7b00730b352acd300f6e92a512c8b7b884f0  protocol/v1/binary-layouts.json
a81cec20b5db5109ff02e0031e6d457c932f0fa1a9c0b186d5468f4ca0afe1bd  protocol/v1/envelope.schema.json
cc1247925c8ae63159b43bac54f62da2cbb8f76218ffa9654a4b436749c288c6  protocol/v1/fixture.schema.json
862e2b2d99ecfcfa80f34754d1817a7295032c07e1e5a2e2553ec202f3714bcc  protocol/v1/limits.json
37c275b8f09221fa1b2cdf7845f3682979ea08f3e38f7c8f273fe8733fff2ac4  protocol/v1/manifest.json
4ca9266251b4a550896f02b034fb8cc38418b97559b5591f0fbcddf31889c585  protocol/v1/states.json
ecea209f471929d4ad57e01ccdf78ce55a64f3ec598544d73d5f5f136d610be7  protocol/v1/fixtures/bridge.json
d6e72e1a92fb9c7bc0c9e627f3abc231bf5a608a5b0c39fa51f39a905b3cf06a  protocol/v1/fixtures/crypto.json
009183ae86fbe7128b1a68eaa751332e4b645dea7ffb0f381270967c56cee99e  protocol/v1/fixtures/encoding.json
8add218ee183897c07fb6a78712ba6bcde04d1ff39dd2bd44c38d21163f59026  protocol/v1/fixtures/enrollment.json
37b90665d760aaf0710a32dc040eef05c96063345094fd53224ad9341d269f24  protocol/v1/fixtures/fragmentation.json
c3a1fc51499469717728fe2b035b6d2bd63ec3f73b56a5f2757db4eb57342b27  protocol/v1/fixtures/reconnect.json
f88498fbbe0f3846391120fae02c8f86a88d3c464c7b3f7df3f9197fc90f3e0f  protocol/v1/fixtures/records.json
38e27bb9b71dbb8e177d2b0a483581da3e6c6e12446633fe1a04a3643ec309a9  protocol/v1/fixtures/PROVENANCE.md
b3ae0fe63f0950127484fc7d3bf0338ae7dc6c156d6f8cfcb83943bd22362450  protocol/checks/binary.mjs
2de21c8d17af4c03905600f240396a1468e83816609fac8847e17a583ac79c07  protocol/checks/bridge.test.mjs
30698564039b3984ea29a543c9d229600c2bf0b969901ca05bf8ecbad78c3ace  protocol/checks/check.mjs
b0cbb0eb12c6f8611615acdd38d372b16b11757ce84a592958a2a193e7df861f  protocol/checks/contract.test.mjs
2a1899c7a272c1077ad24ec0a447e1c59161fb087afd38f5a868b3f7275ea1ce  protocol/checks/crypto.mjs
5842650df93812136f3011b7b77d5dbf251eb84079e801ffe217de7208cabf5e  protocol/checks/crypto.test.mjs
863a511d8408c1e105fc837bef6c3e92fd9514cf2d631cd96ba6b0eebcb2cc35  protocol/checks/envelope.mjs
6e989b3a915837e0ec6bc942f2556e097f339829f4d376431cfb835ba17b1de9  protocol/checks/evidence.mjs
3b491dd0afe9000051466482cd9649e0c8538d2ed771fde15f14793e09477787  protocol/checks/fixture.mjs
6d1bcd9d98a86e4aa12df42a5d6a3048b55b31330ff81bcc089ee34909be0446  protocol/checks/state.mjs
06bfb471321cb1f192b7c42d7c0b4f23797d6645f328354db676709082652892  protocol/checks/state.test.mjs
9a86f52bc880424b525ce27d9238bde8670f3fb7cf134bde4588b3a86d449e04  protocol/tools/check-contract-text.mjs
73ce0cec1256c1ffe058f0cf772f4d41fd9f4c65c3461ca5513ff1666a154c33  protocol/tools/generate-vectors.mjs
f752f24e08fd568a24e3bcd0e6ecf438e2a83f203329825b72cc0c8c3152ef06  protocol/tools/crosscheck-vectors/openssl-check.mjs
d35a2de5d4a2bb69bec5c7369f104b5280e4d4a712ad4ea728913cc91c5944c9  protocol/tools/crosscheck-vectors/oracle.py
44b3455354545152a3eb9765e6bd5f66f765efd8858ed4966b9e25f4fea2ffd6  rust/veles-crypto/tests/protocol_fixtures.rs
6f1b89020f265204eac15a5418300a8f520110d5494d1490bdd608e867b282b6  web-extension/test/protocol-fixtures.test.ts
73dd7fabac22289def623c5c6aa0f47fc2615b278167ddcd8ef8affa17ec22af  native-bridge/test/protocol-fixtures.test.ts
f75175179acb77feca64e3550f054710212fc0625bd9afc6f6882c723239314c  app/src/test/java/me/nagaev/veles/protocol/ProtocolFixturesTest.kt
56da4c436d40db2c8082c711aa79ab456ad683d3eed8e05631940822b74f3a75  build.gradle.kts
1ed3e441c1f67a7a4b5f73f0f4303ca1e7a1943c9847462bd6061cfc4e121274  app/build.gradle.kts
8ab09e362493fd24b15b4bf496baf27b0793ab5a0fd4a74ff19ed21a622ff8f8  web-extension/package.json
5813f6ca927f51784191316db993ab13fdf3fd5661bc5548f37aec2133a07a2d  native-bridge/package.json
1f5ac1e8161ebb29285141ad2bd376fec39802a010f9b20685ee744528eb1ef4  rust/Cargo.lock
3d0a1b92e025bded7a9bb663222a0275787a923cc5cdb5c296d2487dc448221b  rust/veles-crypto/Cargo.toml
e3e1ce11fbc2f68b29b8d99787f14613ecd3c49ae426454e0739635ebbf479a3  .github/workflows/ci.yml
73a71b17f8b4497db7f0b210eba790e22ce57bee4f2752e16ba4bdd799d39cc9  .github/workflows/build-supply-chain.yml
1f56d0d85ef13ee5bc3b887a9f28dbcc2a597da8fb92857431bb789ddb8a0075  verify/check-npm-install-scripts.mjs
07b97f7e243a8a0cce3d080db671683520c5473353f40a854b1a51ad7d7b550c  verify/check-npm-licenses.mjs
25535a6b1e7d497412f27a0d5065fba770b59ce8d27984616a63365599912261  verify/generate-sboms.sh
640c35110170069b265c44c233a38c0a6697c76727ac3f66a2a733bcd8600f7a  verify/verify-sboms.mjs
16d9ea1dbc79f91bdc10666fa951cecd4e64ae340b21b79fa87e6d7d8e3c8bc9  verify/verify-supply-chain.sh
d1a3f5f4391e51aeb718de8843caa63995c7285542a48116993e1a85d419e458  verify/test/sbom.test.mjs
dd4e6a46a9a38a6930e063dfff4271114ce300bda7fc92f9c9aa9da352ef0ade  verify/test/workflow-contracts.test.mjs
```
