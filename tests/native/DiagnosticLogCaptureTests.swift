import Foundation

@main
struct DiagnosticLogCaptureTests {
    static func require(_ condition: @autoclosure () -> Bool, _ message: String) throws {
        if !condition() { throw NSError(domain: message, code: 1) }
    }

    static func temporaryDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    static func child(_ script: String, capture: DiagnosticLogCapture) throws -> Process {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/bin/sh")
        process.arguments = ["-c", script]
        process.standardOutput = capture.pipe
        process.standardError = capture.pipe
        try process.run()
        try capture.pipe.fileHandleForWriting.close()
        return process
    }

    static func wait(_ process: Process) throws {
        let deadline = Date().addingTimeInterval(15)
        while process.isRunning && Date() < deadline { Thread.sleep(forTimeInterval: 0.01) }
        if process.isRunning {
            process.terminate()
            throw NSError(domain: "Child blocked while writing diagnostics", code: 1)
        }
        process.waitUntilExit()
        try require(process.terminationStatus == 0, "Child failed")
    }

    static func retained(_ directory: URL, stream: String, count: Int) throws -> Data {
        var result = Data()
        for index in stride(from: count - 1, through: 0, by: -1) {
            let name = "\(stream).log" + (index == 0 ? "" : ".\(index)")
            let url = directory.appendingPathComponent(name)
            if FileManager.default.fileExists(atPath: url.path) {
                result.append(try Data(contentsOf: url))
            }
        }
        return result
    }

    static func largeConcurrentOutput() throws {
        let directory = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let logs = try DiagnosticLogs(directory: directory, maxBytes: 32_768, fileCount: 5)
        let capture = try DiagnosticLogCapture(logs: logs, stream: .server)
        let process = try child("dd if=/dev/zero bs=65536 count=32 2>/dev/null & dd if=/dev/zero bs=65536 count=32 1>&2 2>/dev/null & wait; printf FINAL_TRAILING_BYTES", capture: capture)
        try wait(process)
        capture.finish()
        capture.finish()
        let data = try retained(directory, stream: "server", count: 5)
        try require(data.count <= 5 * 32_768, "Rotation exceeded configured bound")
        try require(data.count > 4 * 32_768, "Large output was not drained")
        try require(data.suffix(20) == Data("FINAL_TRAILING_BYTES".utf8).suffix(20), "Final bytes were lost")
        try require(capture.failureMessage == nil, "Unexpected capture error")
    }

    static func inheritedWriterDoesNotBlockFinish() throws {
        let directory = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let capture = try DiagnosticLogCapture(logs: DiagnosticLogs(directory: directory), stream: .update)
        let process = try child("sleep 3 & printf inherited-tail", capture: capture)
        try wait(process)
        let start = Date()
        capture.finish()
        try require(Date().timeIntervalSince(start) < 1, "Finish waited for inherited writer")
        let data = try retained(directory, stream: "update", count: 5)
        try require(data == Data("inherited-tail".utf8), "Inherited writer tail missing")
    }

    static func clearWhileChildActive() throws {
        let directory = try temporaryDirectory()
        defer { try? FileManager.default.removeItem(at: directory) }
        let signal = directory.appendingPathComponent("continue")
        let logs = try DiagnosticLogs(directory: directory)
        let capture = try DiagnosticLogCapture(logs: logs, stream: .server)
        let process = try child("printf before-clear; while [ ! -f '\(signal.path)' ]; do sleep 0.01; done; printf after-clear", capture: capture)
        let deadline = Date().addingTimeInterval(5)
        while !(FileManager.default.fileExists(atPath: directory.appendingPathComponent("server.log").path)) && Date() < deadline {
            Thread.sleep(forTimeInterval: 0.01)
        }
        try require(process.isRunning, "Child should be active during clear")
        try logs.clear()
        try Data().write(to: signal)
        try wait(process)
        capture.finish()
        let data = try retained(directory, stream: "server", count: 5)
        try require(data == Data("after-clear".utf8), "Logging did not resume cleanly after clear")
    }

    static func writeFailureStillDrains() throws {
        let directory = try temporaryDirectory()
        let logs = try DiagnosticLogs(directory: directory)
        let capture = try DiagnosticLogCapture(logs: logs, stream: .server)
        try FileManager.default.removeItem(at: directory)
        let process = try child("dd if=/dev/zero bs=65536 count=64 2>/dev/null", capture: capture)
        try wait(process)
        capture.finish()
        try require(capture.failureMessage != nil, "Missing write failure report")
    }

    static func main() throws {
        try largeConcurrentOutput()
        try inheritedWriterDoesNotBlockFinish()
        try clearWhileChildActive()
        try writeFailureStillDrains()
        print("DiagnosticLogCaptureTests passed")
    }
}
