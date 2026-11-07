# Personal Brain for Mac: selected local imports

A native SwiftUI app for selecting Calendar sources and Messages conversations,
importing a chosen date range into a local evidence store, and browsing provenance.
No source writes, cloud uploads or network listener. Optional local learning and
GBrain indexing are described in the 0.3 section below. The portfolio is unchanged.

Requires Apple Silicon, macOS 14+, Python 3 and Apple Command Line Tools.

```sh
python3 desktop/macos/build.py --test --launch
```

Builds stay in this worktree's ignored `.runtime/personal-brain-macos/` directory.
Quit the previous app before launching a build from a different worktree. Swift's
first module-cache build can take longer. No developer checkout is used for data.

## Import flow

1. Load calendars and/or message threads. This reads calendar names and conversation
   labels, not event/message bodies. All checkboxes start unchecked. Thread search
   matches labels/addresses; no Contacts permission or name lookup is requested.
2. Select sources and a date range. From is inclusive and Until is exclusive; the
   initial range is the previous 90 days through tomorrow, including today. Adjust
   dates before starting. The app copies only the selected calendars/conversations.
3. Click **Import selected sources**. This is the collection action. Imported records
   may include event notes/attendee names, message text/speaker identifiers and original
   rich-body bytes. Data remains local. Messages attachments are not fetched; supported rich text is decoded.
4. Pause at a batch boundary, or Quit. Resume saved import continues its original
   source selection/range, not any subsequently edited checkboxes. Stop pending import
   ends a plan but retains committed evidence. A new import can then be started.
5. Browse local brain. Search plain-text bodies/titles; the latest 100 matching records
   show timestamps and source metadata. Rich binary bytes are retained but hidden from
   the viewer. No text is sent to a model. Sources are evidence, not owner beliefs.

Selections are not a live access-control filter: deselecting a source does not delete
previous imports. This version has no removal/purge UI. Do not import sources you do
not want retained locally. There is no background sync or deletion reconciliation.

## Local store and durability

`~/Library/Application Support/PersonalBrain/brain.sqlite` is an independent, version-1
SQLite evidence database. The directory is mode 0700 and database mode 0600; SQLite
uses DELETE journaling. These are filesystem protections, not app-level encryption.
The app does not migrate or touch the Afterhours data directory or serving Temporal.

Each source record has a source-scoped ID, original timestamps and metadata, stable
payload hash and revision counter. Reimporting identical data does not change its
revision. Reimporting changed data updates the current record and increments its
revision; old revisions are not retained in this initial version. Calendar occurrences
use EventKit identity plus occurrence date. Provider ID changes can produce new records.

Calendar import commits one selected calendar/month window at a time. Messages import
commits up to 200 selected conversation records per page. Evidence and its import
checkpoint commit in the same transaction. Failure retries the last uncommitted batch;
no claim of exactly-once source reads. The persisted plan survives restart. Live source
content can change between batches; this is a bounded import, not an archival snapshot.

One app process is supported. Imported evidence is not automatically exported, backed
up or shared with another assistant. GBrain projection is described below.

## Source limitations

Calendar reads EventKit events only from chosen calendars, with monthly predicates
and recurrence occurrence IDs. It preserves scheduled/canceled state, original timezone,
all-day flag, dates, location and attendee display names. It does not infer attendance,
resolve attendee identities, or create/update Calendar events. An event overlapping the
range can be returned by EventKit; its start may lie before the chosen range. Duplicate
occurrences across windows coalesce by ID. Source deletions and moves require future
reconciliation; reimport does not prove complete history or remove missing records.

Messages opens the live database read-only with bounded SQLite busy timeout. It uses
`chat_message_join`, checks required schema columns, supports seconds/nanoseconds since
Apple's epoch, and keeps the sender distinct from the owner for received messages.
The cursor is message ROWID within a frozen thread/date selection. Metadata includes
available edit/reaction/retraction fields. Known retractions suppress old body bytes.
It does not interpret reactions as independent owner statements. Missing GUID/date rows
are counted as skipped; malformed/incompatible databases stop with a generic error.

