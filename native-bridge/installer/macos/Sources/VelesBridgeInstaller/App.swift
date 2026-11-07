// Veles Bridge Installer - native macOS frontend (OTP-25 Task 11).
//
// This app is a thin renderer. It owns no transaction logic, no trust decision and no
// filesystem policy: it starts the signed engine that ships inside its own bundle
// (`carrier.app/Contents/MacOS/veles-bridge-maintenance`) with a structured argument vector and
// renders the single bounded report that engine prints (`InstallReport`, Task 10 CLI contract).
//
// Hard rules, each enforced by `scripts/test_macos_package.py` and `FrontendTests.swift`:
//  * No shell: the engine is started with `Process.executableURL` + `arguments`. Paths with spaces,
//    quotes, `$(...)` or non-ASCII characters are plain argv elements.
//  * The only program this app may start is the engine inside the signed bundle. It never launches,
//    signals or inspects the Bluetooth host, Chrome, or any other process.
//  * The installer does not own Bluetooth. The usage text lives on the host bundle; this app has no
//    CoreBluetooth code and requests no entitlement.
//  * No privileged helper, no launch agent, no system path, no TCC change. Everything the engine
//    installs lands in the fixed per-user layout the engine resolves itself; this app never passes it
//    a destination, so an App-Translocated bundle path can never become the installed host path.
//  * "Done" is shown only for a mutating operation that the engine reports as complete, with a
//    consistent exit status and no cleanup left pending. Anything else - including a trust check that
//    could not be made - is a failure or a blocker, never a success.

import Foundation
#if canImport(SwiftUI)
import SwiftUI
#endif

// MARK: - Engine vocabulary (mirrors `InstallReport` in `src/model.rs`)

/// The closed set of engine operation words. `update` is `install` with a newer package.
enum MaintenanceOperation: String, Decodable, CaseIterable, Sendable {
    case inspect = "inspect"
    case install = "install"
    case repair = "repair"
    case rollback = "rollback"
    case uninstall = "uninstall"

    /// Only `install` and `repair` take (exactly) `--package <absolute directory>`.
    var needsPackage: Bool { self == .install || self == .repair }
    var mutates: Bool { self != .inspect }
}

/// Stable failure vocabulary shared with both native frontends.
enum FailureCode: String, Decodable, CaseIterable, Sendable {
    case invalidInput = "invalid-input"
    case originRejected = "origin-rejected"
    case invalidPackage = "invalid-package"
    case trustDenied = "trust-denied"
    case trustUnavailable = "trust-unavailable"
    case policyDenied = "policy-denied"
    case unsupportedTarget = "unsupported-target"
    case busy = "busy"
    case hostActive = "host-active"
    case inspectionDenied = "inspection-denied"
    case foreignState = "foreign-state"
    case unsafePath = "unsafe-path"
    case io = "io"
    case recoveryRequired = "recovery-required"
    case unsupportedState = "unsupported-state"
    case noRollback = "no-rollback"
    case repairPackageRequired = "repair-package-required"
    case cleanupPending = "cleanup-pending"
}

/// Additive, bounded guidance attached to a report that is not a plain success.
enum WarningCode: String, Decodable, CaseIterable, Sendable {
    case compatibilityWindow = "compatibility-window"
    case leftoverFiles = "leftover-files"
    case cleanupPending = "cleanup-pending"
}

/// States after which a mutating operation has not settled (see `exit_code` in `src/cli.rs`).
private let unsettledStates: Set<String> = ["damaged", "foreign", "recovery-required", "failed"]

/// The engine's one report. It mirrors the `InstallReport` fields and nothing else: there is no
/// path, payload, environment value or free text for a host to smuggle into the UI.
struct MaintenanceReport: Equatable, Decodable, Sendable {
    /// Hard bound on the report document (`MAX_REPORT_BYTES` in `src/cli.rs`).
    static let maxBytes = 64 * 1024
    /// At most this many warning codes (`MAX_REPORT_WARNINGS` in `src/model.rs`).
    static let maxWarnings = 8
    static let maxStateLength = 64
    /// The closed key set. An unknown field is a protocol violation, not something to ignore.
    static let allowedKeys: Set<String> = [
        "operation", "state", "prior_usable", "active_payload", "failure", "warnings",
    ]

