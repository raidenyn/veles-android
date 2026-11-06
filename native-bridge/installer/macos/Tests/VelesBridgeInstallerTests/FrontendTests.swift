// Frontend tests for the macOS installer (OTP-25 Task 11).
//
// NOT RUN on the Linux authoring host (no Swift toolchain, no Xcode). They are written against the
// public behaviour of `App.swift` and must be run on macOS:
//
//     swift test --package-path native-bridge/installer/macos
//
// Nothing here signs, notarizes, launches the app, talks to Gatekeeper or starts the host.

import XCTest

@testable import VelesBridgeInstaller

/// Records every process start instead of making one, and answers with a canned result.
final class RecordingRunner: ProcessRunning, @unchecked Sendable {
    struct Call: Equatable {
        let executable: URL
        let arguments: [String]
    }

    private let lock = NSLock()
    private var recorded: [Call] = []
    private let result: ProcessResult

    init(result: ProcessResult) { self.result = result }

    var calls: [Call] {
        lock.lock()
        defer { lock.unlock() }
        return recorded
    }

    func run(executable: URL, arguments: [String]) async throws -> ProcessResult {
        lock.lock()
        recorded.append(Call(executable: executable, arguments: arguments))
        lock.unlock()
        return result
    }
}

/// One canonical, newline-terminated engine report, as `veles-bridge-maintenance` prints it.
func reportData(
    operation: String,
    state: String,
    priorUsable: Bool = true,
    failure: String? = nil,
    warnings: [String] = []
) -> Data {
    let failureJSON = failure.map { "\"\($0)\"" } ?? "null"
    let warningsJSON = "[" + warnings.map { "\"\($0)\"" }.joined(separator: ",") + "]"
    let text =
        "{\"operation\":\"\(operation)\",\"state\":\"\(state)\",\"prior_usable\":\(priorUsable),"
        + "\"active_payload\":null,\"failure\":\(failureJSON),\"warnings\":\(warningsJSON)}\n"
    return Data(text.utf8)
}

func result(_ status: Int32, _ output: Data) -> ProcessResult {
    ProcessResult(status: status, standardOutput: output, standardError: Data())
}

let hostileEngine = URL(
    fileURLWithPath:
        "/Users/Jörg Ünal ☃/Veles Bridge Installer.app/Contents/Resources/package/carrier.app/Contents/MacOS/veles-bridge-maintenance"
)
let hostilePackage = URL(
    fileURLWithPath: "/Users/Jörg Ünal ☃/it's $(touch pwned); `id` & \"x\" | y/package",
    isDirectory: true)

final class FrontendTests: XCTestCase {
    /// A path full of spaces, quotes, `$(...)`, backticks, `;`, `&`, `|` and non-ASCII characters
    /// reaches the engine as exactly one argv element, with no shell in between.
    func testUnicodePathsUseProcessArgumentsNotShell() async throws {
        let runner = RecordingRunner(result: result(0, reportData(operation: "install", state: "installed")))
        let client = MaintenanceClient(engine: hostileEngine, package: hostilePackage, runner: runner)

        _ = try await client.run("install")

        let calls = runner.calls
        XCTAssertEqual(calls.count, 1)
        XCTAssertEqual(calls[0].executable.path, hostileEngine.path)
        XCTAssertEqual(calls[0].arguments, ["install", "--package", hostilePackage.path])
        for shell in ["/bin/sh", "/bin/bash", "/bin/zsh", "/usr/bin/env"] {
            XCTAssertNotEqual(calls[0].executable.path, shell)
        }
        XCTAssertFalse(calls[0].arguments.contains("-c"))

        // The real runner: the same hostile text, passed to a real program, comes back verbatim and
        // is never evaluated.
        let marker = NSTemporaryDirectory() + "veles-must-not-exist-\(UUID().uuidString)"
        let hostile = ["$(touch \(marker))", "a b;c", "`id`", "é☃ ü"]
        let echoed = try await FoundationProcessRunner().run(
            executable: URL(fileURLWithPath: "/bin/echo"), arguments: hostile)
        XCTAssertEqual(echoed.status, 0)
        XCTAssertEqual(String(decoding: echoed.standardOutput, as: UTF8.self), hostile.joined(separator: " ") + "\n")
        XCTAssertFalse(FileManager.default.fileExists(atPath: marker))
    }