Many modern messages have only an Apple rich-body archive rather than plain `text`.
Version 0.3 decodes supported formats and retains original bytes. Unknown formats are
explicitly marked unsupported. Search cannot find undecoded content. An original rich body is not
an attachment download or a complete copy of all edit history. Voice notes, images,
attachment files and contact-name resolution are not imported.

## Permissions and packaging

Access settings retains the original inventory-only Calendar probe and Messages
`SELECT 1 FROM message LIMIT 1` probe. Calendar full access is required by EventKit
for reading; the app never writes Calendar data. Grant/deny is under owner control.
For Messages, if blocked, add the actual app under System Settings → Privacy & Security
→ Full Disk Access, quit/reopen and load threads again. This is broad system access;
never alter TCC databases, disable protections or grant Terminal access as a workaround.

Permission probes save only generic states, calendar count and check timestamp to
`access-status.json` in the same app home. The app does not log private source contents
or SQLite error text. Rebuilds/moving may require permission reapproval.

Builds use ad-hoc signing with designated identifier `local.personalbrain.mac`. This
is not publisher-authenticated Developer ID signing/notarization or an App Sandbox.
It is a development pilot and has the ordinary privileges of the owner process.
Window close leaves the menu-bar app running; Quit stops it at the last committed batch.
No launch-at-login helper or unrelated services are started/stopped.

## Verification

`--test` runs synthetic SQLite fixtures only. It tests read-only source byte stability,
empty/missing/malformed/incompatible databases, selected-thread/date boundaries,
seconds/nanoseconds, owner/sender attribution, rich/retracted bodies, 200-record paging,
checkpoint reopen/resume, idempotent updates, literal search and transaction rollback
when checkpoint persistence fails. No API key, personal fixture or real source read is
required. Real permission/inventory checks run from the packaged app under macOS.

Public source publication permits `.swift` only under `desktop/macos/Sources/`.
Generated apps, databases and local reports remain outside source control. No frontend
or harness workflow changes are required by this milestone.

## Rich message decoding and local memory (0.3)

This version replaces the earlier rich-body limitation. Imports now decode supported
Apple typedstream NSAttributedString/NSMutableAttributedString bodies with pinned
pytypedstream 0.1.0. Keyed NSAttributedString archives use a bounded property-list
reader. Neither path instantiates native Objective-C objects from untrusted archives.
Unicode, emoji, multiline text and attachment placeholders are preserved. Other root
classes, malformed/truncated input, oversized input and unknown formats remain marked
unsupported; original bytes stay local. Attachments themselves are not downloaded.

Open **Memory & dreaming**:

1. **Decode stored rich messages** repairs up to 200 previously imported records per
   click, checkpointing each update. Already successful or unsupported-at-this-decoder
   records are not retried endlessly. New imports decode automatically.
2. **Index imports in GBrain** projects evidence and owner-confirmed memory into a new,
   isolated `PersonalBrain/gbrain/` home. Changed-page receipts make retries incremental.
   Successful new imports trigger indexing automatically. If another memory job is
   running, use the button afterward. The SQLite store remains authoritative.
3. **GBrain maintenance** indexes changes and runs the pinned native `dream` command
   with only `extract` and `orphans` selected. This maintains links/timeline metadata
   and reports orphan pages. The UI reports native partial/warning status; no suggestion
   of personal insight is implied by successful maintenance. It does not run synthesis,
   embeddings, model extraction or consolidation of hot facts into takes.
4. **Learn from next 5 records** calls the selected installed Ollama model on loopback,
   with no proxy, redirects or cloud fallback. Cloud-routed model metadata/names are
   refused. Eligible inputs are plain/decoded owner messages and calendar entries.
   Other people's messages are excluded. Calendar suggestions are forcibly rewritten
   as scheduled plans, never attendance or preferences. The model can validly return
   no suggestions. Each proposal needs an exact quote and current source hash.