    let operation: MaintenanceOperation
    let state: String
    let priorUsable: Bool
    let activePayload: String?
    let failure: FailureCode?
    let warnings: [WarningCode]

    init(
        operation: MaintenanceOperation,
        state: String,
        priorUsable: Bool,
        activePayload: String? = nil,
        failure: FailureCode? = nil,
        warnings: [WarningCode] = []
    ) {
        self.operation = operation
        self.state = state
        self.priorUsable = priorUsable
        self.activePayload = activePayload
        self.failure = failure
        self.warnings = warnings
    }

    private struct Key: CodingKey {
        let stringValue: String
        var intValue: Int? { nil }
        init?(stringValue: String) { self.stringValue = stringValue }
        init?(intValue: Int) { nil }
    }

    init(from decoder: Decoder) throws {
        let container = try decoder.container(keyedBy: Key.self)
        for key in container.allKeys where !Self.allowedKeys.contains(key.stringValue) {
            throw DecodingError.dataCorruptedError(
                forKey: key, in: container, debugDescription: "unknown report field")
        }
        func key(_ name: String) -> Key { Key(stringValue: name)! }

        operation = try container.decode(MaintenanceOperation.self, forKey: key("operation"))
        let state = try container.decode(String.self, forKey: key("state"))
        guard Self.isValidState(state) else {
            throw DecodingError.dataCorruptedError(
                forKey: key("state"), in: container, debugDescription: "state is not a bounded word")
        }
        self.state = state
        priorUsable = try container.decode(Bool.self, forKey: key("prior_usable"))

        let payload = try container.decodeIfPresent(String.self, forKey: key("active_payload"))
        if let payload, !Self.isDigest(payload) {
            throw DecodingError.dataCorruptedError(
                forKey: key("active_payload"), in: container,
                debugDescription: "payload is not a SHA-256 digest")
        }
        activePayload = payload
        failure = try container.decodeIfPresent(FailureCode.self, forKey: key("failure"))

        let warnings = try container.decodeIfPresent([WarningCode].self, forKey: key("warnings")) ?? []
        guard warnings.count <= Self.maxWarnings, Set(warnings).count == warnings.count else {
            throw DecodingError.dataCorruptedError(
                forKey: key("warnings"), in: container,
                debugDescription: "too many or duplicate warnings")
        }
        self.warnings = warnings
    }

    private static func isValidState(_ value: String) -> Bool {
        guard !value.isEmpty, value.utf8.count <= maxStateLength else { return false }
        return value.unicodeScalars.allSatisfy {
            ("a"..."z").contains($0) || ("0"..."9").contains($0) || $0 == "-"
        }
    }

    private static func isDigest(_ value: String) -> Bool {
        value.utf8.count == 64
            && value.unicodeScalars.allSatisfy { ("0"..."9").contains($0) || ("a"..."f").contains($0) }
    }

    /// Strict decode of the engine's stdout: one canonical JSON document followed by exactly one
    /// newline, within the size bound. Anything else is `ClientError.malformedReport`.
    static func decode(_ data: Data) throws -> MaintenanceReport {
        guard !data.isEmpty, data.count <= maxBytes else { throw ClientError.malformedReport }
        guard data.last == 0x0A else { throw ClientError.malformedReport }
        let body = data.dropLast()
        guard !body.contains(0x0A) else { throw ClientError.malformedReport }
        do {
            return try JSONDecoder().decode(MaintenanceReport.self, from: Data(body))
        } catch {
            throw ClientError.malformedReport
        }
    }

    /// A same-version `install` is the repair of that release (`Operation::Install` reruns repair), so the engine
    /// answers it with a `repair` report. Every other requested word must be answered by itself.
    func answers(requested: MaintenanceOperation) -> Bool {
        requested == .install && operation == .repair
    }

