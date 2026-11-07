import Foundation

func runImportTests() -> Bool {
    let root = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString)
    do {
        defer { try? FileManager.default.removeItem(at: root) }
        let store = try BrainStore(root: root)
        let thread = SourceChoice(id: "synthetic-thread", title: "Synthetic conversation", subtitle: "fixture")
        var plan = ImportPlan(id: "test", calendars: [], threads: [thread], start: 978307200, end: 1900000000, calendarWindow: 978307200)
        let record = Evidence(id: "fixture", source: "test", kind: "message", title: "Synthetic 100% title", body: "Synthetic source", occurred: 1700000000, metadata: ["speaker": "owner"])
        try store.commit([record], plan: plan)
        try store.commit([record], plan: plan)
        guard try store.count() == 1, try store.search("100%").count == 1, try store.search("no match").isEmpty else { print("Synthetic import test failed at line \(#line)"); return false }
        guard try store.sql.rows("SELECT revision FROM evidence").first?["revision"] == "1" else { print("Synthetic import test failed at line \(#line)"); return false }
        let changed = Evidence(id: record.id, source: record.source, kind: record.kind, title: record.title, body: "Corrected synthetic source", occurred: record.occurred, metadata: record.metadata)
        try store.commit([changed], plan: plan)
        guard try store.sql.rows("SELECT revision FROM evidence").first?["revision"] == "2" else { print("Synthetic import test failed at line \(#line)"); return false }
        let reopened = try BrainStore(root: root)
        guard try reopened.latestPlan()?.id == plan.id, try reopened.count() == 1 else { print("Synthetic import test failed at line \(#line)"); return false }
        // A checkpoint write failure rolls back the evidence in that batch too.
        try store.sql.run("CREATE TRIGGER fail_checkpoint BEFORE INSERT ON imports BEGIN SELECT RAISE(ABORT, 'synthetic failure'); END")
        let failed = Evidence(id: "must-not-commit", source: "test", kind: "message", title: "Fixture", body: "", occurred: 1, metadata: [:])
        do { try store.commit([failed], plan: plan); print("Synthetic import test failed at line \(#line)"); return false } catch { }
        guard try store.count() == 1 else { print("Synthetic import test failed at line \(#line)"); return false }
        try store.sql.run("DROP TRIGGER fail_checkpoint")
        let sourcePath = root.appendingPathComponent("messages.sqlite").path
        do {
            let fixture = try SQL(sourcePath)
            try fixture.run("CREATE TABLE chat (guid TEXT, display_name TEXT, chat_identifier TEXT)")
            try fixture.run("CREATE TABLE handle (id TEXT)")
            try fixture.run("CREATE TABLE message (guid TEXT,text TEXT,date INTEGER,is_from_me INTEGER,handle_id INTEGER,attributedBody BLOB,date_retracted INTEGER)")
            try fixture.run("CREATE TABLE chat_message_join (chat_id INTEGER,message_id INTEGER)")
            try fixture.run("INSERT INTO chat VALUES ('synthetic-thread','Synthetic conversation','fixture')")
            try fixture.run("INSERT INTO chat VALUES ('excluded-thread','Excluded','fixture')")
            try fixture.run("INSERT INTO handle VALUES ('synthetic-speaker')")
            try fixture.run("BEGIN")
            for i in 1...201 {
                try fixture.run("INSERT INTO message VALUES (?, ?, ?, 0, 1, NULL, 0)", ["m\(i)", "synthetic \(i)", "700000000000000000"])
                try fixture.run("INSERT INTO chat_message_join VALUES (1,?)", [String(i)])
            }
            try fixture.run("INSERT INTO message VALUES ('excluded', 'not selected', 700000000, 1, 1, NULL,0)")
            try fixture.run("INSERT INTO chat_message_join VALUES (2,202)")
            try fixture.run("INSERT INTO message VALUES ('rich',NULL,700000001,1,1,X'010203',0)")
            try fixture.run("INSERT INTO chat_message_join VALUES (1,203)")
            try fixture.run("INSERT INTO message VALUES ('retracted','old text',700000002,1,1,X'010203',700000003)")
            try fixture.run("INSERT INTO chat_message_join VALUES (1,204)")
            try fixture.run("COMMIT")
        }
        let before = try Data(contentsOf: URL(fileURLWithPath: sourcePath))
        guard try MessagesSource.choices(path: sourcePath).count == 2 else { print("Synthetic import test failed at line \(#line)"); return false }
        let first = try MessagesSource.batch(thread: thread, start: plan.start, end: plan.end, after: 0, path: sourcePath)
        guard first.scanned == 200, first.records.count == 200, first.cursor == 200 else { print("Synthetic import test failed at line \(#line)"); return false }
        guard first.records.first?.metadata["speaker"] == "synthetic-speaker" else { print("Synthetic import test failed at line \(#line)"); return false }
        guard first.records.first?.occurred == 1678307200 else { print("Synthetic import test failed at line \(#line)"); return false }
        plan.messageCursor = first.cursor
        try store.commit(first.records, plan: plan)
        let saved = try reopened.latestPlan()!
        let second = try MessagesSource.batch(thread: thread, start: saved.start, end: saved.end, after: saved.messageCursor, path: sourcePath)
        guard second.scanned == 3, second.records.count == 3 else { print("Synthetic import test failed at line \(#line)"); return false }
        guard second.records.first?.id.contains("m201") == true else { print("Synthetic import test failed at line \(#line)"); return false }
        guard second.records[1].metadata["body_status"] == "rich_body_or_attachment_not_decoded" else { print("Synthetic import test failed at line \(#line)"); return false }
        guard second.records[2].body == "[Retracted message]", second.records[2].metadata["attributed_hex"] == nil else { print("Synthetic import test failed at line \(#line)"); return false }
        let outside = try MessagesSource.batch(thread: thread, start: 1800000000, end: 1900000000, after: 0, path: sourcePath)
        guard outside.records.isEmpty, try Data(contentsOf: URL(fileURLWithPath: sourcePath)) == before else { print("Synthetic import test failed at line \(#line)"); return false }
        do { _ = try MessagesSource.batch(thread: SourceChoice(id: "missing", title: "", subtitle: ""), start: 0, end: plan.end, after: 0, path: sourcePath); print("Synthetic import test failed at line \(#line)"); return false } catch { }
        plan.messageCursor = second.cursor; plan.complete = true
        try store.commit(second.records, plan: plan)
        guard try store.count() == 204, try reopened.latestPlan()?.complete == true else { print("Synthetic import test failed at line \(#line)"); return false }
        // All generated store files must remain in the private fixture directory.
        let attributes = try FileManager.default.attributesOfItem(atPath: root.appendingPathComponent("brain.sqlite").path)
        return (attributes[.posixPermissions] as? NSNumber)?.intValue == 0o600
    } catch { print("Synthetic import test failed at line \(#line)"); return false }
}
