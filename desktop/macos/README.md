# Personal Brain for Mac: selected local imports

A native SwiftUI app for selecting Calendar sources and Messages conversations,
importing a chosen date range into a local evidence store, and browsing provenance.
No model calls, inference, network listener, source writes or cloud uploads. GBrain
indexing and inferred preferences are not enabled yet. The portfolio is unchanged.

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
   rich-body bytes. Data remains local. Messages attachments are not fetched.
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
up, indexed in GBrain or shared with another assistant. Those are subsequent milestones.

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
This version retains its hex representation with the record and marks the body as
pending decoding. It never guesses text from arbitrary binary bytes or treats an
attachment as text. Search cannot find undecoded content. An original rich body is not
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
