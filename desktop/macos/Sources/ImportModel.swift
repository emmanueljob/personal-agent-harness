import SwiftUI
import EventKit
import Contacts

@MainActor
final class ImportModel: ObservableObject {
    @Published var calendars: [SourceChoice] = []
    @Published var threads: [SourceChoice] = []
    @Published var selectedCalendars = Set<String>()
    @Published var selectedThreads = Set<String>()
    @Published var start = Calendar.current.startOfDay(for: Calendar.current.date(byAdding: .day, value: -90, to: Date())!)
    @Published var end = Calendar.current.startOfDay(for: Calendar.current.date(byAdding: .day, value: 1, to: Date())!)
    @Published var threadFilter = ""
    @Published var query = ""
    @Published var evidence: [Evidence] = []
    @Published var selectedEvidence: Evidence?
    @Published var coverage: [String] = []
    @Published var total = 0
    @Published var plan: ImportPlan?
    @Published var running = false
    @Published var loadingThreads = false
    @Published var notice = "Select sources and a date range. Nothing is selected by default."
    @Published var showBrowser = false
    private var pauseRequested = false
    @Published var contactNotice = "Show contact names to match phone numbers and email addresses locally."
    private var contactNames = ContactNames()
    private let eventStore = EKEventStore()
    private var database: BrainStore?

    init() {
        do {
            database = try BrainStore()
            plan = try database?.latestPlan()
            refreshSummary()
            if let plan, !plan.complete { notice = "An interrupted import is ready to resume from its last committed batch." }
        } catch { notice = "Local brain could not open. Check disk space and permissions. No import started." }
    }
    var visibleThreads: [SourceChoice] { threadFilter.isEmpty ? threads : threads.filter { $0.title.localizedCaseInsensitiveContains(threadFilter) || $0.subtitle.localizedCaseInsensitiveContains(threadFilter) } }
    var canStart: Bool { !running && database != nil && start < end && (!selectedCalendars.isEmpty || !selectedThreads.isEmpty) }

