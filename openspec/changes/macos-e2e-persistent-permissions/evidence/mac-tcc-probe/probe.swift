import CoreBluetooth
import Foundation

// Read-only TCC probe: creates one CBCentralManager (which is what asks macOS for Bluetooth
// authorization), waits for the authorization/state to settle, and records what the OS reports.
// The bundle identity and signing are the canonical test identity; the executable is only a probe.

let args = CommandLine.arguments
let outPath = args.count > 1 ? args[1] : "/tmp/veles-tcc-probe.json"
let deadlineSeconds = args.count > 2 ? (Double(args[2]) ?? 240.0) : 240.0

func authName(_ value: CBManagerAuthorization) -> String {
    switch value {
    case .notDetermined: return "not_determined"
    case .restricted: return "restricted"
    case .denied: return "denied"
    case .allowedAlways: return "allowed_always"
    @unknown default: return "unknown"
    }
}

func stateName(_ value: CBManagerState) -> String {
    switch value {
    case .unknown: return "unknown"
    case .resetting: return "resetting"
    case .unsupported: return "unsupported"
    case .unauthorized: return "unauthorized"
    case .poweredOff: return "powered_off"
    case .poweredOn: return "powered_on"
    @unknown default: return "unknown_state"
    }
}

final class Delegate: NSObject, CBCentralManagerDelegate {
    var lastState: CBManagerState = .unknown
    func centralManagerDidUpdateState(_ central: CBCentralManager) {
        lastState = central.state
    }
}

let started = Date()
let before = CBManager.authorization
let delegate = Delegate()
let manager = CBCentralManager(
    delegate: delegate,
    queue: nil,
    options: [CBCentralManagerOptionShowPowerAlertKey: false]
)

let deadline = started.addingTimeInterval(deadlineSeconds)
while Date() < deadline {
    let settled = manager.state == .poweredOn || manager.state == .unauthorized
    if settled && CBManager.authorization != .notDetermined { break }
    RunLoop.current.run(until: Date().addingTimeInterval(0.25))
}

let result: [String: Any] = [
    "probe_version": "1",
    "bundle_id": Bundle.main.bundleIdentifier ?? "",
    "bundle_path": Bundle.main.bundlePath,
    "authorization_before": authName(before),
    "authorization_after": authName(CBManager.authorization),
    "central_state": stateName(manager.state),
    "last_delegate_state": stateName(delegate.lastState),
    "waited_seconds": Date().timeIntervalSince(started),
    "finished_utc": ISO8601DateFormatter().string(from: Date()),
]
let data = try JSONSerialization.data(withJSONObject: result, options: [.sortedKeys, .prettyPrinted])
try data.write(to: URL(fileURLWithPath: outPath))
FileHandle.standardOutput.write(data)
FileHandle.standardOutput.write("\n".data(using: .utf8)!)
