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
