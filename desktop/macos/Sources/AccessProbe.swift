import Foundation
import SQLite3

struct ProbeResult: Codable {
    let state: String
    let detail: String
    var count: Int? = nil
}

enum MessagesProbe {
    // Read only: no snapshot, message bodies, identifiers or attachments leave this connection.
    static func check(path: String) -> ProbeResult {
        var db: OpaquePointer?
        let status = sqlite3_open_v2(path, &db, SQLITE_OPEN_READONLY | SQLITE_OPEN_FULLMUTEX, nil)
        defer { if let db { sqlite3_close(db) } }
        guard status == SQLITE_OK, let db else {
            return ProbeResult(state: "unavailable", detail: "Cannot read the local Messages database. It may be absent or macOS may require Full Disk Access. No history was imported.")
        }
        sqlite3_busy_timeout(db, 1500)
        var statement: OpaquePointer?
        guard sqlite3_prepare_v2(db, "SELECT 1 FROM message LIMIT 1", -1, &statement, nil) == SQLITE_OK else {
            return ProbeResult(state: "unavailable", detail: "Database opened, but the message table could not be read. Access may be restricted or this schema is unsupported.")
        }
        defer { sqlite3_finalize(statement) }
        let step = sqlite3_step(statement)
        guard step == SQLITE_ROW || step == SQLITE_DONE else {
            return ProbeResult(state: "unavailable", detail: "The read check did not complete. Try again after checking permissions.")
        }
        return ProbeResult(state: "verified", detail: step == SQLITE_ROW
            ? "Read-only access verified. A message record exists; no text, participants or attachments were read. Local history may be incomplete."
            : "Read-only access verified. The message table is empty; local history may not have synced.")
    }
}

func runSyntheticTests() -> Bool {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    do {
        try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let path = root.appendingPathComponent("synthetic.sqlite").path
        var db: OpaquePointer?
        guard sqlite3_open(path, &db) == SQLITE_OK else { return false }
        guard sqlite3_exec(db, "CREATE TABLE message (text TEXT);", nil, nil, nil) == SQLITE_OK else { sqlite3_close(db); return false }
        sqlite3_close(db)
        guard MessagesProbe.check(path: path).state == "verified" else { return false }
        sqlite3_open(path, &db)
        sqlite3_exec(db, "INSERT INTO message VALUES ('synthetic fixture');", nil, nil, nil)
        sqlite3_close(db)
        let before = try Data(contentsOf: URL(fileURLWithPath: path))
        guard MessagesProbe.check(path: path).state == "verified" else { return false }
        guard try Data(contentsOf: URL(fileURLWithPath: path)) == before else { return false }
        let missing = root.appendingPathComponent("missing.sqlite").path
        guard MessagesProbe.check(path: missing).state == "unavailable", !FileManager.default.fileExists(atPath: missing) else { return false }
        let bad = root.appendingPathComponent("invalid.sqlite")
        try Data("synthetic invalid database".utf8).write(to: bad)
        guard MessagesProbe.check(path: bad.path).state == "unavailable" else { return false }
        let unsupported = root.appendingPathComponent("unsupported.sqlite").path
        sqlite3_open(unsupported, &db)
        sqlite3_exec(db, "CREATE TABLE other (id INTEGER);", nil, nil, nil)
        sqlite3_close(db)
        return MessagesProbe.check(path: unsupported).state == "unavailable"
    } catch { return false }
}
