// swift-tools-version:5.9
import PackageDescription

let package = Package(
    name: "wake-watcher",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(
            name: "wake-watcher",
            path: "Sources/wake-watcher"
        )
    ]
)
