# Personal Brain for Mac: product specification and build plan

Status: proposed implementation baseline; no personal data imported by this document.
Date: 2026-11-07. Repository baseline: `origin/main` at `b0708d5`.
Working name: Personal Brain. Final name and bundle identifier remain open.

## 1. Outcome and scope

Build a Mac desktop application that turns selected personal history into useful,
inspectable memory independently of an assistant. The owner can talk to different
assistants without rebuilding their background each time. Personal life comes first:
trips, purchases, restaurants, relationships, plans and preferences. LinkedIn supplies
optional professional context; professional surveillance and employer data ingestion
are outside this POC.

The first success is a cold start, not an autonomous digital twin: reconstruct a past
vacation from Calendar, email and selected conversations; answer questions with
evidence; correct a misunderstanding; preserve the correction across sessions.
The next success is purchases/returns and reuse of reviewed memory by two clients.
The app should be useful before remote assistant integration works.

### Deliverable boundaries

| Stage | Included | Explicitly deferred |
|---|---|---|
| Core POC | Mac app, local sources/storage/inference, guided imports, evidence browser, life records, corrections, resumable jobs, cited answers, portable export | Cloud hosting, multi-user service, autonomous external actions |
| Source completion | Calendar; email archive; Amazon and LinkedIn archives; WhatsApp exports; Messages collector if feasibility passes | Universal live synchronization, account scraping, media transcription at scale |
| Optional source | Historical location archive import where obtainable | Guaranteed Apple Maps history access; new iPhone app collecting future location |
| Independence extension | Local authenticated tools for two clients, scoped memory retrieval and proposals | Guaranteed Muse/Instinct/Wabi support, invisible capture of all agent work, internet exposure |
| Later coordination | Agent registry, work ledger, event subscriptions and delivery receipts | General agent marketplace, autonomous agent-to-agent delegation |

All seven requested sources have an explicit setup path or an honest unavailable
state. Location or Messages limitations must not block an otherwise useful cold start.

## 2. Product principles

- The owner controls sources, date ranges, exclusions, corrections and deletion.
- Preserve evidence separately from interpretations; import does not mean endorsement.
- Distinguish scheduled, booked, canceled, returned, attended, visited and enjoyed.
- Attribute statements to their actual speaker. Quoted text and assistant suggestions
  are not the owner's beliefs.
- Display coverage and uncertainty. No data is different from a negative answer.
- A correction takes effect at retrieval time, without waiting for reindexing.
- Preserve exportability: canonical records must remain usable without GBrain or the UI.
- Keep one authoritative personal store; indexes and context packs are disposable.
- Default to local inference and no cloud fallback. Source-provider downloads are
  network operations and remain distinct from sending records to an inference provider.
- Imported text is evidence, never instructions governing tools or permissions.

## 3. Owner journey and desktop experience

### First launch

1. Explain local storage, processing, sleep/offline behavior and optional model download.
2. Choose an application data location; default to a new, independent namespace.
3. Detect supported architecture, available disk, local model and source capabilities.
4. Offer a short interview: current priorities, important people, preferences and
   exclusions. Allow skipping and editing later.
5. Choose a first project: reconstruct a trip, understand purchases, or general recall.
6. Select source/date/conversation scope and preview inventory before import.

No permissions are requested until their source is selected. Calendar permissions
must belong to the signed desktop app/helper, not a transient shell or terminal.
Source setup describes exactly which account/device data is accessible.

### Main navigation

| View | Required behavior |
|---|---|
| Home | Coverage summary, last activity, current import, next useful step, pending review |
| Sources | Account/archive, actual coverage, counts, last success, checkpoint, exclusions, disconnect/remove |
| Build my brain | Ordered stages with progress, pause/resume, bounded errors and partial completion |
| My life | People, trips, purchases, places and commitments; chronological and searchable |
| What I learned | Confirmed facts, sourced observations, candidate preferences, contradictions and corrections |
| Ask | Cited answers; open evidence in an in-app viewer; clearly identify missing coverage |
| Activity | Import/extraction/index jobs and later agent work, with last-known timestamps |
| Settings | Model, disk usage, permissions, retention, export/backup/restore, background operation |

Evidence viewers work even when the original app lacks a deep link. Native links are
optional conveniences, not prerequisites for provenance. Never embed active email HTML,
remote tracking pixels or executable attachments in the viewer. Accessibility, keyboard
navigation, Dynamic Type where applicable and reduced motion are part of the app shell.

