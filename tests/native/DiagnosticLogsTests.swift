import Foundation

private enum TestFailure: Error { case failed(String) }

private func check(_ condition: @autoclosure () throws -> Bool, _ message: String) throws {
    if try !condition() { throw TestFailure.failed(message) }
}

private func contents(_ directory: URL, _ name: String) throws -> Data {
    let url = directory.appendingPathComponent(name)
    return FileManager.default.fileExists(atPath: url.path) ? try Data(contentsOf: url) : Data()
}

private func retained(_ directory: URL, _ stream: String, count: Int = 5) throws -> Data {
    var result = Data()
    for index in (0..<count).reversed() {
        result.append(try contents(directory, stream + ".log" + (index == 0 ? "" : ".\(index)")))
    }
    return result
}

private func expectError(_ body: () throws -> Void) throws {
    do { try body() } catch { return }
    throw TestFailure.failed("Expected an error")
}

@main
struct DiagnosticLogsTests {
    static func main() throws {
        let files = FileManager.default
        let root = files.temporaryDirectory.appendingPathComponent(UUID().uuidString)
        try files.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? files.removeItem(at: root) }
        let dir = root.appendingPathComponent("logs")
        let logs = try DiagnosticLogs(directory: dir, maxBytes: 4, fileCount: 5)
        let input = Data("abcdefghijklmnopqrstuvwxyz".utf8)
        try logs.append(input, to: .server)
        try check(try retained(dir, "server") == input.suffix(20), "rotation must retain newest bytes in order")
        for index in 0..<5 {
            try check(try contents(dir, "server.log" + (index == 0 ? "" : ".\(index)")).count <= 4, "file exceeds limit")
        }
        try logs.append(Data("updates".utf8), to: .update)
        try check(try retained(dir, "update") == Data("updates".utf8), "streams must be independent")
        let sentinel = dir.appendingPathComponent("sentinel")
        try Data("keep".utf8).write(to: sentinel)
        try logs.clear()
        try check(try retained(dir, "server").isEmpty && retained(dir, "update").isEmpty, "clear must remove both streams")
        try check(try Data(contentsOf: sentinel) == Data("keep".utf8), "clear removed unrelated file")
        try logs.append(Data("next".utf8), to: .server)
        try check(try retained(dir, "server") == Data("next".utf8), "append after clear")

        let hugeDir = root.appendingPathComponent("huge")
        try files.createDirectory(at: hugeDir, withIntermediateDirectories: true)
        try Data(repeating: 65, count: 100_000).write(to: hugeDir.appendingPathComponent("server.log"))
        let bounded = try DiagnosticLogs(directory: hugeDir, maxBytes: 4)
        try check(try contents(hugeDir, "server.log") == Data(repeating: 65, count: 4), "existing oversized log must be bounded")
        try bounded.append(Data("B".utf8), to: .server)
        try check(try retained(hugeDir, "server") == Data("AAAAB".utf8), "bounded log rotation")

        let target = root.appendingPathComponent("target")
        try Data("protected".utf8).write(to: target)
        let unsafe = dir.appendingPathComponent("update.log.2")
        try files.createSymbolicLink(at: unsafe, withDestinationURL: target)
        try expectError { try logs.clear() }
        try expectError { try logs.append(Data("x".utf8), to: .server) }
        try check(try Data(contentsOf: target) == Data("protected".utf8), "symlink target changed")
        try check(try contents(dir, "server.log") == Data("next".utf8), "clear must validate all files first")
        try files.removeItem(at: unsafe)
        try files.createDirectory(at: unsafe, withIntermediateDirectories: false)
        try expectError { try logs.clear() }
        try files.removeItem(at: unsafe)
        try files.linkItem(at: target, to: unsafe)
        try expectError { try logs.clear() }
        try expectError { try logs.append(Data("x".utf8), to: .server) }
        try expectError { _ = try DiagnosticLogs(directory: dir, maxBytes: 4) }
        try check(try Data(contentsOf: target) == Data("protected".utf8), "hard-linked target changed")
        try check(try contents(dir, "server.log") == Data("next".utf8), "hard link must block clear before deletion")
        try files.removeItem(at: unsafe)
        let linkedDir = root.appendingPathComponent("linked")
        try files.createSymbolicLink(at: linkedDir, withDestinationURL: dir)
        try expectError { _ = try DiagnosticLogs(directory: linkedDir) }

        try expectError { _ = try DiagnosticLogs(directory: dir, maxBytes: 0) }
        try expectError { _ = try DiagnosticLogs(directory: dir, maxBytes: -1) }
        try expectError { _ = try DiagnosticLogs(directory: dir, fileCount: 0) }
        try expectError { _ = try DiagnosticLogs(directory: dir, fileCount: -1) }
        let singleDir = root.appendingPathComponent("single")
        let single = try DiagnosticLogs(directory: singleDir, maxBytes: 4, fileCount: 1)
        try single.append(Data("abcdefghij".utf8), to: .server)
        try check(try contents(singleDir, "server.log") == Data("ghij".utf8), "single-file rotation")
        try check(try contents(singleDir, "server.log.1").isEmpty, "single-file rotation created an archive")

        let concurrent = try DiagnosticLogs(directory: root.appendingPathComponent("concurrent"), maxBytes: 64)
        let failures = FailureCounter()
        DispatchQueue.concurrentPerform(iterations: 200) { index in
            do {
                if index % 7 == 0 { try concurrent.clear() }
                else { try concurrent.append(Data("abcdefgh".utf8), to: index % 2 == 0 ? .server : .update) }
            } catch { failures.record() }
        }
        try check(failures.count == 0, "concurrent append/clear failed")
        try concurrent.append(Data("end".utf8), to: .server)
        try check(try retained(root.appendingPathComponent("concurrent"), "server").suffix(3) == Data("end".utf8), "append after concurrent clear")
        print("DiagnosticLogs tests passed")
    }
}

private final class FailureCounter: @unchecked Sendable {
    private let lock = NSLock()
    private var value = 0
    var count: Int { lock.lock(); defer { lock.unlock() }; return value }
    func record() { lock.lock(); defer { lock.unlock() }; value += 1 }
}