    /// Whether the operation reached a settled outcome the engine would exit 0 for. This mirrors
    /// `exit_code` in `src/cli.rs`; the client treats any disagreement as a failed run.
    var expectedExitStatus: Int32 {
        if warnings.contains(.cleanupPending) || failure == .cleanupPending || failure == .unsupportedState {
            return 1
        }
        if operation == .inspect { return 0 }
        return (failure != nil || unsettledStates.contains(state)) ? 1 : 0
    }
}

// MARK: - Running the engine

struct ProcessResult: Equatable, Sendable {
    let status: Int32
    let standardOutput: Data
    let standardError: Data
}

/// The seam between the client and the OS. Production uses `FoundationProcessRunner`; tests record.
protocol ProcessRunning: Sendable {
    func run(executable: URL, arguments: [String]) async throws -> ProcessResult
}

enum ClientError: Error, Equatable, Sendable {
    case unknownOperation
    case packageRequired
    case invalidPackagePath
    case engineUnavailable
    case launchFailed
    case terminatedBySignal
    case outputTooLarge
    case malformedReport
    case operationMismatch
    case exitStatusDisagrees
    /// The engine did not finish within the deadline. The app stops waiting; it never signals the engine.
    case timedOut
}

/// Where the signed engine and the authenticated package live inside this app bundle.
enum EngineLocation {
    static let engineName = "veles-bridge-maintenance"
    /// The engine is the executable of the sealed policy-carrier bundle shipped in the package.
    static let engineRelativePath = "carrier.app/Contents/MacOS/veles-bridge-maintenance"
    static let packageDirectoryName = "package"

    static func resolve(resourceURL: URL) -> (engine: URL, package: URL) {
        let package = resourceURL.appendingPathComponent(packageDirectoryName, isDirectory: true)
            .standardizedFileURL
        let engine = package.appendingPathComponent(engineRelativePath, isDirectory: false)
            .standardizedFileURL
        return (engine, package)
    }

    /// Gatekeeper may App-Translocate a quarantined app to a randomized read-only mount. The engine
    /// copies from the package and installs into its own fixed per-user layout, so such a path is
    /// only ever a *source*. This is surfaced to the user, never recorded or passed as a destination.
    static func isTranslocated(_ url: URL) -> Bool {
        url.standardizedFileURL.pathComponents.contains("AppTranslocation")
    }
}

/// Starts the engine and decodes its report. It will not start anything but the engine.
struct MaintenanceClient: Sendable {
    let engine: URL
    let package: URL?
    let runner: ProcessRunning

    /// Run one operation word: `inspect`, `install`, `repair`, `rollback` or `uninstall`.
    func run(_ operation: String) async throws -> MaintenanceReport {
        guard let operation = MaintenanceOperation(rawValue: operation) else {
            throw ClientError.unknownOperation
        }
        // The program started is always the engine inside the signed bundle, by absolute path.
        guard engine.isFileURL, engine.path.hasPrefix("/"),
            engine.lastPathComponent == EngineLocation.engineName
        else { throw ClientError.engineUnavailable }

        var arguments = [operation.rawValue]
        if operation.needsPackage {
            guard let package else { throw ClientError.packageRequired }
            guard package.isFileURL, package.path.hasPrefix("/"), !package.path.hasPrefix("/-") else {
                throw ClientError.invalidPackagePath
            }
            arguments += ["--package", package.path]
        }

        let result = try await runner.run(executable: engine, arguments: arguments)
        guard result.standardOutput.count <= MaintenanceReport.maxBytes else {
            throw ClientError.outputTooLarge
        }
        let report = try MaintenanceReport.decode(result.standardOutput)
        guard report.operation == operation || report.answers(requested: operation) else {
            throw ClientError.operationMismatch
        }
        guard result.status == report.expectedExitStatus else { throw ClientError.exitStatusDisagrees }
        return report
    }
}

