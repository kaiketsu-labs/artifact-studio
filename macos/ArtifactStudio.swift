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

private struct BackupLibrary: Decodable {
    let id: String
    let kind: String
    let location: String
    let sync_enabled: Bool
    let auto_sync_enabled: Bool
}

private struct BackupStatus: Decodable {
    let library_id: String
    let phase: String
    let last_synced_at: String?
    let error: String
}

private enum BackupRequestError: LocalizedError {
    case response(String)
    var errorDescription: String? {
        switch self { case .response(let message): return message }
    }
}

enum ServerState: String {
    case stopped = "Stopped", starting = "Starting…", running = "Running"
    case stopping = "Stopping…", failed = "Could not start"

}

@MainActor
private func menuBarImage(state: ServerState) -> NSImage {
    let image = NSImage(size: NSSize(width: 24, height: 18), flipped: true) { _ in
        NSColor.black.setFill()
        // The same stepped document and pixel A used by the app icon.
        for rect in [
            NSRect(x: 1, y: 1, width: 10, height: 1),
            NSRect(x: 1, y: 2, width: 1, height: 10),
            NSRect(x: 4, y: 3, width: 11, height: 1),
            NSRect(x: 3, y: 4, width: 1, height: 10),
            NSRect(x: 15, y: 4, width: 1, height: 10),
            NSRect(x: 4, y: 14, width: 11, height: 1),
            NSRect(x: 8, y: 6, width: 3, height: 1),
            NSRect(x: 7, y: 7, width: 1, height: 5),
            NSRect(x: 11, y: 7, width: 1, height: 5),
            NSRect(x: 8, y: 9, width: 3, height: 1),
        ] {
            rect.fill()
        }
        // A filled square means running; an outline means stopped or failed.
        let indicator = NSRect(x: 19, y: 10, width: 4, height: 4)
        if state == .running {
            indicator.fill()
        } else if state == .starting || state == .stopping {
            NSRect(x: 20, y: 11, width: 2, height: 2).fill()
        } else {
            NSRect(x: 19, y: 10, width: 4, height: 1).fill()
            NSRect(x: 19, y: 13, width: 4, height: 1).fill()
            NSRect(x: 19, y: 11, width: 1, height: 2).fill()
            NSRect(x: 22, y: 11, width: 1, height: 2).fill()
        }
        return true
    }
    image.isTemplate = true
    return image
}

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate, NSMenuDelegate {
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
    private let backupItem = NSMenuItem(title: "GitHub Backup", action: nil, keyEquivalent: "")
    private let backupMenu = NSMenu(title: "GitHub Backup")
    private var backupLibraries: [BackupLibrary] = []
    private var backupStatuses: [BackupStatus] = []
    private var backupLoading = false
    private var backupError = ""
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
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        statusItem.button?.setAccessibilityLabel("Artifact Studio")
        menu.autoenablesItems = false
        menu.delegate = self
        backupMenu.autoenablesItems = false
        backupItem.image = NSImage(systemSymbolName: "externaldrive.badge.icloud", accessibilityDescription: nil)
        backupItem.submenu = backupMenu
        status.isEnabled = false
        menu.addItem(status)
        menu.addItem(.separator())
        add("Open Artifact Studio", symbol: "arrow.up.right.square", action: #selector(openWorkspace), key: "o")
        for (item, symbol) in [(startItem, "play.fill"), (stopItem, "stop.fill"), (restartItem, "arrow.clockwise")] {
            item.target = self
            item.image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)
            menu.addItem(item)
        }
        menu.addItem(backupItem)
        menu.addItem(.separator())
        loginItem.target = self
        menu.addItem(loginItem)
        approvalItem.target = self
        menu.addItem(approvalItem)
        add("Show Logs", symbol: "doc.text", action: #selector(showLogs))
        menu.addItem(.separator())
        add("Quit Artifact Studio", symbol: "power", action: #selector(quit), key: "q")
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
        statusItem.button?.toolTip = "Artifact Studio · \(status.title)"
        statusItem.button?.setAccessibilityValue(status.title)
        statusItem.button?.image = menuBarImage(state: state)
        backupItem.isEnabled = state == .running
        if state != .running { backupLibraries = []; backupStatuses = []; backupError = "" }
        renderBackupMenu()
        startItem.isEnabled = state == .stopped || state == .failed
        stopItem.isEnabled = process?.isRunning == true && state != .stopping
        restartItem.isEnabled = process?.isRunning == true && state == .running
        let login = SMAppService.mainApp.status
        loginItem.state = login == .enabled ? .on : login == .requiresApproval ? .mixed : .off
        approvalItem.isHidden = login != .requiresApproval
    }

    func menuWillOpen(_ menu: NSMenu) {
        guard menu === self.menu, state == .running else { return }
        Task { await refreshBackup() }
    }

    private func request(_ path: String, body: [String: Bool]? = nil, timeout: TimeInterval = 15) async throws -> Data {
        guard let url = URL(string: path, relativeTo: workspaceURL) else {
            throw BackupRequestError.response("Invalid backup endpoint.")
        }
        var request = URLRequest(url: url)
        request.timeoutInterval = timeout
        request.cachePolicy = .reloadIgnoringLocalCacheData
        if let body {
            request.httpMethod = "POST"
            request.setValue("application/json", forHTTPHeaderField: "Content-Type")
            request.httpBody = try JSONEncoder().encode(body)
        }
        let (data, response) = try await URLSession.shared.data(for: request)
        guard let http = response as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["detail"] as? String
            throw BackupRequestError.response(detail ?? "Backup request failed.")
        }
        return data
    }

    private func refreshBackup() async {
        guard state == .running, !backupLoading else { return }
        backupLoading = true
        defer { backupLoading = false; renderBackupMenu() }
        do {
            async let libraries = request("api/libraries")
            async let statuses = request("api/library-sync")
            let (libraryData, statusData) = try await (libraries, statuses)
            backupLibraries = try JSONDecoder().decode([BackupLibrary].self, from: libraryData).filter { $0.kind == "github" }
            backupStatuses = try JSONDecoder().decode([BackupStatus].self, from: statusData)
            backupError = ""
        } catch { backupError = error.localizedDescription }
    }

    private func backupLabel(_ library: BackupLibrary, _ status: BackupStatus?) -> String {
        guard library.auto_sync_enabled else { return library.sync_enabled ? "Manual" : "Off" }
        switch status?.phase {
        case "pending": return "Queued"
        case "syncing": return "Syncing"
        case "synced": return "Synced"
        case "error": return "Failed"
        default: return "Status unavailable"
        }
    }

    private func conciseError(_ error: String) -> String {
        let compact = error.split(whereSeparator: \.isWhitespace).joined(separator: " ")
        return compact.count > 100 ? String(compact.prefix(99)) + "…" : compact
    }

    private func renderBackupMenu() {
        backupMenu.removeAllItems()
        if !backupError.isEmpty {
            let item = NSMenuItem(title: "Status unavailable: " + conciseError(backupError), action: nil, keyEquivalent: "")
            item.toolTip = backupError
            item.isEnabled = false
            backupMenu.addItem(item)
        } else if backupLibraries.isEmpty {
            let item = NSMenuItem(title: backupLoading ? "Loading…" : "No connected repositories", action: nil, keyEquivalent: "")
            item.isEnabled = false
            backupMenu.addItem(item)
        }
        for library in backupLibraries {
            let status = backupStatuses.first { $0.library_id == library.id }
            let phase = backupLabel(library, status)
            let single = backupLibraries.count == 1
            let entry = NSMenuItem(title: "\(library.location) · \(phase)", action: nil, keyEquivalent: "")
            let submenu = single ? backupMenu : NSMenu(title: library.location)
            submenu.autoenablesItems = false
            if single { entry.isEnabled = false; submenu.addItem(entry) }
            let summary = NSMenuItem(title: phase, action: nil, keyEquivalent: "")
            summary.isEnabled = false
            summary.toolTip = status?.error.isEmpty == false ? status?.error : nil
            if !single { submenu.addItem(summary) }
            if phase == "Failed", let error = status?.error, !error.isEmpty {
                let message = NSMenuItem(title: "Error: " + conciseError(error), action: nil, keyEquivalent: "")
                message.isEnabled = false; submenu.addItem(message)
            }
            if let value = status?.last_synced_at, !value.isEmpty {
                let formatter = ISO8601DateFormatter()
                formatter.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
                let date = formatter.date(from: value) ?? ISO8601DateFormatter().date(from: value)
                let text = date.map { DateFormatter.localizedString(from: $0, dateStyle: .short, timeStyle: .short) } ?? value
                let last = NSMenuItem(title: "Last synced: " + text, action: nil, keyEquivalent: "")
                last.isEnabled = false; submenu.addItem(last)
            }
            submenu.addItem(.separator())
            let toggle = NSMenuItem(title: "Automatic Backup", action: #selector(toggleBackup(_:)), keyEquivalent: "")
            toggle.target = self; toggle.representedObject = library.id
            toggle.state = library.auto_sync_enabled ? .on : .off
            toggle.isEnabled = state == .running && !backupLoading && backupError.isEmpty
            submenu.addItem(toggle)
            let sync = NSMenuItem(title: phase == "Failed" ? "Retry Backup" : "Sync Now", action: #selector(syncBackup(_:)), keyEquivalent: "")
            sync.target = self; sync.representedObject = library.id
            sync.image = NSImage(systemSymbolName: "arrow.triangle.2.circlepath", accessibilityDescription: nil)
            sync.isEnabled = state == .running && !backupLoading && backupError.isEmpty && library.sync_enabled && phase != "Syncing"
            submenu.addItem(sync)
            if !single { entry.submenu = submenu; backupMenu.addItem(entry) }
        }
        backupMenu.addItem(.separator())
        let settings = NSMenuItem(title: "Open Backup Settings…", action: #selector(openBackupSettings), keyEquivalent: "")
        settings.target = self; settings.isEnabled = state == .running
        settings.image = NSImage(systemSymbolName: "gearshape", accessibilityDescription: nil)
        backupMenu.addItem(settings)
    }

    @objc private func openBackupSettings() {
        if let url = URL(string: "?settings=backup", relativeTo: workspaceURL) { NSWorkspace.shared.open(url.absoluteURL) }
    }

    @objc private func toggleBackup(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String,
              let library = backupLibraries.first(where: { $0.id == id }) else { return }
        changeBackup(id: id, action: "settings", body: ["auto_sync_enabled": !library.auto_sync_enabled])
    }

    @objc private func syncBackup(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String,
              let library = backupLibraries.first(where: { $0.id == id }) else { return }
        changeBackup(id: id, action: library.auto_sync_enabled ? "retry" : "sync", body: [:])
    }

    private func changeBackup(id: String, action: String, body: [String: Bool]) {
        guard state == .running, !backupLoading,
              let encoded = id.addingPercentEncoding(withAllowedCharacters: .urlPathAllowed),
              !encoded.contains("/") else { return }
        backupLoading = true
        renderBackupMenu()
        Task {
            do { _ = try await request("api/libraries/\(encoded)/\(action)", body: body, timeout: action == "sync" ? 240 : 15) }
            catch { showError("GitHub backup: \(error.localizedDescription)") }
            backupLoading = false
            await refreshBackup()
        }
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
            return reply["service"] == "artifact-studio" && reply["status"] == "ready"
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
            child.arguments = ["-m", "uvicorn", "artifact_studio.app:app", "--host", "127.0.0.1", "--port", String(configuration.port), "--no-access-log"]
            var environment = ProcessInfo.processInfo.environment
            environment["ARTIFACT_STUDIO_COLLECTION"] = configuration.collection
            environment["ARTIFACT_STUDIO_DATABASE"] = configuration.database
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
            showError("Could not start Artifact Studio: \(errorMessage)")
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
        alert.messageText = "Artifact Studio"
        alert.informativeText = message
        alert.alertStyle = .warning
        alert.runModal()
    }
}

@main
struct ArtifactStudioMenu {
    @MainActor static func main() {
        if let flag = CommandLine.arguments.firstIndex(of: "--icon-preview"),
           CommandLine.arguments.indices.contains(flag + 1) {
            let previewState: ServerState = CommandLine.arguments.contains("--running") ? .running : .stopped
            let image = menuBarImage(state: previewState)
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
