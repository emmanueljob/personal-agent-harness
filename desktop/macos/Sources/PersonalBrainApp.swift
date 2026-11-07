import SwiftUI
import AppKit
import EventKit

@MainActor
final class AccessModel: ObservableObject {
    @Published var calendar = ProbeResult(state: "not_checked", detail: "Check access to calendars synced to this Mac. Only the number of visible calendars is recorded.")
    @Published var messages = ProbeResult(state: "not_checked", detail: "Check whether this app can read the local Messages table. No message content is collected.")
    @Published var checkingCalendar = false
    @Published var checkingMessages = false
    @Published var receiptNotice = ""
    private let store = EKEventStore()

    func checkCalendar() async {
        checkingCalendar = true
        defer { checkingCalendar = false; saveReceipt() }
        do {
            var authorization = EKEventStore.authorizationStatus(for: .event)
            if authorization == .notDetermined {
                _ = try await store.requestFullAccessToEvents()
                authorization = EKEventStore.authorizationStatus(for: .event)
            }
            guard authorization == .fullAccess else {
                calendar = ProbeResult(state: authorization == .restricted ? "restricted" : "permission_required", detail: "Calendar reading is not authorized. Enable full Calendar access for Personal Brain in System Settings, then check again. The app does not create or modify events.")
                return
            }
            store.reset()
            calendar = ProbeResult(state: "verified", detail: "Calendar access verified. No event titles, attendees or event bodies were read. macOS grants full Calendar capability; this probe only reads the calendar inventory.", count: store.calendars(for: .event).count)
        } catch {
            calendar = ProbeResult(state: "failed", detail: "Calendar authorization could not complete. Check System Settings and retry.")
        }
    }

    func checkMessages() async {
        checkingMessages = true
        let path = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Messages/chat.db").path
        messages = await Task.detached { MessagesProbe.check(path: path) }.value
        checkingMessages = false
        saveReceipt()
    }

    func settings(_ pane: String) {
        guard let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(pane)") else { return }
        NSWorkspace.shared.open(url)
    }

    private func saveReceipt() {
        struct Receipt: Encodable {
            let schemaVersion = 1
            let checkedAt: Date
            let calendar: ProbeResult
            let messages: ProbeResult
        }
        do {
            let root = FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Library/Application Support/PersonalBrain")
            try FileManager.default.createDirectory(at: root, withIntermediateDirectories: true, attributes: [.posixPermissions: 0o700])
            let encoder = JSONEncoder()
            encoder.dateEncodingStrategy = .iso8601
            encoder.outputFormatting = [.prettyPrinted, .sortedKeys]
            let url = root.appendingPathComponent("access-status.json")
            try encoder.encode(Receipt(checkedAt: Date(), calendar: calendar, messages: messages)).write(to: url, options: [.atomic])
            try FileManager.default.setAttributes([.posixPermissions: 0o600], ofItemAtPath: url.path)
            receiptNotice = "Local status saved. No calendar names or message content are stored."
        } catch {
            receiptNotice = "Access check finished, but saving the local status failed."
        }
    }
}

struct ContentView: View {
    @ObservedObject var model: AccessModel
    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 24) {
                HStack(spacing: 12) {
                    Image(systemName: "brain.head.profile").font(.largeTitle).foregroundStyle(.teal)
                    VStack(alignment: .leading) {
                        Text("Personal Brain").font(.largeTitle.bold())
                        Text("Your memory, independent of your assistant.").foregroundStyle(.secondary)
                    }
                }
                Text("First milestone · Source access").font(.headline)
                Text("Verify access before building your brain. These checks run on this Mac, make no network requests, and import no personal history.")
                GroupBox {
                    VStack(alignment: .leading, spacing: 12) {
                        Label("Apple Calendar", systemImage: "calendar").font(.title2.bold())
                        status(model.calendar)
                        HStack {
                            Button(model.checkingCalendar ? "Checking…" : "Check Calendar access") { Task { await model.checkCalendar() } }.disabled(model.checkingCalendar)
                            Button("Calendar settings") { model.settings("Privacy_Calendars") }
                        }
                    }.frame(maxWidth: .infinity, alignment: .leading).padding(12)
                }
                GroupBox {
                    VStack(alignment: .leading, spacing: 12) {
                        Label("Messages", systemImage: "message").font(.title2.bold())
                        status(model.messages)
                        HStack {
                            Button(model.checkingMessages ? "Checking…" : "Check Messages access") { Task { await model.checkMessages() } }.disabled(model.checkingMessages)
                            Button("Full Disk Access settings") { model.settings("Privacy_AllFiles") }
                        }
                        Text("If blocked: open Full Disk Access, add this Personal Brain app with +, enable it, then quit and reopen the app. That macOS permission is broad; this prototype only probes Messages read access. It cannot grant itself permission.").font(.callout).foregroundStyle(.secondary)
                        Button("Show app in Finder") { NSWorkspace.shared.activateFileViewerSelecting([Bundle.main.bundleURL]) }
                    }.frame(maxWidth: .infinity, alignment: .leading).padding(12)
                }
                Text("Window close keeps the app in the menu bar. Choose Quit to stop it. No background imports or model services are started.").font(.callout).foregroundStyle(.secondary)
                if !model.receiptNotice.isEmpty { Text(model.receiptNotice).font(.caption).foregroundStyle(.secondary) }
            }.padding(32).frame(maxWidth: 840, alignment: .leading)
        }.frame(minWidth: 700, minHeight: 620)
    }
    @ViewBuilder func status(_ result: ProbeResult) -> some View {
        Label(result.state.replacingOccurrences(of: "_", with: " ").capitalized, systemImage: result.state == "verified" ? "checkmark.circle.fill" : "circle.dashed")
            .foregroundStyle(result.state == "verified" ? Color.green : Color.secondary)
        Text(result.detail).textSelection(.enabled)
        if let count = result.count { Text("Visible calendars: \(count)").font(.headline) }
    }
}

@main
struct PersonalBrainApp: App {
    @StateObject private var model = AccessModel()
    init() {
        if CommandLine.arguments.contains("--self-test") {
            let passed = runSyntheticTests()
            print(passed ? "Synthetic read-only probe tests passed." : "Synthetic probe tests failed.")
            exit(passed ? 0 : 1)
        }
    }
    var body: some Scene {
        WindowGroup { ContentView(model: model) }.defaultSize(width: 800, height: 750)
        MenuBarExtra("Personal Brain", systemImage: "brain.head.profile") {
            Button("Open Personal Brain") { NSApp.activate(ignoringOtherApps: true); NSApp.windows.first(where: { $0.canBecomeMain })?.makeKeyAndOrderFront(nil) }
            Divider()
            Button("Quit Personal Brain") { NSApp.terminate(nil) }
        }
    }
}
