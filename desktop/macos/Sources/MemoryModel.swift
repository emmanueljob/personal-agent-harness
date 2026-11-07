import SwiftUI

// Worker receives personal data through stdin, never process arguments or logs.
enum MemoryWorker {
    static func call(_ action: String, input: [String: Any] = [:], home: URL = BrainStore.home) throws -> [String: Any] {
        guard let resources = Bundle.main.resourceURL else { throw BrainFailure(message: "App memory resources are missing.") }
        let process = Process()
        process.executableURL = URL(fileURLWithPath: "/usr/bin/python3")
        process.arguments = ["-I", resources.appendingPathComponent("memory_worker.py").path, "--resources", resources.path, "--home", home.path, action]
        process.environment = ["PATH": "/usr/bin:/bin", "HOME": home.path, "LANG": "en_US.UTF-8", "PYTHONDONTWRITEBYTECODE": "1"]
        process.currentDirectoryURL = resources
        let incoming = Pipe(), outgoing = Pipe()
        process.standardInput = incoming; process.standardOutput = outgoing
        process.standardError = FileHandle.nullDevice
        try process.run()
        let timeout = DispatchWorkItem { if process.isRunning { process.terminate() } }
        DispatchQueue.global().asyncAfter(deadline: .now() + (action == "decode" ? 15 : 600), execute: timeout)
        defer { timeout.cancel() }
        let data = try JSONSerialization.data(withJSONObject: input)
        guard data.count <= 16 * 1024 * 1024 else { process.terminate(); throw BrainFailure(message: "Memory batch exceeds size limit.") }
        try incoming.fileHandleForWriting.write(contentsOf: data)
        try incoming.fileHandleForWriting.close()
        let output = outgoing.fileHandleForReading.readDataToEndOfFile()
        process.waitUntilExit()
        guard output.count <= 16 * 1024 * 1024, process.terminationStatus == 0,
              let value = try JSONSerialization.jsonObject(with: output) as? [String: Any], value["ok"] as? Bool == true,
              let result = value["result"] as? [String: Any] else {
            throw BrainFailure(message: "Local memory step did not complete. Check local model availability, GBrain setup and disk space, then retry. Progress already committed is preserved.")
        }
        return result
    }
    static func decode(_ bodies: [String]) -> [String?] {
        guard !bodies.isEmpty else { return [] }
        do {
            let result = try call("decode", input: ["bodies": bodies])
            guard let values = result["texts"] as? [Any], values.count == bodies.count else { return Array(repeating: nil, count: bodies.count) }
            return values.map { $0 as? String }
        } catch { return Array(repeating: nil, count: bodies.count) }
    }
}

struct MemoryProposal: Identifiable {
    let id: String; let evidenceID: String; let claim: String; let quote: String; let kind: String; let state: String
}

@MainActor
final class MemoryModel: ObservableObject {
    @Published var busy = false
    @Published var status = "Index your imports in GBrain, then run local learning to propose memories for review."
    @Published var modelName = "qwen3.8:27b"
    @Published var proposals: [MemoryProposal] = []
    @Published var searchQuery = ""
    @Published var searchResults = ""
    @Published var automaticLearning = false
    init() { refresh() }
    func run(_ action: String) async {
        guard !busy else { return }
        busy = true
        status = action == "learn" ? "Learning locally from up to five eligible records…" : "Running \(action) locally. You can keep using the app…"
        defer { busy = false; refresh() }
        do {
            let input: [String: Any] = ["model": modelName, "query": searchQuery]
            let result = try await Task.detached { try MemoryWorker.call(action, input: input) }.value
            switch action {
            case "repair": status = "Decoded \(result["decoded"] ?? 0) stored rich messages; \(result["unsupported"] ?? 0) unsupported. Re-run for the next batch."
            case "index": status = "GBrain: \(result["indexed"] ?? 0) pages current; \(result["changed"] ?? 0) changed."
            case "learn": status = "Reviewed \(result["processed"] ?? 0) eligible records locally; \(result["proposed"] ?? 0) memory suggestions. Confirm useful ones below."
            case "dream": status = "GBrain maintenance status: \(result["maintenance_status"] ?? "unknown"). \((result["maintenance_phases"] as? [[String: Any]] ?? []).compactMap { $0["summary"] as? String }.joined(separator: "; "))"
            case "search":
                let hits = result["hits"] as? [[String: Any]] ?? []
                searchResults = hits.compactMap { $0["text"] as? String }.joined(separator: "\n\n———\n\n")
                if hits.isEmpty { searchResults = "No current indexed evidence matched. Index imports first, or try a specific keyword." }
                status = "GBrain returned \(hits.count) current evidence pages."
            default: break
            }
        } catch { status = (error as? BrainFailure)?.message ?? "Memory step failed. No cloud fallback was used." }
    }
    func refresh() {
        do {
            let store = try BrainStore()
            // Worker creates learning tables on first run. A fresh app has none yet.
            if try store.sql.rows("SELECT name FROM sqlite_master WHERE name='memory_proposals'").isEmpty { return }
            proposals = try store.sql.rows("SELECT p.* FROM memory_proposals p JOIN evidence e ON e.id=p.evidence_id AND e.hash=p.evidence_hash WHERE p.state<>'rejected' ORDER BY p.created DESC LIMIT 100").map {
                MemoryProposal(id: $0["id"]!, evidenceID: $0["evidence_id"]!, claim: $0["claim"]!, quote: $0["quote"]!, kind: $0["kind"]!, state: $0["state"]!)
            }
        } catch { status = "Could not read memory suggestions." }
    }
    func review(_ proposal: MemoryProposal, confirm: Bool) {
        do {
            let store = try BrainStore()
            try store.sql.run("UPDATE memory_proposals SET state=? WHERE id=? AND EXISTS (SELECT 1 FROM evidence e WHERE e.id=memory_proposals.evidence_id AND e.hash=memory_proposals.evidence_hash)", [confirm ? "confirmed" : "rejected", proposal.id])
            refresh()
            status = "Review saved locally. Index imports to update GBrain's reviewed memory pages."
        } catch { status = "Could not save review; retry." }
    }
}

