# Autonomous private job-search CRM

The authenticated web admin's **Job search** workspace contains Today, Companies,
People, Applications, Activity, Imports and Agent desk. SQLite is authoritative
for operational facts. Reports enter automatically; the owner edits afterward.
There is no per-record acceptance requirement.

## Automatic intake

New source reports, including completed Grok export callbacks, atomically create
durable extraction jobs in the existing SQLite/Temporal turn queue. Each job
reads a bounded source chunk, imports supported records, and reconciles clear
matches without an approval step. Original report text is immutable and retained
with its provider/reference identity, completeness claim and observation date.

Reports are split at line boundaries into ranges of at most 2,400 characters.
Each range has a deterministic source/offset job ID and its own private thread,
so long reports do not silently exceed tool context limits or accumulate a large
conversation. Repeated callbacks and restarts do not create duplicate jobs.
Private import tools enforce the job's original source. They expose bounded
chunks, not all source reports at once. Import snapshots omit unrelated private
brain retrieval and disable web research; the configured model receives the
selected report chunk and relevant CRM records. OpenAI extraction responses have
an 8,192-token ceiling. This is actual configured-provider work, not a CI probe.

On startup the app imports any existing staged records and resumes missing
extraction work. Reports saved while a model is unconfigured retain their jobs
as waiting for that model; startup or Process saved reports queues them once a
model is configured. Terminal import failures automatically retry up to twice; startup/processing can also resume failed jobs within the same limit,
using the existing explicit-retry workflow generations. No scheduled inbox
checks, heartbeats, new workflow types or new external permissions are added.
Muse's web-to-Mac transport remains separate: a received/pasted Muse report uses
this automatic flow, but this change does not claim an autonomous Muse sender.

## Reconciliation and editing

Typed records retain company/person/application relationships, evidence excerpts,
uncertainty and revision history. Company pursuit and application hiring stages
are separate. A drafted message is not a send; a lead is not an application.

Clear company names/domains, people with a matching email or unique name within
a company, and application URLs/normalized role titles reconcile automatically.
Role abbreviations are normalized. A single sufficiently similar potential role
can match an already submitted role within that company. Different known
application dates stay separate. Ambiguous people and events remain separate
rather than losing information. All original source claims remain available.

Automatic merges combine research notes and URLs, preserve stronger known
pipeline stages, and prefer newer source values for unlocked fields. Empty
schema defaults do not erase facts. Higher recorded company priority remains
until the owner changes it. Older sources can fill gaps without replacing the
record's newer source provenance. Manual changes, including explicit field
clears, are detected from existing revision history and always win over later
imports. Edits do not disable importing other fields or related entities.

Malformed model arguments return corrective feedback to the model. Invalid
excerpts or unresolved dependencies are recorded as data issues while valid
records continue importing. Missing dependencies can resolve when later chunks
arrive. Reports and evidence are preserved even if extraction fails. Source
quotes are supporting evidence, not independent verification of their claims.

All records remain directly editable in the company, contact, application and
activity views. Existing personalized links can be attached to activities; visit
counts remain aggregate signals in Visit reports and do not identify a visitor.
Importing information does not send outreach, submit applications, publish
knowledge or spend advertising budget.

## Storage, boundaries and rollout

The existing `harness.sqlite` stores `crm_records`, `crm_sources`,
`crm_candidates`, `crm_changes` and `crm_workspace`. This increment adds
`crm_import_jobs`, `crm_import_issues` and a revision-history index, using
`CREATE ... IF NOT EXISTS`; no existing tables are rewritten or dropped.
`accepted_id` remains the internal mapping from a historical candidate to its
live record, not a requirement for human approval. The legacy acceptance route
remains compatible for existing clients but is not required by the UI or tools.

Back up the application volume before rollout. Deploy web and workers together
for the new private catalog. Existing workflow types and saved execution snapshots
remain compatible. Runtime data, exports, credentials and model logs stay outside
Git and the build. No dependency/lockfile changes are required. Public visitor
schemas, discovery and tools expose no CRM data or mutation capability.

Source/proposal intake remains bounded to 128 KB, with reports limited to 60,000
characters. Partial reports stay labeled. Imports do not publish the brain or
create a competing brain copy of canonical CRM stages and outreach history.
Advertising, third-party outreach execution and continuous Muse transport remain
separate increments.

## Validation

Use synthetic records and fake models in tests. Run the Python suite plus both
real Temporal recovery tests against an isolated development server, never the
serving server. Run `npm ci && npm run build` in frontend. Browser previews use
synthetic runtime state outside the repository. Checks cover automatic intake,
field corrections, explicit clears, duplicate jobs/records, chunk completeness,
partial failures, source-bound tools, retry limits and private context isolation.