/// Collects one pipe's bytes on a background thread; read only after the group has been waited on.
private final class Drained: @unchecked Sendable {
    var data = Data()
}

/// The single place a process is started: `Process` with an explicit executable and argv, a null
/// stdin, bounded captured output and an explicit (not inherited) environment.
struct FoundationProcessRunner: ProcessRunning {
    static let childEnvironment: [String: String] = ["LC_ALL": "C"]
    static let standardErrorLimit = 4096
    /// How long the app waits for the engine. The engine bounds its own waits (quiescence, cleanup); this
    /// is the backstop that keeps a wedged child from freezing the window for ever.
    static let defaultDeadline: TimeInterval = 300

    var deadline: TimeInterval = FoundationProcessRunner.defaultDeadline

    func run(executable: URL, arguments: [String]) async throws -> ProcessResult {
        let deadline = self.deadline
        return try await withCheckedThrowingContinuation { continuation in
            DispatchQueue.global(qos: .userInitiated).async {
                do {
                    continuation.resume(
                        returning: try Self.runBlocking(
                            executable: executable, arguments: arguments, deadline: deadline))
                } catch {
                    continuation.resume(throwing: error)
                }
            }
        }
    }

    private static func runBlocking(
        executable: URL, arguments: [String], deadline: TimeInterval
    ) throws -> ProcessResult {
        let process = Process()
        process.executableURL = executable
        process.arguments = arguments
        process.environment = childEnvironment
        process.standardInput = FileHandle.nullDevice
        let output = Pipe()
        let errors = Pipe()
        process.standardOutput = output
        process.standardError = errors
        let finished = DispatchSemaphore(value: 0)
        process.terminationHandler = { _ in finished.signal() }
        do {
            try process.run()
        } catch {
            throw ClientError.launchFailed
        }
        let group = DispatchGroup()
        let outputBytes = drain(output.fileHandleForReading, limit: MaintenanceReport.maxBytes + 1, group: group)
        let errorBytes = drain(errors.fileHandleForReading, limit: standardErrorLimit, group: group)
        // A bounded wait. On the deadline the app stops waiting and reports it; it does not signal the engine,
        // which owns a crash-safe transaction that a kill could interrupt. A later Inspect shows the truth.
        guard finished.wait(timeout: .now() + deadline) == .success else { throw ClientError.timedOut }
        group.wait()
        guard process.terminationReason == .exit else { throw ClientError.terminatedBySignal }
        return ProcessResult(
            status: process.terminationStatus,
            standardOutput: outputBytes.data,
            standardError: errorBytes.data)
    }

    /// Keep at most `limit` bytes but always read to EOF so the child can never block on a full pipe.
    private static func drain(_ handle: FileHandle, limit: Int, group: DispatchGroup) -> Drained {
        let drained = Drained()
        group.enter()
        DispatchQueue.global(qos: .userInitiated).async {
            while true {
                let chunk = handle.availableData
                if chunk.isEmpty { break }
                if drained.data.count < limit {
                    drained.data.append(chunk.prefix(limit - drained.data.count))
                }
            }
            group.leave()
        }
        return drained
    }
}

// MARK: - Which operations are offered (pure, testable)

/// What the user may ask for. `update` is `install --package` of a release other than the installed one.
enum OfferedOperation: String, CaseIterable, Equatable, Sendable {
    case install
    case update
    case repair
    case rollback
    case uninstall

    /// The engine word that performs it.
    var engineOperation: MaintenanceOperation {
        switch self {
        case .install, .update: return .install
        case .repair: return .repair
        case .rollback: return .rollback
        case .uninstall: return .uninstall
        }
    }
}

/// The installation as a report describes it, reduced to what decides the offered operations.
enum InstalledState: Equatable, Sendable {
    case absent
    case installed
    case damaged
    case foreign
    case recoveryRequired
    case unknown