5. Review the quote/evidence ID and **Confirm** or **Reject / remove**. Only confirmed,
   still-current proposals become reviewed memory pages on the next index. Previously
   indexed stale/rejected pages are excluded immediately from in-app search, and removed
   from GBrain by the next index. There is no external GBrain/MCP access in this version.
6. Optionally enable a five-record learning batch every 30 minutes while the memory
   sheet remains open. It is off by default, not persistent, and starts no new work while
   the sheet is closed, app is quit or Mac sleeps. In-flight work may finish when the
   sheet closes. One bounded worker job runs at a time; no daemon/cron/global hooks.

GBrain does have broader native dreaming: `synthesize`, `patterns`, extraction and
`consolidate` (hot facts to takes), among other phases. A full dream can call models;
`dream --dry-run` is not a universal no-inference guarantee. This app therefore uses
explicit non-model phases plus its own bounded local suggestion/review step. It does
not pretend that indexing alone learns preferences or that canonical memory is a
GBrain hot-facts table. Native synthesis/local tool-loop compatibility and accepted
memory promotion are future, separately measured steps.

### Boundaries, resources and schema

The development app now bundles a checksum-verified official GBrain 0.60.530.0 binary,
pytypedstream wheel, matching source archive and license notices. `build.py` accepts
`--gbrain-binary /path/to/existing/pinned/binary` to avoid a download, still verifying
its pinned release hash. First build requires network access for missing dependencies;
imports never do. No dependency download executes an install script. See
[third-party notices](THIRD-PARTY.md). Runtime requires Apple's `/usr/bin/python3`,
provided with Command Line Tools for this pilot; a bundled interpreter remains a later
packaging task. NSArchiver is used only to generate synthetic test fixtures.

The worker receives record bytes via stdin, not process arguments; stderr/provider
output is suppressed. Decoder batches are capped at 16 MB and 200 bodies, with a
15-second timeout. Rich bodies are capped at 1 MB decoded input each. Stored repair
is capped at 200 records. Local learning runs at most five records per action, with
8,000 characters per input, 1,200 output tokens per call and a 90-second HTTP timeout.
Malformed model output leaves the record pending for retry. Exact quotes constrain
provenance but do not prove entailment; owner review is still necessary.

The GBrain subprocess gets a minimal environment, its own HOME/GBRAIN_HOME and working
folder, no provider secrets, and a macOS sandbox-exec egress-denial profile. This is
network containment, not a general filesystem sandbox. It never opens the serving
Afterhours GBrain indexes or changes their config. No GBrain autopilot, global hooks,
Git synchronization or model phases are enabled. GBrain command timeouts are 120
seconds; the app worker has a 10-minute action deadline. Interrupted indexing resumes
changed pages from its receipts. `/usr/bin/sandbox-exec` is a development dependency;
if it is unavailable, indexing fails closed rather than dropping egress protection.

New additive SQLite tables store projection receipts, per-source learning receipts
and reviewable proposals. Current-hash joins hide proposals based on changed evidence.
Reprocessing does not automatically confirm a proposal. Source groups and accepted
memories link to evidence pages; raw rich-body bytes are never indexed or sent to a
model. Keyword search validates GBrain hits against the current canonical projection.
Confirmed memory pages are still tagged as tool-output provenance when written through
GBrain; the app does not falsely claim they were spoken in a live agent conversation.

Synthetic tests include both Apple archive formats, unsupported roots, corrupt input,
projection idempotency/withdrawal, attribution, rejected/stale memories, local inference
failure, exact-quote validation and cloud-route rejection. The opt-in real GBrain test
uses only a temporary synthetic database with all GBrain network access denied:

```sh
DESKTOP_MEMORY_RESOURCES="$PWD/.runtime/personal-brain-macos/Personal Brain.app/Contents/Resources" \
  python3 -m pytest tests/test_desktop_memory.py -q
```