struct MemoryView: View {
    @ObservedObject var model: MemoryModel
    var body: some View {
        VStack(alignment: .leading, spacing: 14) {
            Text("Memory & dreaming").font(.title2.bold())
            Text("GBrain maintains an isolated local index. Learning uses only your selected local Ollama model. Suggestions are not confirmed facts until you review them.").foregroundStyle(.secondary)
            HStack {
                Button("Decode stored rich messages") { Task { await model.run("repair") } }
                Button("Index imports in GBrain") { Task { await model.run("index") } }
                Button("GBrain maintenance") { Task { await model.run("dream") } }
            }.disabled(model.busy)
            HStack {
                TextField("Installed Ollama model", text: $model.modelName)
                Button("Learn from next 5 records") { Task { await model.run("learn") } }.disabled(model.busy)
            }
            Toggle("Learn a small batch every 30 minutes while this window is open", isOn: $model.automaticLearning)
            Text("GBrain has model-based synthesis/pattern phases too. This app runs explicit non-model maintenance phases only; broad native dream/autopilot is not enabled. No new scheduled run starts while this window is closed or the Mac sleeps; a running batch may finish. Quit stops it.").font(.caption).foregroundStyle(.secondary)
            HStack { if model.busy { ProgressView().controlSize(.small) }; Text(model.status).font(.callout).textSelection(.enabled) }
            HStack {
                TextField("Search GBrain keywords", text: $model.searchQuery)
                Button("Search GBrain") { Task { await model.run("search") } }.disabled(model.busy)
            }
            if !model.searchResults.isEmpty { ScrollView { Text(model.searchResults).font(.caption).textSelection(.enabled).frame(maxWidth: .infinity, alignment: .leading) }.frame(height: 120) }
            ScrollView {
                LazyVStack(alignment: .leading, spacing: 14) {
                    ForEach(model.proposals) { proposal in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(proposal.claim).font(.headline)
                            Text("Evidence: \(proposal.evidenceID)").font(.caption).textSelection(.enabled)
                            Text("Source quote: “\(proposal.quote)”").textSelection(.enabled)
                            HStack {
                                Text("\(proposal.kind) · \(proposal.state)").foregroundStyle(.secondary)
                                if proposal.state == "proposed" { Button("Confirm") { model.review(proposal, confirm: true) } }
                                Button("Reject / remove") { model.review(proposal, confirm: false) }
                            }.disabled(model.busy)
                        }
                        Divider()
                    }
                }
            }.frame(minHeight: 130)
        }.padding(24).frame(width: 960, height: 700)
        .task {
            while !Task.isCancelled {
                do { try await Task.sleep(nanoseconds: 1_800_000_000_000) } catch { break }
                if model.automaticLearning && !model.busy { await model.run("learn") }
            }
        }
    }
}