    init(reportState state: String) {
        switch state {
        case "absent", "uninstalled": self = .absent
        case "installed", "clean", "repaired", "rolled-back", "recovered-active", "recovered-previous":
            self = .installed
        case "damaged": self = .damaged
        case "foreign": self = .foreign
        case "recovery-required": self = .recoveryRequired
        default: self = .unknown
        }
    }
}

/// Derives the offered operations from the OWNED installation state and the package this installer carries -
/// never from how a presentation happened to be classified.
///
/// `report.activePayload` is the payload id of the installed release and `packagePayload` the one this
/// installer carries. They are equal exactly when this installer IS the installed release, the only package
/// the engine accepts for Repair; a different package can only Update.
enum OperationPlanner {
    static func offered(for report: MaintenanceReport, packagePayload: String?) -> [OfferedOperation] {
        // A result that failed, or left a cleanup pending, offers only Retry and Quit (the presentation's
        // actions); the next Inspect shows the truth.
        if report.warnings.contains(.cleanupPending) { return [] }
        if report.operation != .inspect && report.failure != nil { return [] }
        if report.failure == .unsupportedState || report.failure == .cleanupPending { return [] }
        let sameRelease = report.activePayload != nil && report.activePayload == packagePayload
        switch InstalledState(reportState: report.state) {
        case .absent:
            return [.install]
        case .installed:
            return sameRelease ? [.repair, .rollback, .uninstall] : [.update, .rollback, .uninstall]
        case .damaged:
            return sameRelease ? [.repair, .uninstall] : [.uninstall]
        case .recoveryRequired:
            return [.install]
        case .foreign, .unknown:
            return []
        }
    }
}

/// The payload id of the release this installer carries, read from its own `descriptor.json`.
enum PackageIdentity {
    static let maxDescriptorBytes = 64 * 1024

    /// Read at most one byte past the bound, so an oversized or hostile descriptor can never be
    /// loaded whole. This identity only decides which buttons are offered; the engine still
    /// authenticates the package, its inventory and its signature before acting on it.
    static func payloadID(package: URL) -> String? {
        let file = package.appendingPathComponent("descriptor.json", isDirectory: false)
        guard let handle = try? FileHandle(forReadingFrom: file) else { return nil }
        defer { try? handle.close() }
        guard let data = try? handle.read(upToCount: maxDescriptorBytes + 1),
            data.count <= maxDescriptorBytes,
            let object = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
            let id = object["payload_id"] as? String,
            id.utf8.count == 64,
            id.unicodeScalars.allSatisfy({ ("0"..."9").contains($0) || ("a"..."f").contains($0) })
        else { return nil }
        return id
    }
}

// MARK: - Presentation (pure, testable)

/// What the user may do next. There is deliberately no action that opens Chrome, launches the host,
/// or terminates another process: Chrome owns host launch and lifetime.
enum FrontendAction: String, CaseIterable, Equatable, Sendable {
    case retry = "retry"
    case quit = "quit"
}

struct Presentation: Equatable, Sendable {
    enum Kind: Equatable, Sendable {
        /// A mutating operation completed and nothing is pending. The only success.
        case done
        /// A read-only inspection answered; the state decides which operations are offered.
        case info
        /// The user must do something (close tabs, wait) and then retry; nothing was lost.
        case blocked
        /// The operation did not complete.
        case failed
    }

    let kind: Kind
    let title: String
    let detail: String
    /// Whether the prior installation is still usable, when a failure says so; nil when not relevant.
    let priorInstallUsable: Bool?
    let actions: [FrontendAction]
    let warnings: [String]
}

