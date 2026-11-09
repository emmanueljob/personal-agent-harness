# Private job-search workspace

The admin Job search section and its coordinator share the same private SQLite
records and validation rules. Company, person, application and activity titles
open dossiers with notes, dated evidence, source links and related records.
Company pages add a cover/logo, sourced news cards, statistics and a graph of the
owner's recorded opportunities. Empty research and news sections stay visibly
empty until information is saved; no metrics, images or news are fabricated.
Images are referenced by URL and loaded with no referrer. Exports and private
research stay on the application volume, outside public source and builds.

The coordinator reads paginated records and accurate global totals with
`read_job_workspace`, and complete evidence and revisions with `read_job_dossier`.
`write_job_record` creates complete records; `update_job_record` changes selected
fields using the latest revision. Both share the form's validation and revision
checks. Owner-directed chat edits are recorded as owner changes, so later imports
preserve them, including explicit clears. Source extraction turns cannot invoke
these direct mutation tools; they continue through evidence-backed imports.
Report collection/import, source reading, saved-link lookup and import processing
also have tools. These capabilities apply to the private job-search workspace;
public site publishing and outbound outreach are separate surfaces.

Enrich research queues an owner-requested coordinator turn. It uses existing
source evidence and configured public-web tools, then saves dated findings and
source URLs to the same CRM record. News includes explicit publication dates;
undated articles are labeled accordingly. A request does not guarantee web
search availability or a complete research result.

## Saved artifacts

The coordinator and the Artifacts UI share `crm_artifacts` and append-only
`crm_artifact_changes`. These additive tables do not replace or delete CRM data.
Artifacts are Markdown documents or bounded declarative dashboards: columns,
rows, a description and optional linked CRM IDs. The UI renders sortable,
filterable tables, numeric bar charts and grouped boards with ordinary React.
Generated content cannot execute JavaScript or arbitrary HTML. Dashboards are
explicit snapshots with update times, not silently live queries. Chat can read
and revise them using the same optimistic revision checks as the artifact editor.
The existing Markdown document tool remains compatible and its documents also
appear in the coordinator transcript.

Chat shows tool progress while a requested turn is queued/running and refreshes
its response and records on completion. This only observes an active harness
turn; agent report ingestion remains push-based without inbox heartbeats.

No dependency changes or workflow types are introduced. New tool names are
registered through the existing runtime plugin registry; old tools, artifacts,
record JSON and workflow checkpoints remain compatible. Code deployment leaves
the mounted application volume and published brain in place.

## Recoverable enrichment and simplified workspace

Priority enrichment is started through the private `/api/admin/crm/enrichment`
route or `enrich_job_records`. An empty selection includes high-priority companies
and their linked contacts; explicit IDs let the owner restart only unfinished work.
Idempotent batch IDs and active-record deduplication prevent accidental duplicate
runs. Each record/attempt has an isolated private thread, with no unrelated brain
retrieval or accumulated batch conversation. Record mutations are scoped to the
assigned entity and current attempt.

Additive `crm_enrichment_batches`, `crm_enrichment_jobs` and
`crm_enrichment_attempts` tables persist selection, progress and actual save
receipts. A model saying it finished is not success: a CRM write and its receipt
commit atomically. A completed reply without a save, or a failed run without a
save, retries automatically with a fresh attempt after 30, 120 and 480 seconds.
After four attempts the job shows Needs attention instead of spending indefinitely.
A failure after a successful save keeps the record and marks the work Saved.
The existing queue executor resumes this state on restart; no external agent
inbox polling, heartbeat or new scheduler is introduced.

Private model calls and private web-search model calls no longer consume the
public demo quota. The public daily cap remains intact. Mode is taken from the
server-bound thread/activity, never a model-supplied argument. Private provider
billing limits and service availability still apply. No secrets or deployment
settings are changed.

Enrichment appends notes, unions source/news URLs and preserves owner corrections,
existing next actions and application/relationship stages. Automatic research
changes use the `agent_research` history actor. Optional `summary` fields and
professional contact `image_url` fields are backward-compatible JSON additions.
The public page reader exposes observed image/favicon metadata after validating
public URLs; it does not fetch images or execute page code. Private enrichment
has six bounded web calls so searches leave room for original-page verification.
The coordinator retains write tools when research is exhausted, so it can save
partial findings rather than returning an unsaved narrative.

The UI uses compact company/contact lists, a priority shortlist, tabbed record
sections, a separate Enrichment progress view and a side assistant. Setup and raw
import evidence live under workspace settings/Sources. Technical batch prompts
are kept out of the conversational chat view; persisted history is retained.
Research references: Attio's record-page tabs and record-details sidebar, folk's
relationship-focused lists, and HubSpot's overview/activity/association separation.
No vendor assets or product code are copied. The Sources tab retains original
reports; snapshots and saved visual artifacts remain available.

No workflow definition or Activity signature is changed. The existing preparation,
model binding and queue dispatch gain private scope handling; recovery checks must
run against an isolated Temporal development server before release. Deploy the web
and workers from the same release, preserving the application volume and existing
workflow bindings.
