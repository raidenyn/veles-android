#!/usr/bin/env python3
"""Tests for the read-only platform adapter identity probes (no device, no OS calls)."""
import json
import re
import sys
import unittest
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from accdrv import probes  # noqa: E402
from accdrv.errors import Blocked  # noqa: E402

HEX64 = re.compile(r"^[0-9a-f]{64}$")
WINDOWS_RADIO_ID = "USB\\VID_8087&PID_0026\\5&2A1B3C4D&0&10"
WINDOWS_PNP = [
    {"Status": "OK", "FriendlyName": "Intel(R) Wireless Bluetooth(R)", "InstanceId": WINDOWS_RADIO_ID},
    {"Status": "OK", "FriendlyName": "Bluetooth Device (Personal Area Network)",
     "InstanceId": "BTH\\MS_BTHPAN\\7&1F2E3D4C&0&2"},
    {"Status": "Error", "FriendlyName": "Removed radio", "InstanceId": "USB\\VID_0A12&PID_0001\\5&DEADBEEF&0&1"},
    {"Status": "OK", "FriendlyName": "Phone", "InstanceId": "BTHENUM\\DEV_001122334455\\7&ABCDEF&0&0000"},
]
MAC_ADDRESS = "10:20:30:40:50:60"
MAC_BT = {
    "SPBluetoothDataType": [
        {
            "controller_properties": {
                "controller_address": MAC_ADDRESS,
                "controller_chipset": "BCM_4387",
                "controller_transport": "PCIe",
                "controller_firmwareVersion": "23.5.102",
                "controller_vendorID": "0x004C",
                "controller_productID": "0x0001",
            },
            "devices_list": [{"device_address": "AA:BB:CC:DD:EE:FF", "device_connected": "attrib_Yes"}],
        }
    ]
}


def dumps(value):
    return json.dumps(value, indent=2)


class WindowsAdapterProbeTests(unittest.TestCase):
    def test_single_enabled_radio_is_hashed_and_the_raw_instance_id_is_not_recorded(self):
        out = probes.parse_windows_pnp(dumps(WINDOWS_PNP))
        self.assertEqual(out["kind"], "bluetooth")
        self.assertEqual(out["name"], "Intel(R) Wireless Bluetooth(R)")
        self.assertRegex(out["id_sha256"], HEX64)
        text = json.dumps(out)
        self.assertNotIn(WINDOWS_RADIO_ID, text)
        self.assertNotIn("VID_8087", text)
        self.assertNotIn("5&2A1B3C4D", text)

    def test_hash_follows_the_controller_identity(self):
        a = probes.parse_windows_pnp(dumps(WINDOWS_PNP))
        b = probes.parse_windows_pnp(dumps([{**WINDOWS_PNP[0], "InstanceId": "USB\\VID_8087&PID_0026\\OTHER"}]))
        self.assertEqual(probes.parse_windows_pnp(dumps(WINDOWS_PNP)), a)
        self.assertNotEqual(a["id_sha256"], b["id_sha256"])

    def test_single_object_json_is_accepted(self):
        out = probes.parse_windows_pnp(dumps(WINDOWS_PNP[0]))
        self.assertRegex(out["id_sha256"], HEX64)

    def test_only_paired_device_nodes_is_no_adapter(self):
        with self.assertRaisesRegex(Blocked, "no enabled Bluetooth adapter"):
            probes.parse_windows_pnp(dumps([WINDOWS_PNP[3]]))

    def test_two_radios_are_ambiguous(self):
        rows = [WINDOWS_PNP[0], {**WINDOWS_PNP[0], "InstanceId": "USB\\VID_0BDA&PID_8771\\9&1&0&2"}]
        with self.assertRaisesRegex(Blocked, "ambiguous"):
            probes.parse_windows_pnp(dumps(rows))

    def test_absent_or_empty_inventory_is_blocked(self):
        for text in ("", "[]", "null"):
            with self.assertRaises(Blocked, msg=text):
                probes.parse_windows_pnp(text)

    def test_malformed_inventory_is_blocked(self):
        for text in ("not-json", '["USB\\VID_1"]', '[{"Status":"OK"}]'):
            with self.assertRaises(Blocked, msg=text):
                probes.parse_windows_pnp(text)

    def test_name_is_bounded_and_control_characters_are_removed(self):
        row = {**WINDOWS_PNP[0], "FriendlyName": "X" * 300 + "\n\t"}
        out = probes.parse_windows_pnp(dumps([row]))
        self.assertLessEqual(len(out["name"]), probes.NAME_MAX)
        self.assertNotRegex(out["name"], r"[\x00-\x1f\x7f]")