enum Presenter {
    static func present(_ report: MaintenanceReport) -> Presentation {
        let warnings = report.warnings.map(warningText)
        if let failure = report.failure {
            return presentFailure(failure, report: report, warnings: warnings)
        }
        // A cleanup that did not finish is never a success, even with no failure code.
        if report.warnings.contains(.cleanupPending) {
            return Presentation(
                kind: .blocked, title: "Finishing up is not complete",
                detail: "Some files could not be removed yet. Retry to finish; nothing else is pending.",
                priorInstallUsable: nil, actions: [.retry, .quit], warnings: warnings)
        }
        if unsettledStates.contains(report.state) {
            return Presentation(
                kind: .blocked, title: stateTitle(report.state),
                detail: stateDetail(report.state), priorInstallUsable: nil,
                actions: [.retry, .quit], warnings: warnings)
        }
        if !report.operation.mutates {
            return Presentation(
                kind: .info, title: stateTitle(report.state), detail: stateDetail(report.state),
                priorInstallUsable: nil, actions: [.quit], warnings: warnings)
        }
        return Presentation(
            kind: .done, title: doneTitle(report.operation), detail: doneDetail(report.operation),
            priorInstallUsable: nil, actions: [.quit], warnings: warnings)
    }

    static func present(_ error: Error) -> Presentation {
        let (title, detail) = errorCopy(error as? ClientError)
        return Presentation(
            kind: .failed, title: title, detail: detail, priorInstallUsable: nil,
            actions: [.retry, .quit], warnings: [])
    }

    private static func errorCopy(_ error: ClientError?) -> (String, String) {
        switch error {
        case .engineUnavailable:
            return (
                "This installer is incomplete",
                "Its maintenance engine is missing or not where it should be. Download the installer again."
            )
        case .packageRequired, .invalidPackagePath:
            return (
                "The installer package is missing",
                "Open this installer from the original disk image so it can find its package."
            )
        case .timedOut:
            return (
                "The installer engine is taking too long",
                "It may still be finishing. Wait a minute, then choose Retry; nothing is reported as done until it says so."
            )
        case .launchFailed:
            return (
                "The installer could not start its engine",
                "macOS refused to start the maintenance engine. Nothing was changed. Download the installer again."
            )
        default:
            return (
                "The installer did not get a trustworthy answer",
                "The engine ended without a valid result, so Veles cannot tell what changed. Run Inspect or Repair; nothing is reported as done."
            )
        }
    }

    // MARK: copy

    private static func presentFailure(
        _ failure: FailureCode, report: MaintenanceReport, warnings: [String]
    ) -> Presentation {
        let (kind, title, detail, retry) = failureCopy(failure)
        let usable = report.priorUsable
        let tail = usable
            ? " Your existing Veles installation is still usable."
            : " Your existing Veles installation may not be usable; use Repair once this is resolved."
        return Presentation(
            kind: kind, title: title, detail: detail + tail, priorInstallUsable: usable,
            actions: retry ? [.retry, .quit] : [.quit], warnings: warnings)
    }

