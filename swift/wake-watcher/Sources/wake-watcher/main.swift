import AppKit
import Foundation

func eprint(_ s: String) {
    FileHandle.standardError.write((s + "\n").data(using: .utf8)!)
}

guard CommandLine.arguments.count > 1 else {
    eprint("usage: wake-watcher <path-to-sync-script>")
    exit(1)
}
let syncScriptPath = CommandLine.arguments[1]

// Give the network a moment to reconnect after wake before syncing —
// firing immediately on the wake notification reliably races Wi-Fi
// reassociation and fails the run.
let postWakeDelaySeconds = 10.0

func runSync() {
    let task = Process()
    task.executableURL = URL(fileURLWithPath: syncScriptPath)
    do {
        try task.run()
    } catch {
        eprint("wake-watcher: failed to launch sync script: \(error)")
    }
}

NSWorkspace.shared.notificationCenter.addObserver(
    forName: NSWorkspace.didWakeNotification,
    object: nil,
    queue: .main
) { _ in
    DispatchQueue.main.asyncAfter(deadline: .now() + postWakeDelaySeconds) {
        runSync()
    }
}

RunLoop.main.run()