### Lifecycle

Closing the window leaves the app active in the menu bar. Explicit Quit checkpoints and
stops owned workers; continuing after Quit requires a separate future opt-in background
helper. Launch at login is opt-in. The app does not prevent sleep by default. Sleep,
loss of network or permission revocation suspends affected work and resumes safely.
The UI distinguishes paused, offline, needs permission and failed.

## 4. Source contracts and feasibility gates

Defaults are proposals displayed before import, not implicit authorization to collect.
The user may reduce or expand each range.

| Source | First implementation | Suggested scope | Feasibility / failure behavior |
|---|---|---|---|
| Apple Calendar | Native EventKit reader, with ICS import fallback | Selected calendars; previous 12 months and next 3 months | Validate entitlements/TCC in the packaged app. Include recurrence, exceptions, cancellations, all-day values and original timezone. Do not infer attendance. |
| Email | MBOX/EML archive import first; one provider-specific read-only live connector later | Previous 12 months; first prioritize reservations, receipts and chosen correspondents | Provider is unselected. Gmail is a candidate, not assumed. Preserve MIME/message/thread IDs; incremental overlap and edited labels cannot duplicate messages. Attachments are listed before optional processing. |
| Text messages | Read-only snapshot of locally available Mac Messages data if permission/access spike passes | Five to ten selected threads; previous 12 months | Internal database is not a supported stable public API. Test OS/schema versions, WAL-consistent snapshots, attributed bodies, edits/unsends, reactions and missing history. No writes to the live DB. If unavailable, accept a supplied archive and report limitations. |
| WhatsApp | Selected exported chats, text first | Five to ten chosen chats | Parse locale/date ambiguity and multiline messages; show unresolved speakers. Export completeness is measured, not assumed. No linked-device automation required for POC. |
| Amazon | Account data-request archive plus optional email evidence | Previous 24 months initially | Inspect delivered schemas; support recognized variants and show unrecognized files. Preserve order/item/return relationships, currency and status. Never assume all purchases were for the owner. |
| LinkedIn | Account archive, profile/connections first; messages optional | Selected archive content | Parse available files; show missing categories. A connection is not evidence of a close relationship. Employment data retains source date. No login scraping. |
| Location | User-supplied supported archive, if one exists | Selected trip/date window | Apple's Maps history export/access is unverified. Core Location future visits are not historical backfill. Calendar/reservation locations remain planned/booked evidence, not observed presence. Allow approximate visits and uncertain place identity. |

The setup audit produces only local counts, source ranges, permission state and schema
compatibility. It must not copy complete personal archives just to discover availability.
Contacts/vCard may be offered later to improve name resolution; they are not a mandatory
extra source. Avoid combining unrelated people based solely on a shared display name.

Source removal is separate from disconnection: disconnect stops collection, while
remove offers withdrawal or physical purge of local copies and dependent derivatives.
Archive import does not imply future updates. Deletion detection for incremental feeds
must depend on an explicit tombstone or a completed authoritative inventory, never an
item missing from a partial page of results.

## 5. Cold-start pipeline

| Stage | Work | Durable output / checkpoint |
|---|---|---|
| Inventory | Identify formats, coverage, volume, excluded categories and parseability | Source manifest and preview receipt |
| Acquire | Copy authorized archives or consistent local snapshots; download chosen records | Immutable evidence objects, content hashes, acquisition cursor |
| Normalize | Parse source identities, speakers, timestamps, bodies and attachment references | Versioned source records and parse issues |
| Structure | Deterministic purchases/events; model-assisted assertions for prose | Candidate entities, events, claims and source spans |
| Reconcile | Link cross-source records, flag contradictions and duplicates | Reversible links and review queue |
| Review | Ask a few high-value questions, accept corrections | Owner decisions with revisions and supersession |
| Index | Project permitted records into lexical/GBrain search | Index manifest and watermark |
| Validate | Run owner-selected questions, inspect provenance and gaps | Local evaluation results and coverage report |

Every run supports preview, bounded batches, pause, resume, cancel and retry. Cancel
retains committed work and shows a receipt; it does not masquerade as rollback. Disk
full must leave the last checkpoint valid. Reprocess after parser/model changes using
versioned identities, rather than silently replacing confirmed owner decisions.

