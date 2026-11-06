import AppKit
import ServiceManagement

struct Configuration: Decodable, Sendable {
    let project: String
    let python: String
    let collection: String
    let database: String
    let port: Int
    let launchAtLogin: Bool
}

private struct UpdateReply: Decodable, Sendable {
    let current: String?
    let target: String?
    let commits: Int?
    let release_tag: String?
    let ahead: Bool?
    var error: String?
    let restart_safe: Bool?
}

private struct AppInfo: Decodable {
    let latest_tag: String?
    let update_available: Bool
    let update_check: String
    let include_nightly: Bool
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

private struct NavigationEntry: Decodable, Equatable {
    let id: String
    let title: String
    let url: String
    let project_id: String
    let project_title: String
    let harness: String
}

private struct NavigationSnapshot: Decodable, Equatable {
    var connected: [NavigationEntry]
    let recent: [NavigationEntry]
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
    private var serverLog: DiagnosticLogCapture?
    private var diagnosticLogs: DiagnosticLogs?
    private var state: ServerState = .stopped
    private var polling = false
    private var restartPending = false
    private var quitting = false
    private var external = false
    private var updating = false
    private var updateLabel = ""
    private var latestTag: String?
    private var updateAvailable = false
    private var includeNightly = false
    private var updatePreferenceLoading = false
    private var appInfoUpdated = Date.distantPast
    private var appInfoLoading = false
    private var appInfoGeneration = 0
    private var errorMessage = ""
    private let menu = NSMenu()
    private let settingsMenu = NSMenu(title: "Settings")
    private let backupItem = NSMenuItem(title: "GitHub Backup", action: nil, keyEquivalent: "")
    private let backupMenu = NSMenu(title: "GitHub Backup")
    private var backupLibraries: [BackupLibrary] = []
    private var backupStatuses: [BackupStatus] = []
    private var backupLoading = false
    private var backupError = ""
    private var navigation = NavigationSnapshot(connected: [], recent: [])
    private var renderedNavigation: NavigationSnapshot?
    private var navigationItems: [NSMenuItem] = []
    private var navigationLoading = false
    private var navigationUpdated = Date.distantPast
    private var navigationEnabled = false
    private let status = NSMenuItem(title: "", action: nil, keyEquivalent: "")
    private let startItem = NSMenuItem(title: "Start", action: #selector(start), keyEquivalent: "")
    private let stopItem = NSMenuItem(title: "Stop", action: #selector(stop), keyEquivalent: "")
    private let restartItem = NSMenuItem(title: "Restart", action: #selector(restart), keyEquivalent: "")
    private let updateItem = NSMenuItem(title: "Update…", action: #selector(updateApplication), keyEquivalent: "")
    private let nightlyItem = NSMenuItem(title: "Include Nightly Builds", action: #selector(toggleNightly), keyEquivalent: "")
    private let loginItem = NSMenuItem(title: "Launch at Login", action: #selector(toggleLogin), keyEquivalent: "")
    private let approvalItem = NSMenuItem(title: "Allow in System Settings…", action: #selector(loginSettings), keyEquivalent: "")
    private let clearLogsItem = NSMenuItem(title: "Clear Diagnostic Logs…", action: #selector(clearDiagnosticLogs), keyEquivalent: "")

    private var workspaceURL: URL { URL(string: "http://127.0.0.1:\(configuration.port)/")! }
    private var installedVersion: String {
        let version = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "Unknown"
        let tag = Bundle.main.object(forInfoDictionaryKey: "ArtifactStudioReleaseTag") as? String ?? ""
        return version + (tag.hasPrefix("nightly-") ? " · Nightly" : "")
    }
    private var logURL: URL {
        FileManager.default.homeDirectoryForCurrentUser
            .appendingPathComponent("Library/Logs/\(Bundle.main.bundleIdentifier!)/server.log")
    }
    private var updateLogURL: URL { logURL.deletingLastPathComponent().appendingPathComponent("update.log") }
    private func logs() throws -> DiagnosticLogs {
        if let diagnosticLogs { return diagnosticLogs }
        let logs = try DiagnosticLogs(directory: logURL.deletingLastPathComponent())
        diagnosticLogs = logs
        return logs
    }
    private var childEnvironment: [String: String] {
        var environment = ProcessInfo.processInfo.environment
        environment["ARTIFACT_STUDIO_COLLECTION"] = configuration.collection
        environment["ARTIFACT_STUDIO_DATABASE"] = configuration.database
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        environment["PATH"] = "\(home)/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin:" + (environment["PATH"] ?? "")
        return environment
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
        settingsMenu.autoenablesItems = false
        menu.delegate = self
        backupMenu.autoenablesItems = false
        backupItem.image = NSImage(systemSymbolName: "externaldrive.badge.icloud", accessibilityDescription: nil)
        backupItem.submenu = backupMenu
        status.isEnabled = false
        menu.addItem(status)
        updateItem.target = self
        updateItem.image = NSImage(systemSymbolName: "arrow.down.circle", accessibilityDescription: nil)
        menu.addItem(updateItem)
        menu.addItem(.separator())
        add("Open Artifact Studio", symbol: "arrow.up.right.square", action: #selector(openWorkspace), key: "o")
        for (item, symbol) in [(startItem, "play.fill"), (stopItem, "stop.fill"), (restartItem, "arrow.clockwise")] {
            item.target = self
            item.image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)
            menu.addItem(item)
        }
        menu.addItem(.separator())
        let settings = NSMenuItem(title: "Settings", action: nil, keyEquivalent: "")
        settings.image = NSImage(systemSymbolName: "gearshape", accessibilityDescription: nil)
        settings.submenu = settingsMenu
        menu.addItem(settings)
        loginItem.target = self
        loginItem.image = NSImage(systemSymbolName: "power", accessibilityDescription: nil)
        settingsMenu.addItem(loginItem)
        approvalItem.target = self
        settingsMenu.addItem(approvalItem)
        nightlyItem.target = self
        nightlyItem.toolTip = "Include preview builds from main. May be less stable."
        settingsMenu.addItem(nightlyItem)
        settingsMenu.addItem(.separator())
        settingsMenu.addItem(backupItem)
        add("Show Logs", symbol: "doc.text", action: #selector(showLogs))
        clearLogsItem.target = self
        clearLogsItem.image = NSImage(systemSymbolName: "trash", accessibilityDescription: nil)
        menu.addItem(clearLogsItem)
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
        if !CommandLine.arguments.contains("--keep-stopped") { start() }
    }

    private func add(_ title: String, symbol: String, action: Selector, key: String = "") {
        let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
        item.target = self
        item.image = NSImage(systemSymbolName: symbol, accessibilityDescription: nil)
        menu.addItem(item)
    }

    private func updateMenu() {
        status.title = updating ? updateLabel : external ? "Running · started elsewhere" : state.rawValue
        status.toolTip = errorMessage.isEmpty ? workspaceURL.absoluteString : errorMessage
        statusItem.button?.toolTip = "Artifact Studio · \(status.title)"
        statusItem.button?.setAccessibilityValue(status.title)
        statusItem.button?.image = menuBarImage(state: updating ? .starting : state)
        backupItem.isEnabled = !updating && state == .running
        if state != .running { backupLibraries = []; backupStatuses = []; backupError = "" }
        renderBackupMenu()
        renderNavigation()
        startItem.isEnabled = !updating && (state == .stopped || state == .failed)
        stopItem.isEnabled = !updating && process?.isRunning == true && state != .stopping
        restartItem.isEnabled = !updating && process?.isRunning == true && state == .running
        clearLogsItem.isEnabled = !updating
        let hasServer = process?.isRunning == true || state == .running || state == .starting
        startItem.isHidden = hasServer
        stopItem.isHidden = !hasServer
        updateItem.isEnabled = updateAvailable && !updating && state != .starting && state != .stopping
        let updateAction = updating ? updateLabel : updateAvailable ? "Update… (\(latestTag ?? "New version"))" : ""
        updateItem.title = "Version \(installedVersion)" + (updateAction.isEmpty ? "" : "    \(updateAction)")
        updateItem.toolTip = "Installed version: \(installedVersion)" + (updateAction.isEmpty ? "" : ". \(updateAction)")
        updateItem.image = updateAvailable ? NSImage(systemSymbolName: "arrow.down.circle.fill", accessibilityDescription: "Update available") : nil
        nightlyItem.state = includeNightly ? .on : .off
        nightlyItem.isEnabled = state == .running && !updating && !updatePreferenceLoading
        let login = SMAppService.mainApp.status
        loginItem.state = login == .enabled ? .on : login == .requiresApproval ? .mixed : .off
        approvalItem.isHidden = login != .requiresApproval
    }

    func menuWillOpen(_ menu: NSMenu) {
        guard menu === self.menu, state == .running else { return }
        Task { await refreshBackup() }
        Task { await refreshNavigation() }
        Task { await refreshAppInfo() }
    }

    private func refreshNavigation() async {
        guard state == .running, !navigationLoading else { return }
        navigationLoading = true
        defer { navigationLoading = false; renderNavigation() }
        do {
            navigation = try JSONDecoder().decode(NavigationSnapshot.self, from: await request("api/navigation", timeout: 5))
            navigationUpdated = Date()
        } catch { navigation.connected = [] }
    }

    private func renderNavigation() {
        let enabled = state == .running && !updating
        var visible = navigation
        if !enabled { visible.connected = [] }
        guard renderedNavigation != visible || navigationEnabled != enabled else { return }
        renderedNavigation = visible
        navigationEnabled = enabled
        for item in navigationItems { menu.removeItem(item) }
        navigationItems = []
        for (title, entries) in [("Connected", visible.connected), ("Recent Projects", visible.recent)] where !entries.isEmpty {
            let heading = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            let submenu = title == "Recent Projects" ? NSMenu(title: title) : nil
            heading.isEnabled = submenu != nil && enabled
            heading.submenu = submenu
            navigationItems.append(heading)
            for entry in entries {
                let item = NSMenuItem(title: entry.title, action: #selector(openNavigation), keyEquivalent: "")
                item.target = self
                item.representedObject = entry.url
                item.isEnabled = enabled
                if entry.harness.isEmpty {
                    let agents = visible.connected.filter { $0.project_id == entry.project_id }.map { $0.harness == "codex" ? "Codex" : $0.harness == "claude-code" ? "Claude Code" : $0.harness }
                    if agents.isEmpty {
                        item.image = NSImage(systemSymbolName: "square.stack.3d.up", accessibilityDescription: nil)
                        item.toolTip = entry.title
                    } else {
                        item.image = NSImage(systemSymbolName: "powerplug.fill", accessibilityDescription: "Agent connected")?.withSymbolConfiguration(NSImage.SymbolConfiguration(paletteColors: [.systemGreen]))
                        item.image?.isTemplate = false
                        item.toolTip = "\(entry.title) · \(Set(agents).sorted().joined(separator: ", ")) connected"
                    }
                } else {
                    let image = NSImage(size: NSSize(width: 16, height: 16), flipped: false) { _ in
                        NSColor.systemGreen.setFill()
                        NSBezierPath(ovalIn: NSRect(x: 5, y: 5, width: 6, height: 6)).fill()
                        return true
                    }
                    image.isTemplate = false
                    item.image = image
                    item.toolTip = "\(entry.project_title) · \(entry.harness == "codex" ? "Codex" : entry.harness == "claude-code" ? "Claude Code" : entry.harness) connected"
                }
                if let submenu { submenu.addItem(item) } else { navigationItems.append(item) }
            }
            navigationItems.append(.separator())
        }
        for (offset, item) in navigationItems.enumerated() { menu.insertItem(item, at: 3 + offset) }
    }

    @objc private func openNavigation(_ sender: NSMenuItem) {
        guard state == .running, let path = sender.representedObject as? String,
              let url = URL(string: path, relativeTo: workspaceURL)?.absoluteURL,
              url.host == workspaceURL.host, url.port == workspaceURL.port else { return }
        NSWorkspace.shared.open(url)
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

    private func refreshAppInfo() async {
        guard state == .running, !appInfoLoading, !updatePreferenceLoading,
              Date().timeIntervalSince(appInfoUpdated) >= 60 else { return }
        appInfoLoading = true
        let generation = appInfoGeneration
        appInfoUpdated = Date()
        defer { appInfoLoading = false; updateMenu() }
        do {
            let info = try JSONDecoder().decode(AppInfo.self, from: await request("api/app-info", timeout: 5))
            if generation == appInfoGeneration { applyAppInfo(info) }
        } catch { if generation == appInfoGeneration { latestTag = nil; updateAvailable = false } }
    }

    private func applyAppInfo(_ info: AppInfo) {
        includeNightly = info.include_nightly
        latestTag = info.latest_tag
        updateAvailable = ["ok", "checking"].contains(info.update_check) && info.update_available
        if info.update_check == "checking" { appInfoUpdated = .distantPast }
    }

    @objc private func toggleNightly() {
        guard state == .running, !updating, !updatePreferenceLoading else { return }
        updatePreferenceLoading = true
        appInfoGeneration += 1
        updateMenu()
        Task {
            defer { updatePreferenceLoading = false; updateMenu() }
            do {
                let data = try await request("api/app-info/settings", body: ["include_nightly": !includeNightly])
                applyAppInfo(try JSONDecoder().decode(AppInfo.self, from: data))
            } catch { showError("Could not save the update preference: \(error.localizedDescription)") }
        }
    }

    private func poll() async {
        guard !updating, !polling, state != .stopping else { return }
        polling = true
        let ready = await isReady()
        polling = false
        guard !updating, state != .stopping else { return }
        if ready {
            state = .running
            external = process == nil
        } else if state == .running {
            state = process?.isRunning == true ? .starting : .stopped
            external = false
        }
        if let failure = serverLog?.failureMessage { errorMessage = "Diagnostic logging: \(failure)" }
        updateMenu()
        if ready { await refreshAppInfo() }
        if ready && Date().timeIntervalSince(navigationUpdated) > 10 { await refreshNavigation() }
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
            let log = try DiagnosticLogCapture(logs: logs(), stream: .server)
            let child = Process()
            child.executableURL = URL(fileURLWithPath: configuration.python)
            child.currentDirectoryURL = URL(fileURLWithPath: configuration.project)
            child.arguments = ["-m", "uvicorn", "artifact_studio.app:app", "--host", "127.0.0.1", "--port", String(configuration.port), "--no-access-log", "--timeout-graceful-shutdown", "10"]
            child.environment = childEnvironment
            child.standardInput = FileHandle.nullDevice
            child.standardOutput = log.pipe
            child.standardError = log.pipe
            child.terminationHandler = { [weak self, log] terminated in
                let code = terminated.terminationStatus
                log.finish()
                Task { @MainActor in self?.serverEnded(code: code, logFailure: log.failureMessage) }
            }
            do { try child.run() }
            catch { log.finish(); throw error }
            try? log.pipe.fileHandleForWriting.close()
            serverLog = log
            process = child
        } catch {
            state = .failed
            errorMessage = error.localizedDescription
            updateMenu()
            showError("Could not start Artifact Studio: \(errorMessage)")
        }
    }

    private func serverEnded(code: Int32, logFailure: String?) {
        let expected = state == .stopping
        process = nil
        serverLog = nil
        external = false
        state = expected ? .stopped : .failed
        if !expected { errorMessage = "Server exited (\(code)). See Show Logs." }
        if let logFailure { errorMessage += " Diagnostic logging: \(logFailure)" }
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

    @objc private func updateApplication() {
        guard updateAvailable, !updating, state != .starting, state != .stopping else { return }
        let selectedNightly = includeNightly
        updating = true
        updateLabel = "Checking for updates…"
        updateMenu()
        Task {
            var restoreRunning = false
            do {
                if await isReady(), process?.isRunning != true {
                    throw BackupRequestError.response("This server was started elsewhere. Stop it there before updating.")
                }
                let plan = try await runUpdater(["check"] + (selectedNightly ? ["--include-nightly"] : []))
                if let error = plan.error { throw BackupRequestError.response(error) }
                guard let current = plan.current, let target = plan.target else {
                    throw BackupRequestError.response("The update check returned no version. See Show Logs.")
                }
                guard current != target else {
                    let release = plan.release_tag ?? "the latest available version"
                    let detail = plan.ahead == true
                        ? "This checkout is newer than \(release). No downgrade was installed."
                        : "Artifact Studio is on \(release)."
                    showInformation("You’re up to date", detail)
                    updateAvailable = false
                    updating = false; updateMenu(); return
                }
                restoreRunning = process?.isRunning == true
                if restoreRunning {
                    let alert = NSAlert()
                    alert.messageText = "Update Artifact Studio?"
                    let release = plan.release_tag ?? "the latest available version"
                    alert.informativeText = "Install \(release)? The server will stop, update and restart. Active agent connections will be interrupted. Save your work and finish active tasks before continuing."
                    alert.alertStyle = .warning
                    alert.addButton(withTitle: "Update and Restart")
                    alert.addButton(withTitle: "Cancel").keyEquivalent = "\u{1b}"
                    guard alert.runModal() == .alertFirstButtonReturn else {
                        updating = false; updateMenu(); return
                    }
                }
                updateLabel = restoreRunning ? "Stopping for update…" : "Updating…"
                updateMenu()
                if restoreRunning {
                    stop()
                    let deadline = ContinuousClock.now.advanced(by: .seconds(25))
                    while process?.isRunning == true && ContinuousClock.now < deadline {
                        try await Task.sleep(for: .milliseconds(100))
                    }
                    guard process?.isRunning != true else {
                        restoreRunning = false
                        throw BackupRequestError.response("The server is still stopping. No update was installed. Try Update after it stops.")
                    }
                }
                guard await isReady() == false else {
                    restoreRunning = false
                    throw BackupRequestError.response("The server port is still in use. No update was installed.")
                }
                updateLabel = "Updating…"; updateMenu()
                var arguments = ["apply", "--current", current, "--target", target,
                    "--application", Bundle.main.bundleURL.path,
                    "--collection", configuration.collection, "--database", configuration.database,
                    "--port", String(configuration.port)]
                if let release = plan.release_tag { arguments += ["--release-tag", release] }
                if selectedNightly { arguments.append("--include-nightly") }
                if configuration.launchAtLogin { arguments.append("--launch-at-login") }
                if Bundle.main.bundleIdentifier?.hasSuffix(".qa") == true { arguments.append("--qa") }
                let shouldRestart = restoreRunning
                restoreRunning = false
                let result = try await runUpdater(arguments)
                if let error = result.error {
                    restoreRunning = result.restart_safe == true && shouldRestart
                    throw BackupRequestError.response(error)
                }
                try relaunchAfterUpdate(running: shouldRestart)
                updating = false
                NSApp.terminate(nil)
            } catch {
                updating = false; updateMenu()
                if restoreRunning && process?.isRunning != true { start() }
                showError("Could not update Artifact Studio: \(error.localizedDescription)")
            }
        }
    }

    private func runUpdater(_ arguments: [String]) async throws -> UpdateReply {
        let python = configuration.python, project = configuration.project
        let environment = childEnvironment, diagnostics = try logs()
        var result = try await Task.detached {
            try Self.executeUpdater(python: python, project: project, arguments: arguments, environment: environment, logs: diagnostics)
        }.value
        if let failure = result.logFailure {
            if let error = result.reply.error {
                result.reply.error = "\(error) Diagnostic logging: \(failure)"
            } else {
                showInformation("Update diagnostics unavailable", "The update command finished, but its diagnostic log could not be saved: \(failure)")
            }
        }
        return result.reply
    }

    nonisolated private static func executeUpdater(python: String, project: String, arguments: [String], environment: [String: String], logs: DiagnosticLogs) throws -> (reply: UpdateReply, logFailure: String?) {
        let output = Pipe(), errors = try DiagnosticLogCapture(logs: logs, stream: .update)
        defer { errors.finish(); try? output.fileHandleForReading.close() }
        let child = Process()
        child.executableURL = URL(fileURLWithPath: python)
        child.currentDirectoryURL = URL(fileURLWithPath: project)
        child.arguments = ["-m", "artifact_studio.updater"] + arguments
        child.environment = environment
        child.standardInput = FileHandle.nullDevice
        child.standardOutput = output
        child.standardError = errors.pipe
        try child.run()
        try? errors.pipe.fileHandleForWriting.close()
        let data = output.fileHandleForReading.readDataToEndOfFile()
        child.waitUntilExit()
        errors.finish()
        guard let reply = try? JSONDecoder().decode(UpdateReply.self, from: data) else {
            if let failure = errors.failureMessage {
                throw BackupRequestError.response("The updater returned an unreadable response. Diagnostic logging: \(failure)")
            }
            throw BackupRequestError.response("The updater could not finish. See update.log beside the server log.")
        }
        return (reply, errors.failureMessage)
    }

    private func relaunchAfterUpdate(running: Bool) throws {
        let child = Process()
        child.executableURL = URL(fileURLWithPath: configuration.python)
        child.currentDirectoryURL = URL(fileURLWithPath: configuration.project)
        child.environment = childEnvironment
        child.arguments = ["-c", """
import os, subprocess, sys, time
pid = int(sys.argv[1])
for _ in range(200):
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        subprocess.run(['/usr/bin/open', '-n', sys.argv[2], '--args'] + sys.argv[3:], check=True)
        break
    time.sleep(0.1)
""", String(ProcessInfo.processInfo.processIdentifier), Bundle.main.bundleURL.path] + (running ? [] : ["--keep-stopped"])
        child.standardInput = FileHandle.nullDevice
        child.standardOutput = FileHandle.nullDevice
        child.standardError = FileHandle.nullDevice
        try child.run()
    }

    @objc private func openWorkspace() { NSWorkspace.shared.open(workspaceURL) }
    @objc private func showLogs() { NSWorkspace.shared.activateFileViewerSelecting([logURL, updateLogURL].filter { FileManager.default.fileExists(atPath: $0.path) }) }
    @objc private func clearDiagnosticLogs() {
        guard !updating else { return }
        let alert = NSAlert()
        alert.messageText = "Clear diagnostic logs?"
        alert.informativeText = "Deletes server and update logs, including their rotated copies. Projects, conversations, version history and backups are kept. New diagnostics continue to be recorded."
        let cancel = alert.addButton(withTitle: "Cancel")
        cancel.keyEquivalent = "\u{1b}"
        alert.window.defaultButtonCell = cancel.cell as? NSButtonCell
        alert.addButton(withTitle: "Clear Logs").hasDestructiveAction = true
        guard alert.runModal() == .alertSecondButtonReturn else { return }
        do {
            try logs().clear()
            showInformation("Diagnostic logs cleared", "New diagnostics will be recorded as needed.")
        } catch { showError("Could not clear all diagnostic logs. Some may already be cleared. \(error.localizedDescription)") }
    }
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
        if updating { showInformation("Update in progress", "Wait for the update to finish before quitting."); return .terminateCancel }
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

    private func showInformation(_ title: String, _ message: String) {
        let alert = NSAlert()
        alert.messageText = title
        alert.informativeText = message
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
