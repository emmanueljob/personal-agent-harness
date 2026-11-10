import Foundation
import SQLite3
import CryptoKit

struct BrainFailure: Error { let message: String }
let transient = unsafeBitCast(-1, to: sqlite3_destructor_type.self)

final class SQL {
    private var db: OpaquePointer?
    init(_ path: String, readOnly: Bool = false) throws {
        let flags = (readOnly ? SQLITE_OPEN_READONLY : SQLITE_OPEN_READWRITE | SQLITE_OPEN_CREATE) | SQLITE_OPEN_FULLMUTEX
        guard sqlite3_open_v2(path, &db, flags, nil) == SQLITE_OK else {
            if let db { sqlite3_close(db) }; db = nil
            throw BrainFailure(message: "Database unavailable. Check permissions and retry.")
        }
        sqlite3_busy_timeout(db, 2000)
    }
    deinit { sqlite3_close(db) }
    func run(_ sql: String, _ args: [String] = []) throws { _ = try rows(sql, args) }
    func rows(_ sql: String, _ args: [String] = []) throws -> [[String: String]] {
        var stmt: OpaquePointer?
        guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK else { throw BrainFailure(message: "Unsupported database schema or query.") }
        defer { sqlite3_finalize(stmt) }
        for (index, value) in args.enumerated() { sqlite3_bind_text(stmt, Int32(index + 1), value, -1, transient) }
        var output = [[String: String]]()
        while true {
            let code = sqlite3_step(stmt)
            if code == SQLITE_DONE { return output }
            guard code == SQLITE_ROW else { throw BrainFailure(message: "Database read or write failed. No partial batch was committed.") }
            var row = [String: String]()
            for i in 0..<sqlite3_column_count(stmt) {
                let key = String(cString: sqlite3_column_name(stmt, i))
                if sqlite3_column_type(stmt, i) != SQLITE_NULL, let value = sqlite3_column_text(stmt, i) { row[key] = String(cString: value) }
            }
            output.append(row)
        }
    }
}

struct SourceChoice: Identifiable, Codable, Hashable {
    let id: String
    let title: String
    let subtitle: String
}
struct Evidence: Identifiable, Codable {
    let id: String
    let source: String
    let kind: String
    let title: String
    let body: String
    let occurred: Double
    let metadata: [String: String]
}
struct ImportPlan: Codable {
    let id: String
    let calendars: [SourceChoice]
    let threads: [SourceChoice]
    let start: Double
    let end: Double
    var calendarIndex = 0
    var calendarWindow: Double
    var threadIndex = 0
    var messageCursor: Int64 = 0
    var processed = 0
    var skipped = 0
    var complete = false
    var stage: String { calendarIndex < calendars.count ? "Calendar" : threadIndex < threads.count ? "Messages" : "Finished" }
}