    func loadCalendars() async {
        do {
            if EKEventStore.authorizationStatus(for: .event) == .notDetermined { _ = try await eventStore.requestFullAccessToEvents() }
            guard EKEventStore.authorizationStatus(for: .event) == .fullAccess else { throw BrainFailure(message: "Enable full Calendar access in System Settings, then load again.") }
            eventStore.reset()
            calendars = eventStore.calendars(for: .event).map { SourceChoice(id: $0.calendarIdentifier, title: $0.title, subtitle: $0.source.title) }.sorted { $0.title.localizedStandardCompare($1.title) == .orderedAscending }
            notice = "Calendar names loaded. Select the calendars to import; events have not been imported."
        } catch { notice = (error as? BrainFailure)?.message ?? "Calendar access failed. Check permissions and retry." }
    }
    func loadThreads() async {
        loadingThreads = true
        defer { loadingThreads = false }
        do {
            let names = contactNames
            threads = try await Task.detached { try MessagesSource.choices(names: names) }.value
            notice = "Conversation labels loaded locally. Search and select threads. Message bodies have not been imported."
        } catch { notice = "Could not list Messages. Check Full Disk Access, quit/reopen, then retry. The schema may also be unsupported." }
    }
    func showContactNames() async {
        guard !loadingThreads else { return }
        loadingThreads = true
        do {
            let store = CNContactStore()
            if CNContactStore.authorizationStatus(for: .contacts) == .notDetermined {
                _ = try await store.requestAccess(for: .contacts)
            }
            let authorization = CNContactStore.authorizationStatus(for: .contacts)
            // Limited access (macOS 15+) may enumerate only the owner's chosen contacts.
            guard authorization == .authorized || authorization.rawValue == 4 else {
                contactNotice = "Contacts access is off. Allow Personal Brain in System Settings → Privacy & Security → Contacts, then retry. Phone numbers remain usable."
                loadingThreads = false
                return
            }
            contactNames = try await Task.detached { try ContactNames.load() }.value
            contactNotice = "Names matched locally from permitted Contacts. Unmatched or ambiguous addresses stay visible. Names are labels, not verified identities."
        } catch { contactNotice = "Could not read Contacts. Phone numbers remain visible; check Contacts permission and retry." }
        loadingThreads = false
        await loadThreads()
    }
    func startImport() async {
        guard canStart, let database else { return }
        if let plan, !plan.complete { notice = "Resume the pending import first, or stop it before starting a different selection."; return }
        let next = ImportPlan(id: UUID().uuidString, calendars: calendars.filter { selectedCalendars.contains($0.id) }, threads: threads.filter { selectedThreads.contains($0.id) }, start: start.timeIntervalSince1970, end: end.timeIntervalSince1970, calendarWindow: start.timeIntervalSince1970)
        do { try database.commit([], plan: next); plan = next; await resume() }
        catch { notice = "Could not save the import selection. No source content was imported." }
    }
    func pause() { pauseRequested = true }
    func stopPending() {
        guard !running, var next = plan, let database else { return }
        next.complete = true
        do { try database.commit([], plan: next); plan = next; notice = "Import stopped. Committed evidence is retained; a new selection can be imported." }
        catch { notice = "Could not stop the pending import. Retry after checking local storage." }
    }
    func resume() async {
        guard !running, var current = plan, !current.complete, let database else { return }
        running = true; pauseRequested = false
        defer { running = false; refreshSummary() }
        do {
            while !pauseRequested {
                var records: [Evidence] = []
                if current.calendarIndex < current.calendars.count {
                    guard EKEventStore.authorizationStatus(for: .event) == .fullAccess else { throw BrainFailure(message: "Calendar permission is missing. Restore access and resume.") }
                    let selected = current.calendars[current.calendarIndex]
                    guard let calendar = eventStore.calendar(withIdentifier: selected.id) else { throw BrainFailure(message: "A selected calendar is unavailable. Restore it and resume, or stop this import.") }
                    let windowStart = Date(timeIntervalSince1970: current.calendarWindow)
                    let windowEnd = min(Calendar.current.date(byAdding: .month, value: 1, to: windowStart)!, Date(timeIntervalSince1970: current.end))
                    let predicate = eventStore.predicateForEvents(withStart: windowStart, end: windowEnd, calendars: [calendar])
                    let events = eventStore.events(matching: predicate)
                    for event in events {
                        guard let eventID = event.eventIdentifier, let date = event.startDate, let finish = event.endDate else { current.skipped += 1; continue }
                        let occurrence = event.occurrenceDate.map { String($0.timeIntervalSince1970) } ?? "single"
                        let status = event.status == .canceled ? "canceled" : "scheduled_attendance_unconfirmed"
                        let metadata = ["calendar": selected.title, "event_identifier": eventID, "occurrence": occurrence, "end": String(finish.timeIntervalSince1970), "timezone": event.timeZone?.identifier ?? "floating", "all_day": String(event.isAllDay), "status": status, "location": event.location ?? "", "attendees": (event.attendees ?? []).map { $0.name ?? "Unnamed attendee" }.joined(separator: "; ")]
                        records.append(Evidence(id: "calendar:\(selected.id):\(eventID):\(occurrence)", source: "calendar:\(selected.id)", kind: "calendar", title: event.title ?? "Untitled event", body: event.notes ?? "", occurred: date.timeIntervalSince1970, metadata: metadata))
                    }
                    current.calendarWindow = windowEnd.timeIntervalSince1970
                    if current.calendarWindow >= current.end { current.calendarIndex += 1; current.calendarWindow = current.start }
                    current.processed += records.count
                } else if current.threadIndex < current.threads.count {
                    let thread = current.threads[current.threadIndex]
                    let from = current.start, to = current.end, cursor = current.messageCursor
                    let batch = try await Task.detached { try MessagesSource.batch(thread: thread, start: from, end: to, after: cursor) }.value
                    records = batch.records
                    current.messageCursor = batch.cursor
                    current.processed += batch.scanned; current.skipped += batch.skipped
                    if batch.scanned < 200 { current.threadIndex += 1; current.messageCursor = 0 }
                } else { current.complete = true }
                try Task.checkCancellation()
                try database.commit(records, plan: current)
                plan = current
                notice = current.complete ? "Import finished. Evidence is stored locally. Preferences and attendance have not been inferred." : "\(current.stage) · \(current.processed) records processed · \(current.skipped) skipped"
                if current.complete { break }
                // Yield to native UI/permission changes between durable batches.
                try await Task.sleep(nanoseconds: 20_000_000)
            }
            if pauseRequested { notice = "Paused at a saved batch. Resume continues the original selection and date range." }
        } catch { notice = (error as? BrainFailure)?.message ?? "Import paused after an error. Committed records are safe; resume retries the last uncommitted batch." }
    }
    func refreshSummary() {
        do { total = try database?.count() ?? 0; coverage = try database?.coverage() ?? [] }
        catch { notice = "Could not read the local brain summary." }
    }
    func search() {
        do { evidence = try database?.search(query) ?? [] }
        catch { notice = "Could not search local evidence." }
    }
}
