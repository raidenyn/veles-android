//! Disposable pipeline-proof firmware for the LILYGO T-Watch-S3 (research
//! artifact, not the RFC's Appendix-A `watch/` layout).
//!
//! It exists to prove that an agent on the VPS can build, flash, talk to,
//! debug and BLE-test the physical watch through the home PC. It speaks a
//! line-based JSON test-hook protocol on the USB-Serial/JTAG console:
//!
//! ```text
//! -> {"cmd":"ping"}
//! <- @@{"ok":true,"fw":"0.1.0","uptime_ms":1234,"free_heap":234567}
//! ```
//!
//! Every reply is one line starting with `@@`, so hosts can pick replies out
//! of the log output that shares the console. Commands: `ping`, `ble.scan`,
//! `ble.probe`, `restart` (see `handle`).

use std::collections::BTreeMap;
use std::io::{BufRead, Write};

use anyhow::{anyhow, Context};
use esp32_nimble::utilities::BleUuid;
use esp32_nimble::{BLEDevice, BLEScan};
use esp_idf_svc::hal::task::block_on;
use esp_idf_svc::sys;
use serde_json::{json, Value};

const FW: &str = env!("CARGO_PKG_VERSION");
const REPLY_PREFIX: &str = "@@";
const MAX_SCAN_SECS: u64 = 30;

fn main() {
    sys::link_patches();
    esp_idf_svc::log::EspLogger::initialize_default();
    install_console_driver();

    log::info!("watch-pipeline {FW} ready");
    reply(json!({"event": "ready", "fw": FW}));

    for line in std::io::stdin().lock().lines() {
        let Ok(line) = line else { continue };
        let line = line.trim();
        if line.is_empty() {
            continue;
        }
        let response = match serde_json::from_str::<Value>(line) {
            Ok(request) => handle(&request).unwrap_or_else(|e| json!({"ok": false, "error": format!("{e:#}")})),
            Err(e) => json!({"ok": false, "error": format!("bad json: {e}")}),
        };
        reply(response);
    }
}

/// Without the driver, reads from the USB-Serial/JTAG console return nothing;
/// with it installed and routed through VFS, `stdin` blocks per line.
fn install_console_driver() {
    let mut config = sys::usb_serial_jtag_driver_config_t {
        tx_buffer_size: 1024,
        rx_buffer_size: 1024,
    };
    unsafe {
        sys::esp!(sys::usb_serial_jtag_driver_install(&mut config)).expect("usb_serial_jtag driver");
        // ESP-IDF v5.5's bindings expose the pre-5.3 name of this call.
        sys::esp_vfs_usb_serial_jtag_use_driver();
        // Hosts send `\n`; keep it as the line terminator.
        sys::esp_vfs_dev_usb_serial_jtag_set_rx_line_endings(sys::esp_line_endings_t_ESP_LINE_ENDINGS_LF);
    }
}

fn reply(value: Value) {
    let mut out = std::io::stdout().lock();
    let _ = writeln!(out, "{REPLY_PREFIX}{value}");
    let _ = out.flush();
}

fn handle(request: &Value) -> anyhow::Result<Value> {
    let cmd = request["cmd"].as_str().ok_or_else(|| anyhow!("missing \"cmd\""))?;
    match cmd {
        "ping" => Ok(json!({
            "ok": true,
            "fw": FW,
            "uptime_ms": unsafe { sys::esp_timer_get_time() } / 1000,
            "free_heap": unsafe { sys::esp_get_free_heap_size() },
        })),
        "ble.scan" => {
            let secs = scan_secs(request, 5)?;
            let filter = request["uuid"].as_str().map(parse_uuid).transpose()?;
            let devices = block_on(ble_scan(secs, filter))?;
            Ok(json!({"ok": true, "count": devices.len(), "devices": devices}))
        }
        "ble.probe" => {
            let uuid = request["uuid"].as_str().ok_or_else(|| anyhow!("ble.probe needs \"uuid\""))?;
            let secs = scan_secs(request, 10)?;
            block_on(ble_probe(parse_uuid(uuid)?, secs))
        }
        "restart" => {
            reply(json!({"ok": true, "restarting": true}));
            unsafe { sys::esp_restart() }
        }
        other => Err(anyhow!("unknown cmd {other:?}")),
    }
}

