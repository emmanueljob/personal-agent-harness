# Private Gmail Activity capture

The owner connects Gmail in CRM → Connections. Authorization requests only identity
and `gmail.readonly`, with offline access. It reuses `/admin-auth/callback` from the
existing admin Google client unless a separate client is configured. Gmail state,
nonce, browser cookie, PKCE and exact chosen mailbox are verified independently of
admin MCP authorization. Connecting Gmail does not authorize sending messages.

Enable Gmail API in the OAuth client's Google Cloud project. Configure the OAuth
consent screen to permit the owner and Gmail read-only scope. Testing-mode external
Google apps may issue short-lived refresh tokens; use a suitable publishing state
for unattended access. The owner must grant the new Gmail permission in Google.
The Codex Gmail connection does not supply tokens to this application.

For push delivery, create a Pub/Sub topic in the same project and grant
`gmail-api-push@system.gserviceaccount.com` the Pub/Sub Publisher role on that topic
only. Create a dedicated push service account and an authenticated push subscription
with endpoint `https://YOUR_ORIGIN/api/hooks/gmail`. Configure Google's required
service-agent token-creation permission and subscription creator's service-account
act-as permission as documented by Google. Set its OIDC audience to that endpoint.
Set these private runtime variables (never commit real values):

- `GMAIL_PUBSUB_TOPIC`: `projects/PROJECT/topics/TOPIC`
- `GMAIL_PUBSUB_AUDIENCE`: exact configured OIDC audience
- `GMAIL_PUBSUB_SERVICE_ACCOUNT`: dedicated authenticated push identity's email
- Optional `GMAIL_GOOGLE_CLIENT_ID`, `GMAIL_GOOGLE_CLIENT_SECRET`: otherwise existing
  `ADMIN_GOOGLE_CLIENT_ID` and `ADMIN_GOOGLE_CLIENT_SECRET` are reused.

Only a correctly signed Google JWT with that audience and verified service-account
email can enqueue a notification. The linked mailbox must also match. The webhook
acknowledges after saving the durable job. Google push carries a history cursor,
not email content. The worker reads deltas using the mailbox's saved cursor and
advances it only after relevant messages are saved. Gmail watch registration renews
daily; it is not an inbox heartbeat or scheduled message check. Connection status
becomes “live” only after a verified push arrives.

Initial backfill searches the last 90 days for known company/contact identities and
job-related subjects, including both sides of matching threads. New events are
filtered against known identities, tracked threads and job-related subjects. Drafts,
spam and trash are excluded. Unknown matches stay saved and unlinked; they do not
invent contacts or companies. Generic mail-host domains do not establish company
identity. Review and manually link messages in Activity → Messages when needed.

Activity evidence records immutable message versions, provider IDs, original links,
actual sent/received timestamps and capture method. Owner-imported messages are
labeled separately from native provider observations. Incoming messages become
replies only when an earlier outbound message exists in the captured thread.
The UI renders plain text and does not load email HTML, remote images or attachments.
Existing manually entered and agent-reported activity remains in All activity.

Interrupted jobs recover from a durable SQLite lease and retry with bounded backoff.
Expired history cursors queue a fresh bounded backfill; revoked authorization or
project setup errors are visible in Connections. Sync now is an explicit recovery
action. Disconnect stops the watch, removes local tokens and cancels pending jobs;
already captured messages remain. A provider request already in flight can finish.

Tokens and pending OAuth state are encrypted in `gmail-oauth.sqlite`, with a separate
mode-0600 key on the persistent private application volume. Back up that key and vault
with the private runtime data; never publish them. The application database gains
additive `crm_messages`, `crm_message_versions`, `crm_gmail_connection` and
`crm_mail_jobs` tables. Existing workflows and CRM tables remain compatible.

References: [Gmail push](https://developers.google.com/workspace/gmail/api/guides/push),
[authenticated Pub/Sub push](https://docs.cloud.google.com/pubsub/docs/authenticate-push-subscriptions),
[Google OAuth web server flow](https://developers.google.com/identity/protocols/oauth2/web-server).

LinkedIn is deliberately unconnected pending selection of a separate adapter.
