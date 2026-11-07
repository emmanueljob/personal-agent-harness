import Foundation

struct MessageBatch { let records: [Evidence]; let cursor: Int64; let scanned: Int; let skipped: Int }

enum MessagesSource {
    static var path: String { FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Messages/chat.db").path }
    static func choices(path: String = path) throws -> [SourceChoice] {
        let db = try SQL(path, readOnly: true)
        return try db.rows("SELECT ROWID AS id, guid, display_name, chat_identifier FROM chat ORDER BY ROWID DESC").compactMap { row in
            guard let id = row["id"], let guid = row["guid"], !guid.isEmpty else { return nil }
            let display = row["display_name"]?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
            let address = row["chat_identifier"] ?? ""
            return SourceChoice(id: guid, title: display.isEmpty ? (address.isEmpty ? "Conversation \(id)" : address) : display, subtitle: "Local conversation · \(id)")
        }
    }
    static func batch(thread: SourceChoice, start: Double, end: Double, after: Int64, path: String = path) throws -> MessageBatch {
        let db = try SQL(path, readOnly: true)
        let columns = Set(try db.rows("PRAGMA table_info(message)").compactMap { $0["name"] })
        let required: Set<String> = ["guid", "text", "date", "is_from_me", "handle_id"]
        guard required.isSubset(of: columns) else { throw BrainFailure(message: "This Messages schema is not supported. No batch was imported.") }
        let present = try db.rows("SELECT ROWID FROM chat WHERE guid=?", [thread.id])
        guard !present.isEmpty else { throw BrainFailure(message: "A selected conversation is no longer available. The import is paused.") }
        let optional = ["service", "date_edited", "date_retracted", "associated_message_type", "associated_message_guid"].filter { columns.contains($0) }.map { "m.\($0)" }.joined(separator: ",")
        let extra = optional.isEmpty ? "" : "," + optional
        let rawBody = columns.contains("attributedBody") ? ",hex(m.attributedBody) AS attributed_hex" : ""
        // Both historical seconds and newer nanoseconds are compared in Apple epoch seconds.
        let seconds = "(CASE WHEN abs(m.date)>100000000000 THEN m.date/1000000000.0 ELSE m.date END)"
        let rows = try db.rows("SELECT m.ROWID AS row_id,m.guid,m.text,m.date,m.is_from_me,h.id AS sender\(extra)\(rawBody) FROM message m JOIN chat_message_join j ON j.message_id=m.ROWID JOIN chat c ON c.ROWID=j.chat_id LEFT JOIN handle h ON h.ROWID=m.handle_id WHERE c.guid=? AND m.ROWID>? AND \(seconds)>=CAST(? AS REAL) AND \(seconds)<CAST(? AS REAL) ORDER BY m.ROWID LIMIT 200", [thread.id, String(after), String(start - 978307200), String(end - 978307200)])
        var records = [Evidence](); var skipped = 0
        for row in rows {
            guard let guid = row["guid"], !guid.isEmpty, let date = Double(row["date"] ?? "") else { skipped += 1; continue }
            let timestamp = (abs(date) > 100000000000 ? date / 1_000_000_000 : date) + 978307200
            var metadata = row
            metadata.removeValue(forKey: "text")
            metadata["speaker"] = row["is_from_me"] == "1" ? "owner" : (row["sender"] ?? "unknown")
            metadata["thread"] = thread.title
            let retracted = Double(row["date_retracted"] ?? "0") ?? 0
            if retracted > 0 {
                metadata.removeValue(forKey: "attributed_hex")
                records.append(Evidence(id: "messages:\(thread.id):\(guid)", source: "messages:\(thread.id)", kind: "message", title: thread.title, body: "[Retracted message]", occurred: timestamp, metadata: metadata))
                continue
            }
            let plain = row["text"] ?? ""
            // Preserve unsupported rich bodies as evidence, never guess text from arbitrary bytes.
            metadata["body_status"] = plain.isEmpty ? "rich_body_or_attachment_not_decoded" : "plain_text"
            records.append(Evidence(id: "messages:\(thread.id):\(guid)", source: "messages:\(thread.id)", kind: "message", title: thread.title, body: plain.isEmpty ? "[Rich message or attachment; text decoding pending]" : plain, occurred: timestamp, metadata: metadata))
        }
        return MessageBatch(records: records, cursor: Int64(rows.last?["row_id"] ?? "") ?? after, scanned: rows.count, skipped: skipped)
    }
}