fn scan_secs(request: &Value, default: u64) -> anyhow::Result<u64> {
    let secs = request["secs"].as_u64().unwrap_or(default);
    if secs == 0 || secs > MAX_SCAN_SECS {
        return Err(anyhow!("secs must be 1..={MAX_SCAN_SECS}"));
    }
    Ok(secs)
}

fn parse_uuid(text: &str) -> anyhow::Result<BleUuid> {
    BleUuid::from_uuid128_string(text).map_err(|e| anyhow!("bad uuid {text:?}: {e:?}"))
}

fn new_scan() -> BLEScan {
    let mut scan = BLEScan::new();
    scan.active_scan(true).interval(100).window(99);
    scan
}

/// Advertisers seen during `secs`, merged by address (advertising and scan
/// response reports arrive separately); with `filter`, only those advertising
/// that service.
async fn ble_scan(secs: u64, filter: Option<BleUuid>) -> anyhow::Result<Vec<Value>> {
    let device = BLEDevice::take();
    let mut seen: BTreeMap<String, Value> = BTreeMap::new();
    new_scan()
        .start(device, (secs * 1000) as i32, |dev, data| {
            let entry = seen.entry(dev.addr().to_string()).or_insert_with(|| {
                json!({"addr": dev.addr().to_string(), "rssi": dev.rssi(), "name": null, "services": [], "match": false})
            });
            if let Some(name) = data.name() {
                entry["name"] = json!(name.to_string());
            }
            for uuid in data.service_uuids() {
                let uuid = uuid.to_string();
                let services = entry["services"].as_array_mut().expect("services array");
                if !services.iter().any(|s| s == &uuid) {
                    services.push(json!(uuid));
                }
            }
            if filter.is_some_and(|f| data.is_advertising_service(&f)) {
                entry["match"] = json!(true);
            }
            None::<()>
        })
        .await
        .map_err(|e| anyhow!("scan failed: {e:?}"))?;
    Ok(seen
        .into_values()
        .filter(|d| filter.is_none() || d["match"] == json!(true))
        .collect())
}

/// Real-radio central round trip: find a peripheral advertising `uuid`,
/// connect, discover all services and characteristics, disconnect.
async fn ble_probe(uuid: BleUuid, secs: u64) -> anyhow::Result<Value> {
    let device = BLEDevice::take();
    let found = new_scan()
        .start(device, (secs * 1000) as i32, |dev, data| data.is_advertising_service(&uuid).then_some(*dev))
        .await
        .map_err(|e| anyhow!("scan failed: {e:?}"))?
        .with_context(|| format!("no peripheral advertising {uuid} within {secs}s"))?;

    let mut client = device.new_client();
    client.connect(&found.addr()).await.map_err(|e| anyhow!("connect failed: {e:?}"))?;
    let mut services = Vec::new();
    for service in client.get_services().await.map_err(|e| anyhow!("service discovery failed: {e:?}"))? {
        let service_uuid = service.uuid().to_string();
        let mut characteristics = Vec::new();
        for c in service.get_characteristics().await.map_err(|e| anyhow!("characteristic discovery failed: {e:?}"))? {
            characteristics.push(json!({
                "uuid": c.uuid().to_string(),
                "read": c.can_read(), "write": c.can_write(),
                "write_no_response": c.can_write_no_response(),
                "notify": c.can_notify(), "indicate": c.can_indicate(),
            }));
        }
        services.push(json!({"uuid": service_uuid, "characteristics": characteristics}));
    }
    client.disconnect().map_err(|e| anyhow!("disconnect failed: {e:?}"))?;

    Ok(json!({"ok": true, "addr": found.addr().to_string(), "rssi": found.rssi(), "services": services}))
}
