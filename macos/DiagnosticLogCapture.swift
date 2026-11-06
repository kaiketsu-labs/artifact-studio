import Foundation
import Dispatch
#if canImport(Darwin)
import Darwin
#else
import Glibc
#endif

/// Drains child diagnostics independently of the main actor, including after a log write fails.
final class DiagnosticLogCapture: @unchecked Sendable {
    let pipe: Pipe
    private let logs: DiagnosticLogs
    private let stream: DiagnosticStream
    private let source: any DispatchSourceRead
    private let lock = NSLock()
    private var closed = false
    private var firstFailure: String?

    var failureMessage: String? {
        lock.lock()
        defer { lock.unlock() }
        return firstFailure
    }

    init(logs: DiagnosticLogs, stream: DiagnosticStream) throws {
        self.logs = logs
        self.stream = stream
        pipe = Pipe()
        let descriptor = pipe.fileHandleForReading.fileDescriptor
        let flags = fcntl(descriptor, F_GETFL)
        guard flags >= 0, fcntl(descriptor, F_SETFL, flags | O_NONBLOCK) >= 0 else {
            let code = errno
            try? pipe.fileHandleForReading.close()
            try? pipe.fileHandleForWriting.close()
            throw NSError(domain: NSPOSIXErrorDomain, code: Int(code))
        }
        source = DispatchSource.makeReadSource(
            fileDescriptor: descriptor,
            queue: DispatchQueue(label: "ArtifactStudio.diagnostics.\(stream.rawValue)")
        )
        let reader = pipe.fileHandleForReading
        // Close only after dispatch has stopped monitoring the descriptor. FileHandle's
        // readabilityHandler setter synchronizes onto its own callback queue on Linux.
        source.setCancelHandler { try? reader.close() }
        source.setEventHandler { [weak self] in self?.readAvailable() }
        source.resume()
    }

    private func readAvailable() {
        lock.lock()
        defer { lock.unlock() }
        guard !closed else { return }
        drain(maxBatches: 16)
    }

    /// Call after the child exits. Never waits for descendants that inherited the writer.
    func finish() {
        lock.lock()
        defer { lock.unlock() }
        guard !closed else { return }
        try? pipe.fileHandleForWriting.close()
        // More than the kernel pipe capacity, but finite even if an inherited writer is busy.
        drain(maxBatches: 256)
        closeReader()
    }

    private func drain(maxBatches: Int) {
        var buffer = [UInt8](repeating: 0, count: 65_536)
        for _ in 0..<maxBatches {
            guard !closed else { return }
            let count = buffer.withUnsafeMutableBytes { bytes in
                read(pipe.fileHandleForReading.fileDescriptor, bytes.baseAddress!, bytes.count)
            }
            if count > 0 {
                do {
                    try logs.append(Data(buffer.prefix(count)), to: stream)
                } catch {
                    recordFailure("Could not save \(stream.rawValue) diagnostics: \(error.localizedDescription)")
                }
            } else if count == 0 {
                closeReader()
                return
            } else {
                let code = errno
                if code == EINTR { continue }
                if code == EAGAIN || code == EWOULDBLOCK { return }
                recordFailure("Could not read \(stream.rawValue) diagnostics: \(String(cString: strerror(code)))")
                closeReader()
                return
            }
        }
    }

    private func recordFailure(_ message: String) {
        if firstFailure == nil { firstFailure = message }
    }

    private func closeReader() {
        guard !closed else { return }
        closed = true
        source.cancel()
    }

    deinit { finish() }
}