Suggested order: Calendar plus a trip's email evidence, selected conversations,
Amazon, LinkedIn, then optional location. A source may proceed while another awaits an
export or permission. The app reports parsed/total records, extraction queue depth,
failed counts, elapsed time and last checkpoint; it does not invent progress percentages
for unknown totals. Limit review to ten high-value questions per session by default.

### Extraction and reconciliation rules

- Deterministic parsers establish IDs, dates, amounts and statuses whenever possible.
- Model output uses validated structured schemas; malformed output is quarantined or
  retried within a budget. Excerpts, not whole archives, enter model context.
- Every assertion cites a source revision and span. A validation step rejects missing
  citations, incompatible speakers and unsupported dates/amounts where determinable.
- Identity merges use exact identifiers first; fuzzy matches are proposals with evidence.
  Shared phone numbers and shared email accounts must remain representable.
- Cross-source duplicate evidence stays linked, not discarded; a confirmation email
  and calendar event are different evidence for one booking.
- Candidate preferences are not automatically promoted to confirmed preferences.
  Frequency is not preference; purchase is not satisfaction; location is not intent.
- A correction adds a superseding decision and invalidates dependent context. Imported
  history cannot overwrite it. Ambiguous conflicts go to review.
- Use event time, source modification time and ingestion time separately. Preserve
  original timezone and relative-date basis; distinguish event windows from exact times.

## 6. Canonical model and storage ownership

Use a new app-owned SQLite database for transactional metadata, structured records,
claims, revisions, job checkpoints and an outbox. Preserve immutable evidence bytes
as content-addressed local blobs. SQLite is authoritative for IDs, visibility, current
revision, accepted claims and removal state. Exported JSON/Markdown is a portable
representation, not a competing editable store. GBrain receives a derived projection.

| Record | Minimum fields |
|---|---|
| Source | ID, kind, local account alias, scope selection, granted permissions, parser version, coverage intervals, cursor, last success |
| Evidence | Source ID, external stable ID, revision, raw hash/blob reference, normalized hash, timestamp fields, speaker, local locator, removal state |
| Entity | ID, type, display label, exact aliases, merge history |
| Life event | ID, type, status, time/window, participants, amounts/currency where relevant, supporting evidence |
| Assertion | Subject/predicate/value, qualifiers, valid interval, epistemic status, source spans, extractor/model version, review state |
| Owner decision | Target, previous revision, corrected value, reason, supersedes, timestamp |
| Job | Type, source/scope, cursor, pipeline version, idempotency key, state, attempts, heartbeat, counts, bounded diagnostic |
| Outbox event | Event ID, aggregate/revision, kind, affected IDs, delivery state; payload references instead of raw text |
| Access grant (later) | Client identity, allowed collections/operations, expiry/revocation, audit records |

Assertion states include source-observed, explicitly owner-stated, inferred,
owner-confirmed, disputed and superseded. Booked/visited/liked are event predicates,
not interchangeable confidence levels. Store qualitative confidence and its basis;
do not show an uncalibrated model score as a probability.

Logical layout, under `~/Library/Application Support/PersonalBrain/` (provisional):

```text
store.sqlite                 # app-owned canonical metadata and state
evidence/                    # immutable source blobs; never executable
gbrain/                      # isolated, rebuildable projection/index
exports/                     # explicit owner exports
backups/                     # consistent manifests and snapshots
logs/                        # content-free diagnostics by default
```

Keep this entirely separate from the existing Afterhours home, portfolio knowledge,
runtime databases and Temporal service. Add a schema version and migration receipts.
Backup uses a consistent SQLite snapshot and referenced evidence manifest; index
rebuild is sufficient after restore. Test restore, not only backup creation. Model
files are separately managed and excluded from ordinary personal-data backup.

Withdrawal immediately blocks retrieval, queued extraction and cached context.
Purge removes local evidence, projections and affected generated artifacts or marks
them inaccessible until rebuilt. Explicit owner statements can survive source removal
only as independently attributed owner records. Backups and exports need separate
retention/deletion notices; no promise of erasure from previously contacted assistants.

## 7. Architecture and desktop packaging

Recommended POC: SwiftUI/AppKit desktop shell plus a bundled Python worker, with a
small Swift native source layer for Calendar/Keychain and Mac permission handling.
This gives the app a native permission identity and keeps the valuable Python ingestion
and model code reusable. Do not add Electron/Tauri simply to reuse the portfolio UI.

