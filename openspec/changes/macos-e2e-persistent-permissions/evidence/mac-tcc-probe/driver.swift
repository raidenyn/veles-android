import Foundation

// Launched through LaunchServices (open) so its bundle identity is the responsible process for
// TCC. Spawns the real veles-native-host as a child, drives hello -> start_scan -> stop -> shutdown
// over the native-messaging framing and records the host's responses.
//
// argv: HOST_PATH RESULT_JSON [ORIGIN]

let args = CommandLine.arguments
guard args.count >= 3 else {
    FileHandle.standardError.write(Data("usage: driver HOST_PATH RESULT_JSON [ORIGIN]\n".utf8))
    exit(64)
}
let hostPath = args[1]
let resultPath = args[2]
let origin = args.count > 3 ? args[3] : "chrome-extension://ppfjeblflhibkohilcnholalpgbcacng/"
let service = "0000fff0-0000-1000-8000-00805f9b34fb"
let deadline = Date().addingTimeInterval(30)

DispatchQueue.global().asyncAfter(deadline: .now() + 32) {
    FileHandle.standardError.write(Data("driver: watchdog fired\n".utf8))
    exit(9)
}

let process = Process()
process.executableURL = URL(fileURLWithPath: hostPath)
process.arguments = [origin, "--parent-window=0"]
let input = Pipe()
let output = Pipe()
let errors = Pipe()
process.standardInput = input
process.standardOutput = output
process.standardError = errors

func send(_ object: [String: Any]) throws {
    let data = try JSONSerialization.data(withJSONObject: object, options: [.sortedKeys])
    var length = UInt32(data.count).littleEndian
    let header = withUnsafeBytes(of: &length) { Data($0) }
    input.fileHandleForWriting.write(header)
    input.fileHandleForWriting.write(data)
}

func readExactly(_ count: Int) -> Data? {
    var buffer = Data()
    while buffer.count < count {
        let chunk = output.fileHandleForReading.readData(ofLength: count - buffer.count)
        if chunk.isEmpty { return nil }
        buffer.append(chunk)
    }
    return buffer
}

func receive() -> [String: Any]? {
    guard let header = readExactly(4) else { return nil }
    let length = header.withUnsafeBytes { $0.loadUnaligned(fromByteOffset: 0, as: UInt32.self) }
    guard let body = readExactly(Int(UInt32(littleEndian: length))) else { return nil }
    return try? JSONSerialization.jsonObject(with: body) as? [String: Any]
}

func until(_ types: Set<String>) -> [[String: Any]] {
    var seen: [[String: Any]] = []
    while Date() < deadline {
        guard let message = receive() else { return seen }
        seen.append(message)
        if let type = message["type"] as? String, types.contains(type) { return seen }
    }
    return seen
}

var transcript: [[String: Any]] = []
var exitCode = 0
do {
    try process.run()
    try send(["envelope_version": 1, "type": "hello", "request_id": "tcc-hello", "api_versions": [1]])
    transcript += until(["ready", "error"])
    try send([
        "envelope_version": 1, "api_version": 1, "type": "start_scan",
        "request_id": "tcc-scan", "service_uuid": service,
    ])
    let scan = until(["scan_state", "error"])
    transcript += scan
    if let handle = scan.first(where: { $0["type"] as? String == "scan_state" })?["scan_handle"] as? String {
        try send([
            "envelope_version": 1, "api_version": 1, "type": "stop_scan",
            "request_id": "tcc-stop", "scan_handle": handle,
        ])
        transcript += until(["scan_state", "error"])
    }
    try send(["envelope_version": 1, "api_version": 1, "type": "shutdown", "request_id": "tcc-shutdown"])
    transcript += until(["shutdown_complete", "error"])
    input.fileHandleForWriting.closeFile()
    process.waitUntilExit()
    exitCode = Int(process.terminationStatus)
} catch {
    transcript.append(["driver_error": String(describing: error)])
    exitCode = 70
}

let stderrText = String(data: errors.fileHandleForReading.readDataToEndOfFile(), encoding: .utf8) ?? ""
let result: [String: Any] = [
    "origin": origin,
    "host_path": hostPath,
    "host_exit": exitCode,
    "host_stderr": stderrText,
    "bundle_id": Bundle.main.bundleIdentifier ?? "",
    "bundle_path": Bundle.main.bundlePath,
    "transcript": transcript,
    "finished_utc": ISO8601DateFormatter().string(from: Date()),
]
if let data = try? JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys]) {
    try? data.write(to: URL(fileURLWithPath: resultPath))
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
}
exit(Int32(exitCode))
