// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "reminders-bridge",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "reminders-bridge",
            path: "Sources/reminders-bridge"
        )
    ]
)