    /// Exhaustive on purpose: a new engine failure code must get its own guidance.
    private static func failureCopy(_ failure: FailureCode) -> (Presentation.Kind, String, String, Bool) {
        switch failure {
        case .hostActive:
            return (
                .blocked, "Veles is still in use",
                "Close every Chrome tab that uses the Veles connector, in all Chrome profiles, then choose Retry. The installer never closes Chrome or any other program for you.",
                true)
        case .busy:
            return (
                .blocked, "Another Veles operation is running",
                "Wait for it to finish, then choose Retry.", true)
        case .inspectionDenied:
            return (
                .blocked, "Veles could not confirm that it is idle",
                "macOS did not let the installer check running programs. Close the Veles connector tabs in Chrome, then choose Retry.",
                true)
        case .recoveryRequired:
            return (
                .blocked, "An earlier change was interrupted",
                "Choose Retry to finish recovering it before anything else changes.", true)
        case .cleanupPending:
            return (
                .blocked, "Finishing up is not complete",
                "Some files could not be removed yet. Choose Retry to finish.", true)
        case .io:
            return (
                .failed, "A file operation failed",
                "Check that there is free disk space, then choose Retry.", true)
        case .trustUnavailable:
            return (
                .failed, "Veles could not verify this installer",
                "macOS could not confirm the signature or Gatekeeper status right now, so nothing was installed or changed. Check your connection and choose Retry; it is not complete until it says Done.",
                true)
        case .trustDenied:
            return (
                .failed, "macOS rejected this installer",
                "The signature was not accepted, so nothing was changed. Download a fresh copy from the official source.",
                false)
        case .policyDenied:
            return (
                .failed, "This version is not allowed",
                "This release has been withdrawn or is older than permitted. Download the current installer.",
                false)
        case .invalidPackage:
            return (
                .failed, "The installer package is damaged",
                "Nothing was changed. Download the installer again.", false)
        case .originRejected:
            return (
                .failed, "This build is not set up for a published extension",
                "Nothing was changed. Download an official installer.", false)
        case .unsupportedTarget:
            return (
                .failed, "This Mac is not supported",
                "This installer needs a newer macOS or an Apple silicon Mac.", false)
        case .foreignState:
            return (
                .failed, "Files at the Veles location are not ours",
                "They were left untouched. Move or remove them yourself, then run the installer again.",
                false)
        case .unsafePath:
            return (
                .failed, "An unexpected link was found",
                "Something unexpected is in the Veles folder, so nothing was changed.", false)
        case .unsupportedState:
            return (
                .failed, "Veles was set up by a newer installer",
                "Nothing was changed. Use the newer installer.", false)
        case .noRollback:
            return (.failed, "There is no earlier version to return to", "Nothing was changed.", false)
        case .repairPackageRequired:
            return (
                .failed, "Repair needs the installer package",
                "Open this installer from the original disk image and try again.", false)
        case .invalidInput:
            return (
                .failed, "The installer could not run that request",
                "Nothing was changed.", false)
        }
    }

    private static func warningText(_ warning: WarningCode) -> String {
        switch warning {
        case .compatibilityWindow:
            return "This version of Veles may not match the extension you have; update the extension too."
        case .leftoverFiles:
            return "Some files that Veles did not create were left in its folder."
        case .cleanupPending:
            return "Some cleanup is still pending."
        }
    }

    private static func stateTitle(_ state: String) -> String {
        switch state {
        case "absent": return "Veles is not installed"
        case "installed", "clean": return "Veles is installed"
        case "damaged": return "The Veles installation is damaged"
        case "foreign": return "Something else is at the Veles location"
        case "recovery-required": return "An earlier change needs recovery"
        case "failed": return "The operation did not complete"
        default: return "Veles status: \(state)"
        }
    }

    private static func stateDetail(_ state: String) -> String {
        switch state {
        case "absent": return "Choose Install to set it up for this user account."
        case "installed", "clean": return "You can repair, roll back or uninstall it."
        case "damaged": return "Repair restores it from the installer of the same version; otherwise remove it and install again."
        case "foreign": return "The installer did not create these files and left them untouched."
        case "recovery-required": return "Choose Install: the installer settles the interrupted change first."
        case "failed": return "Nothing was reported as done."
        default: return "No change was made."
        }
    }

    private static func doneTitle(_ operation: MaintenanceOperation) -> String {
        switch operation {
        case .install: return "Veles is installed"
        case .repair: return "Veles was repaired"
        case .rollback: return "Veles was rolled back"
        case .uninstall: return "Veles was removed"
        case .inspect: return "Veles status"
        }
    }

    private static func doneDetail(_ operation: MaintenanceOperation) -> String {
        switch operation {
        case .install, .repair, .rollback:
            return "Open the Veles connector in Chrome when you are ready. The installer does not start it, and macOS may ask for Bluetooth permission the first time."
        case .uninstall:
            return "The Veles files for this user account were removed."
        case .inspect:
            return "No change was made."
        }
    }
}

// MARK: - SwiftUI shell

#if canImport(SwiftUI)
@MainActor
final class InstallerViewModel: ObservableObject {
    enum Screen: Equatable {
        case inspecting
        case running(MaintenanceOperation)
        case result(Presentation, offered: [OfferedOperation])
    }

