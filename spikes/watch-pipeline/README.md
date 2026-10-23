# watch-pipeline (spike)

Disposable **research artifact**. This is not product firmware, not the RFC's Appendix-A `watch/`
layout, and no claim about the follow-up firmware RFC
([`docs/rfcs/2026-08-21-otp-external-devices.md`](../../docs/rfcs/2026-08-21-otp-external-devices.md)).

It proves the **remote development pipeline** for the physical LILYGO T-Watch-S3, the part that
OTP-26/27 leave to "the physical-hardware follow-up":

- an agent on the VPS builds Rust-on-ESP-IDF (`esp-idf-svc` + `esp32-nimble`) firmware;
- flashes it to the watch plugged into a home PC;
- talks to it over the network (serial test hooks), debugs it over JTAG;
- and exercises the watch as a **BLE central** against an Android phone peripheral.

The remote plumbing (`homepc watch …`, the home PC's bridge, OpenOCD and flasher) lives in the
infrastructure repo `raidenyn/server-config` (`home/README.md`).

## Test-hook protocol

Line-based JSON on the USB-Serial/JTAG console. Each command gets exactly one reply line prefixed
with `@@`; everything else on the console is log output.

| Command | Reply |
| --- | --- |
| `{"cmd":"ping"}` | `{"ok":true,"fw":…,"uptime_ms":…,"free_heap":…}` |
| `{"cmd":"ble.scan","secs":5,"uuid":"…"?}` | advertisers seen (merged by address; `uuid` filters by advertised service) |
| `{"cmd":"ble.probe","uuid":"…","secs":10}` | finds a peripheral advertising `uuid`, connects, discovers all services and characteristics, disconnects |
| `{"cmd":"restart"}` | reboots |

## Build, flash, test (VPS dev container)

```bash
. ~/export-esp.sh                         # LIBCLANG_PATH + Xtensa gcc (non-interactive shells)
cd spikes/watch-pipeline
cargo build --release                     # first build fetches ESP-IDF v5.5.5 into ~/.espressif
ELF=target/xtensa-esp32s3-espidf/release/watch-pipeline

homepc watch start --jtag                 # serial bridge + OpenOCD on the home PC
homepc watch flash "$ELF"                 # merged image, esptool runs on the PC
homepc watch cmd '{"cmd":"ping"}'
homepc watch gdb "$ELF" -ex "info threads"

python3 -m venv .venv && .venv/bin/pip install -r tests/requirements.txt
WATCH_PORT=$(homepc watch url) .venv/bin/pytest tests -v

homepc watch stop
```

### Phone as BLE peer

The phone-side peripheral available today is the debug-build Bluetooth spike service (branch
`feat/77-popup-web-bluetooth-spike`, service `7e570001-6f74-702d-7370-696b65000001`). Install that
debug APK on a phone, start the spike's GATT service from its screen
(`adb shell am start -a me.nagaev.veles.bluetoothspike.OPEN`), then:

```bash
WATCH_PORT=$(homepc watch url) WATCH_PEER_UUID=7e570001-6f74-702d-7370-696b65000001 \
  .venv/bin/pytest tests -v -k phone_peer
```

The OTP-06 sharing service replaces it as the peer once it exists.

## Findings

Evidence for the follow-up firmware RFC, from the first run (2026-10-23, ESP32-S3 QFN56 rev v0.2,
8 MB PSRAM, 16 MB flash). Builds ran in an Ubuntu 26.04 container on Docker Desktop (WSL2) that
mirrors the VPS dev image. Device steps ran on the home PC, through the same `watch.ps1` /
bridge code the VPS uses.

**Toolchain**
- espup 0.17.1 (`esp` channel) + ldproxy 0.3.5 + espflash 4.6.0. ESP-IDF **v5.5.5** builds fine
  with Ubuntu 26.04's **Python 3.14.4**; no separate Python was needed.
- First build 236 s, including fetching ESP-IDF into `~/.espressif`. Incremental builds take 32 s.
- esp-idf-svc 0.53 / esp-idf-sys 0.38 bindings expose the pre-5.3 name
  `esp_vfs_usb_serial_jtag_use_driver`; the 5.3+ `usb_serial_jtag_vfs_use_driver` is not bound.
- App image 772 KB. The merged image without padding is 818 KB, versus 16 MB padded, which
  matters for uploads over the tunnel. `espflash save-image` lays out its own partition table (one
  factory app spanning the flash) rather than the sdkconfig partition choice.

**Flash and console**
- Flashing must run next to the device. esptool can't drive the ESP32-S3's USB-Serial/JTAG reset
  over `rfc2217://` (it detects that interface by local USB IDs). Locally, esptool 5.6.0 at
  921 600 baud wrote and verified the image in 6 s (14 s including connect and reset).
- **Asserting DTR/RTS resets the chip through USB-Serial/JTAG.** esptool's `esp_rfc2217_server`
  raises both on every client connect, and pyserial raises them on open, so each test command
  rebooted the firmware. The home PC bridge now freezes the control lines, and resets are an
  explicit operation. Verified: uptime keeps growing across connections, including a client that
  requests DTR/RTS.
- The LILYGO factory firmware sleeps when the screen is off, and the USB link then stops
  answering (Windows: "device … not functioning"). It had to be woken with the crown before the
  first flash. Test firmware must not sleep while under test.

**BLE central (esp32-nimble 0.13, NimBLE)**
- An active 5 s scan from the watch saw 17 advertisers. Free heap after NimBLE init was 324 KB.
- Not yet run: `ble.probe` against the Android spike peripheral, which needs that debug APK on a
  phone.

**JTAG**
- Windows binds the USB-JTAG interface (MI_02) to the in-box `winusb.inf` without a device
  interface GUID. OpenOCD's libusb then finds the device but fails with `LIBUSB_ERROR_NOT_FOUND`.
  The home PC installer registers a GUID; a gdb attach is still to be confirmed after that.

`pytest tests -v` through the bridge: 3 passed, 1 skipped (the phone peer).
