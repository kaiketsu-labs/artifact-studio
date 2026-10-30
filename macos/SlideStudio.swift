import AppKit
import ServiceManagement

struct Configuration: Decodable {
    let project: String
    let python: String
    let collection: String
    let database: String
    let port: Int
    let launchAtLogin: Bool
}

enum ServerState: String {
    case stopped = "Stopped", starting = "Starting…", running = "Running"
    case stopping = "Stopping…", failed = "Could not start"

    var color: NSColor {
        switch self {
        case .running: .systemGreen
        case .starting, .stopping: .systemOrange
        case .failed: .systemRed
        case .stopped: .secondaryLabelColor
        }
    }
}

@MainActor
private func menuBarImage(color: NSColor) -> NSImage {
    NSImage(size: NSSize(width: 29, height: 18), flipped: false) { _ in
        NSColor.white.setStroke()
        let back = NSBezierPath()
        back.lineWidth = 1.4
        back.lineCapStyle = .round
        back.lineJoinStyle = .round
        back.move(to: NSPoint(x: 1.5, y: 7))
        back.line(to: NSPoint(x: 1.5, y: 14))
        back.curve(to: NSPoint(x: 3.5, y: 16), controlPoint1: NSPoint(x: 1.5, y: 15.5), controlPoint2: NSPoint(x: 2, y: 16))
        back.line(to: NSPoint(x: 17, y: 16))
        back.stroke()
        let front = NSBezierPath(roundedRect: NSRect(x: 4.5, y: 1.5, width: 16, height: 11.5), xRadius: 2, yRadius: 2)
        front.lineWidth = 1.4
        front.stroke()
        NSColor.white.setFill()
        let play = NSBezierPath()
        play.move(to: NSPoint(x: 11, y: 4.5))
        play.line(to: NSPoint(x: 15.5, y: 7.25))
        play.line(to: NSPoint(x: 11, y: 10))
        play.close()
        play.fill()
        color.setFill()
        NSBezierPath(ovalIn: NSRect(x: 23, y: 2, width: 4.5, height: 4.5)).fill()
        return true
    }
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {
    private var configuration: Configuration!
    private var statusItem: NSStatusItem!
    private var timer: Timer?
    private var process: Process?
    private var serverLog: FileHandle?
    private var state: ServerState = .stopped
    private var polling = false
    private var restartPending = false
    private var quitting = false
    private var external = false
    private var errorMessage = ""
    private let menu = NSMenu()
    private let status = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private let startItem = NSMenuItem(title: "Start", action: #selector(start), keyEquivalent: "")
    private let stopItem = NSMenuItem(title: "Stop", action: #selector(stop), keyEquivalent: "")
    private let restartItem = NSMenuItem(title: "Restart", action: #selector(restart), keyEquivalent: "")
    private let loginItem = NSMenuItem(title: "Launch at Login", action: #selector(toggleLogin), keyEquivalent: "")
    private let approvalItem = NSMenuItem(title: "Allow in System Settings…", action: #selector(loginSettings), keyEquivalent: "")

    private var workspaceURL: URL { URL(string: "http://127.0.0.1:\(configuration.port)/")! }
    private var logURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/\(Bundle.main.bundleIdentifier!)/server.log")
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        do {
            let url = Bundle.main.url(forResource: "configuration", withExtension: "json")!
            configuration = try JSONDecoder().decode(Configuration.self, from: Data(contentsOf: url))
        } catch {
            showError("Cannot read the app configuration: \(error.localizedDescription)")
            NSApp.terminate(nil)
            return
        }
        NSApp.setActivationPolicy(.accessory)
        statusItem = NSStatusBar.system.statusItem(withLength: 33)
        statusItem.button?.setAccessibilityLabel("Slide Studio")
        menu.autoenablesItems = false
        status.isEnabled = false
        menu.addItem(status)
        menu.addItem(.separator())
        add("Open Slide Studio", symbol: "arrow.up.right.square", action: #selector(openWorkspace), key: "o")
        for (item, symbol) in [(startItem, "play.fill"), (stopItem, "stop.fill"), (restartItem, "arrow.clockwise")] {
            item.target = self
            item.image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)
            menu.addItem(item)
        }
        menu.addItem(.separator())
        loginItem.target = self
        menu.addItem(loginItem)
        approvalItem.target = self
        menu.addItem(approvalItem)
        add("Show Logs", symbol: "doc.text", action: #selector(showLogs))
        menu.addItem(.separator())
        add("Quit Slide Studio", symbol: "power", action: #selector(quit), key: "q")
        statusItem.menu = menu
        updateMenu()
        timer = Timer.scheduledTimer(withTimeInterval: 2, repeats: true) { [weak self] _ in
            Task { @MainActor in await self?.poll() }
        }
        if configuration.launchAtLogin && !UserDefaults.standard.bool(forKey: "loginConfigured") {
            do {
                try SMAppService.mainApp.register()
                UserDefaults.standard.set(true, forKey: "loginConfigured")
            } catch {
                showError("Launch at Login could not be enabled: \(error.localizedDescription)")
            }
        }
        start()
    }

    private func add(_ title: String, symbol: String, action: Selector, key: String = "") {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = self
        item.image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)
        menu.addItem(item)
    }

    private func updateMenu() {
        status.title = external ? "Running · started elsewhere" : state.rawValue
        status.toolTip = errorMessage.isEmpty ? workspaceURL.absoluteString : errorMessage
        statusItem.button?.toolTip = "Slide Studio · \(status.title)"
        statusItem.button?.setAccessibilityValue(status.title)
        statusItem.button?.image = menuBarImage(color: state.color)
        startItem.isEnabled = state == .stopped || state == .failed
        stopItem.isEnabled = process?.isRunning == true && state != .stopping
        restartItem.isEnabled = process?.isRunning == true && state == .running
        let login = SMAppService.mainApp.status
        loginItem.state = login == .enabled ? .on : login == .requiresApproval ? .mixed : .off
        approvalItem.isHidden = login != .requiresApproval
    }

    private func isReady() async -> Bool {
        var request = URLRequest(url: workspaceURL.appendingPathComponent("api/health"))
        request.timeoutInterval = 1.5
        request.cachePolicy = .reloadIgnoringLocalCacheData
        do {
            let (data, response) = try await URLSession.shared.data(for: request)
            guard (response as? HTTPURLResponse)?.statusCode == 200,
                  let reply = try JSONSerialization.jsonObject(with: data) as? [String: String]
            else { return false }
            return reply["service"] == "slide-studio" && reply["status"] == "ready"
        } catch { return false }
    }

    private func poll() async {
        guard !polling, state != .stopping else { return }
        polling = true
        let ready = await isReady()
        polling = false
        guard state != .stopping else { return }
        if ready {
            state = .running
            external = process == nil
        } else if state == .running {
            state = process?.isRunning == true ? .starting : .stopped
            external = false
        }
        updateMenu()
    }

    @objc private func start() {
        guard process?.isRunning != true, state != .starting, state != .stopping else { return }
        state = .starting
        external = false
        errorMessage = ""
        updateMenu()
        Task { @MainActor in
            if await isReady() {
                state = .running
                external = true
                updateMenu()
                return
            }
            launchServer()
        }
    }

    private func launchServer() {
        do {
            try FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
            if !FileManager.default.fileExists(atPath: logURL.path) {
                FileManager.default.createFile(atPath: logURL.path, contents: nil)
            }
            let log = try FileHandle(forWritingTo: logURL)
            try log.seekToEnd()
            let child = Process()
            child.executableURL = URL(fileURLWithPath: configuration.python)
            child.currentDirectoryURL = URL(fileURLWithPath: configuration.project)
            child.arguments = ["-m", "uvicorn", "studio.app:app", "--host", "127.0.0.1", "--port", String(configuration.port), "--no-access-log"]
            var environment = ProcessInfo.processInfo.environment
            environment["SLIDE_STUDIO_COLLECTION"] = configuration.collection
            environment["SLIDE_STUDIO_DATABASE"] = configuration.database
            let home = FileManager.default.homeDirectoryForCurrentUser.path
            environment["PATH"] = "\(home)/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:" + (environment["PATH"] ?? "")
            child.environment = environment
            child.standardInput = FileHandle.nullDevice
            child.standardOutput = log
            child.standardError = log
            child.terminationHandler = { [weak self] terminated in
                let code = terminated.terminationStatus
                Task { @MainActor in self?.serverEnded(code: code) }
            }
            try child.run()
            serverLog = log
            process = child
        } catch {
            state = .failed
            errorMessage = error.localizedDescription
            updateMenu()
            showError("Could not start Slide Studio: \(errorMessage)")
        }
    }

    private func serverEnded(code: Int32) {
        let expected = state == .stopping
        process = nil
        try? serverLog?.close()
        serverLog = nil
        external = false
        state = expected ? .stopped : .failed
        if !expected { errorMessage = "Server exited (\(code)). See Show Logs." }
        updateMenu()
        if quitting { NSApp.reply(toApplicationShouldTerminate: true) }
        else if restartPending { restartPending = false; start() }
    }

    @objc private func stop() {
        guard let process, process.isRunning, state != .stopping else { return }
        state = .stopping
        updateMenu()
        process.terminate()
    }

    @objc private func restart() { restartPending = true; stop() }
    @objc private func openWorkspace() { NSWorkspace.shared.open(workspaceURL) }
    @objc private func showLogs() { NSWorkspace.shared.activateFileViewerSelecting([logURL]) }
    @objc private func loginSettings() { SMAppService.openSystemSettingsLoginItems() }
    @objc private func quit() { NSApp.terminate(nil) }

    @objc private func toggleLogin() {
        Task { @MainActor in
            do {
                if SMAppService.mainApp.status == .enabled || SMAppService.mainApp.status == .requiresApproval {
                    try await SMAppService.mainApp.unregister()
                } else {
                    try SMAppService.mainApp.register()
                }
                UserDefaults.standard.set(true, forKey: "loginConfigured")
            } catch { showError("Could not change Launch at Login: \(error.localizedDescription)") }
            updateMenu()
        }
    }

    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard process?.isRunning == true else { return .terminateNow }
        quitting = true
        restartPending = false
        stop()
        return .terminateLater
    }

    private func showError(_ message: String) {
        let alert = NSAlert()
        alert.messageText = "Slide Studio"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.runModal()
    }
}

@main
struct SlideStudioMenu {
    @MainActor static func main() {
        if let flag = CommandLine.arguments.firstIndex(of: "--icon-preview"),
           CommandLine.arguments.indices.contains(flag + 1) {
            let image = menuBarImage(color: .systemGreen)
            if let data = image.tiffRepresentation,
               let bitmap = NSBitmapImageRep(data: data),
               let png = bitmap.representation(using: .png, properties: [:]) {
                try? png.write(to: URL(fileURLWithPath: CommandLine.arguments[flag + 1]))
            }
            return
        }
        if CommandLine.arguments.contains("--login-status") {
            let status = SMAppService.mainApp.status
            let label = status == .enabled ? "enabled" : status == .requiresApproval ? "requires-approval" : "disabled"
            print(label)
            return
        }
        let app = NSApplication.shared
        let delegate = AppDelegate()
        app.delegate = delegate
        withExtendedLifetime(delegate) { app.run() }
    }
}
