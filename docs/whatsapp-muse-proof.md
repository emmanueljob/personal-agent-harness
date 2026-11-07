# Local Muse WhatsApp MCP connection proof

This is a local message-delivery proof for the owner's existing Muse. It uses a
separately installed, authenticated WhatsApp companion MCP and a narrow Python
MCP facade in `harness.whatsapp_muse`. It does not deploy WhatsApp sessions to
Fly, add public messaging routes, enable scheduled agent checks, or implement
Muse report ingestion yet.

## Scope and account access

The facade offers three tools: `whatsapp_muse_status`,
`send_muse_connection_test(request_id)`, and
`read_muse_connection_test(request_id)`. Sending is fixed to the known Muse bot
JID and a synthetic nonce prompt; callers cannot choose recipients, arbitrary
messages, media, contacts, or conversation exports. Reads return only an exact
nonce response from Muse in that same chat, never its other conversation text.
The optional LangChain tools are available only in private mode when
`WHATSAPP_MUSE_ENABLED=true`; public tools never receive them.

The underlying linked-device session has broader access than this facade. The
reviewed upstream server synchronizes and archives WhatsApp history locally.
Its bearer token must not be given directly to an agent, exposed on a public
network, committed, or included in a model prompt. Its send allowlist adds a
second boundary for Muse, but upstream allows self-messages too and its read
tools remain broader. Those tools are not forwarded through our facade.

These are unofficial WhatsApp companion protocols, not the Business Cloud API.
Review the external software and obtain owner permission before running and
pairing it. Pairing must be completed on the primary phone. It grants a WhatsApp
companion session, independently of linking the WhatsApp account to Muse.

## Reviewed companion implementation

The proof uses https://github.com/StephanMeijer/whatsapp-mcp at commit
`9b21e52d135576220bc5d5609738967ad792e4eb`. Its lockfile pins a November 2026
`whatsmeow` version with explicit Muse send support. The original lharries
implementation's older dependency pin predates that support.

Build the reviewed upstream source outside this repository with Go 1.27.3:

```sh
git clone https://github.com/StephanMeijer/whatsapp-mcp /absolute/private/tools/whatsapp-mcp-source
git -C /absolute/private/tools/whatsapp-mcp-source checkout 9b21e52d135576220bc5d5609738967ad792e4eb
cd /absolute/private/tools/whatsapp-mcp-source
go build -o /absolute/private/tools/whatsapp-mcp ./cmd/whatsapp-mcp
```

This is an external runtime tool; its source, binaries, Go caches and dependencies
are not vendored into the application or container image. The application adds
no Python dependencies. Re-review an upstream update before changing this pin.

## Initialize and pair

Use the repository's Python environment. `init` does not connect or pair an
account. It creates mode-600 configuration with an independent random bearer and
a loopback URL in the external application home:

```sh
python scripts/whatsapp_muse.py init --binary /absolute/private/tools/whatsapp-mcp
python scripts/whatsapp_muse.py serve
```

Keep `serve` running in its own terminal. It binds to `127.0.0.1` on the configured
port, forces the Muse send allowlist, disables group administration, status
posting and media paths, and sends diagnostics to the private integration log.
It does not install a background launch agent or change an existing service.
Transport reconnection and archive expiration housekeeping are upstream protocol
maintenance, not scheduled agent inbox checks.

There are two distinct phone links:

1. In Muse, open Settings → Messaging channels → WhatsApp. Scan its linking code
   with the phone camera and complete the WhatsApp prompt. This links your
   WhatsApp identity to the existing Muse account.
2. In a second terminal, run `python scripts/whatsapp_muse.py pair`. It prints the
   path of a private, uniquely named PNG. Scan this companion QR using WhatsApp →
   Settings → Linked devices → Link a device. This authorizes the local MCP.

QR codes expire. Fetch a fresh image and use the new path to avoid cached codes.
If all offered codes have expired, stop only this proof's `serve` process and
start it again before fetching a fresh image. Never reset the session database
or unlink unrelated devices to refresh a QR.

## Run the smoke test through MCP

```sh
python scripts/whatsapp_muse.py status
python scripts/whatsapp_muse.py test --request-id synthetic-test-0001
python scripts/whatsapp_muse.py reply --request-id synthetic-test-0001
```

The CLI launches the actual stdio facade and calls its MCP tools, which use a
separate authenticated Streamable HTTP MCP session to reach the companion.
`status` reports connected/paired state without exposing the account identity.
The test asks Muse to reply exactly `HARNESS_WHATSAPP_OK_<request_id>` and forbids
private-source access, applications, outreach, publishing and spending.

A send receipt is not proof of a reply. `reply` performs one explicit read of the
local archive populated by upstream message events. A verified result requires
an exact nonce response from Muse, not a copy of our own outgoing message. Rich
response decoding depends on the upstream bridge: lack of a matching archived
response is not proof that Muse did not receive or answer the message. Check the
phone's actual Muse conversation before diagnosing a parsing problem.

The live proof confirmed delivery through MCP and an exact nonce response in
Muse's WhatsApp side chat. The pinned companion did not archive the incoming
response, so the MCP read tool still reported `verified: false`. Its text
extractor does not handle `RichResponseMessage` and drops unrecognized message
types. This is an identified compatibility gap, not evidence that this specific
reply reached that extractor: receipt/decryption also needs diagnosis. Do not
treat this version as a complete inbound results channel or import existing
knowledge through it until a reply has passed the MCP verification path.

Request IDs are persisted before sending. Concurrent/repeated IDs do not send
again. A timeout, cancellation or rejected operation conservatively becomes
`unknown`; crashes can leave `sending`. Neither state is automatically retried.
Inspect the actual chat and resolve the blocker before creating a new test ID.
No background polling or automatic send retry is installed.

## Private runtime and harness configuration

On macOS, defaults live under the external Afterhours application home:
`integrations/whatsapp-muse/`. Override with `WHATSAPP_MUSE_HOME` if needed, but
never point it into the source checkout. This directory contains connection
configuration, the upstream session/archive, pairing images, private logs and
`proof.sqlite`. The proof database contains only request IDs and send status.
Do not copy any of these files into a build or PR. The initialization launcher
uses umask 077; the facade rejects group/world-readable credential files.

For a local private harness worker, set `WHATSAPP_MUSE_ENABLED=true` in its
private environment and restart only that instance after adopting this code.
The companion must be running on the same Mac. This flag does not connect the
public Fly worker to a Mac's loopback interface. Remote control from the hosted
admin workspace and Muse's authenticated report callback remain future work.

To connect another trusted MCP client directly to the narrow facade:

```json
{
  "mcpServers": {
    "muse-connection-proof": {
      "command": "/absolute/path/to/python",
      "args": ["-m", "harness.whatsapp_muse"],
      "env": {"PYTHONPATH": "/absolute/path/to/source-checkout"}
    }
  }
}
```

No bearer token belongs in this client configuration. The facade loads its
separate private file and has no general-purpose tool forwarding operation.

## Verification and compatibility

Synthetic tests cover real stdio and Streamable HTTP MCP transports, fixed
recipient/message routing, private-mode gating, credential-file permissions,
nonce filtering, unknown sends and duplicate suppression. They do not pair real
accounts, call a model, or contact WhatsApp.

```sh
python -m pytest tests/test_whatsapp_muse.py -q
```

The existing application/Temporal databases and workflows are unchanged. The new
proof database is separate local runtime state. The existing Grok Bot connection
is independent. Stop this proof's server to stop local MCP access; revoke its
specific linked device from the phone if the owner wants to remove its account
session. Neither step should touch other linked devices or the public app.
