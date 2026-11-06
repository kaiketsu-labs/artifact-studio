import Foundation

enum DiagnosticStream: String, CaseIterable, Sendable {
    case server, update
}

final class DiagnosticLogs: @unchecked Sendable {
    private enum LogError: LocalizedError {
        case invalidConfiguration
        case unsafeDirectory(URL)
        case unsafeFile(URL)
        case cannotCreateFile(URL)

        var errorDescription: String? {
            switch self {
            case .invalidConfiguration:
                return "The diagnostic log directory or size limits are invalid."
            case .unsafeDirectory(let url):
                return "The diagnostic log directory is unsafe or unavailable: \(url.path)"
            case .unsafeFile(let url):
                return "The diagnostic log file is unsafe: \(url.path)"
            case .cannotCreateFile(let url):
                return "Could not create the diagnostic log file: \(url.path)"
            }
        }
    }

    private let directory: URL
    private let maxBytes: Int
    private let fileCount: Int
    private let lock = NSLock()
    private let files = FileManager.default

    init(directory: URL, maxBytes: Int = 10_000_000, fileCount: Int = 5) throws {
        guard directory.isFileURL, maxBytes > 0, fileCount > 0 else {
            throw LogError.invalidConfiguration
        }
        self.directory = directory.standardizedFileURL
        self.maxBytes = maxBytes
        self.fileCount = fileCount
        if !files.fileExists(atPath: self.directory.path) {
            try files.createDirectory(at: self.directory, withIntermediateDirectories: true)
        }
        try validateDirectory()
        try validateFiles()
        for stream in DiagnosticStream.allCases {
            for index in 0..<fileCount {
                try boundFile(at: url(for: stream, index: index))
            }
        }
    }

    func append(_ data: Data, to stream: DiagnosticStream) throws {
        lock.lock()
        defer { lock.unlock() }
        try validateDirectory()
        try validateFiles()
        var offset = 0
        // A large write replaces the whole retention window; align its first
        // byte with a file boundary so no part of the newest window is lost.
        let (capacity, overflow) = maxBytes.multipliedReportingOverflow(by: fileCount)
        if !overflow && data.count >= capacity {
            for index in 0..<fileCount {
                let file = url(for: stream, index: index)
                if try attributesIfPresent(at: file) != nil { try files.removeItem(at: file) }
            }
            offset = data.count - capacity
        }
        while offset < data.count {
            let active = url(for: stream, index: 0)
            try boundFile(at: active)
            var size = try fileSize(at: active)
            if size == maxBytes {
                try rotate(stream)
                size = 0
            }
            let count = min(maxBytes - size, data.count - offset)
            if !files.fileExists(atPath: active.path) {
                guard files.createFile(atPath: active.path, contents: nil) else {
                    throw LogError.cannotCreateFile(active)
                }
            }
            let handle = try FileHandle(forWritingTo: active)
            do {
                try handle.seekToEnd()
                try handle.write(contentsOf: data.subdata(in: offset..<(offset + count)))
                try handle.close()
            } catch {
                try? handle.close()
                throw error
            }
            offset += count
        }
    }

    func clear() throws {
        lock.lock()
        defer { lock.unlock() }
        try validateDirectory()
        try validateFiles()
        for stream in DiagnosticStream.allCases {
            for index in 0..<fileCount {
                let file = url(for: stream, index: index)
                if try attributesIfPresent(at: file) != nil {
                    try files.removeItem(at: file)
                }
            }
        }
    }

    private func url(for stream: DiagnosticStream, index: Int) -> URL {
        let name = "\(stream.rawValue).log" + (index == 0 ? "" : ".\(index)")
        return directory.appendingPathComponent(name, isDirectory: false)
    }

    private func attributesIfPresent(at url: URL) throws -> [FileAttributeKey: Any]? {
        do {
            return try files.attributesOfItem(atPath: url.path)
        } catch let error as NSError where error.domain == NSCocoaErrorDomain && error.code == NSFileReadNoSuchFileError {
            return nil
        }
    }

    private func validateDirectory() throws {
        guard let attributes = try attributesIfPresent(at: directory),
              attributes[.type] as? FileAttributeType == .typeDirectory else {
            throw LogError.unsafeDirectory(directory)
        }
    }

    private func validateFiles() throws {
        for stream in DiagnosticStream.allCases {
            for index in 0..<fileCount {
                let file = url(for: stream, index: index)
                if let attributes = try attributesIfPresent(at: file) {
                    guard attributes[.type] as? FileAttributeType == .typeRegular,
                          let links = attributes[.referenceCount] as? NSNumber,
                          links.intValue == 1 else {
                        throw LogError.unsafeFile(file)
                    }
                }
            }
        }
    }

    private func fileSize(at url: URL) throws -> Int {
        guard let attributes = try attributesIfPresent(at: url) else { return 0 }
        return (attributes[.size] as? NSNumber)?.intValue ?? 0
    }

    private func boundFile(at url: URL) throws {
        let size = try fileSize(at: url)
        guard size > maxBytes else { return }
        let reader = try FileHandle(forReadingFrom: url)
        let tail: Data
        do {
            try reader.seek(toOffset: UInt64(size - maxBytes))
            tail = try reader.read(upToCount: maxBytes) ?? Data()
            try reader.close()
        } catch {
            try? reader.close()
            throw error
        }
        let writer = try FileHandle(forWritingTo: url)
        do {
            try writer.truncate(atOffset: 0)
            try writer.write(contentsOf: tail)
            try writer.close()
        } catch {
            try? writer.close()
            throw error
        }
    }

    private func rotate(_ stream: DiagnosticStream) throws {
        let oldest = url(for: stream, index: fileCount - 1)
        if try attributesIfPresent(at: oldest) != nil {
            try files.removeItem(at: oldest)
        }
        if fileCount > 1 {
            for index in stride(from: fileCount - 2, through: 0, by: -1) {
                let source = url(for: stream, index: index)
                if try attributesIfPresent(at: source) != nil {
                    try files.moveItem(at: source, to: url(for: stream, index: index + 1))
                }
            }
        }
    }
}