final class BrainStore {
    let sql: SQL
    static var home: URL { FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/PersonalBrain") }
    init(root: URL = BrainStore.home) throws {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
        try FileManager.default.setAttributes([.posixPermissions: 0o700], ofItemAtPath: root.path)
        let path = root.appendingPathComponent("brain.sqlite").path
        sql = try SQL(path)
        try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: path)
        // DELETE journal avoids long-lived personal-data sidecars. Batches are short transactions.
        try sql.run("PRAGMA journal_mode=DELETE")
        try sql.run("CREATE TABLE IF NOT EXISTS schema_version (version INTEGER NOT NULL)")
        let version = try sql.rows("SELECT version FROM schema_version")
        if version.isEmpty { try sql.run("INSERT INTO schema_version VALUES (1)") }
        else if version.first?["version"] != "1" { throw BrainFailure(message: "This brain needs a newer app version.") }
        try sql.run("CREATE TABLE IF NOT EXISTS evidence (id TEXT PRIMARY KEY, source TEXT NOT NULL, kind TEXT NOT NULL, title TEXT NOT NULL, body TEXT NOT NULL, occurred REAL NOT NULL, payload TEXT NOT NULL, hash TEXT NOT NULL, revision INTEGER NOT NULL DEFAULT 1)")
        try sql.run("CREATE INDEX IF NOT EXISTS evidence_source ON evidence(source)")
        try sql.run("CREATE TABLE IF NOT EXISTS imports (id TEXT PRIMARY KEY, payload TEXT NOT NULL, updated REAL NOT NULL)")
    }
    private func encode<T: Encodable>(_ value: T) throws -> String {
        let encoder = JSONEncoder(); encoder.outputFormatting = [.sortedKeys]
        return String(decoding: try encoder.encode(value), as: UTF8.self)
    }
    func commit(_ records: [Evidence], plan: ImportPlan) throws {
        try sql.run("BEGIN IMMEDIATE")
        do {
            for record in records {
                // Repair adds decoder state after import. Compare the source
                // semantically so Python/Swift JSON serialization cannot churn
                // the revision of an unchanged repaired message.
                if record.kind == "message",
                   record.metadata["body_status"] == "rich_body_or_attachment_not_decoded",
                   let raw = record.metadata["attributed_hex"], !raw.isEmpty,
                   let stored = try sql.rows("SELECT payload FROM evidence WHERE id=?", [record.id]).first?["payload"] {
                    let previous = try JSONDecoder().decode(Evidence.self, from: Data(stored.utf8))
                    var incomingMetadata = record.metadata
                    var storedMetadata = previous.metadata
                    for key in ["decode_attempt", "decoder_version", "body_status"] {
                        incomingMetadata.removeValue(forKey: key)
                        storedMetadata.removeValue(forKey: key)
                    }
                    if previous.metadata["decode_attempt"] != nil,
                       previous.source == record.source, previous.kind == record.kind,
                       previous.title == record.title, previous.occurred == record.occurred,
                       incomingMetadata == storedMetadata {
                        continue
                    }
                }
                let payload = try encode(record)
                let hash = SHA256.hash(data: Data(payload.utf8)).map { String(format: "%02x", $0) }.joined()
                try sql.run("INSERT INTO evidence(id,source,kind,title,body,occurred,payload,hash) VALUES (?,?,?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET source=excluded.source,kind=excluded.kind,title=excluded.title,body=excluded.body,occurred=excluded.occurred,payload=excluded.payload,hash=excluded.hash,revision=evidence.revision+1 WHERE evidence.hash<>excluded.hash", [record.id, record.source, record.kind, record.title, record.body, String(record.occurred), payload, hash])
            }
            try sql.run("INSERT INTO imports VALUES (?,?,?) ON CONFLICT(id) DO UPDATE SET payload=excluded.payload,updated=excluded.updated", [plan.id, try encode(plan), String(Date().timeIntervalSince1970)])
            try sql.run("COMMIT")
        } catch { try? sql.run("ROLLBACK"); throw error }
    }
    func latestPlan() throws -> ImportPlan? {
        guard let payload = try sql.rows("SELECT payload FROM imports ORDER BY updated DESC LIMIT 1").first?["payload"] else { return nil }
        return try JSONDecoder().decode(ImportPlan.self, from: Data(payload.utf8))
    }
    func count() throws -> Int { Int(try sql.rows("SELECT count(*) AS total FROM evidence").first?["total"] ?? "0") ?? 0 }
    func search(_ query: String) throws -> [Evidence] {
        let rows = try sql.rows("SELECT payload FROM evidence WHERE title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\' ORDER BY occurred DESC LIMIT 100", [pattern(query), pattern(query)])
        return try rows.compactMap { row in try row["payload"].map { try JSONDecoder().decode(Evidence.self, from: Data($0.utf8)) } }
    }
    private func pattern(_ query: String) -> String { "%" + query.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "%", with: "\\%").replacingOccurrences(of: "_", with: "\\_") + "%" }
    func coverage() throws -> [String] {
        try sql.rows("SELECT kind,count(*) AS total,min(occurred) AS earliest,max(occurred) AS latest FROM evidence GROUP BY kind ORDER BY kind").map { row in
            let start = Date(timeIntervalSince1970: Double(row["earliest"] ?? "0") ?? 0).formatted(date: .abbreviated, time: .omitted)
            let end = Date(timeIntervalSince1970: Double(row["latest"] ?? "0") ?? 0).formatted(date: .abbreviated, time: .omitted)
            return "\(row["kind"] ?? "Records"): \(row["total"] ?? "0") · \(start) – \(end)"
        }
    }
}