```text
SwiftUI app + menu bar
  -> typed, framed local IPC -> Python worker / job runner
  -> native source layer -> EventKit, approved file access, Keychain
Python worker
  -> canonical SQLite + evidence objects
  -> deterministic source adapters + bounded local extraction
  -> GBrain projection + query service
  -> local model endpoint
Later: authenticated local MCP facade -> policy -> same query/proposal service
```

IPC starts as private inherited pipes with bounded framed messages, request IDs,
cancel signals and event streams. The app controls worker lifecycle; workers never
receive arbitrary shell commands or unvalidated filesystem paths from model output.
Avoid a localhost HTTP listener for the UI. If one is later justified, require an
ephemeral secret, origin validation, loopback binding and strict request limits.

Provisional target: Apple Silicon, macOS 14 or later; validate the actual supported OS
matrix in the first spike. Choose a stable bundle ID before TCC testing. Ship a signed,
notarized owner-installable app outside the App Store for the pilot; Messages collection
may require permissions inconsistent with an App Store sandbox. Do not instruct users
to disable system protections. Permission denial has a documented fallback.

Bundle a reproducible Python runtime and pinned dependencies; do not depend on the
developer's `.venv`, checkout or shell PATH. Start by requiring a separately installed
local Ollama service, with guided health/model checks. Do not bundle an unreviewed model
or download one silently. Validate model license, size, memory needs and actual extraction
quality before selecting a default. Packaging GBrain requires release provenance and
checksum verification; the current setup script downloads a versioned executable but
does not verify its digest.

### Inference contract

Local-only is the default processing mode. Missing model means imports and deterministic
search still work while extraction waits. No fallback to the harness's current provider
default: `harness/models.py` defaults to OpenAI unless configured otherwise. The desktop
must explicitly configure and validate local mode at startup and before each model job.
Disable public-web research during personal extraction. Persist model/parser versions,
context bounds, extraction receipts and redacted errors. Provide concurrency, memory,
per-run token/time budgets and disk thresholds; refine them from measurements.

### Durable jobs

For the cold-start pipeline use a small app-owned SQLite job runner: transactional
enqueue, lease/heartbeat, checkpoint per bounded batch, retry backoff and stable
idempotency keys. This is new implementation, informed by harness patterns. Do not
require a Temporal server just to run a desktop import. At-least-once job execution
requires idempotent record writes and outbox delivery.

Keep the harness's Temporal/Deep Agents workflow unchanged. If later autonomous work
needs Temporal, introduce it as a separate decision with isolated data, versioned
workflows and recovery tests. A desktop UI is not a reason to migrate serving workflows.

## 8. Reuse audit: what exists and what changes

Paths below refer to this repository baseline, not hypothetical upstream features.

| Component | Reuse decision | Adaptation / limitation |
|---|---|---|
| `harness/ingestion.py`, `docs/ingestion.md` | Reuse validation, stable IDs, hashes, revisions, atomic writes and provenance patterns | Current schema requires HTTP(S) URLs, only public/private scope, 2 MB text limit; batch is not transactional. Add a separate personal-record contract with local locators and transactional cursors. Never invent URLs to satisfy old validation. |
| `harness/gbrain.py` | Reuse isolated subprocess environment, locking, delta manifest, current-source validation and fail-closed retrieval ideas | Current search scans/syncs documents on queries and private search includes public data. Desktop needs private-only policy and background projection watermarks; stale/removed data checked against canonical state. |
| `harness/brain.py` | Reuse provider abstraction and file fallback concept | Existing provider carries portfolio/book assumptions; introduce a desktop query interface rather than importing all presentation semantics. |
| `harness/learning.py` | Reuse proposed-versus-active corrections, revision conflicts and replay preservation | Book/podcast/feedback schema is too narrow. Generalize in a new owner-decision service; remove publication from desktop flow. |
| `harness/retrieval.py` | Reuse source deduplication concept | Book prioritization and hard-coded scores are unsuitable for trips/purchases. Replace domain ranking and add structured filters. |
| `harness/store.py`, `harness/durable.py` | Reuse transactional queue/idempotency patterns and test approach | Chat turns, routines and labels are not an ingestion ledger. New database and job schema; no reuse of serving data. |
| `harness/deep_runtime.py`, `harness/deep_workflows.py` | Optional later agent execution library | Not needed for deterministic cold start. Model/tool Activities and workflow history are not source-level import checkpoints. |
| `harness/models.py` | Reuse local Ollama adapter after factoring provider configuration | Explicit local-only override required; structured extraction and budget handling are new. |
| `harness/paths.py` | Reuse external app-data convention | New app namespace, explicit path injection; no global env change to a running harness. |
| `harness/admin_mcp.py`, `harness/agent_surfaces.py` | Reference only for later MCP facade | Existing owner administration/public discovery are not least-privilege personal-memory access. No public routes in desktop app. |
| `frontend/src`, owner studio | Reuse interaction patterns for source evidence, draft/review and conflict feedback | SwiftUI shell is new; portfolio, bookshelf and visitor personalization are not desktop UI modules. |
| `harness/model_bridge.py` | Do not reuse for local POC | Designed for a hosted app's outbound Mac inference worker, not needed when app and model run on the same Mac. |
| `scripts/setup_gbrain.py` | Reuse version pin and conservative initialization flags as a starting point | Add verified installation, new home, runtime capability checks; no blind migrations of existing databases. |
| Existing tests | Reuse synthetic fixtures and invariants around updates, withdrawal, scope and retries | Add native permissions, personal-source parsing, deletion propagation, job crash tests and packaged-app verification. |

