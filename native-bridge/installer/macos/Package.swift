// swift-tools-version:5.9
//
// OTP-25 Task 11: the thin native macOS installer frontend. It owns no transaction logic: it starts
// the signed engine inside its own bundle (`veles-bridge-maintenance`) with structured arguments and
// renders the one bounded report that engine prints. Built for arm64 only against the pinned
// deployment target; the pinned SDK is the Xcode selected by the release job (DEVELOPER_DIR).
//
// swift test --package-path native-bridge/installer/macos        (macOS only; NOT RUN on Linux)
import PackageDescription

let package = Package(
    name: "VelesBridgeInstaller",
    platforms: [.macOS("26.0")],
    products: [
        .executable(name: "VelesBridgeInstaller", targets: ["VelesBridgeInstaller"]),
    ],
    targets: [
        .executableTarget(
            name: "VelesBridgeInstaller",
            path: "Sources/VelesBridgeInstaller"
        ),
        .testTarget(
            name: "VelesBridgeInstallerTests",
            dependencies: ["VelesBridgeInstaller"],
            path: "Tests/VelesBridgeInstallerTests"
        ),
    ]
)