class MacosAdapterProbeTests(unittest.TestCase):
    def test_controller_is_hashed_and_the_raw_address_is_not_recorded(self):
        out = probes.parse_macos_bluetooth(dumps(MAC_BT))
        self.assertEqual(out["kind"], "bluetooth")
        self.assertEqual(out["name"], "BCM_4387 (PCIe)")
        self.assertRegex(out["id_sha256"], HEX64)
        text = json.dumps(out)
        self.assertNotIn(MAC_ADDRESS, text)
        self.assertNotIn(MAC_ADDRESS.replace(":", ""), text)

    def test_address_case_is_normalized_so_the_identity_is_stable(self):
        upper = probes.parse_macos_bluetooth(dumps(MAC_BT))
        lower = json.loads(dumps(MAC_BT))
        lower["SPBluetoothDataType"][0]["controller_properties"]["controller_address"] = MAC_ADDRESS.lower()
        self.assertEqual(upper, probes.parse_macos_bluetooth(dumps(lower)))

    def test_absent_controller_is_blocked(self):
        for doc in ({"SPBluetoothDataType": []}, {"SPBluetoothDataType": [{"devices_list": []}]}, {}):
            with self.assertRaises(Blocked, msg=doc):
                probes.parse_macos_bluetooth(dumps(doc))

    def test_multiple_controllers_are_ambiguous(self):
        doc = json.loads(dumps(MAC_BT))
        doc["SPBluetoothDataType"].append(json.loads(dumps(MAC_BT))["SPBluetoothDataType"][0])
        with self.assertRaisesRegex(Blocked, "ambiguous"):
            probes.parse_macos_bluetooth(dumps(doc))

    def test_mixed_valid_and_malformed_controller_entry_is_blocked(self):
        doc = json.loads(dumps(MAC_BT))
        doc["SPBluetoothDataType"].append({})
        with self.assertRaisesRegex(Blocked, "controller_properties"):
            probes.parse_macos_bluetooth(dumps(doc))

    def test_malformed_or_missing_address_is_blocked(self):
        bad = json.loads(dumps(MAC_BT))
        del bad["SPBluetoothDataType"][0]["controller_properties"]["controller_address"]
        for text in ("not-json", "[]", dumps(bad)):
            with self.assertRaises(Blocked, msg=text):
                probes.parse_macos_bluetooth(text)

    def test_name_is_bounded(self):
        doc = json.loads(dumps(MAC_BT))
        doc["SPBluetoothDataType"][0]["controller_properties"]["controller_chipset"] = "C" * 300
        out = probes.parse_macos_bluetooth(dumps(doc))
        self.assertLessEqual(len(out["name"]), probes.NAME_MAX)


class AdapterContextTests(unittest.TestCase):
    def test_uses_the_injected_runner_for_the_platform_command(self):
        calls = []

        def run(argv):
            calls.append(argv)
            return 0, dumps(WINDOWS_PNP), ""

        out = probes.adapter_context("windows", run)
        self.assertRegex(out["id_sha256"], HEX64)
        self.assertEqual(calls[0][:3], ["powershell", "-NoProfile", "-NonInteractive"])

        def mac_run(argv):
            calls.append(argv)
            return 0, dumps(MAC_BT), ""

        out = probes.adapter_context("macos", mac_run)
        self.assertEqual(calls[-1], ["system_profiler", "SPBluetoothDataType", "-json"])
        self.assertRegex(out["id_sha256"], HEX64)

    def test_command_failure_is_blocked(self):
        with self.assertRaisesRegex(Blocked, "Bluetooth"):
            probes.adapter_context("windows", lambda argv: (1, "", "denied"))
        with self.assertRaisesRegex(Blocked, "Bluetooth"):
            probes.adapter_context("macos", lambda argv: (1, "", "denied"))

    def test_unsupported_platform_is_blocked(self):
        with self.assertRaises(Blocked):
            probes.adapter_context("linux", lambda argv: (0, "{}", ""))

    def test_bounded_runner_turns_a_launch_failure_into_blocked(self):
        with self.assertRaises(Blocked):
            probes.bounded_run(["/definitely/not/a/command"])


if __name__ == "__main__":
    unittest.main()
