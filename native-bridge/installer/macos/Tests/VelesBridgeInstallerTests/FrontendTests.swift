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
    activePayload: String? = nil,
    failure: String? = nil,
    warnings: [String] = []
) -> Data {
    let failureJSON = failure.map { "\"\($0)\"" } ?? "null"
    let payloadJSON = activePayload.map { "\"\($0)\"" } ?? "null"
    let warningsJSON = "[" + warnings.map { "\"\($0)\"" }.joined(separator: ",") + "]"
    let text =
        "{\"operation\":\"\(operation)\",\"state\":\"\(state)\",\"prior_usable\":\(priorUsable),"
        + "\"active_payload\":\(payloadJSON),\"failure\":\(failureJSON),\"warnings\":\(warningsJSON)}\n"
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

// MARK: - F11: the offered operations come from installation and package state

/// The installed payload this fake installer would report, and another release's payload.
private let installedPayload = String(repeating: "a", count: 64)
private let otherPayload = String(repeating: "b", count: 64)

private func stateReport(
    _ operation: MaintenanceOperation,
    _ state: String,
    activePayload: String? = nil,
    failure: FailureCode? = nil,
    warnings: [WarningCode] = []
) -> MaintenanceReport {
    MaintenanceReport(
        operation: operation, state: state, priorUsable: false,
        activePayload: activePayload, failure: failure, warnings: warnings)
}

/// A writable fake app bundle whose `Resources/package/descriptor.json` names this installer's payload.
private func fakeBundle(resources: String) throws -> URL {
    let root = FileManager.default.temporaryDirectory
        .appendingPathComponent("veles-installer-tests-\(UUID().uuidString)", isDirectory: true)
    let package = root.appendingPathComponent("package", isDirectory: true)
    try FileManager.default.createDirectory(at: package, withIntermediateDirectories: true)
    let descriptor = "{\"payload_id\":\"\(resources)\"}\n"
    try Data(descriptor.utf8).write(to: package.appendingPathComponent("descriptor.json"))
    return root
}

final class OfferedOperationTests: XCTestCase {
    /// A newer installer over an existing installation offers Update - the engine's `install`.
    func testAnInstalledReleaseOffersRepairOnlyForItsOwnPackageAndUpdateForAnother() {
        let installed = stateReport(.inspect, "installed", activePayload: installedPayload)
        XCTAssertEqual(
            OperationPlanner.offered(for: installed, packagePayload: installedPayload),
            [.repair, .rollback, .uninstall])
        XCTAssertEqual(
            OperationPlanner.offered(for: installed, packagePayload: otherPayload),
            [.update, .rollback, .uninstall])
        // Without a readable package identity there is no same-version package to repair with.
        XCTAssertEqual(
            OperationPlanner.offered(for: installed, packagePayload: nil),
            [.update, .rollback, .uninstall])
        XCTAssertEqual(OfferedOperation.update.engineOperation, .install)
        XCTAssertEqual(OfferedOperation.repair.engineOperation, .repair)
    }

    func testAnAbsentOrRecoverableInstallationOffersInstall() {
        XCTAssertEqual(
            OperationPlanner.offered(for: stateReport(.inspect, "absent"), packagePayload: otherPayload),
            [.install])
        XCTAssertEqual(
            OperationPlanner.offered(
                for: stateReport(.inspect, "recovery-required", failure: .recoveryRequired),
                packagePayload: otherPayload),
            [.install])
    }

    /// F11's second defect: a damaged inspection carries a failure, so the old presentation-kind guard hid
    /// Repair even though the damage is safely ours. The planner must not look at the presentation.
    func testDamagedStateStillOffersRepairForTheSameVersionPackage() {
        for failure in [nil, .recoveryRequired] as [FailureCode?] {
            let damaged = stateReport(
                .inspect, "damaged", activePayload: installedPayload, failure: failure)
            let presented = Presenter.present(damaged).kind
            XCTAssertNotEqual(presented, .info, "a damaged result is not informational")
            XCTAssertNotEqual(presented, .done, "a damaged result is never done")
            XCTAssertEqual(
                OperationPlanner.offered(for: damaged, packagePayload: installedPayload),
                [.repair, .uninstall],
                "repair is still the safe same-version operation for owned damage")
            XCTAssertEqual(
                OperationPlanner.offered(for: damaged, packagePayload: otherPayload),
                [.uninstall],
                "another release of the product cannot repair this one")
        }
    }

    func testForeignUnknownAndUnreadableStatesOfferNothing() {
        for state in ["foreign", "some-future-state"] {
            XCTAssertEqual(
                OperationPlanner.offered(for: stateReport(.inspect, state), packagePayload: installedPayload),
                [])
        }
        XCTAssertEqual(
            OperationPlanner.offered(
                for: stateReport(.inspect, "installed", failure: .unsupportedState),
                packagePayload: installedPayload),
            [])
    }

    /// A mutating result that failed, or left cleanup pending, offers only the presentation's Retry.
    func testAFailedOrPendingMutationOffersOnlyRetry() {
        for report in [
            stateReport(.install, "failed", failure: .io),
            stateReport(.uninstall, "uninstalled", warnings: [.cleanupPending]),
            stateReport(.repair, "failed", failure: .cleanupPending),
        ] {
            XCTAssertEqual(OperationPlanner.offered(for: report, packagePayload: installedPayload), [])
        }
    }

    func testPackageIdentityReadsOnlyTheBoundedDigestFromTheDescriptor() throws {
        XCTAssertEqual(
            PackageIdentity.payloadID(package: try fakeBundle(resources: installedPayload)),
            Optional(installedPayload))
        for bad in [String(repeating: "A", count: 64), String(installedPayload.dropLast()),
                    String(installedPayload.dropLast()) + "g"] {
            XCTAssertNil(PackageIdentity.payloadID(package: try fakeBundle(resources: bad)), bad)
        }
        let missing = FileManager.default.temporaryDirectory
            .appendingPathComponent("veles-installer-tests-\(UUID().uuidString)", isDirectory: true)
        XCTAssertNil(PackageIdentity.payloadID(package: missing))

        // An oversized descriptor is never loaded whole: the read itself is bounded.
        let oversized = try fakeBundle(resources: installedPayload)
        defer { try? FileManager.default.removeItem(at: oversized) }
        try Data(repeating: 0x20, count: PackageIdentity.maxDescriptorBytes + 1)
            .write(to: oversized.appendingPathComponent("package/descriptor.json"))
        XCTAssertNil(PackageIdentity.payloadID(package: oversized))
    }
}

final class InstallerViewModelTransitionTests: XCTestCase {
    /// A damaged inspect result reaches the view model with Repair offered, even though the presentation is
    /// a failure. This is the transition the old `presentation.kind == .info || .done` guard could not make.
    func testADamagedInspectOffersRepairWithoutAnyPresentationGuard() async throws {
        let bundle = try fakeBundle(resources: installedPayload)
        defer { try? FileManager.default.removeItem(at: bundle) }
        let report = reportData(
            operation: "inspect", state: "damaged", priorUsable: false,
            activePayload: installedPayload, failure: "recovery-required")
        let runner = RecordingRunner(result: result(0, report))
        let model = InstallerViewModel(resourceURL: bundle, runner: runner)
        await model.perform(.inspect)
        guard case .result(let presentation, let offered) = model.screen else {
            return XCTFail("the inspection must settle into a result: \(model.screen)")
        }
        XCTAssertNotEqual(presentation.kind, .info, "the old guard would have hidden Repair here")
        XCTAssertNotEqual(presentation.kind, .done)
        XCTAssertEqual(offered, [.repair, .uninstall])
        XCTAssertEqual(runner.calls.map(\.arguments.first), ["inspect"] as [String?])
    }

    /// Choosing Update starts the engine's `install`; choosing Repair starts `repair`.
    func testUpdateAndRepairStartTheEngineWordTheyMean() async throws {
        let newer = try fakeBundle(resources: otherPayload)
        defer { try? FileManager.default.removeItem(at: newer) }
        let runner = RecordingRunner(
            result: result(0, reportData(operation: "install", state: "installed")))
        let model = InstallerViewModel(resourceURL: newer, runner: runner)
        await model.performOffered(.update)
        XCTAssertEqual(runner.calls.last?.arguments.first, "install")
        let installed = try fakeBundle(resources: installedPayload)
        defer { try? FileManager.default.removeItem(at: installed) }
        let repairRunner = RecordingRunner(
            result: result(0, reportData(operation: "repair", state: "repaired")))
        let repairing = InstallerViewModel(resourceURL: installed, runner: repairRunner)
        await repairing.performOffered(.repair)
        XCTAssertEqual(repairRunner.calls.last?.arguments.first, "repair")
    }

    func testAnAbsentInstallationDoesNotPretendToOfferMaintenance() async throws {
        let bundle = try fakeBundle(resources: installedPayload)
        defer { try? FileManager.default.removeItem(at: bundle) }
        let runner = RecordingRunner(
            result: result(0, reportData(operation: "inspect", state: "absent", priorUsable: false)))
        let model = InstallerViewModel(resourceURL: bundle, runner: runner)
        await model.perform(.inspect)
        guard case .result(_, let offered) = model.screen else {
            return XCTFail("the inspection must settle into a result: \(model.screen)")
        }
        XCTAssertEqual(offered, [.install])
    }
}

final class EngineDeadlineTests: XCTestCase {
    /// The Swift child wait is bounded: a wedged engine is reported, never waited on for ever, and it is
    /// never signalled (a kill could interrupt the engine's crash-safe transaction).
    func testAWedgedEngineIsReportedAfterTheDeadline() async {
        var runner = FoundationProcessRunner()
        runner.deadline = 0.2
        do {
            _ = try await runner.run(
                executable: URL(fileURLWithPath: "/bin/sleep"), arguments: ["2"])
            XCTFail("a child that does not exit must not be waited on without a deadline")
        } catch {
            XCTAssertEqual(error as? ClientError, .timedOut)
        }
    }

    func testTheDefaultDeadlineIsFiniteAndGenerous() {
        XCTAssertEqual(FoundationProcessRunner.defaultDeadline, 300)
        XCTAssertGreaterThan(FoundationProcessRunner().deadline, 0)
    }

    /// `install` is answered with `repair` only when this installer IS the installed release; that is the
    /// same-version rerun. Nothing else may be answered by another operation word.
    func testASameVersionInstallIsAnsweredByTheRepairItPerformed() throws {
        let repaired = MaintenanceReport(
            operation: .repair, state: "repaired", priorUsable: true, activePayload: installedPayload)
        XCTAssertTrue(repaired.answers(requested: .install))
        XCTAssertFalse(repaired.answers(requested: .rollback))
        XCTAssertFalse(
            MaintenanceReport(operation: .install, state: "installed", priorUsable: true)
                .answers(requested: .repair))
    }
}

/// N1: the installer runs the engine bundled with its own release and channel.
final class BundledEngineSelectionTests: XCTestCase {
    /// A persistent engine published by a previously installed release is never substituted for this
    /// installer's own engine. Its compiled descriptor is another release's, which exact-descriptor
    /// authentication rejects, and a fixture build must not fall into production state through a
    /// hard-coded production path.
    func testTheBundledEngineIsAlwaysUsedEvenWhenAnotherPersistentEngineExists() throws {
        let root = FileManager.default.temporaryDirectory
            .appendingPathComponent("veles-n1-\(UUID().uuidString)", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let resources = root.appendingPathComponent("Resources", isDirectory: true)
        let package = resources.appendingPathComponent("package", isDirectory: true)
        let bundled = package.appendingPathComponent(EngineLocation.engineRelativePath)
        try FileManager.default.createDirectory(
            at: bundled.deletingLastPathComponent(), withIntermediateDirectories: true)
        XCTAssertTrue(FileManager.default.createFile(atPath: bundled.path, contents: Data()))

        // An executable persistent engine already exists at the production location.
        let persistent = root.appendingPathComponent(
            "Library/Application Support/Veles/NativeBridge/maintenance/veles-bridge-maintenance")
        try FileManager.default.createDirectory(
            at: persistent.deletingLastPathComponent(), withIntermediateDirectories: true)
        XCTAssertTrue(FileManager.default.createFile(atPath: persistent.path, contents: Data()))
        try FileManager.default.setAttributes(
            [.posixPermissions: 0o700], ofItemAtPath: persistent.path)

        let location = EngineLocation.resolve(resourceURL: resources)
        XCTAssertEqual(location.engine.path, bundled.standardizedFileURL.path)
        XCTAssertFalse(location.engine.path.contains("Application Support"))
        XCTAssertEqual(location.package.path, package.standardizedFileURL.path)
    }

    /// Two releases: a newer installer over an installed older release must start the newer
    /// installer's own engine with the newer package, never the older persistent engine.
    func testANewerInstallerRunsItsOwnEngineNotAnOlderPersistentOne() async throws {
        let newer = try fakeBundle(resources: otherPayload)
        defer { try? FileManager.default.removeItem(at: newer) }
        let bundled = newer.appendingPathComponent("package")
            .appendingPathComponent(EngineLocation.engineRelativePath)
        try FileManager.default.createDirectory(
            at: bundled.deletingLastPathComponent(), withIntermediateDirectories: true)
        XCTAssertTrue(FileManager.default.createFile(atPath: bundled.path, contents: Data()))

        let runner = RecordingRunner(
            result: result(0, reportData(operation: "install", state: "installed")))
        let model = InstallerViewModel(resourceURL: newer, runner: runner)
        await model.performOffered(.update)

        XCTAssertEqual(runner.calls.count, 1)
        XCTAssertEqual(runner.calls[0].executable.path, bundled.standardizedFileURL.path)
        XCTAssertEqual(runner.calls[0].arguments.first, "install")
        XCTAssertEqual(
            runner.calls[0].arguments.last,
            newer.appendingPathComponent("package").standardizedFileURL.path)
    }
}