    /// `host-active` tells the user to close the connector tabs and offers Retry; it never offers to
    /// close anything itself, and it is a blocker, not a success.
    func testActiveHostShowsCloseTabsAndRetry() async throws {
        let runner = RecordingRunner(
            result: result(1, reportData(operation: "install", state: "installed", failure: "host-active")))
        let client = MaintenanceClient(engine: hostileEngine, package: hostilePackage, runner: runner)

        let report = try await client.run("install")
        let presentation = Presenter.present(report)

        XCTAssertEqual(report.failure, .hostActive)
        XCTAssertEqual(presentation.kind, .blocked)
        XCTAssertTrue(presentation.detail.localizedCaseInsensitiveContains("close every chrome tab"))
        XCTAssertTrue(presentation.detail.localizedCaseInsensitiveContains("retry"))
        XCTAssertEqual(presentation.actions, [.retry, .quit])
        XCTAssertEqual(presentation.priorInstallUsable, true)
        XCTAssertNotEqual(presentation.kind, .done)
        // No action exists that closes, kills or launches anything.
        XCTAssertEqual(FrontendAction.allCases.map(\.rawValue).sorted(), ["quit", "retry"])
    }

    /// A trust check that could not be made, or was refused, is never presented as Done - and neither
    /// is any report whose exit status, state or warnings contradict a success.
    func testTrustUnavailableNeverShowsDone() async throws {
        for operation in MaintenanceOperation.allCases {
            for failure in ["trust-unavailable", "trust-denied"] {
                // An inspection that produced a report exits 0; every other operation exits 1.
                let status: Int32 = operation == .inspect ? 0 : 1
                let runner = RecordingRunner(
                    result: result(
                        status, reportData(operation: operation.rawValue, state: "failed", failure: failure)))
                let client = MaintenanceClient(engine: hostileEngine, package: hostilePackage, runner: runner)
                let report = try await client.run(operation.rawValue)
                let presentation = Presenter.present(report)
                XCTAssertEqual(presentation.kind, .failed, "\(operation) \(failure)")
                XCTAssertNotEqual(presentation.kind, .done)
                XCTAssertFalse(presentation.title.localizedCaseInsensitiveContains("is installed"))
            }
        }
        let unavailable = Presenter.present(
            MaintenanceReport(operation: .install, state: "failed", priorUsable: false, failure: .trustUnavailable))
        XCTAssertEqual(unavailable.actions, [.retry, .quit], "trust-unavailable is retryable")
        XCTAssertEqual(unavailable.priorInstallUsable, false)

        // Not a success even without a failure code.
        for report in [
            MaintenanceReport(operation: .install, state: "damaged", priorUsable: false),
            MaintenanceReport(operation: .repair, state: "recovery-required", priorUsable: false),
            MaintenanceReport(operation: .uninstall, state: "uninstalled", priorUsable: false, warnings: [.cleanupPending]),
        ] {
            XCTAssertNotEqual(Presenter.present(report).kind, .done, "\(report)")
        }
        // Inspection is information, never Done.
        XCTAssertEqual(
            Presenter.present(MaintenanceReport(operation: .inspect, state: "installed", priorUsable: true)).kind,
            .info)

        // A success-looking report with the wrong exit status, or an engine that answered with
        // nothing or garbage, is an error - which also renders as a failure, never Done.
        let contradictions: [(Int32, Data)] = [
            (1, reportData(operation: "install", state: "installed")),
            (0, reportData(operation: "install", state: "failed", failure: "trust-unavailable")),
            (0, reportData(operation: "install", state: "installed", warnings: ["cleanup-pending"])),
            (2, Data()),
            (0, Data("{\"operation\":\"install\"}\n".utf8)),
            (0, Data(
                "{\"operation\":\"install\",\"state\":\"installed\",\"prior_usable\":true,\"active_payload\":null,\"failure\":null,\"warnings\":[],\"path\":\"/x\"}\n"
                    .utf8)),
        ]
        for (status, output) in contradictions {
            let client = MaintenanceClient(
                engine: hostileEngine, package: hostilePackage, runner: RecordingRunner(result: result(status, output)))
            do {
                _ = try await client.run("install")
                XCTFail("a contradictory or malformed answer must not decode as a result")
            } catch {
                XCTAssertEqual(Presenter.present(error).kind, .failed)
            }
        }
    }

