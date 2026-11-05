# Observability

Open **Owner studio → observability** on the environment you want to inspect.
The existing owner password protects metrics, conversation search and exports.
Local data and production data are separate; this screen reads the current
application volume. Never put exported conversations in Git or build contexts.

## UI metrics

The existing first-party `/api/visit-events` relay records page views, browser
sessions, contact clicks, bookshelf opens, suggested questions, retries and theme
changes. The dashboard summarizes the last 30 days. The SQLite `ui_events` table
is created additively at web startup and retains at most 50,000 events / 30 days;
counts therefore describe retained events, not a lifetime audit. Existing outreach
attribution and optional Plausible delivery continue independently.

The admin portal’s tracking on/off switch controls this relay, including local
metrics and PostHog delivery. Only explicit events are collected. DNT and Global Privacy Control disable client
tracking, and their request headers are respected by the server. A random
sessionStorage ID identifies a browser tab session, is hashed on the server, and
is unrelated to the conversation cookie. Tracking is best effort; blocked or lost
requests undercount activity. Neither this integration nor its PostHog relay sends
chat text, tool bodies, IP headers, names, referrers or arbitrary URLs. Session
replay, autocapture and automatic exception collection are not enabled.

To connect a PostHog free project later, set these **runtime** environment values
on the web process (Fly secrets/config in production; private environment locally):

- `POSTHOG_PROJECT_TOKEN`: the project's ingestion token, not a personal API key.
- `POSTHOG_REGION`: `us` (default) or `eu`, matching the project.

No frontend rebuild is needed. Events use the region's fixed HTTPS ingestion
endpoint. Server-side delivery disables GeoIP enrichment and person profiles.
PostHog receives the explicit event name, hashed tab session ID, section enum,
contact target enum and light/dark theme. Delivery times out after three seconds
and is not retried; local metrics remain available during PostHog outages. Invalid
regions disable external delivery. Leaving the token blank disables PostHog.
Existing optional analytics providers keep their own configured behavior.

Suggested PostHog insights: section page views, pageview → guide_question_asked →
cta_click funnel, bookshelf opens, and theme usage. These are UI interaction
metrics; authoritative accepted turns and failures are shown from the database.

## Conversation logs

All existing saved conversations are searchable by user prompt or agent reply,
including public and private modes. Pagination remains stable when new chats
arrive. Open a conversation to inspect all saved user messages, answers, errors,
events, tool arguments/results, artifacts and the latest saved Deep Agents model
transcript for each turn. Events are not truncated to the visitor UI's 200-item
window. The workflow ID, runtime and selected provider/model help correlate
Temporal history. Refresh to see a running turn's latest persisted state.

**Export complete JSON** downloads that conversation, including recorded tool and
model messages. The protected API uses `no-store` and is excluded from public agent
schemas. Exports omit the execution-context snapshot (system prompt, retrieved
brain and model connection settings); tool/transcript content can still contain
sensitive visitor text, so treat exports as private data. The page clears its
loaded records when the entered owner password changes.

These are durable application records, not a new model-call tracing system. Old
turns cannot acquire intermediate messages they never saved. The transcript is
the latest checkpoint rather than every superseded checkpoint; hidden model
reasoning is not recorded. Exact token counts, per-call latency, provider costs,
raw process stack traces and historical Temporal attempts are not reconstructed.
The change does not modify workflow definitions, model execution or checkpoints.

Conversation records have no new automatic expiry. They live on the persistent
application volume and survive app restarts/deploys; they are not independently
backed up by this feature. The public composer discloses that the site owner may review
saved conversations. Decide a retention/backup policy before treating the PoC as
a permanent archive.
