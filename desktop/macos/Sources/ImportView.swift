import SwiftUI

struct ImportView: View {
    @StateObject private var model = ImportModel()
    @State private var showAccess = false
    @State private var showMemory = false
    @State private var showEmail = false
    @StateObject private var memory = MemoryModel()
    @StateObject private var access = AccessModel()
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            HStack {
                Image(systemName: "brain.head.profile").font(.largeTitle).foregroundStyle(.teal)
                VStack(alignment: .leading) {
                    Text("Build your personal brain").font(.title.bold())
                    Text("Selected sources. Local evidence. Your control.").foregroundStyle(.secondary)
                }
                Spacer()
                Button("Email imports") { showEmail = true }
                Button("Memory & dreaming") { showMemory = true }
                Button("Access settings") { showAccess = true }
            }
            HStack {
                DatePicker("From", selection: $model.start, displayedComponents: [.date])
                DatePicker("Until (exclusive)", selection: $model.end, displayedComponents: [.date])
            }.disabled(model.running)
            Text("Only selected calendars and conversations within this range will be copied. Existing imports are retained when you change selections. No cloud processing or inferred preferences.").font(.callout).foregroundStyle(.secondary)
            HStack(alignment: .top, spacing: 16) {
                GroupBox("Calendars · \(model.selectedCalendars.count) selected") {
                    VStack(alignment: .leading) {
                        Button("Load calendars") { Task { await model.loadCalendars() } }.disabled(model.running)
                        List(model.calendars) { item in selection(item, selected: $model.selectedCalendars) }.frame(minHeight: 200)
                    }.padding(4)
                }
                GroupBox("Messages · \(model.selectedThreads.count) selected") {
                    VStack(alignment: .leading) {
                        Button(model.loadingThreads ? "Loading…" : "Load message threads") { Task { await model.loadThreads() } }.disabled(model.running || model.loadingThreads)
                        Button("Show contact names") { Task { await model.showContactNames() } }.disabled(model.running || model.loadingThreads)
                        Text(model.contactNotice).font(.caption).foregroundStyle(.secondary)
                        TextField("Search conversation names or addresses", text: $model.threadFilter)
                        List(model.visibleThreads) { item in selection(item, selected: $model.selectedThreads) }.frame(minHeight: 170)
                    }.padding(4)
                }
            }.disabled(model.running)
            HStack {
                Button("Import selected sources") { Task { await model.startImport() } }.buttonStyle(.borderedProminent).disabled(!model.canStart || (model.plan != nil && model.plan?.complete == false))
                if let plan = model.plan, !plan.complete {
                    if model.running { Button("Pause import") { model.pause() } }
                    else {
                        Button("Resume saved import") { Task { await model.resume() } }
                        Button("Stop pending import") { model.stopPending() }
                    }
                }
                if model.running { ProgressView().controlSize(.small) }
                Spacer()
                Text("\(model.total) stored records").monospacedDigit()
                Button("Browse local brain") { model.showBrowser = true; model.search() }
            }
            Text(model.notice).font(.callout).textSelection(.enabled).accessibilityIdentifier("import-status")
            ForEach(model.coverage, id: \.self) { Text($0).font(.caption).foregroundStyle(.secondary) }
            Text("Rich message text is decoded where supported; original bytes are retained locally. Attachments are not downloaded. Records are evidence, not proof of attendance, agreement or preference. Quit pauses work at the last committed batch.").font(.caption).foregroundStyle(.secondary)
        }
        .padding(24).frame(minWidth: 920, minHeight: 650)
        .sheet(isPresented: $showEmail) { EmailImportView().onDisappear { model.refreshSummary() } }
        .sheet(isPresented: $showAccess) { VStack { ContentView(model: access); Button("Done") { showAccess = false }.padding() }.frame(width: 780, height: 750) }
        .sheet(isPresented: $model.showBrowser) { browser }
        .sheet(isPresented: $showMemory) { VStack { MemoryView(model: memory); Button("Done") { showMemory = false }.padding() } }
        .onChange(of: model.running) { _, running in
            if !running && model.plan?.complete == true { Task { await memory.run("index") } }
        }
    }
    func selection(_ item: SourceChoice, selected: Binding<Set<String>>) -> some View {
        Toggle(isOn: Binding(get: { selected.wrappedValue.contains(item.id) }, set: { value in
            if value { selected.wrappedValue.insert(item.id) } else { selected.wrappedValue.remove(item.id) }
        })) {
            VStack(alignment: .leading) { Text(item.title).lineLimit(1); Text(item.subtitle).font(.caption).foregroundStyle(.secondary).lineLimit(1) }
        }.toggleStyle(.checkbox)
    }
    var browser: some View {
        VStack(alignment: .leading) {
            HStack { Text("Your local evidence").font(.title2.bold()); Spacer(); Button("Done") { model.showBrowser = false } }
            HStack { TextField("Search titles and plain text", text: $model.query).onSubmit { model.search() }; Button("Search") { model.search() } }
            Text("Latest 100 matching records. Source evidence only; no generated claims.").font(.caption).foregroundStyle(.secondary)
            HSplitView {
                List(model.evidence) { item in
                    Button { model.selectedEvidence = item } label: {
                        VStack(alignment: .leading) {
                            Text(item.title).lineLimit(1)
                            Text("\(item.kind) · \(Date(timeIntervalSince1970: item.occurred).formatted(date: .abbreviated, time: .shortened))").font(.caption).foregroundStyle(.secondary)
                        }
                    }.buttonStyle(.plain)
                }.frame(minWidth: 260)
                ScrollView {
                    if let item = model.selectedEvidence {
                        VStack(alignment: .leading, spacing: 12) {
                            Text(item.title).font(.headline)
                            Text(item.body.isEmpty ? "No plain-text body" : item.body)
                            Divider()
                            ForEach(item.metadata.keys.sorted().filter { $0 != "attributed_hex" }, id: \.self) { key in Text("\(key): \(item.metadata[key] ?? "")").font(.caption) }
                            Text("Evidence ID: \(item.id)").font(.caption).foregroundStyle(.secondary)
                        }.textSelection(.enabled).padding().frame(maxWidth: .infinity, alignment: .leading)
                    } else { Text("Select a record to inspect its provenance.").padding() }
                }.frame(minWidth: 360)
            }
        }.padding(24).frame(width: 900, height: 650)
    }
}
