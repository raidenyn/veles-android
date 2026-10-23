"""Pipeline proof: remote serial round trip and the watch acting as BLE central."""

import pytest

SPIKE_SERVICE = "7e570001-6f74-702d-7370-696b65000001"


def test_ping(watch):
    reply = watch.cmd({"cmd": "ping"})
    assert reply["ok"] is True
    assert reply["fw"]
    assert reply["free_heap"] > 0


def test_unknown_command_is_rejected(watch):
    reply = watch.cmd({"cmd": "nope"})
    assert reply["ok"] is False


def test_ble_scan_sees_advertisers(watch):
    reply = watch.cmd({"cmd": "ble.scan", "secs": 5}, timeout=20)
    assert reply["ok"] is True
    assert reply["count"] >= 1, "no BLE advertisers around the watch at all"


def test_ble_finds_and_probes_phone_peer(watch, peer_uuid):
    if not peer_uuid:
        pytest.skip("start the Veles Bluetooth spike on a phone and set WATCH_PEER_UUID=" + SPIKE_SERVICE)
    scan = watch.cmd({"cmd": "ble.scan", "secs": 10, "uuid": peer_uuid}, timeout=25)
    assert scan["ok"] is True and scan["count"] >= 1, f"no peripheral advertising {peer_uuid}"

    probe = watch.cmd({"cmd": "ble.probe", "uuid": peer_uuid, "secs": 10}, timeout=40)
    assert probe["ok"] is True, probe
    services = {s["uuid"].lower(): s for s in probe["services"]}
    assert peer_uuid.lower() in services, f"connected, but {peer_uuid} not discovered: {list(services)}"