    /// Maintenance only ever starts the engine inside the signed bundle: never the Bluetooth host,
    /// never a path under the installed `versions/` tree, and the UI has no launch action.
    func testMaintenanceDoesNotLaunchHost() async throws {
        let answers: [(MaintenanceOperation, String)] = [
            (.inspect, "installed"), (.install, "installed"), (.repair, "repaired"),
            (.rollback, "rolled-back"), (.uninstall, "uninstalled"),
        ]
        let runner = RecordingRunner(result: result(0, Data()))
        for (operation, state) in answers {
            let one = RecordingRunner(result: result(0, reportData(operation: operation.rawValue, state: state)))
            let client = MaintenanceClient(engine: hostileEngine, package: hostilePackage, runner: one)
            _ = try await client.run(operation.rawValue)
            XCTAssertEqual(one.calls.count, 1)
            XCTAssertEqual(one.calls[0].executable.path, hostileEngine.path)
            XCTAssertEqual(one.calls[0].arguments.first, operation.rawValue)
            XCTAssertEqual(
                one.calls[0].arguments.contains("--package"), operation.needsPackage,
                "only install and repair take a package")
            XCTAssertFalse(one.calls[0].arguments.joined().contains("veles-native-host"))
        }

        // Pointing the client at the host (or any other program) is refused before anything starts.
        for stranger in [
            "/Users/u/Library/Application Support/Veles/NativeBridge/versions/r/Veles Native Bridge.app/Contents/MacOS/veles-native-host",
            "/bin/sh",
            "/Users/u/Veles Bridge Installer.app/Contents/MacOS/VelesBridgeInstaller",
        ] {
            let client = MaintenanceClient(
                engine: URL(fileURLWithPath: stranger), package: hostilePackage, runner: runner)
            do {
                _ = try await client.run("uninstall")
                XCTFail("must refuse to start \(stranger)")
            } catch {
                XCTAssertEqual(error as? ClientError, .engineUnavailable)
            }
        }
        XCTAssertTrue(runner.calls.isEmpty, "nothing was started")

        // Operation words outside the closed set never reach a process.
        for word in ["update", "launch", "open", "sh -c id", "--package", ""] {
            do {
                _ = try await MaintenanceClient(engine: hostileEngine, package: hostilePackage, runner: runner)
                    .run(word)
                XCTFail("unknown operation \(word) must be refused")
            } catch {
                XCTAssertEqual(error as? ClientError, .unknownOperation)
            }
        }
        XCTAssertTrue(runner.calls.isEmpty)

        // The engine is located inside this app's own bundle resources, never in the install root.
        let resources = URL(fileURLWithPath: "/Volumes/Veles Bridge/Veles Bridge Installer.app/Contents/Resources")
        let location = EngineLocation.resolve(resourceURL: resources)
        XCTAssertEqual(
            location.engine.path,
            resources.path + "/package/carrier.app/Contents/MacOS/veles-bridge-maintenance")
        XCTAssertEqual(location.package.path, resources.path + "/package")
        XCTAssertFalse(location.engine.path.contains("Application Support"))
    }

    // MARK: - Report decoding is strict (extra coverage)

    func testReportDecoderIsClosedAndBounded() throws {
        let good = reportData(operation: "install", state: "installed", warnings: ["leftover-files"])
        XCTAssertEqual(try MaintenanceReport.decode(good).warnings, [.leftoverFiles])

        var oversized = Data(repeating: 0x20, count: MaintenanceReport.maxBytes)
        oversized.append(0x0A)
        let rejected: [Data] = [
            Data(),
            Data(good.dropLast()),  // no terminating newline
            good + Data("\n".utf8),  // a second document / trailing line
            oversized,
            reportData(operation: "install", state: "Installed"),  // not a bounded lower-case word
            reportData(operation: "install", state: "installed", failure: "made-up-code"),
            reportData(operation: "install", state: "installed", warnings: ["cleanup-pending", "cleanup-pending"]),
            reportData(operation: "launch", state: "installed"),
        ]
        for data in rejected {
            XCTAssertThrowsError(try MaintenanceReport.decode(data))
        }
    }

    func testTranslocatedBundlePathIsRecognisedAndOnlyEverASource() {
        let translocated = URL(
            fileURLWithPath:
                "/private/var/folders/ab/xyz/T/AppTranslocation/0000/d/Veles Bridge Installer.app/Contents/Resources")
        XCTAssertTrue(EngineLocation.isTranslocated(EngineLocation.resolve(resourceURL: translocated).engine))
        XCTAssertFalse(
            EngineLocation.isTranslocated(
                URL(fileURLWithPath: "/Volumes/Veles/Veles Bridge Installer.app/Contents/Resources")))
        // The only place a bundle path can go is `--package`, which the engine authenticates and
        // copies from; it is never an argument of any other operation.
        let package = EngineLocation.resolve(resourceURL: translocated).package
        XCTAssertTrue(package.path.hasSuffix("/package"))
    }
}
