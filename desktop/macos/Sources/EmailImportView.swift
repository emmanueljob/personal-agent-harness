import SwiftUI

struct EmailAccountProgress: Identifiable {
    let id: String; let role: String; let discovered: Int
    let imported: Int; let pending: Int; let failed: Int; let excluded: Int
    let discoveryComplete: Bool
}

@MainActor
final class EmailImportModel: ObservableObject {
    @Published var accounts: [EmailAccountProgress] = []
    @Published var status = "No Gmail backfill has started."
    @Published var range = ""
    @Published var paused = false
    @Published var hasActiveRun = false
    private var runID: String?
    func refresh() {
        do {
            let store = try BrainStore()
            guard !(try store.sql.rows("SELECT name FROM sqlite_master WHERE name='email_runs'")).isEmpty,
                  let run = try store.sql.rows("SELECT * FROM email_runs ORDER BY updated DESC LIMIT 1").first else { return }
            runID = run["id"]
            paused = run["pause_requested"] == "1"
            let state = run["state"] ?? "unknown"
            hasActiveRun = !["complete", "needs_retry"].contains(state)
            status = paused ? "Paused after the current batch. Ask this chat to resume fetching Gmail." : state.replacingOccurrences(of: "_", with: " ").capitalized
            let start = Date(timeIntervalSince1970: Double(run["start"] ?? "0") ?? 0)
            let end = Date(timeIntervalSince1970: Double(run["end"] ?? "0") ?? 0)
            range = "\(start.formatted(date: .abbreviated, time: .omitted)) – \(end.formatted(date: .abbreviated, time: .omitted)) (end exclusive)"
            accounts = try store.sql.rows("SELECT * FROM email_accounts WHERE run_id=? ORDER BY role,account", [run["id"]!]).map { account in
                let counts = try store.sql.rows("SELECT state,count(*) AS total FROM email_queue WHERE run_id=? AND account=? GROUP BY state", [run["id"]!,account["account"]!])
                func count(_ state: String) -> Int { Int(counts.first(where: { $0["state"] == state })?["total"] ?? "0") ?? 0 }
                return EmailAccountProgress(id: account["account"]!, role: account["role"]!, discovered: counts.reduce(0) { $0 + (Int($1["total"] ?? "0") ?? 0) }, imported: count("imported"), pending: count("pending"), failed: count("failed"), excluded: count("excluded"), discoveryComplete: account["discovery_complete"] == "1")
            }
        } catch { status = "Could not read local email progress. Committed mail remains saved." }
    }
    func pause() {
        guard let runID else { return }
        do {
            let store = try BrainStore()
            try store.sql.run("UPDATE email_runs SET pause_requested=1,state='paused' WHERE id=?", [runID])
            refresh()
        } catch { status = "Could not request pause. Retry." }
    }
}

struct EmailImportView: View {
    @StateObject private var model = EmailImportModel()
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        VStack(alignment: .leading, spacing: 18) {
            Text("Gmail imports").font(.title2.bold())
            Text("Backfills use the Gmail accounts connected in this chat. This screen shows progress saved on your Mac; native Gmail sign-in and automatic email sync are not available yet.").foregroundStyle(.secondary)
            Text(model.status).font(.headline).accessibilityIdentifier("email-import-status")
            if !model.range.isEmpty { Text(model.range).font(.caption) }
            ForEach(model.accounts) { account in
                GroupBox {
                    VStack(alignment: .leading, spacing: 8) {
                        Text(account.id).font(.headline)
                        Text("\(account.role == "shared" ? "Shared mailbox — authorship is not attributed to you" : "Personal mailbox")")
                        Text("\(account.imported) imported · \(account.pending) pending · \(account.failed) need retry · \(account.excluded) excluded")
                        ProgressView(value: Double(account.imported + account.excluded), total: Double(max(1,account.discovered)))
                        Text("\(account.discovered) discovered · \(account.discoveryComplete ? "search complete" : "search in progress")").font(.caption).foregroundStyle(.secondary)
                    }.frame(maxWidth: .infinity, alignment: .leading).padding(8)
                }
            }
            Text("Email text and headers stay in your local evidence store. Attachments, remote images, Spam, Trash and Drafts are excluded. Received mail is evidence of what a sender reported; shared-account mail is not proof of who wrote it.").font(.callout)
            Text("After importing, open Memory & dreaming to process the new evidence. Large backfills can take a long time to index and synthesize; saved work is resumable.").font(.caption).foregroundStyle(.secondary)
            Spacer()
            HStack {
                if model.hasActiveRun && !model.paused { Button("Pause after current batch") { model.pause() } }
                Spacer()
                Button("Done") { dismiss() }
            }
        }.padding(24).frame(width: 760,height: 570)
        .task {
            while !Task.isCancelled {
                model.refresh()
                do { try await Task.sleep(nanoseconds: 1_000_000_000) } catch { break }
            }
        }
    }
}