### GBrain specifically

The repository pins GBrain `0.60.530.0`. Its integration currently selects conservative
keyword retrieval and disables facts extraction, ambient writeback and auto-chronicle.
It is a derived index over canonical files, not a configured full personal memory agent.
Preserve that distinction when estimating reuse.

Use GBrain initially for the rebuildable search projection. The app owns canonical
facts, provenance and corrections. Export approved entities/preferences as indexed
documents with stable IDs; query the canonical store for exact dates/amounts/statuses.
Do not expose GBrain's broad write surface directly to external agents.

The pinned upstream documentation describes cold-start sequencing, resumable imports,
bootstrap identity files/warm context, and a Google connector. Reuse the concepts and
evaluate the connector in an isolated spike. These are CLI/agent workflows, not a Mac
onboarding UI or verified support for the seven sources. The cold-start text contains
mixed fallback instructions; implement our own explicit access contract rather than
executing the runbook wholesale.

Do not run bootstrap against existing agents: no global hook installation, new persona,
private GitHub brain repository, auto-push or hidden cron jobs. This POC is local and
assistant-independent. Session-start warm context means retrieving a bounded relevant
pack; it is not permission to distribute all personal history.

If GBrain fails package, latency or correctness gates, use local SQLite FTS as a
temporary backend behind the same interface. No GBrain version upgrade is assumed.
New extraction features and semantic embeddings require a measured experiment and
separate dependency/data migration review.

Omnesis remains an alternative collector to evaluate, not a committed dependency. Its
repository is AGPL-3.0; inspect packaging, maintenance and license implications before
copying or embedding code, especially for a future commercial product. Running it as
a separate process alone is not a conclusion about license obligations.

## 9. Local security, permissions and data controls

- Store credentials in macOS Keychain via the native layer; never put them in prompts,
  CLI arguments, source exports, logs or the repository. If evaluating GBrain's Google
  connector, note its documented local file vault is not the same as Keychain. Either
  bridge that deliberately or keep it an isolated experiment.
- Default evidence directories to 0700 and files to 0600. This is filesystem access
  control, not application-level encryption. FileVault protects a locked disk if enabled;
  the POC does not claim to resist malware or another process running as the owner.
- Collection is read-only. Extraction gets selected text, not provider credentials,
  unrestricted filesystem tools, shell or browser automation. Source content cannot
  authorize network calls or change collection scope.
- Block archive path traversal, symlinks, decompression bombs and oversized records.
  Partition large sources at adapter boundaries without dropping tails silently.
- Show outgoing network categories: source sync, explicit model download, later remote
  assistant. Telemetry and crash reporting contain no personal content by default.
- Export requires an explicit destination and lists included categories. Backup and
  diagnostics never upload automatically. Existing public profile content is not a sink.
- Removing or narrowing source scope filters canonical retrieval immediately and
  invalidates stale model context/results before display. In-flight extraction checks
  source revision/access again at commit. Revoked jobs cannot resurrect deleted records.

## 10. Assistant access and ledger evolution

Core POC has no internet listener. Independence milestone supplies an app-mediated,
authenticated local MCP/API facade for two explicitly configured local clients.
Operations: search evidence, read an allowed record, get relevant context, propose a
memory, report work. Owner confirmation and purge are not granted to ordinary agents.
Each request enforces client/collection/operation scope, limits and revision checks.

