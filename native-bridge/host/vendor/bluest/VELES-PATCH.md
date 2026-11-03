# Veles patch of bluest 0.6.9

Vendored from the crates.io release `bluest 0.6.9` (BSD-2-Clause OR Apache-2.0; licence files kept) and wired in
through `[patch.crates-io]` of `native-bridge/host/Cargo.toml`. It is compiled on macOS only. Exactly two source files
differ from the pristine crate (`VELES-PATCH.diff` is the full change; `examples/`, `tests/` and `Cargo.lock` were
dropped, nothing else was edited):

| file | pristine sha256 | patched sha256 |
|---|---|---|
| `src/corebluetooth/characteristic.rs` | `2ddaacaa60daa01c11710851bd165ac593782a4996252be364ca0f14fe9abe5f` | `0d88ea8bbb86c41325bc27990d9c81811881141b9d5a04ce2d40efeb47d5122c` |
| `src/lib.rs` | `0d4c00add59b85c956bd5f2c5b90690719a67037568764181fe95c900d172dec` | `b561908bdfc075c61732f41207a0bc42812cbaef723408674ddbbc72c54c0e5d` |

1. **Loss-aware notifications** (`Characteristic::notify`). Upstream reads the 16-slot, overflow-dropping peripheral
   event channel as a `Stream`; `async_broadcast`'s `Stream` impl silently `continue`s past `RecvError::Overflowed`, so
   a lagging consumer loses notifications with no signal. The patch reads `recv()` directly and yields the overflow as
   an `Err` item. Any gap in the channel (not only value updates) is reported, which is deliberately conservative.
2. **Marked native disconnect.** The peripheral delegate's `didDisconnectPeripheral` event is yielded as an `Err`
   whose kind is `NotConnected` and whose message is `bluest::DID_DISCONNECT_PERIPHERAL`, so the adapter can tell this
   authoritative native event from any other `NotConnected`-kinded error.

Provenance: the pristine crate is crates.io `bluest 0.6.9`, upstream crate sha256 `a0c911a419028965c201759adadc1b556d1ee50eb2dfcceec3acb765c80e96f6`
(the checksum `native-bridge/feasibility/Cargo.lock` pins). Reverse-applying `VELES-PATCH.diff` to the two patched files
reproduces the pristine hashes in the table, and every other vendored file equals the registry copy (only `examples/`,
`tests/`, `Cargo.lock` and `.cargo_vcs_info.json` are absent). `verify/check-vendored-patches.mjs` re-proves all of this
in the supply-chain gate; the crate has no build script.

`VELES-MANIFEST.sha256` is the complete reviewed file-and-hash manifest of the vendored tree (60 files; the 58 unpatched
entries were compared with the registry copy by hash). The only files outside it are the named provenance metadata
`VELES-MANIFEST.sha256`, `VELES-PATCH.md` and `VELES-PATCH.diff`. Any edit to an ordinary source file, any added file
(of any kind) and any deletion fails the gate. Regenerate it only as part of a reviewed upgrade.

Re-verify the hashes above before upgrading or touching either file. (An earlier round also added an exact
`Device::is_disconnected` state read; it was removed — a state poll is not a release acknowledgement.)
