# Grok Bot connection proof

The owner portal at `/admin` has a **Job search · Grok Bot** section. It sends
assignments to a webhook-triggered routine on your existing Grok Bot. The Bot
acknowledges and submits reports through `/grok-mcp`, a separate authenticated
Streamable HTTP MCP endpoint. No heartbeat, scheduled check or polling agent is
used. The browser's Refresh button only reads results already saved by callbacks.

This implements the transport and original-report intake. It does not yet merge
contacts or opportunities into accepted records, reconcile conflicts, publish
knowledge, or give the harness model autonomous dispatch tools.

## Configure the server and existing Bot

1. Start with a deployment of this branch that Grok Bot can reach over HTTPS. Set
   `PUBLIC_ORIGIN` to that app's canonical origin. For an isolated local instance,
   a separately configured HTTPS tunnel can expose it; do not change or stop the
   serving instance. Hosting and credential setup remain owner-operated.
2. In the existing Bot, use the routine setup instructions shown in admin. Create
   a webhook-triggered routine without a schedule. Its routine instruction must
   require acknowledgement before work and result submission afterwards.
3. On desktop, open the routine's Webhook section. Copy **POST to** into
   `GROKBOT_WEBHOOK_URL` and **key** into `GROKBOT_WEBHOOK_KEY` in the server's
   private environment. This uses the Grok **Bot** routine protocol: HTTP POST
   with `Authorization: Bearer <key>`, JSON input and HTTP 200 acceptance.
   The separate Grok chat automation product documents a different signed
   webhook protocol; do not substitute those endpoints or keys.
4. Generate an independent random credential, for example with
   `python3 -c 'import secrets; print(secrets.token_urlsafe(32))'`, and store it
   as `GROKBOT_MCP_TOKEN` in the private server environment. Do not put it in chat,
   source files, reports or the webhook payload.
5. Add the private MCP URL shown in admin to Grok Bot's connected tools. Configure
   its `Authorization: Bearer <GROKBOT_MCP_TOKEN>` header through the provider's
   secure credential flow. The server intentionally uses this separate bearer
   credential, not the owner's password or the broad admin OAuth connection.
   Whether your account's connector supports custom bearer headers and unattended
   calls must be verified. Do not broaden permissions to work around a failure.
6. Restart only the app instance being configured, sign into admin, and refresh
   the connection section. Configuration indicators establish presence of values,
   not reachability or a successful handshake.
7. Choose **Send synthetic connection test**. Expect delivered, acknowledged, then
   completed with `SYNTHETIC CONNECTION OK`. Refresh to see server callbacks.
8. Once that succeeds, choose **Request job-search report**. The server also
   enforces the successful-test prerequisite. This asks only for existing
   information, with no outreach, research, publishing or spending.

Official references:
[Grok Bot webhook routines](https://docs.x.ai/grok-bot/skills-routines-and-automations)
and [custom MCP tools](https://docs.x.ai/grok-bot/how-grok-bot-works).
The local test suite verifies the application side against a fake webhook
receiver and a real MCP transport. A successful live Grok Bot round trip is still
required before this integration is considered connected.

## Callback contract

The webhook body contains `schema_version`, `task_id`, `action`, `instruction`,
`mcp_endpoint`, and `protocol`. There are two fixed actions: `connection_test` and
`export_job_search`. Owner-authenticated HTTP routes create and dispatch tasks;
there is no caller-supplied destination or arbitrary instruction field.

The private MCP endpoint exposes exactly two tools:

- `acknowledge_assignment(task_id, claim_id)`: generate a unique claim ID for each
  run. Only a response with `should_run=true` authorizes that run to execute.
  A duplicate acknowledgement, including one with the same claim ID, returns
  false. If an acknowledgement response is lost, stop and have the owner inspect
  the claim rather than risk running the work twice.
- `submit_result(task_id, claim_id, result)`: result contains `submission_id`,
  `outcome` (`completed`, `blocked`, or `failed`), `report` (up to 20,000 characters),
  and `complete_export` (defaults to false). Retry uncertain submissions with the
  exact same ID and content. Conflicting results cannot overwrite a report.
  The report can include source URLs and original agent record IDs as text.

The first import preserves the submitted report, rather than claiming to retrieve
all hidden memory or complete conversation history. Reports must label drafts,
confirmed sends and missing sources. Truncated or incomplete exports should set
`complete_export=false`; larger exports and structured reconciliation are future
work. Report content is untrusted and rendered as text, never executed.

## Delivery and failure behavior

Assignments are committed before dispatch. A synchronous bounded webhook attempt
runs in the API's thread pool. HTTP 200 records delivery acceptance only. Other
HTTP responses are rejected; a timeout or transport/configuration error is
unknown because acceptance may be uncertain. Provider bodies and exceptions are
not returned or retained. The original task can be explicitly retried, always
with the same task ID. There is no automatic background delivery retry yet.

Acknowledgements claim tasks transactionally, so duplicate deliveries cannot
intentionally execute the same assignment twice. This is not an exactly-once
execution guarantee. A Bot that claims a task and then crashes or cannot report
back leaves it acknowledged. There is no automatic claim expiry or takeover;
inspect Grok Bot's routine run history before creating a replacement assignment.
An app restart during dispatch can leave a sending task; retry is available after
30 seconds. MCP callback results win over a later webhook acceptance response.

Changing the callback credential takes effect on the next request. Removing it
disables MCP access and blocks dispatch. Removing the webhook credentials stops
successful dispatch but preserves existing assignments and reports. The private
MCP tools cannot read profiles, visitor conversations or site configuration.

## Storage and compatibility

This adds one additive `grok_assignments` table to the external `harness.sqlite`
database on first use. Existing tables and Temporal workflow definitions are
unchanged; there are no dependency or workflow replay changes. Back up the
persistent application volume as usual. Rolling back the application leaves the
new table intact. Original reports are private runtime data outside Git and are
not automatically indexed or exposed through public APIs or the public MCP.

Validation uses synthetic reports and fake upstream responses, without model
calls or API keys:

```sh
python -m pytest tests/test_grok_connection.py -q
```