Later cloud agents need a separately reviewed authenticated transport; never publish
a raw local endpoint just to make a demo work. Context sent to a cloud assistant leaves
the Mac even if storage and inference in the desktop app are otherwise local.

Distributed entry is compatible with one local authoritative ledger. Direct work in
Muse is captured only if a supported connector/hook reports it or a later reconciliation
finds evidence. Instructions to remember to report are not guaranteed capture. The app
must show unverified coverage, rather than imply it sees all activity.

Prepare outbox event types now (`source.updated`, `assertion.corrected`,
`work.updated`) without implementing a broadcast network. Future delivery records
queued/delivered/acknowledged/acted-on separately. Push support, tool calling, polling
and human notifications are different capabilities per agent. No universal pause or
cancel promise. Explicit task ownership, deduplication, bounded subscriptions and loop
prevention are prerequisites for automated cross-agent coordination.

## 11. Phased implementation plan

Each row is a focused PR or small PR series, not a promise of elapsed time. Complete
exit evidence before broadening scope; do not begin with all connectors in parallel.

| Phase | Work | Exit gate |
|---|---|---|
| 0. Feasibility and decisions | Signed Swift shell; permission ownership; Calendar read; Messages compatibility inventory; local model health; GBrain binary compatibility; tiny source sample previews; choose email provider/archive | Written supported-source matrix and explicit fallbacks; no source writes; actual model/resource measurements; choose final bundle ID and data home |
| 1. Desktop foundation | Window/menu lifecycle, bundled worker IPC, app-owned data home, versioned SQLite, job queue, source manifests, evidence viewer, synthetic sample import | Launch without checkout/PATH dependencies; quit/relaunch resumes bounded import; duplicate import stable; no public routes |
| 2. First useful cold start | Calendar + email archives, trip/event models, factual extraction, provenance, source coverage, simple search and cited Ask | Reconstruct one synthetic trip end-to-end; owner-run local trip review; parse failures and canceled bookings visible |
| 3. Conversation context | WhatsApp export adapter; Messages collector if phase 0 passes; speaker/time handling; reversible people linking | Cross-source trip evidence with correct speakers; ambiguous names unresolved; denied access fallback works |
| 4. Purchases and background | Amazon archive adapter; returns/replacements; LinkedIn profile/connections; optional location archive adapter | Purchase/return sample correct; gifts remain unknown until reviewed; stale career facts dated; absent location never claimed |
| 5. Personal understanding | Candidate preferences, review queue, decisions/revisions, conflict handling, correction propagation, GBrain projection and bounded context packs | Correction survives reprocessing/restart and stale index; deleted source not retrieved; evaluation gates pass |
| 6. Independence and pilot packaging | Scoped local facade, two client adapters, export/restore, signed/notarized build, dependency/license inventory, permission recovery | Two clients honor one correction; unauthorized reads fail; clean-machine owner install and restore succeed |
| 7. Separate future proposal | Chosen live email refresh, remote agents, source-change notifications, work ledger integration | Per-agent integration contract proven before enabling each path |

Phase 0 chooses whether GBrain projection starts in phase 2 or phase 5. Plain evidence
search must remain available if model extraction is slow. Location's future collection
and universal live chat ingestion do not become hidden dependencies of phase 6.

Suggested code boundaries: `desktop/macos/` for shell/native permissions;
`personal_brain/` for app-owned domain, adapters, jobs, retrieval and protocol;
`tests/personal_brain/` for synthetic fixtures/evaluations. Factor shared pure helpers
from `harness/` only when needed, keeping existing imports and behavior compatible.
No lock changes or schema migrations are part of this specification-only PR.

## 12. Validation and acceptance

Use synthetic source fixtures and fake inference in automated tests. Personal pilot
data and question/answer records stay outside Git, run locally and are never sent to
a cloud model as verification. Tests must not depend on a paid key.

