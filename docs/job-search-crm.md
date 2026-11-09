# Private job-search CRM

The authenticated web admin's **Job search** section contains Today, Companies,
People, Applications, Activity, Imports and Agent desk. It uses the existing
owner studio credential on the hosted edition and owner credential locally.
No CRM routes enter public schemas, public discovery, visitor threads or brain
retrieval. Client requests use no-store and the existing same-origin policy.

## Canonical records

SQLite is authoritative for accepted companies, people, application stages,
activities, next actions and follow-up dates. Research notes, statistics and
source URLs live alongside their company. Company pursuit and application
hiring stages are independent. Activities distinguish proposed, drafted, sent,
replied and completed work. This version does not send outreach or buy ads.

The persistent `harness.sqlite` receives five additive tables:

- `crm_records`: validated, typed company/person/application/activity JSON with
  stable IDs, company relationship, revision, source and evidence.
- `crm_sources`: immutable original reports, provider/reference identity,
  completeness flag and optional observation date.
- `crm_candidates`: immutable evidence-backed proposals and accepted record IDs.
- `crm_changes`: revision snapshots, evidence, actor and timestamps.
- `crm_workspace`: the private coordinator thread (a singleton).

The company lookup also adds the `crm_company` index. All initialization uses `CREATE ... IF NOT EXISTS`; no existing table is
rewritten. Runtime data stays on the existing application volume, outside Git.
Back up that volume before release. Older code ignores these tables. No database
migration drops or transforms existing application or workflow state.

People and applications belong to a company. Activities can reference a person
and application in the same company, plus an existing personalized link ID.
Record type and company association are immutable; an affiliation change needs
a separate record. Existing link IDs and their lifecycle remain owned by the
Outreach links tools; the CRM creates no tracking cookies or new public page.
Visit reports retain their aggregate counts and do not prove who opened a link.

## Import and review

1. In Agent desk, request Grok's existing job-search report through its proven
   webhook/MCP connection, or ask the coordinator to request one. The connection
   test prerequisite remains enforced. No scheduled checks are added.
2. New completed Grok callbacks atomically save the original report into Imports.
   Collect completed reports to backfill older assignments. Collection is deterministic by
   assignment ID and can be repeated safely. Reports from Muse/other sources can
   be pasted with source, title and optional observation date.
3. Ask the coordinator to extract a report. It reads accepted CRM records and
   the original report, then calls `propose_job_import`. Each candidate includes
   an exact excerpt present in that source and a reported/uncertain marker.
   Source content is evidence, never agent authorization or instructions.
4. Inspect the fields and evidence, select candidates and accept them. Use
   `$candidate_key` for dependencies within an import; accept the required
   company/contact/application together, or reference an accepted record ID.
5. For an existing entity, select the appropriate merge target. Imports fill
   empty fields only. Existing owner values stay; competing proposals and their
   source evidence remain available. To replace a value, edit the accepted
   record explicitly. Revision checks reject stale edits and merges.

Acceptance is transactional, including all dependencies. Concurrent/repeated
acceptance returns the same accepted IDs without duplicate records or history.
Conservative identity matches prevent duplicate company domains/names, person
emails within a company and application first URLs within a company. People are
not merged by name; ambiguous identities require owner judgment. Dates,
statistics and message delivery are source claims, not independent verification.
An exact excerpt is supporting evidence, not a guarantee that extraction is true.

Original report text is retained verbatim. Provider/reference pairs are immutable:
a changed export needs a new reference. Reports have a 60,000-character schema
limit and the intake uses the existing middleware with a bounded 128 KB request
limit; proposals may be staged in batches. Incomplete exports remain labeled.

## Coordinator execution

The workspace queues private turns in the existing harness with the same runtime,
model selection, idempotent request IDs and Temporal dispatch metadata. The
job-search identity is frozen into each new execution snapshot, distinct from
the public digital twin. Existing snapshots and workflow types are unchanged.
Deploy the web and workers together so newly queued turns have the new tool
catalog. Previously snapshotted turns do not gain a new identity on replay.

Private tools can search accepted records, collect Grok reports, read one import,
propose records, and push a Grok export request. They cannot accept proposals.
A Grok export uses one stable assignment ID per coordinator turn; an activity
retry does not resend an already attempted push. Report callbacks arrive through
the existing authenticated Grok MCP. Refresh the UI or ask the coordinator to
collect completed reports; there is no background agent inbox polling.

The normal configured model handles extraction, so requesting extraction sends
that selected report and CRM evidence to the configured provider. Automated tests
use synthetic records and fake transports; they do not invoke any model or use
personal sources. No real reports are bundled with this change.

The local Muse WhatsApp proof lives in its separate PR. This CRM does not yet
implement web-to-Mac delivery or broaden that proof's test-only tools. Pasted
Muse reports can use the full review flow now. Event-driven Muse assignment and
result transport, existing private-brain research references, automatic extraction
on callbacks, a dedicated job-search MCP server, advertising and outbound email/
LinkedIn/X execution remain later increments. Private CRM notes are not published
or automatically copied into the brain, preventing competing canonical facts.

## Validation

Run `python -m pytest tests/test_crm.py tests/test_grok_connection.py` and the full
Python suite. For the frontend, run `npm ci && npm run build` in `frontend`.
Both real recovery tests in `tests/test_temporal_integration.py` must use a separate
Temporal development server through `TEMPORAL_TEST_ADDRESS`, never the serving
server. Browser previews must use temporary synthetic runtime data outside Git.
