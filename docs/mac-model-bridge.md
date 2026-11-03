# Mac model bridge

The cloud retains the agent, tools, public knowledge and Temporal durability. The
Mac runs only model inference. Ollama stays bound to `127.0.0.1:11434`; an outbound
worker connects to the public app over authenticated HTTPS, sends model availability
heartbeats, claims queued inference work and posts results. There is no router port
forward, public Ollama listener, VPN dependency or incoming Mac service.

`MODEL_PROVIDER=hybrid` selects the configured `OLLAMA_MODEL` when an authenticated
heartbeat is less than 25 seconds old. Otherwise new turns select OpenAI, which still
requires funded API billing. The provider and model are snapshotted per turn, so
retries keep their original model. An old OpenAI failure will not switch providers
when retried; start a new turn to use a newly connected Mac. A Mac disconnect during
inference may fail that turn; new turns use the fallback once the heartbeat expires.

Install `scripts/mac_model_worker.py` in a private local directory. Its `--config`
file contains `origin` (the app's HTTPS origin), `model`, and a random `token`. Keep
this file outside Git with owner-only permissions. Install the same token as Fly's
`MODEL_BRIDGE_TOKEN` secret. Run the worker as a per-user LaunchAgent while logged
in. The worker checks that the configured model is installed before advertising
availability. Stop it to test offline fallback; no model call is needed to inspect
`/api/status` and confirm selection.

The bridge endpoints are excluded from the public API catalog and schemas. Every
endpoint requires the token, and the inference submission endpoint additionally
requires a loopback caller. Only Ollama chat fields are forwarded. Claims have
per-job secrets so stale or forged completions cannot overwrite another claim.
Bodies and pending work are bounded. Successful, failed and timed-out jobs are
removed; expired work is also pruned on heartbeats. Payloads are transiently stored
on the cloud volume and sent to the owner's Mac, never committed to source.

The worker receives public-chat model context, including the public profile and
published reference excerpts. It does not sync the local private brain. Model
output remains untrusted like any other model response. This is at-least-once
inference: a crash before a checkpoint can repeat a call.

The root hostname can be listed in `REDIRECT_HOSTS`; safe GET/HEAD requests receive
a 308 redirect to the fixed configured `PUBLIC_ORIGIN`, preserving path and query.
Writes to an alias are rejected rather than replayed to another hostname. DNS and
a Fly certificate must also be configured for each alias. Google mail records
remain independent of web A/AAAA records.