    @Published private(set) var screen: Screen = .inspecting
    @Published private(set) var translocated = false
    private var lastOperation: MaintenanceOperation = .inspect
    private let client: MaintenanceClient?
    private let packagePayload: String?

    init(resourceURL: URL? = Bundle.main.resourceURL, runner: ProcessRunning = FoundationProcessRunner()) {
        if let resourceURL {
            let location = EngineLocation.resolve(resourceURL: resourceURL)
            translocated = EngineLocation.isTranslocated(location.engine)
            client = MaintenanceClient(engine: location.engine, package: location.package, runner: runner)
            packagePayload = PackageIdentity.payloadID(package: location.package)
        } else {
            client = nil
            packagePayload = nil
        }
    }

    func start() { Task { await perform(.inspect) } }
    func retry() { Task { await perform(lastOperation) } }

    func performOffered(_ offered: OfferedOperation) async { await perform(offered.engineOperation) }

    func perform(_ operation: MaintenanceOperation) async {
        lastOperation = operation
        screen = operation == .inspect ? .inspecting : .running(operation)
        guard let client else {
            screen = .result(Presenter.present(ClientError.engineUnavailable), offered: [])
            return
        }
        do {
            let report = try await client.run(operation.rawValue)
            screen = .result(
                Presenter.present(report),
                offered: OperationPlanner.offered(for: report, packagePayload: packagePayload))
        } catch {
            screen = .result(Presenter.present(error), offered: [])
        }
    }
}

struct InstallerView: View {
    @StateObject private var model = InstallerViewModel()
    @State private var confirmUninstall = false

    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Veles Bridge").font(.title2).bold()
            switch model.screen {
            case .inspecting:
                ProgressView("Checking this account…")
            case .running(let operation):
                ProgressView("Working: \(operation.rawValue)…")
            case .result(let presentation, let offered):
                resultView(presentation, offered: offered)
            }
            if model.translocated {
                Text("This app is running from a temporary read-only location. That is fine: Veles is never installed there.")
                    .font(.footnote).foregroundStyle(.secondary)
            }
        }
        .padding(24)
        .frame(minWidth: 460, minHeight: 280, alignment: .topLeading)
        .task { model.start() }
        .confirmationDialog("Remove Veles for this user?", isPresented: $confirmUninstall) {
            Button("Uninstall", role: .destructive) { Task { await model.perform(.uninstall) } }
        }
    }

    @ViewBuilder
    private func resultView(_ presentation: Presentation, offered: [OfferedOperation]) -> some View {
        Text(presentation.title).font(.headline)
        Text(presentation.detail).fixedSize(horizontal: false, vertical: true)
        ForEach(presentation.warnings, id: \.self) { Text($0).font(.footnote).foregroundStyle(.secondary) }
        HStack {
            // The buttons come from the owned installation state and this installer's package, not from how the
            // result happened to be classified: a damaged installation still offers Repair.
            ForEach(offered, id: \.self) { operation in
                switch operation {
                case .install:
                    Button("Install") { Task { await model.performOffered(operation) } }
                        .keyboardShortcut(.defaultAction)
                case .update:
                    Button("Update") { Task { await model.performOffered(operation) } }
                        .keyboardShortcut(.defaultAction)
                case .repair:
                    Button("Repair") { Task { await model.performOffered(operation) } }
                case .rollback:
                    Button("Roll back") { Task { await model.performOffered(operation) } }
                case .uninstall:
                    Button("Uninstall…", role: .destructive) { confirmUninstall = true }
                }
            }
            if presentation.actions.contains(.retry) {
                Button("Retry") { model.retry() }.keyboardShortcut(.defaultAction)
            }
            Spacer()
            if presentation.actions.contains(.quit) {
                Button("Quit") { NSApplication.shared.terminate(nil) }
            }
        }
    }
}

@main
struct VelesBridgeInstallerApp: App {
    var body: some Scene {
        WindowGroup { InstallerView() }
            .windowResizability(.contentSize)
    }
}
#endif