| Area | Required checks |
|---|---|
| Source parsers | Calendar DST/all-day/recurrence/cancellation; MIME and thread IDs; WhatsApp locale/multiline; Messages edits/reactions/missing bodies; Amazon multi-item/return/currency; LinkedIn missing files; location precision/timezones |
| Durability | Kill worker mid-acquisition/commit/index; resume no duplicates; no lost committed records; disk-full and permission revocation; replay cannot overwrite owner correction |
| Truthfulness | Attribution, gifts vs ownership, scheduled vs visited, source conflicts, no unsupported preference promotion |
| Isolation | No public or portfolio access; no remote fetch from imported content; IPC/client auth failures; same-scope retrieval under index contention |
| Removal | Withdraw/purge with stale index, cached answer, active extraction, old backup restore and subsequent source reimport |
| Native package | Permission allow/deny/revoke/regrant, quit vs window-close, sleep/wake, offline, missing model, fresh user install, no developer tools required |
| Performance | Record import throughput, model throughput, peak RAM/disk and query latency on the owner's Mac; keep UI responsive during indexing |

For removal tests, distinguish deliberate new reimport from a revoked in-flight job.
Explicit reimport can restore removed data only after a scope preview and owner action.

Initial targets, to calibrate after phase 0: deterministic first search within two
minutes of starting a small import; first reviewed trip within a 30-minute processing
session excluding archive delivery/model installation; UI operations below 200 ms
where not awaiting I/O; lexical search p95 below two seconds on 10,000 fixture records.
If these fail, show measured bottlenecks and adjust scope, not invented completion times.

Acceptance corpus: 25 answerable questions with a private owner-reviewed answer key,
plus at least 5 deliberately unanswerable questions. Pass requires at least 20/25
correct supported answers, citations for every substantive personal claim, and no
fabricated answer on the 5 unsupported cases. Inspect citation entailment, not just
presence. No incorrect automatic person merges in the reviewed sample. Two-client
correction reuse, duplicate-import stability and removal tests must also pass.

Repository checks follow `AGENTS.md`: relevant Python tests; frontend build only if
frontend changes; both real Temporal recovery tests against an isolated server if
durable harness execution changes. New desktop runner needs its own crash/recovery
tests even though it does not use Temporal. Code changes require privacy hooks,
focused PRs and owner-operated exact-head local CI before merge. Never operate the
serving Temporal server or the existing application volume for POC tests.

## 13. Open decisions and next action

Resolve during phase 0: email provider, actual Mac OS/hardware support, Messages
permission feasibility, available location archive, source export schemas, local model
quality/resource fit, app name/bundle ID, packaging/signing access and GBrain projection
cost. Do not delay archive ingestion for unknown future agent push APIs.

Next implementation task: a signed Mac source-access prototype that reports Calendar
and Messages availability, imports one synthetic email archive, checks local inference
and initializes an isolated GBrain index. It must leave the portfolio and existing
brain unchanged. This produces the evidence needed to size the rest of the plan.

## 14. Research and implementation references

Reviewed 2026-11-07; external documentation describes capability, not a completed
integration or an authorization to import personal data.

- [Harness ingestion contract](ingestion.md), [Deep Agents runtime](deepagents-runtime.md),
  [repository privacy rules](../security/README.md), [release process](contributing-and-releases.md).
- [Pinned GBrain cold-start workflow](https://github.com/garrytan/gbrain/blob/v0.60.530.0/skills/cold-start/SKILL.md): phased import, source linking and resume concepts.
- [Pinned GBrain bootstrap guide](https://github.com/garrytan/gbrain/blob/v0.60.530.0/docs/guides/bootstrap.md): identity setup, warm context and lifecycle limits; not enabled by this spec.
- [Pinned GBrain Google connector](https://github.com/garrytan/gbrain/blob/v0.60.530.0/docs/guides/google-connect.md): candidate live connector, local credential vault and backfill behavior.
- [Apple EventKit access](https://developer.apple.com/documentation/eventkit/accessing-the-event-store),
  [Calendar export](https://support.apple.com/en-ca/guide/calendar/icl1023/mac),
  [Keychain Services](https://developer.apple.com/documentation/security/keychain-services).
- [Apple visit monitoring](https://developer.apple.com/documentation/corelocation/cllocationmanager/startmonitoringvisits()): future visit delivery, not a historical Maps export guarantee.
- [WhatsApp export](https://faq.whatsapp.com/1180414079177245/),
  [LinkedIn account download](https://www.linkedin.com/help/linkedin/answer/a1339364/downloading-your-account-data?lang=en),
  [Amazon data request](https://www.amazon.com/hz/privacy-central/data-requests/preview.html).
- [Omnesis repository](https://github.com/omnesis-dev/Omnesis): collector alternative; AGPL-3.0 at review time, not an adopted dependency.
